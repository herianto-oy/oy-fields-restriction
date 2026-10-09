/** @odoo-module **/

import { Domain } from "@web/core/domain";
import { DomainSelector } from "@web/core/domain_selector/domain_selector";
import { DomainSelectorDialog } from "@web/core/domain_selector_dialog/domain_selector_dialog";
import { getDefaultDomain } from "@web/core/domain_selector/utils";
import { _t } from "@web/core/l10n/translation";
import { evaluateExpr } from "@web/core/py_js/py";
import { condition } from "@web/core/tree_editor/condition_tree";
import { patch } from "@web/core/utils/patch";
import { SearchModel } from "@web/search/search_model";

export function fieldAllowed(fields, path, operation = "search") {
    if (typeof path !== "string") {
        return true; // Constant domains such as (1, '=', 1).
    }
    const name = path.split(/[.:]/)[0];
    return fields[name]?.[`oy_${operation}_allowed`] !== false &&
        fields[path]?.[`oy_${operation}_allowed`] !== false;
}

export function domainPaths(domain) {
    try {
        return new Domain(domain || []).ast.value
            .filter((leaf) => Array.isArray(leaf.value))
            .map((leaf) => leaf.value[0]?.value)
            .filter((path) => typeof path === "string");
    } catch {
        return [];
    }
}

export function domainAllowed(fields, domain) {
    if (!domain) {
        return true;
    }
    try {
        // Inspect field names without evaluating uid, self, dates, or context expressions.
        return new Domain(domain).ast.value.every((leaf) => {
            if (!Array.isArray(leaf.value)) {
                return true;
            }
            return fieldAllowed(fields, leaf.value[0]?.value);
        });
    } catch {
        // Let Odoo handle unsupported domains normally.
        return true;
    }
}

export function selectableFields(fields) {
    return Object.fromEntries(Object.entries(fields).filter(([, field]) =>
        field.searchable && field.oy_search_allowed !== false &&
        !["json", "separator"].includes(field.type)
    ));
}

export function searchItemAllowed(fields, item, context = {}) {
    const operation = ["groupBy", "dateGroupBy"].includes(item.type) ? "group_by" : "search";
    if (!fieldAllowed(fields, item.propertyFieldName || item.fieldName, operation)) {
        return false;
    }
    // A <field domain="..."> limits relational suggestions on its comodel;
    // filter_domain and filter/favorite domains target the searched model.
    const domain = item.type === "field" ? null : item.domain;
    if (!domainAllowed(fields, domain) || !domainAllowed(fields, item.filterDomain)) {
        return false;
    }
    if (item.comparison && !fieldAllowed(fields, item.comparison.fieldName)) {
        return false;
    }
    let itemContext = item.context || {};
    try {
        if (typeof itemContext === "string") {
            itemContext = evaluateExpr(itemContext, context);
        }
    } catch {
        itemContext = {};
    }
    const groupBys = item.groupBys || itemContext.group_by || [];
    if (!(Array.isArray(groupBys) ? groupBys : [groupBys]).every((name) =>
        fieldAllowed(fields, name, "group_by")
    )) {
        return false;
    }
    return (item.optionsParams?.customOptions || []).every((option) => domainAllowed(fields, option.domain));
}

patch(SearchModel.prototype, {
    _oyItemAllowed(item) {
        return !!item && searchItemAllowed(this.searchViewFields || {}, item, this.domainEvalContext);
    },

    _createGroupOfSearchItems(items) {
        return super._createGroupOfSearchItems(items.filter((item) => this._oyItemAllowed(item)));
    },

    _activateDefaultSearchItems(favoriteId) {
        // A default favorite may have been excluded while building the menu.
        return super._activateDefaultSearchItems(this.searchItems[favoriteId] ? favoriteId : null);
    },

    _enrichItem(item) {
        return this._oyItemAllowed(item) ? super._enrichItem(item) : null;
    },

    async load(config) {
        await super.load(config);
        this.searchViewFields = { ...this.searchViewFields };
        await this._oyLoadDomainFields(Object.values(this.searchItems).flatMap((item) => [
            item.type === "field" ? null : item.domain,
            item.filterDomain,
            ...(item.optionsParams?.customOptions || []).map((option) => option.domain),
        ]));
        // Restored breadcrumbs contain search items and active queries from a previous view.
        this.query = this.query.filter((entry) => this._oyItemAllowed(this.searchItems[entry.searchItemId]));
        this._reset();
    },

    _importState(state) {
        super._importState(state);
        this.query = this.query.filter((entry) => this._oyItemAllowed(this.searchItems[entry.searchItemId]));
    },

    _getGroupBy(options = {}) {
        return super._getGroupBy(options).filter((name) => fieldAllowed(this.searchViewFields, name, "group_by"));
    },

    async _oyLoadDomainFields(domains) {
        const paths = new Set(domains.flatMap(domainPaths).filter((path) => path.includes(".")));
        await Promise.all([...paths].map(async (path) => {
            const { modelsInfo, names, isInvalid } = await this.fieldService.loadPath(this.resModel, path);
            if (!isInvalid) {
                const allowed = modelsInfo.every(({ fieldDefs }, index) =>
                    fieldDefs[names[index]]?.oy_search_allowed !== false
                );
                // Preserve metadata used by property searches, if already present.
                this.searchViewFields[path] = { ...this.searchViewFields[path], oy_search_allowed: allowed };
            }
        }));
    },

    createNewGroupBy(name, options = {}) {
        if (fieldAllowed(this.searchViewFields, name, "group_by")) {
            return super.createNewGroupBy(name, options);
        }
    },

    async splitAndAddDomain(domain, groupId) {
        await this._oyLoadDomainFields([domain]);
        if (!domainAllowed(this.searchViewFields, domain)) {
            this.env.services.notification.add(_t("This filter uses a field restricted by your search configuration."), { type: "warning" });
            return;
        }
        return super.splitAndAddDomain(domain, groupId);
    },

    async spawnCustomFilterDialog() {
        if (!Object.values(this.searchViewFields).some((field) => field.oy_search_allowed === false)) {
            return super.spawnCustomFilterDialog();
        }
        const fields = selectableFields(this.searchViewFields);
        if (!Object.keys(fields).length) {
            this.env.services.notification.add(_t("No fields are available for custom filters."), { type: "info" });
            return;
        }
        this.dialog.add(DomainSelectorDialog, {
            resModel: this.resModel,
            defaultConnector: "|",
            domain: getDefaultDomain(fields),
            context: this.domainEvalContext,
            onConfirm: (domain) => this.splitAndAddDomain(domain),
            disableConfirmButton: (domain) => domain === "[]" || !domainAllowed(this.searchViewFields, domain),
            title: _t("Add Custom Filter"),
            confirmButtonText: _t("Add"),
            discardButtonText: _t("Cancel"),
            isDebugMode: this.isDebugMode,
        });
    },
});

patch(DomainSelector.prototype, {
    getDefaultCondition(fields) {
        if (!Object.values(fields).some((field) => field.oy_search_allowed === false)) {
            return super.getDefaultCondition(fields);
        }
        const allowed = selectableFields(fields);
        // The constant condition lets an empty selector render without picking a denied field.
        if (!Object.keys(allowed).length) {
            return condition(1, "=", 1);
        }
        return super.getDefaultCondition(allowed);
    },

    async onPropsUpdated(props) {
        this.oyFields = await this.fieldService.loadFields(props.resModel);
        return super.onPropsUpdated(props);
    },

    getShowArchivedCheckBox(hasActiveField, props) {
        return this.oyFields?.active?.oy_search_allowed !== false &&
            super.getShowArchivedCheckBox(hasActiveField, props);
    },
});
