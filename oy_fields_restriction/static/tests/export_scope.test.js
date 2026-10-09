import { describe, expect, test } from "@odoo/hoot";
import { mockFetch } from "@odoo/hoot-mock";
import { ExportDataDialog } from "@web/views/view_dialogs/export_data_dialog";
import { getScopedExportFields } from "@oy_fields_restriction/ui_scope";

describe.current.tags("headless");

const context = { oy_field_action_id: 101, oy_field_menu_id: 11 };
const root = {
    resModel: "res.partner", domain: [["active", "=", true]],
    isDomainSelected: false, selection: [{ resId: 7 }],
};

test("export field picker sends scope and selected records", async () => {
    mockFetch((url, { body }) => {
        expect(url).toBe("/web/export/get_fields");
        expect(JSON.parse(body).params).toEqual({
            model: "res.partner", domain: [["id", "in", [7]]],
            import_compat: true, context,
        });
        return { result: [{ id: "name", string: "Name" }] };
    });
    expect(await getScopedExportFields(root, context, true)).toEqual([{ id: "name", string: "Name" }]);
});

test("related export fields retain the action and menu scope", async () => {
    mockFetch((url, { body }) => {
        expect(JSON.parse(body).params).toEqual({
            model: "res.country", prefix: "country_id", domain: [],
            import_compat: false, context,
        });
        return { result: [] };
    });
    await getScopedExportFields({ ...root, isDomainSelected: true }, context, false,
        { model: "res.country", prefix: "country_id" });
});

test("saved export template uses current scope and compatibility mode", async () => {
    mockFetch((url, { body }) => {
        expect(url).toBe("/web/export/namelist");
        expect(JSON.parse(body).params).toEqual({
            model: "res.partner", export_id: 8, context, import_compat: true,
        });
        return { result: { fields: [{ id: "name" }], export_languages: ["fr_FR"] } };
    });
    const dialog = {
        props: { root, context }, state: {}, isCompatible: true,
        languagesInstalled: [{ code: "en_US" }, { code: "fr_FR" }],
    };
    await ExportDataDialog.prototype.loadExportList.call(dialog, "8");
    expect(dialog.state.exportList).toEqual([{ id: "name" }]);
    expect(dialog.state.exportLanguages).toEqual([{ code: "fr_FR" }]);
});
