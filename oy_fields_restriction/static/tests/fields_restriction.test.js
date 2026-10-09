import { describe, expect, test } from "@odoo/hoot";
import { Component, xml } from "@odoo/owl";
import { defineModels, fields, models, mountWithSearch } from "@web/../tests/web_test_helpers";
import { domainAllowed, fieldAllowed, searchItemAllowed, selectableFields } from "@oy_fields_restriction/fields_restriction";

describe.current.tags("headless");

const FIELDS = {
    id: { name: "id", string: "ID", type: "integer", searchable: true, oy_search_allowed: false },
    name: { name: "name", string: "Name", type: "char", searchable: true, groupable: true },
    email: { name: "email", string: "Email", type: "char", searchable: false, oy_search_allowed: false, groupable: true },
    birthday: { name: "birthday", string: "Birthday", type: "date", searchable: true, groupable: false, oy_group_by_allowed: false },
};

class FieldsRestrictionPartner extends models.Model {
    _name = "fields.restriction.test.partner";
    name = fields.Char();
    email = fields.Char();
    birthday = fields.Date();
}
defineModels([FieldsRestrictionPartner]);

class SearchHost extends Component {
    static template = xml`<div/>`;
}

async function makeSearch(extra = {}) {
    const component = await mountWithSearch(SearchHost, {
        resModel: "fields.restriction.test.partner",
        searchViewId: false,
        searchViewFields: FIELDS,
        searchViewArch: `<search>
            <field name="name"/><field name="email"/>
            <filter name="has_email" string="Has email" domain="[('email', '!=', False)]"/>
            <filter name="named" string="Named" domain="[('name', '!=', False)]"/>
            <group><filter name="birthday" string="Birthday" context="{'group_by': 'birthday:month'}"/>
            <filter name="name_group" string="Name" context="{'group_by': 'name'}"/></group>
        </search>`,
        ...extra,
    });
    return component.env.searchModel;
}

test("restrictions are independent for search and group by", () => {
    expect(fieldAllowed(FIELDS, "email")).toBe(false);
    expect(fieldAllowed(FIELDS, "email", "group_by")).toBe(true);
    expect(fieldAllowed(FIELDS, "birthday:month", "group_by")).toBe(false);
    expect(fieldAllowed(FIELDS, "email.related")).toBe(false);
});

test("compound domains inspect names without evaluating expressions", () => {
    expect(domainAllowed(FIELDS, "['|', ('name', '=', self), ('email', '=', context.get('email'))]")).toBe(false);
    expect(domainAllowed(FIELDS, "[('name', '=', uid)]")).toBe(true);
    expect(domainAllowed(FIELDS, "[(1, '=', 1)]")).toBe(true);
});

test("favorites and custom filter domains respect blocked fields", () => {
    expect(searchItemAllowed(FIELDS, { type: "favorite", domain: "[]", groupBys: ["birthday:year"] })).toBe(false);
    expect(searchItemAllowed(FIELDS, { type: "field", fieldName: "name", filterDomain: "[('email', '=', self)]" })).toBe(false);
    expect(searchItemAllowed(FIELDS, { type: "filter", context: "{'group_by': 'birthday'}" })).toBe(false);
});

test("default custom filter candidates exclude blocked fields", () => {
    expect(Object.keys(selectableFields(FIELDS))).toEqual(["name", "birthday"]);
    expect(selectableFields({ email: FIELDS.email })).toEqual({});
});

test("only allowed built-in filters remain available and activate by default", async () => {
    const search = await makeSearch({ context: { search_default_has_email: 1, search_default_birthday: 1, search_default_named: 1 } });
    expect(search.getSearchItems().map((item) => item.name || item.fieldName)).toEqual(["name", "named", "name_group"]);
    expect(search.domain).toEqual([["name", "!=", false]]);
    expect(search.groupBy).toEqual([]);
});

test("unnamed built-in filters on restricted fields are hidden", async () => {
    const search = await makeSearch({
        searchViewArch: `<search><filter string="Has email" domain="[('email', '!=', False)]"/></search>`,
    });
    const items = search.getSearchItems();
    expect(items).toHaveLength(0);
    expect(search.domain).toEqual([]);
});

test("built-in date filters on restricted fields are hidden", async () => {
    const search = await makeSearch({
        searchViewFields: { ...FIELDS, birthday: { ...FIELDS.birthday, searchable: false, oy_search_allowed: false } },
        searchViewArch: `<search><filter name="birthday_filter" string="Birthday" date="birthday"/></search>`,
        context: { search_default_birthday_filter: 1 },
    });
    const items = search.getSearchItems((item) => item.type === "dateFilter");
    expect(items).toHaveLength(0);
    expect(search.domain).toEqual([]);
});

test("sales-style search with name and user_id only keeps My Quotations", async () => {
    const search = await makeSearch({
        searchViewFields: {
            name: FIELDS.name,
            user_id: { name: "user_id", string: "Salesperson", type: "many2one", relation: "res.users", searchable: true, oy_search_allowed: true },
            state: { name: "state", string: "Status", type: "selection", searchable: false, oy_search_allowed: false },
            create_date: { name: "create_date", string: "Create Date", type: "datetime", searchable: false, oy_search_allowed: false },
        },
        searchViewArch: `<search>
            <field name="name"/>
            <filter name="my_quotation" string="My Quotations" domain="[('user_id', '=', uid)]"/>
            <separator/>
            <filter name="quotations" string="Quotations" domain="[('state', 'in', ['draft', 'sent'])]"/>
            <filter name="sales_orders" string="Sales Orders" domain="[('state', '=', 'sale')]"/>
            <separator/>
            <filter name="create_date" string="Create Date" date="create_date"/>
        </search>`,
        context: { search_default_my_quotation: 1 },
    });
    const filters = search.getSearchItems((item) => ["filter", "dateFilter"].includes(item.type));
    expect(filters.map((item) => item.description)).toEqual(["My Quotations"]);
    expect(filters[0].isActive).toBe(true);
    expect(Object.keys(selectableFields(search.searchViewFields))).toEqual(["name", "user_id"]);
});

test("blocked default favorite does not crash or activate", async () => {
    const search = await makeSearch({
        irFilters: [{ id: 1, name: "Old favorite", domain: "[('email', '!=', False)]", context: "{}", sort: "[]", is_default: true, user_ids: [] }],
    });
    expect(search.getSearchItems((item) => item.type === "favorite")).toEqual([]);
    expect(search.domain).toEqual([]);
});

test("global default group by and custom group creation are restricted", async () => {
    const search = await makeSearch({ groupBy: ["birthday:month"] });
    expect(search.groupBy).toEqual([]);
    search.createNewGroupBy("birthday");
    expect(search.groupBy).toEqual([]);
    search.createNewGroupBy("name");
    expect(search.groupBy).toEqual(["name"]);
});

test("custom domain submission cannot use a blocked field", async () => {
    const search = await makeSearch();
    await search.splitAndAddDomain("[('email', '=', 'x')]");
    expect(search.domain).toEqual([]);
    await search.splitAndAddDomain("[('name', '=', 'Allowed')]");
    expect(search.domain).toEqual([["name", "=", "Allowed"]]);
});

test("restored search state preserves allowed defaults but drops restricted filters", async () => {
    const search = await makeSearch({ context: { search_default_named: 1 } });
    const state = search.exportState();
    state.searchItems[999] = { id: 999, type: "filter", groupId: 999, domain: "[('email', '=', 'x')]", description: "Old filter", oyBuiltinFilter: true };
    state.query.push({ searchItemId: 999 });
    search._importState(state);
    expect(search.domain).toEqual([["name", "!=", false]]);
});
