import { describe, expect, test } from "@odoo/hoot";
import { EventBus } from "@odoo/owl";
import { fieldService } from "@web/core/field_service";
import { uiScope } from "@oy_fields_restriction/ui_scope";

describe.current.tags("headless");

const entries = [
    { id: 11, actionID: 101 },
    { id: 12, actionID: 101 },
    { id: 13, actionID: 102 },
];
const menus = {
    getMenu: (id) => entries.find((menu) => menu.id === id),
    getAll: () => entries,
};

test("two menus sharing one action keep distinct origins", () => {
    expect(uiScope({ oy_field_menu_id: 11 }, 101, menus)).toEqual({ oy_field_action_id: 101, oy_field_menu_id: 11 });
    expect(uiScope({ oy_field_menu_id: 12 }, 101, menus)).toEqual({ oy_field_action_id: 101, oy_field_menu_id: 12 });
    expect(uiScope({}, 101, menus)).toEqual({ oy_field_action_id: 101, oy_field_menu_id: false });
});

test("refresh restores a menu only for its action", () => {
    expect(uiScope({}, 101, menus, { oy_field_menu_id: 12 }).oy_field_menu_id).toBe(12);
    expect(uiScope({}, 102, menus, { oy_field_menu_id: 12 }).oy_field_menu_id).toBe(13);
    expect(uiScope({}, 999, menus, { oy_field_menu_id: 12 }).oy_field_menu_id).toBe(false);
    expect(uiScope({}, undefined, menus, { oy_field_menu_id: 12 }).oy_field_menu_id).toBe(false);
});

test("relational dialogs inherit explicit scope without a window action id", () => {
    expect(uiScope({ oy_field_action_id: 101, oy_field_menu_id: 12 }, undefined, menus)).toEqual({
        oy_field_action_id: 101, oy_field_menu_id: 12,
    });
});

test("field caches do not leak between menus and carry scope into relational paths", async () => {
    const calls = [];
    const env = { bus: new EventBus() };
    const orm = {
        cache() { return this; },
        async call(model, method, args, kwargs) {
            calls.push({ model, method, context: kwargs.context });
            return {
                email: { type: "char", searchable: kwargs.context.oy_field_menu_id === 12 },
                country_id: { type: "many2one", relation: "res.country" },
                name: { type: "char" },
            };
        },
    };
    const service = fieldService.start(env, { orm });
    const a = service.forContext({ oy_field_action_id: 101, oy_field_menu_id: 11 });
    const b = service.forContext({ oy_field_action_id: 101, oy_field_menu_id: 12 });
    expect((await a.loadFields("res.partner")).email.searchable).toBe(false);
    expect((await b.loadFields("res.partner")).email.searchable).toBe(true);
    await a.loadFields("res.partner");
    expect(calls).toHaveLength(2);
    await b.loadPath("res.partner", "country_id.name");
    expect(calls.at(-1)).toEqual({
        model: "res.country", method: "fields_get",
        context: { oy_field_action_id: 101, oy_field_menu_id: 12 },
    });
    env.bus.trigger("CLEAR-CACHES");
    await a.loadFields("res.partner");
    expect(calls).toHaveLength(4);
});
