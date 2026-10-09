/** @odoo-module **/

import { useSubEnv } from "@odoo/owl";
import { router } from "@web/core/browser/router";
import { DomainSelectorDialog } from "@web/core/domain_selector_dialog/domain_selector_dialog";
import { fieldService } from "@web/core/field_service";
import { rpc } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";
import { View } from "@web/views/view";
import { viewService } from "@web/views/view_service";
import { ExportDataDialog } from "@web/views/view_dialogs/export_data_dialog";
import { menuService } from "@web/webclient/menus/menu_service";

export function uiScope(context = {}, actionId, menus, route = {}) {
    const action = Number(actionId ?? context.oy_field_action_id) || false;
    let menu = menus?.getMenu(Number(context.oy_field_menu_id ?? route.oy_field_menu_id));
    if (!action || Number(menu?.actionID) !== action) {
        menu = undefined;
    }
    // An action with several menu entries needs an explicit origin. Never guess
    // between those menus when opening an action directly.
    if (!menu && action && context.oy_field_menu_id === undefined) {
        const candidates = menus?.getAll().filter((item) => Number(item.actionID) === action) || [];
        if (candidates.length === 1) {
            menu = candidates[0];
        }
    }
    return { oy_field_action_id: action, oy_field_menu_id: menu?.id || false };
}

patch(menuService, {
    async start(env, ...args) {
        const service = await super.start(env, ...args);
        router.addLockedKey("oy_field_menu_id");
        service.selectMenu = async (menu) => {
            menu = typeof menu === "number" ? service.getMenu(menu) : menu;
            if (!menu?.actionID) {
                return;
            }
            await env.services.action.doAction(menu.actionID, {
                clearBreadcrumbs: true,
                additionalContext: { oy_field_menu_id: menu.id },
                onActionReady: () => {
                    service.setCurrentMenu(menu);
                    router.pushState({ oy_field_menu_id: menu.id });
                },
            });
        };
        return service;
    },
});

patch(fieldService, {
    start(env, { orm }) {
        const start = super.start.bind(this);
        const service = start(env, { orm });
        const scopes = new Map();
        // Native field-service caches are shared only within the same UI scope.
        // The native CLEAR-CACHES handler also invalidates each scoped cache.
        service.forContext = (context) => {
            const scope = {
                oy_field_action_id: context.oy_field_action_id || false,
                oy_field_menu_id: context.oy_field_menu_id || false,
            };
            const key = JSON.stringify(scope);
            if (!scopes.has(key)) {
                const scopedOrm = Object.create(orm);
                const cache = new Map();
                env.bus.addEventListener("CLEAR-CACHES", () => cache.clear());
                scopedOrm.call = function (model, method, args, kwargs = {}) {
                    const scopedKwargs = { ...kwargs, context: { ...kwargs.context, ...scope } };
                    const cacheKey = JSON.stringify([model, method, args, scopedKwargs]);
                    // Permissions are cached only for this page and scope, never on disk.
                    // A browser reload or CLEAR-CACHES must read the current rules.
                    const target = this._silent ? orm.silent : orm;
                    if (method !== "fields_get") {
                        return target.call(model, method, args, scopedKwargs);
                    }
                    if (!cache.has(cacheKey)) {
                        const pending = target.call(model, method, args, scopedKwargs);
                        cache.set(cacheKey, pending);
                        pending.catch(() => cache.delete(cacheKey));
                    }
                    return cache.get(cacheKey);
                };
                const scoped = start(env, { orm: scopedOrm });
                scoped.forContext = service.forContext;
                scopes.set(key, scoped);
            }
            return scopes.get(key);
        };
        return service;
    },
});

// View metadata contains user-specific restriction flags. Odoo 19+ caches
// get_views on disk, so scoped views must fetch current flags after a reload.
patch(viewService, {
    start(env, dependencies) {
        const orm = dependencies.orm;
        const scopedOrm = Object.create(orm);
        scopedOrm.call = function (model, method, args, kwargs = {}) {
            if (method === "get_views" && "oy_field_action_id" in (kwargs.options || {})) {
                const target = this._silent ? orm.silent : orm;
                return target.call(model, method, args, kwargs);
            }
            return orm.call.call(this, model, method, args, kwargs);
        };
        return super.start(env, { ...dependencies, orm: scopedOrm });
    },
});

patch(View.prototype, {
    _oyScopedProps(props) {
        this.oyFieldScope = uiScope(props.context, this.env.config?.actionId, this.env.services.menu, router.current);
        return { ...props, context: { ...props.context, ...this.oyFieldScope } };
    },

    setup() {
        this._oyScopedProps(this.props);
        const services = this.env.services;
        const field = Object.create(services.field);
        for (const method of ["loadFields", "loadPath", "loadPropertyDefinitions", "loadFieldInfo", "loadPathDescription"]) {
            field[method] = (...args) => (services.field.forContext?.(this.oyFieldScope) || services.field)[method](...args);
        }
        const view = Object.create(services.view);
        view.loadViews = (params, options = {}) => services.view.loadViews(params, {
            ...this.oyFieldScope, ...options,
        });
        useSubEnv({ services: { ...services, field, view } });
        super.setup();
    },

    loadView(props) {
        return super.loadView(this._oyScopedProps(props));
    },

    onWillUpdateProps(props) {
        const previous = JSON.stringify(this.oyFieldScope);
        const scopedProps = this._oyScopedProps(props);
        if (previous !== JSON.stringify(this.oyFieldScope)) {
            return this.loadView(scopedProps);
        }
        return super.onWillUpdateProps(scopedProps);
    },
});

// Dialogs are mounted outside the view's sub-environment. Their explicit context
// restores the same field service for custom filters and relational field pickers.
patch(DomainSelectorDialog.prototype, {
    setup() {
        const services = this.env.services;
        if (services.field.forContext) {
            useSubEnv({ services: { ...services, field: services.field.forContext(this.props.context || {}) } });
        }
        super.setup();
    },
});

// Export moved from ListController to the shared export hook in Odoo 19.
// Keep the scope at the dialog boundary for both list and other supported views.
export async function getScopedExportFields(root, context, importCompat, parentParams) {
    let domain = parentParams ? [] : root.domain;
    if (!root.isDomainSelected && root.selection.length) {
        domain = [["id", "in", root.selection.map((record) => record.resId)]];
    }
    return rpc("/web/export/get_fields", {
        model: root.resModel, domain, import_compat: importCompat,
        ...parentParams, context,
    });
}

patch(ExportDataDialog.prototype, {
    async loadFields(id, preventLoad = false) {
        let parentField, parentParams;
        if (id) {
            if (this.expandedFields[id]) {
                return this.expandedFields[id].fields;
            }
            parentField = this.knownFields[id];
            parentParams = {
                ...parentField.params,
                parent_field_type: parentField.field_type,
                parent_field: parentField,
                parent_name: parentField.string,
                exclude: [parentField.relation_field],
            };
        }
        if (preventLoad) {
            return;
        }
        const fields = await getScopedExportFields(
            this.props.root, this.props.context, this.isCompatible, parentParams
        );
        for (const field of fields) {
            field.parent = parentField;
            if (!this.knownFields[field.id]) {
                this.knownFields[field.id] = field;
            }
        }
        if (id) {
            this.expandedFields[id] = { fields };
        }
        return fields;
    },

    async loadExportList(value) {
        this.state.templateId = value === "new_template" ? value : Number(value);
        this.state.isEditingTemplate = value === "new_template";
        if (!value || value === "new_template") {
            return;
        }
        const templateId = this.state.templateId;
        const importCompat = this.isCompatible;
        const fields = await rpc("/web/export/namelist", {
            model: this.props.root.resModel,
            export_id: Number(value),
            context: this.props.context,
            import_compat: importCompat,
        });
        if (this.state.templateId === templateId && this.isCompatible === importCompat) {
            this.state.exportList = fields;
        }
    },
});
