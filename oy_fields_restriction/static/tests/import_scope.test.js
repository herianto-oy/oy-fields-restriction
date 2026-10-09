import { describe, expect, test } from "@odoo/hoot";
import { Component, xml } from "@odoo/owl";
import { BaseImportModel } from "@base_import/import_model";
import { mountWithCleanup } from "@web/../tests/web_test_helpers";
import "@oy_fields_restriction/import_scope";

describe.current.tags("headless");

class ImportHost extends Component {
    static template = xml`<div/>`;
    static props = ["context", "orm"];
    setup() {
        this.model = new BaseImportModel({
            env: this.env, context: this.props.context, orm: this.props.orm,
        });
        this.model.setResModel("res.partner");
    }
}

test("import preview and reload retain scope without leaking to another import", async () => {
    const calls = [];
    let nextId = 0;
    const orm = {
        get silent() {
            return Object.assign(Object.create(this), { _silent: true });
        },
        async call(model, method, args, kwargs = {}) {
            calls.push({ model, method, context: kwargs.context, silent: Boolean(this._silent) });
            if (method === "create") {
                return ++nextId;
            }
            if (method === "get_import_templates") {
                return [];
            }
            if (method === "execute_import") {
                return { messages: [] };
            }
            if (method === "parse_preview") {
                const restricted = kwargs.context?.oy_field_menu_id === 11;
                return {
                    fields: (restricted ? ["name"] : ["name", "email"]).map((name) => ({
                        id: name, name, string: name, type: "char", fields: [], required: false,
                        model_name: "res.partner",
                    })),
                    matches: restricted ? { 0: ["name"] } : { 0: ["name"], 1: ["email"] },
                    headers: ["name", "email"], header_types: [["char"], ["char"]],
                    preview: [["Test"], ["test@example.com"]],
                    options: { has_headers: true, date_format: "%Y-%m-%d", datetime_format: "%Y-%m-%d %H:%M:%S" },
                    advanced_mode: false,
                };
            }
            throw new Error(`Unexpected RPC: ${model}.${method}`);
        },
    };
    const originalCall = orm.call;
    const a = await mountWithCleanup(ImportHost, {
        props: { orm, context: { oy_field_action_id: 101, oy_field_menu_id: 11 } },
    });
    const b = await mountWithCleanup(ImportHost, {
        props: { orm, context: { oy_field_action_id: 101, oy_field_menu_id: 12 } },
    });
    await a.model.init();
    await b.model.init();
    await a.model.updateData(true);
    expect(a.model.fields.map((field) => field.name)).toEqual(["name"]);
    expect(a.model.columns[1].fieldInfo).toBe(undefined);
    await b.model.updateData(true);
    expect(b.model.fields.map((field) => field.name)).toEqual(["name", "email"]);
    await a.model.updateData();
    expect(a.model.fields.map((field) => field.name)).toEqual(["name"]);
    expect(calls.filter((call) => call.method === "parse_preview").map((call) => call.context.oy_field_menu_id)).toEqual([11, 12, 11]);
    expect(calls.filter((call) => call.method === "create").map((call) => call.context.oy_field_menu_id)).toEqual([11, 12]);
    await a.model._callImport(true, [a.model.id, ["name"], ["name"], {}]);
    expect(calls.at(-1).context.oy_field_menu_id).toBe(11);
    expect(calls.at(-1).silent).toBe(true);
    expect(orm.call).toBe(originalCall);
});
