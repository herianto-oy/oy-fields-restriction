/** @odoo-module **/

import { BaseImportModel } from "@base_import/import_model";
import { patch } from "@web/core/utils/patch";

patch(BaseImportModel.prototype, {
    async init() {
        const importModel = this;
        const orm = this.orm;
        const scopedOrm = Object.create(orm);
        // Native create/parse_preview omit context, unlike execute_import.
        // Scope only this import model's wizard calls, including preview reloads.
        scopedOrm.call = function (model, method, args = [], kwargs = {}) {
            if (model === "base_import.import") {
                kwargs = {
                    ...kwargs,
                    context: { ...importModel.context, ...kwargs.context },
                };
            }
            const target = this._silent ? orm.silent : orm;
            return target.call(model, method, args, kwargs);
        };
        this.orm = scopedOrm;
        return super.init();
    },
});
