import csv
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command
from odoo.addons.web.controllers import export as web_export
from odoo.addons.oy_fields_restriction.controllers import export as field_export
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFieldRestrictionRule(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group = cls.env["res.groups"].create({"name": "Fields restriction test"})
        cls.user = new_test_user(cls.env, login="fields_restriction_test", groups="base.group_user,base.group_allow_export")
        cls.user.groups_id = [Command.link(cls.group.id)]
        cls.other_user = new_test_user(cls.env, login="fields_restriction_other", groups="base.group_user")
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.Rule = cls.env["field.restriction.rule"]
        cls.Partner = cls.env["res.partner"].with_user(cls.user)
        cls.action_a, cls.action_b = cls.env["ir.actions.act_window"].create([
            {"name": "Contacts A", "res_model": "res.partner", "view_mode": "list,form"},
            {"name": "Contacts B", "res_model": "res.partner", "view_mode": "list,form"},
        ])
        cls.menu_a, cls.menu_b = cls.env["ir.ui.menu"].create([
            {"name": "Menu A", "action": f"ir.actions.act_window,{cls.action_a.id}"},
            {"name": "Menu B", "action": f"ir.actions.act_window,{cls.action_a.id}"},
        ])

    def setUp(self):
        super().setUp()
        for method in ("get_fields", "namelist"):
            routing = getattr(field_export.FieldsRestrictionExport, method).original_routing
            self.startPatcher(patch.dict(routing, {"type": "json"}))

    def field_ids(self, *names):
        return self.env["ir.model.fields"].search([
            ("model_id", "=", self.partner_model.id), ("name", "in", names),
        ]).ids

    def rule(self, **values):
        return self.Rule.create({
            "name": "Test", "model_id": self.partner_model.id,
            "scope": "user", "priority": 1, "user_ids": [Command.set(self.user.ids)], **values,
        })

    def test_user_rule_independent_operations(self):
        self.rule(search_mode="deny", search_field_ids=[Command.set(self.field_ids("email"))],
                  group_by_mode="deny", group_by_field_ids=[Command.set(self.field_ids("country_id"))])
        fields = self.Partner.fields_get()
        self.assertFalse(fields["email"]["searchable"])
        self.assertTrue(fields["email"]["oy_group_by_allowed"])
        self.assertFalse(fields["country_id"]["groupable"])
        self.assertTrue(fields["country_id"]["searchable"])
        self.assertTrue(self.Partner.with_user(self.other_user).fields_get()["email"]["searchable"])

    def test_configuration_candidates_follow_technical_capabilities(self):
        metadata = {
            "id": {"type": "integer", "searchable": True, "groupable": True, "exportable": True, "readonly": True},
            "name": {"type": "char", "searchable": True, "groupable": True, "exportable": True, "readonly": False},
            "search_method": {"type": "char", "searchable": True, "groupable": False, "exportable": False, "readonly": True},
            "export_only": {"type": "float", "searchable": True, "groupable": True, "exportable": True, "readonly": True},
            "import_only": {"type": "binary", "searchable": False, "groupable": False, "exportable": False, "readonly": False},
            "unavailable": {"type": "char", "searchable": False, "groupable": False, "exportable": False, "readonly": True},
            "json_data": {"type": "json", "searchable": True, "groupable": True, "exportable": False, "readonly": True},
            "create_uid": {"type": "many2one", "searchable": True, "groupable": True, "exportable": False, "readonly": False},
            "properties": {"type": "properties", "searchable": True, "groupable": False, "exportable": False, "readonly": True},
        }

        def fields_get(model, **kwargs):
            self.assertTrue(model.env.su)
            return metadata

        with patch.object(type(self.Partner), "fields_get", fields_get):
            eligible = self.Rule.with_user(self.user)._get_eligible_field_names("res.partner")
        self.assertEqual(eligible["search"], {"id", "name", "search_method", "export_only", "create_uid", "properties"})
        self.assertEqual(eligible["group_by"], {"name", "create_uid", "properties"})
        self.assertEqual(eligible["export"], {"name", "export_only", "import_only", "properties"})

    def test_configuration_candidates_ignore_existing_restrictions(self):
        blocked = [Command.set(self.field_ids("name"))]
        self.rule(scope="global", search_mode="deny", search_field_ids=blocked,
                  group_by_mode="deny", group_by_field_ids=blocked,
                  export_mode="deny", export_field_ids=blocked)
        self.assertFalse(self.Partner.fields_get(["name"])["name"]["exportable"])
        draft = self.Rule.new({"model_id": self.partner_model.id})
        for operation in ("search", "group_by", "export"):
            available = draft[f"available_{operation}_field_ids"]
            self.assertIn("name", available.mapped("name"))
            self.assertEqual(available.mapped("model_id"), self.partner_model)
        self.assertNotIn("id", draft.available_group_by_field_ids.mapped("name"))
        self.assertNotIn("id", draft.available_export_field_ids.mapped("name"))
        draft.model_id = False
        self.assertFalse(draft.available_search_field_ids)
        self.assertFalse(draft.available_group_by_field_ids)
        self.assertFalse(draft.available_export_field_ids)

    def test_group_allowlist_with_user_field_block(self):
        self.rule(scope="group", user_ids=[Command.clear()], group_ids=[Command.set(self.group.ids)],
                  search_mode="allow", search_field_ids=[Command.set(self.field_ids("name", "email"))])
        self.rule(search_mode="deny", search_field_ids=[Command.set(self.field_ids("email"))])
        fields = self.Partner.fields_get()
        self.assertTrue(fields["name"]["searchable"])
        self.assertFalse(fields["email"]["searchable"])
        self.assertFalse(fields["phone"]["searchable"])

    def test_empty_allowlist_and_sudo(self):
        self.rule(search_mode="allow", group_by_mode="allow")
        self.assertTrue(all(not field["searchable"] for field in self.Partner.fields_get().values()))
        self.assertTrue(all(not field["groupable"] for field in self.Partner.fields_get().values()))
        self.assertTrue(self.Partner.sudo().fields_get()["name"]["searchable"])

    def test_changes_and_group_membership_are_not_cached(self):
        rule = self.rule(scope="group", user_ids=[Command.clear()], group_ids=[Command.set(self.group.ids)], search_mode="allow")
        self.assertFalse(self.Partner.fields_get()["name"]["searchable"])
        rule.active = False
        self.assertTrue(self.Partner.fields_get()["name"]["searchable"])
        rule.active = True
        self.user.groups_id = [Command.unlink(self.group.id)]
        self.assertTrue(self.Partner.fields_get()["name"]["searchable"])

    def test_attributes_and_model_isolation(self):
        self.rule(search_mode="allow")
        self.assertEqual(set(self.Partner.fields_get(["name"], ["string"])["name"]), {"string"})
        self.assertFalse(self.Partner.fields_get(["name"], ["searchable"])["name"]["searchable"])
        self.assertTrue(self.env["res.company"].with_user(self.user).fields_get()["name"]["searchable"])

    def test_invalid_configuration(self):
        other_field = self.env["ir.model.fields"]._get("res.company", "name")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.rule(search_field_ids=[Command.set(other_field.ids)])

    def test_rule_can_be_saved_without_targets_and_completed_later(self):
        rule = self.Rule.create({
            "name": "Incomplete rule", "model_id": self.partner_model.id,
            "scope": "user",
            "search_mode": "allow", "group_by_mode": "allow",
        })
        for user in self.user | self.other_user:
            fields = self.Partner.with_user(user).fields_get(["name", "country_id"])
            self.assertTrue(fields["name"]["searchable"])
            self.assertTrue(fields["country_id"]["groupable"])
        rule.user_ids = [Command.set(self.user.ids)]
        self.assertFalse(self.Partner.fields_get()["name"]["searchable"])
        # Users can clear the old target before choosing a replacement group.
        rule.user_ids = [Command.clear()]
        self.assertTrue(self.Partner.fields_get()["name"]["searchable"])
        rule.write({"scope": "group", "group_ids": [Command.set(self.group.ids)]})
        self.assertFalse(self.Partner.fields_get()["name"]["searchable"])
        rule.group_ids = [Command.clear()]
        self.assertTrue(self.Partner.fields_get()["name"]["searchable"])

    def test_global_scope_applies_to_every_user(self):
        rule = self.rule(scope="global", user_ids=[Command.clear()],
                         search_mode="deny", search_field_ids=[Command.set(self.field_ids("email"))],
                         group_by_mode="deny", group_by_field_ids=[Command.set(self.field_ids("country_id"))])
        for user in self.user | self.other_user:
            fields = self.Partner.with_user(user).fields_get()
            self.assertFalse(fields["email"]["searchable"])
            self.assertFalse(fields["country_id"]["groupable"])
            self.assertTrue(fields["name"]["searchable"])
        self.assertTrue(self.Partner.sudo().fields_get()["email"]["searchable"])
        rule.active = False
        self.assertTrue(self.Partner.fields_get()["email"]["searchable"])

    def test_scope_ignores_targets_from_other_scope(self):
        rule = self.rule(scope="user", user_ids=[Command.set(self.other_user.ids)],
                         group_ids=[Command.set(self.group.ids)], search_mode="allow")
        self.assertTrue(self.Partner.fields_get()["name"]["searchable"])
        self.assertFalse(self.Partner.with_user(self.other_user).fields_get()["name"]["searchable"])
        rule.scope = "group"
        self.assertFalse(self.Partner.fields_get()["name"]["searchable"])
        self.assertTrue(self.Partner.with_user(self.other_user).fields_get()["name"]["searchable"])
        rule.group_ids = [Command.clear()]
        self.assertTrue(self.Partner.with_user(self.other_user).fields_get()["name"]["searchable"])

    def test_user_field_block_preserves_global_allowlist(self):
        self.rule(scope="global", user_ids=[Command.clear()], search_mode="allow",
                  search_field_ids=[Command.set(self.field_ids("name", "email"))])
        self.rule(scope="user", search_mode="deny",
                  search_field_ids=[Command.set(self.field_ids("email"))])
        fields = self.Partner.fields_get()
        self.assertTrue(fields["name"]["searchable"])
        self.assertFalse(fields["email"]["searchable"])
        self.assertFalse(fields["phone"]["searchable"])
        self.assertTrue(self.Partner.with_user(self.other_user).fields_get()["email"]["searchable"])

    def test_priority_defaults_to_one_and_remains_manual(self):
        values = {"name": "Manual priority", "model_id": self.partner_model.id}
        self.Rule.create({**values, "priority": 10})
        rules = self.Rule.create([values, values])
        self.assertEqual(rules.mapped("priority"), [1, 1])
        manual = self.Rule.create({**values, "priority": 7})
        self.assertEqual(manual.priority, 7)
        draft = self.Rule.new({**values, "priority": 4})
        draft._onchange_model_id()
        self.assertEqual(draft.priority, 4)
        draft.model_id = self.env["ir.model"]._get("res.company")
        draft._onchange_model_id()
        self.assertEqual(draft.priority, 4)

    def test_higher_priority_user_rule_overrides_global_block(self):
        self.rule(scope="global", priority=10, search_mode="deny",
                  search_field_ids=[Command.set(self.field_ids("email"))],
                  group_by_mode="deny", group_by_field_ids=[Command.set(self.field_ids("country_id"))])
        override = self.rule(priority=5, search_mode="allow",
                             search_field_ids=[Command.set(self.field_ids("name", "email"))])
        fields = self.Partner.fields_get()
        self.assertTrue(fields["email"]["searchable"])
        self.assertFalse(fields["phone"]["searchable"])
        # Search permissions do not cancel an independent Group By block.
        self.assertFalse(fields["country_id"]["groupable"])
        self.assertFalse(self.Partner.with_user(self.other_user).fields_get()["email"]["searchable"])
        override.active = False
        self.assertFalse(self.Partner.fields_get()["email"]["searchable"])

    def test_priority_changes_and_ties(self):
        block = self.rule(scope="global", priority=5, search_mode="deny",
                          search_field_ids=[Command.set(self.field_ids("email"))])
        allow = self.rule(scope="global", priority=10, search_mode="allow",
                          search_field_ids=[Command.set(self.field_ids("name", "email"))])
        self.assertFalse(self.Partner.fields_get()["email"]["searchable"])
        allow.priority = 5
        self.assertFalse(self.Partner.fields_get()["email"]["searchable"])
        allow.priority = 0
        self.assertTrue(self.Partner.fields_get()["email"]["searchable"])
        self.assertFalse(self.Partner.fields_get()["phone"]["searchable"])
        block.priority = -1
        self.assertFalse(self.Partner.fields_get()["email"]["searchable"])
        self.assertFalse(self.Partner.fields_get()["phone"]["searchable"])

    def test_distinct_field_blocks_combine_across_scopes_and_priorities(self):
        operations = ("search", "group_by", "export", "import")

        def blocked_values(name):
            values = {}
            for policy in ("search", "group_by", "export"):
                values[f"{policy}_mode"] = "deny"
                values[f"{policy}_field_ids"] = [Command.set(self.field_ids(name))]
            return values

        global_rule = self.rule(scope="global", **blocked_values("name"))
        group_rule = self.rule(scope="group", group_ids=[Command.set(self.group.ids)],
                               **blocked_values("email"))
        user_rule = self.rule(**blocked_values("phone"))
        policy = self.Rule.with_user(self.user)
        self.assertEqual(policy._get_blocked_fields("res.partner", operations), ({"name", "email", "phone"},) * 4)
        # Priority changes do not discard rules selecting different fields.
        global_rule.priority = 0
        self.assertEqual(policy._get_blocked_fields("res.partner", operations), ({"name", "email", "phone"},) * 4)
        global_rule.priority = 1
        user_rule.active = False
        self.assertEqual(policy._get_blocked_fields("res.partner", operations), ({"name", "email"},) * 4)
        group_rule.active = False
        self.assertEqual(policy._get_blocked_fields("res.partner", operations), ({"name"},) * 4)
        # Unmatched User and Group rules cannot take precedence over Global.
        user_rule.write({"active": True, "user_ids": [Command.set(self.other_user.ids)]})
        group_rule.write({"active": True, "group_ids": [Command.clear()]})
        self.assertEqual(policy._get_blocked_fields("res.partner", operations), ({"name"},) * 4)

    def test_same_field_ties_and_independent_operations(self):
        self.rule(scope="global", search_mode="deny", search_field_ids=[Command.set(self.field_ids("name"))])
        self.rule(scope="group", group_ids=[Command.set(self.group.ids)],
                  export_mode="deny", export_field_ids=[Command.set(self.field_ids("name"))])
        self.rule(search_mode="allow", search_field_ids=[Command.set(self.field_ids("name", "email"))])
        self.rule(search_mode="deny", search_field_ids=[Command.set(self.field_ids("email"))])
        search, group_by, export, imports = self.Rule.with_user(self.user)._get_blocked_fields(
            "res.partner", ("search", "group_by", "export", "import"),
        )
        self.assertNotIn("name", search)
        self.assertIn("email", search)
        self.assertIn("phone", search)
        self.assertEqual((group_by, export, imports), (set(), {"name"}, {"name"}))
        self.assertEqual([value for value, label in self.Rule._fields["scope"].selection],
                         ["global", "group", "user"])

    def test_allowlists_union_and_conflicts_resolve_per_field(self):
        operations = ("search", "group_by", "export", "import")

        def values(mode, *names):
            result = {}
            for policy in ("search", "group_by", "export"):
                result[f"{policy}_mode"] = mode
                result[f"{policy}_field_ids"] = [Command.set(self.field_ids(*names))]
            return result

        def assert_allowed(*names):
            blocked = self.Rule.with_user(self.user)._get_blocked_fields("res.partner", operations)
            for operation, fields in zip(operations, blocked):
                expected = set(self.Partner._fields) - set(names)
                if operation in ("export", "import"):
                    expected.discard("id")
                self.assertEqual(fields, expected, operation)

        global_rule = self.rule(scope="global", **values("allow", "name", "email"))
        self.rule(scope="group", priority=20, group_ids=[Command.set(self.group.ids)],
                  **values("allow", "phone"))
        group_block = self.rule(scope="group", group_ids=[Command.set(self.group.ids)],
                                **values("deny", "email"))
        assert_allowed("name", "phone")
        user_allow = self.rule(**values("allow", "email"))
        assert_allowed("name", "email", "phone")
        # A numeric priority wins before scope, but only for the shared field.
        user_allow.priority = 2
        assert_allowed("name", "phone")
        global_rule.priority = 0
        assert_allowed("name", "email", "phone")
        global_rule.priority = user_allow.priority = 1
        user_block = self.rule(**values("deny", "email"))
        assert_allowed("name", "phone")
        user_block.active = False
        assert_allowed("name", "email", "phone")
        user_allow.active = False
        assert_allowed("name", "phone")
        group_block.active = False
        assert_allowed("name", "email", "phone")

    def test_same_scope_allowlists_keep_distinct_fields(self):
        self.rule(search_mode="allow", search_field_ids=[Command.set(self.field_ids("name", "email"))])
        self.rule(search_mode="allow", search_field_ids=[Command.set(self.field_ids("name", "phone"))])
        blocked, = self.Rule.with_user(self.user)._get_blocked_fields("res.partner", ("search",))
        self.assertEqual(blocked, set(self.Partner._fields) - {"name", "email", "phone"})

    def test_unmatched_rules_do_not_take_priority(self):
        self.rule(priority=10, search_mode="deny",
                  search_field_ids=[Command.set(self.field_ids("email"))])
        self.rule(priority=0, user_ids=[Command.set(self.other_user.ids)])
        self.rule(priority=0, scope="group", group_ids=[Command.clear()])
        self.rule(priority=0, scope="global", model_id=self.env["ir.model"]._get("res.company").id)
        self.assertFalse(self.Partner.fields_get()["email"]["searchable"])

    def test_configuration_acl_and_business_queries(self):
        self.rule(search_mode="allow", group_by_mode="allow")
        with self.assertRaises(AccessError):
            self.Rule.with_user(self.user).search([])
        # UI policy must not break model computations or record-rule domains.
        self.Partner.search([("name", "ilike", "test")], limit=1)
        self.Partner.read_group([], ["country_id"], ["country_id"])

    def test_export_and_import_share_policy_independent_of_search(self):
        self.rule(export_mode="deny", export_field_ids=[Command.set(self.field_ids("email"))])
        fields = self.Partner.fields_get(["email", "phone"])
        self.assertFalse(fields["email"]["exportable"])
        self.assertTrue(fields["email"]["searchable"])
        self.assertTrue(fields["phone"]["exportable"])
        self.assertTrue(self.Partner.with_user(self.other_user).fields_get(["email"])["email"]["exportable"])
        tree = self.env["base_import.import"].with_user(self.user).get_fields_tree("res.partner")
        names = {field["name"] for field in tree}
        self.assertNotIn("email", names)
        self.assertIn("phone", names)
        result = self.Partner.get_views([(False, "list")])
        self.assertFalse(result["models"]["res.partner"]["fields"]["email"]["exportable"])

    def test_transfer_paths_respect_related_models_and_ids(self):
        country = self.env["ir.model"]._get("res.country")
        self.Rule.create({
            "name": "Country transfer", "model_id": country.id, "scope": "global",
            "export_mode": "deny", "export_field_ids": [Command.set((
                self.env["ir.model.fields"]._get("res.country", "code") |
                self.env["ir.model.fields"]._get("res.country", "id")
            ).ids)],
        })
        policy = self.Rule.with_user(self.user)
        self.assertEqual(policy._filter_transfer_paths("res.partner", ["country_id/code", "country_id/name"], "export"), ["country_id/name"])
        paths = ["country_id/id", "country_id/.id", "country_id:id", "country_id.id", "country_id"]
        for operation in ("export", "import"):
            self.assertEqual(policy._filter_transfer_paths("res.partner", paths, operation), paths)
        tree = self.env["base_import.import"].with_user(self.user).get_fields_tree("res.partner")
        self.assertEqual({field["name"] for field in next(field for field in tree if field["name"] == "country_id")["fields"]}, {"id", ".id"})
        self.rule(export_mode="deny", export_field_ids=[Command.set(self.field_ids("country_id"))])
        self.assertEqual(policy._filter_transfer_paths("res.partner", ["country_id/name"], "export"), [])
        self.assertEqual(policy._filter_transfer_paths("res.partner", ["country_id/id", "country_id/.id"], "import"), [])

    def test_transfer_priority_and_empty_allowlist(self):
        lower = self.rule(scope="global", priority=2, export_mode="allow")
        selected = self.rule(priority=1, export_mode="allow", export_field_ids=[Command.set(self.field_ids("name"))])
        self.assertTrue(self.Partner.fields_get(["name"])["name"]["exportable"])
        # Both flows combine the same explicit field selections.
        tree = self.env["base_import.import"].with_user(self.user).get_fields_tree("res.partner")
        self.assertEqual({field["name"] for field in tree}, {"name", "id", ".id"})
        lower.priority = 1
        self.assertTrue(self.Partner.fields_get(["name"])["name"]["exportable"])
        lower.priority = 0
        self.assertTrue(self.Partner.fields_get(["name"])["name"]["exportable"])
        selected.active = False
        fields = self.Partner.fields_get()
        self.assertTrue(fields["id"]["exportable"])
        self.assertTrue(all(not field["exportable"] for name, field in fields.items() if name != "id"))
        tree = self.env["base_import.import"].with_user(self.user).get_fields_tree("res.partner")
        self.assertEqual({field["name"] for field in tree}, {"id", ".id"})

    def test_transfer_without_configuration_and_archived_rules(self):
        policy = self.Rule.with_user(self.user)
        paths = ["name", "email", "country_id/id"]
        for operation in ("export", "import"):
            self.assertEqual(policy._filter_transfer_paths("res.partner", paths, operation), paths)
        rule = self.rule(export_mode="allow")
        for operation in ("export", "import"):
            self.assertEqual(policy._filter_transfer_paths("res.partner", paths, operation), [])
        rule.active = False
        for operation in ("export", "import"):
            self.assertEqual(policy._filter_transfer_paths("res.partner", paths, operation), paths)

    def test_transfer_ids_cannot_be_blocked_but_search_and_group_by_can(self):
        ids = [Command.set(self.field_ids("id"))]
        self.rule(export_mode="allow")
        self.rule(export_mode="deny", export_field_ids=ids,
                  search_mode="deny", search_field_ids=ids,
                  group_by_mode="deny", group_by_field_ids=ids)
        policy = self.Rule.with_user(self.user)
        search, group, export, imports = policy._get_blocked_fields("res.partner", ("search", "group_by", "export", "import"))
        self.assertIn("id", search)
        self.assertIn("id", group)
        self.assertNotIn("id", export)
        self.assertNotIn("id", imports)
        for operation in ("export", "import"):
            self.assertEqual(policy._filter_transfer_paths("res.partner", ["id", ".id", "name"], operation), ["id", ".id"])
        fields = self.Partner.fields_get(["id"])
        self.assertTrue(fields["id"]["exportable"])
        self.assertFalse(fields["id"]["searchable"])
        self.assertFalse(fields["id"]["groupable"])
        mock_request = SimpleNamespace(env=self.Partner.env)
        with patch.object(field_export, "request", mock_request), patch.object(web_export, "request", mock_request):
            for compatible in (False, True):
                available = field_export.FieldsRestrictionExport().get_fields("res.partner", [], import_compat=compatible)
                self.assertEqual({field["id"] for field in available}, {"id", ".id"} if compatible else set())

    def test_import_updates_using_both_identifiers_without_selecting_id(self):
        self.user.groups_id = [Command.link(self.env.ref("base.group_partner_manager").id)]
        self.rule(export_mode="allow", export_field_ids=[Command.set(self.field_ids("name"))])
        self.rule(export_mode="deny", export_field_ids=[Command.set(self.field_ids("id"))])
        partner = self.env["res.partner"].create({"name": "Before ID import"})
        external_id = partner.export_data(["id"])["datas"][0][0]
        for identifier, value in ((".id", str(partner.id)), ("id", external_id)):
            wizard = self.env["base_import.import"].with_user(self.user).create({
                "res_model": "res.partner", "file_name": "update.csv", "file_type": "text/csv",
                "file": f"{identifier},name\n{value},Updated through {identifier}\n".encode(),
            })
            options = {"quoting": '"', "separator": ",", "has_headers": True}
            preview = wizard.parse_preview({**options, "keep_matches": True, "fields": [identifier, "name"]})
            self.assertNotIn("error", preview)
            self.assertEqual(preview["matches"][0], [identifier])
            result = wizard.execute_import([identifier, "name"], [identifier, "name"], dict(options))
            self.assertFalse(any(message["type"] == "error" for message in result.get("messages", [])))
            self.assertEqual(result["ids"], partner.ids)
            self.assertEqual(partner.name, f"Updated through {identifier}")

    def test_transfer_configuration_rejects_fields_from_another_model(self):
        other_field = self.env["ir.model.fields"]._get("res.company", "name")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.rule(export_field_ids=[Command.set(other_field.ids)])

    def test_import_preview_and_submission_recheck_mapping(self):
        self.user.groups_id = [Command.link(self.env.ref("base.group_partner_manager").id)]
        self.rule(export_mode="deny", export_field_ids=[Command.set(self.field_ids("email"))])
        wizard = self.env["base_import.import"].with_user(self.user).create({
            "res_model": "res.partner", "file": b"name,email\nTransfer Test,test@example.com\n",
            "file_name": "contacts.csv", "file_type": "text/csv",
        })
        options = {"quoting": '"', "separator": ",", "has_headers": True}
        preview = wizard.parse_preview({**options, "keep_matches": True, "fields": ["name", "email"]})
        self.assertNotIn("error", preview)
        self.assertEqual(preview["matches"], {0: ["name"]})
        for dryrun in (True, False):
            result = wizard.execute_import(["name", "email"], ["name", "email"], dict(options), dryrun=dryrun)
            self.assertEqual(result["messages"][0]["type"], "error")
            self.assertIn("email", result["messages"][0]["message"])
        result = wizard.execute_import(["name", False], ["name", "email"], dict(options))
        self.assertFalse(any(message["type"] == "error" for message in result.get("messages", [])))
        self.assertTrue(result["ids"])
        self.assertFalse(self.env["res.partner"].browse(result["ids"]).email)

    def test_export_picker_and_saved_template(self):
        self.user.groups_id = [Command.link(self.env.ref("base.group_allow_export").id)]
        self.rule(export_mode="allow", export_field_ids=[Command.set(self.field_ids("name"))])
        mock_request = SimpleNamespace(env=self.Partner.env)
        controller = field_export.FieldsRestrictionExport()
        template = self.env["ir.exports"].with_user(self.user).create({
            "name": "Existing template", "resource": "res.partner",
            "export_fields": [Command.create({"name": name}) for name in ("name", "email", "id", ".id")],
        })
        with patch.object(field_export, "request", mock_request), patch.object(web_export, "request", mock_request):
            for compatible in (False, True):
                fields = controller.get_fields("res.partner", [], import_compat=compatible)
                self.assertEqual({field["id"] for field in fields}, {"name", "id", ".id"} if compatible else {"name"})
                fields = controller.namelist("res.partner", template.id, import_compat=compatible)
                self.assertEqual([field["id"] for field in fields], ["name", "id", ".id"] if compatible else ["name"])

    def test_both_export_formats_filter_columns_and_group_headers(self):
        self.rule(export_mode="allow", export_field_ids=[Command.set(self.field_ids("name"))])
        params = {
            "model": "res.partner", "fields": [{"name": "name"}, {"name": "email"}, {"name": "id"}],
            "groupby": ["email"],
        }
        mock_request = SimpleNamespace(env=self.Partner.env)
        with patch.object(field_export, "request", mock_request), patch.object(web_export.ExportFormat, "base", side_effect=json.loads):
            for controller in (field_export.FieldsRestrictionCSVExport(), field_export.FieldsRestrictionExcelExport()):
                for compatible in (False, True):
                    result = controller.base(json.dumps({**params, "import_compat": compatible}))
                    self.assertEqual(result["fields"], [{"name": "name"}, {"name": "id"}] if compatible else [{"name": "name"}])
                    self.assertNotIn("groupby", result)
                with self.assertRaises(UserError):
                    controller.base(json.dumps({**params, "fields": [{"name": "email"}]}))

    def test_export_related_ids_follow_compatibility_mode(self):
        mock_request = SimpleNamespace(env=self.Partner.env)
        with patch.object(field_export, "request", mock_request), patch.object(web_export, "request", mock_request):
            for compatible in (False, True):
                fields = field_export.FieldsRestrictionExport().get_fields(
                    "res.country", [], prefix="country_id", parent_name="Country", import_compat=compatible,
                )
                names = {field["id"] for field in fields}
                self.assertEqual("country_id/id" in names, compatible)
                self.assertEqual("country_id/.id" in names, compatible)
        identifiers = ["id", ".id", "country_id/id", "country_id/.id", "country_id:id", "country_id.id"]
        params = {"model": "res.partner", "fields": [{"name": name} for name in ["name", *identifiers]]}
        with patch.object(field_export, "request", mock_request), patch.object(web_export.ExportFormat, "base", side_effect=json.loads):
            for controller in (field_export.FieldsRestrictionCSVExport(), field_export.FieldsRestrictionExcelExport()):
                for compatible in (False, True):
                    result = controller.base(json.dumps({**params, "import_compat": compatible}))
                    self.assertEqual(result["fields"], params["fields"] if compatible else [{"name": "name"}])

    def test_csv_download_and_direct_orm_export(self):
        self.rule(export_mode="allow", export_field_ids=[Command.set(self.field_ids("name"))])
        partner = self.env["res.partner"].create({"name": "Transfer Test", "email": "hidden@example.com"})
        mock_request = SimpleNamespace(
            env=self.Partner.env, httprequest=SimpleNamespace(environ={"REMOTE_ADDR": "127.0.0.1"}),
            make_response=lambda data, headers: data,
        )
        params = {
            "model": "res.partner", "fields": [{"name": "name", "label": "Name"}, {"name": "email", "label": "Email"}],
            "ids": partner.ids, "domain": [], "import_compat": False,
        }
        with patch.object(field_export, "request", mock_request), patch.object(web_export, "request", mock_request):
            data = field_export.FieldsRestrictionCSVExport().base(json.dumps(params))
        self.assertEqual(list(csv.reader(io.StringIO(data))), [["Name"], ["Transfer Test"]])
        self.assertEqual(partner.with_user(self.user).export_data(["email"])["datas"], [["hidden@example.com"]])

    def test_view_metadata(self):
        self.rule(search_mode="allow")
        result = self.Partner.get_views([(False, "search")])
        self.assertFalse(result["models"]["res.partner"]["fields"]["name"]["oy_search_allowed"])

    def test_optional_action_scope_for_all_four_operations(self):
        operations = ("search", "group_by", "export", "import")
        policies = ("search", "group_by", "export")
        values = {f"{policy}_mode": "deny" for policy in policies}
        values.update({f"{policy}_field_ids": [Command.set(self.field_ids("email"))] for policy in policies})
        self.rule(action_id=self.action_a.id, **values)
        policy = self.Rule.with_user(self.user)
        for action_id in (False, self.action_a.id, self.action_b.id):
            blocked = policy.with_context(oy_field_action_id=action_id)._get_blocked_fields("res.partner", operations)
            self.assertEqual(blocked, tuple({"email"} if action_id == self.action_a.id else set() for operation in operations))
        # Leaving the action/menu empty retains the original model-wide behavior.
        self.rule(**values)
        self.assertEqual(policy._get_blocked_fields("res.partner", operations), tuple({"email"} for operation in operations))

    def test_two_menus_sharing_one_action_and_priority(self):
        model_rule = self.rule(priority=2, search_mode="deny", search_field_ids=[Command.set(self.field_ids("email"))])
        targeted = self.rule(action_id=self.action_a.id, menu_id=self.menu_a.id, priority=1,
                             search_mode="allow", search_field_ids=[Command.set(self.field_ids("name", "email"))])
        for menu_id, email_allowed in ((self.menu_a.id, True), (self.menu_b.id, False), (False, False)):
            partner = self.Partner.with_context(oy_field_action_id=self.action_a.id, oy_field_menu_id=menu_id)
            self.assertEqual(partner.fields_get(["email"])["email"]["searchable"], email_allowed)
        # A stale menu from another action must not select its rules.
        stale = self.Partner.with_context(oy_field_action_id=self.action_b.id, oy_field_menu_id=self.menu_a.id)
        self.assertFalse(stale.fields_get(["email"])["email"]["searchable"])
        scoped = self.Partner.with_context(oy_field_action_id=self.action_a.id, oy_field_menu_id=self.menu_a.id)
        model_rule.priority = 1
        self.assertFalse(scoped.fields_get(["email"])["email"]["searchable"])
        model_rule.priority = 2
        targeted.active = False
        self.assertFalse(scoped.fields_get(["email"])["email"]["searchable"])

    def test_menu_action_configuration_validation(self):
        draft = self.Rule.new({"name": "Menu", "model_id": self.partner_model.id, "menu_id": self.menu_a.id})
        draft._onchange_menu_id()
        self.assertEqual(draft.action_id, self.action_a)
        draft.action_id = self.action_b
        draft._onchange_action_id()
        self.assertFalse(draft.menu_id)
        self.assertIn(self.menu_a, draft.available_menu_ids._origin)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.rule(action_id=self.action_b.id, menu_id=self.menu_a.id)
        company_action = self.env["ir.actions.act_window"].create({"name": "Companies", "res_model": "res.company"})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.rule(action_id=company_action.id)
        parent = self.env["ir.ui.menu"].create({"name": "Parent without action"})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.rule(menu_id=parent.id)

    def test_action_choices_follow_menu_and_expand_when_cleared(self):
        menu_c = self.env["ir.ui.menu"].create({
            "name": "Menu C", "action": f"ir.actions.act_window,{self.action_b.id}",
        })
        draft = self.Rule.new({"model_id": self.partner_model.id})
        self.assertTrue(self.action_a in draft.available_action_ids._origin)
        self.assertTrue(self.action_b in draft.available_action_ids._origin)
        self.assertTrue(all(action.res_model == "res.partner" for action in draft.available_action_ids._origin))
        draft.menu_id = self.menu_a
        draft._onchange_menu_id()
        self.assertEqual(draft.available_action_ids._origin, self.action_a)
        self.assertEqual(draft.action_id, self.action_a)
        # The auto-filled action must not prevent switching to another menu.
        self.assertIn(menu_c, draft.available_menu_ids._origin)
        draft.menu_id = menu_c
        draft._onchange_menu_id()
        self.assertEqual(draft.available_action_ids._origin, self.action_b)
        self.assertEqual(draft.action_id, self.action_b)
        draft.action_id = False
        draft._onchange_action_id()
        self.assertEqual(draft.menu_id, menu_c)
        self.assertEqual(draft.available_action_ids._origin, self.action_b)
        draft.menu_id = False
        draft._onchange_menu_id()
        self.assertIn(self.action_a, draft.available_action_ids._origin)
        self.assertIn(self.action_b, draft.available_action_ids._origin)
        draft.model_id = False
        self.assertFalse(draft.available_action_ids._origin)

    def test_view_options_carry_scope_to_field_metadata(self):
        self.rule(action_id=self.action_a.id, menu_id=self.menu_a.id,
                  search_mode="deny", search_field_ids=[Command.set(self.field_ids("name"))],
                  group_by_mode="deny", group_by_field_ids=[Command.set(self.field_ids("name"))],
                  export_mode="deny", export_field_ids=[Command.set(self.field_ids("name"))])
        for menu in (self.menu_a, self.menu_b):
            result = self.Partner.get_views([(False, "search")], options={
                "action_id": self.action_a.id, "oy_field_menu_id": menu.id,
            })
            name = result["models"]["res.partner"]["fields"]["name"]
            self.assertEqual(name["oy_search_allowed"], menu == self.menu_b)
            self.assertEqual(name["oy_group_by_allowed"], menu == self.menu_b)
            self.assertEqual(name["exportable"], menu == self.menu_b)

    def test_import_tree_and_submission_keep_originating_scope(self):
        self.rule(action_id=self.action_a.id, menu_id=self.menu_a.id,
                  export_mode="deny", export_field_ids=[Command.set(self.field_ids("email"))])
        wizard = self.env["base_import.import"].with_user(self.user).with_context(
            oy_field_action_id=self.action_a.id, oy_field_menu_id=self.menu_a.id,
        ).create({"res_model": "res.partner", "file": b"email\ntest@example.com\n", "file_name": "email.csv"})
        self.assertNotIn("email", {field["name"] for field in wizard.get_fields_tree("res.partner")})
        other = wizard.with_context(oy_field_menu_id=self.menu_b.id)
        self.assertIn("email", {field["name"] for field in other.get_fields_tree("res.partner")})
        result = wizard.execute_import(["email"], ["email"], {}, dryrun=True)
        self.assertEqual(result["messages"][0]["type"], "error")

    def test_import_preview_filters_dropdown_and_kept_matches_by_menu(self):
        self.rule(action_id=self.action_a.id, menu_id=self.menu_a.id,
                  export_mode="allow", export_field_ids=[Command.set(self.field_ids("name"))])
        wizard = self.env["base_import.import"].with_user(self.user).create({
            "res_model": "res.partner", "file": b"name,email\nTest,test@example.com\n",
            "file_name": "contacts.csv", "file_type": "text/csv",
        })
        options = {"quoting": '"', "separator": ",", "has_headers": True,
                   "keep_matches": True, "fields": ["name", "email"]}
        for menu in (self.menu_a, self.menu_b, self.menu_a):
            preview = wizard.with_context(
                oy_field_action_id=self.action_a.id, oy_field_menu_id=menu.id,
            ).parse_preview(dict(options))
            self.assertNotIn("error", preview)
            names = {field["name"] for field in preview["fields"]}
            self.assertIn("name", names)
            self.assertEqual("email" in names, menu == self.menu_b)
            self.assertEqual(1 in preview["matches"], menu == self.menu_b)

    def test_export_download_uses_payload_scope(self):
        self.rule(action_id=self.action_a.id, menu_id=self.menu_a.id,
                  export_mode="deny", export_field_ids=[Command.set(self.field_ids("email"))])
        mock_request = SimpleNamespace(env=self.Partner.env)
        with patch.object(field_export, "request", mock_request), patch.object(web_export.ExportFormat, "base", side_effect=json.loads):
            for menu in (self.menu_a, self.menu_b):
                result = field_export.FieldsRestrictionCSVExport().base(json.dumps({
                    "model": "res.partner", "fields": [{"name": "name"}, {"name": "email"}],
                    "context": {"oy_field_action_id": self.action_a.id, "oy_field_menu_id": menu.id},
                }))
                self.assertEqual([field["name"] for field in result["fields"]], ["name"] if menu == self.menu_a else ["name", "email"])

    def test_export_picker_and_template_use_request_scope(self):
        self.user.groups_id = [Command.link(self.env.ref("base.group_allow_export").id)]
        self.rule(action_id=self.action_a.id, menu_id=self.menu_a.id,
                  export_mode="deny", export_field_ids=[Command.set(self.field_ids("email"))])
        template = self.env["ir.exports"].create({
            "name": "Shared template", "resource": "res.partner",
            "export_fields": [Command.create({"name": "name"}), Command.create({"name": "email"})],
        })
        mock_request = SimpleNamespace(env=self.Partner.env)
        mock_request.update_context = lambda **context: setattr(
            mock_request, "env", mock_request.env["res.partner"].with_context(**context).env,
        )
        controller = field_export.FieldsRestrictionExport()
        with patch.object(field_export, "request", mock_request), patch.object(web_export, "request", mock_request):
            for menu in (self.menu_a, self.menu_b):
                context = {"oy_field_action_id": self.action_a.id, "oy_field_menu_id": menu.id}
                available = controller.get_fields("res.partner", [], import_compat=False, context=context)
                self.assertEqual("email" in {field["id"] for field in available}, menu == self.menu_b)
                selected = controller.namelist("res.partner", template.id, context=context)
                self.assertEqual([field["id"] for field in selected], ["name"] if menu == self.menu_a else ["name", "email"])
