from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class FieldRestrictionRule(models.Model):
    _name = "field.restriction.rule"
    _description = "Fields Restriction Rules"
    _order = "model_id, priority, name, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    priority = fields.Integer(
        default=1, required=True, index=True, copy=False,
        help="Priority resolves conflicting decisions for the same selected field. Lower numbers win. "
             "At the same priority, User overrides Group, then Global. If both priority and scope match, "
             "blocking wins. Different fields are combined across matching rules, separately for each operation. "
             "New rules default to 1. Set the priority manually as needed. Archived rules do not apply.",
    )
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", index=True)
    model_name = fields.Char(string="Technical Model", related="model_id.model", readonly=True)
    action_id = fields.Many2one(
        "ir.actions.act_window", string="Action", ondelete="cascade", index=True,
        help="Optional. Apply only when this window action is open. Leave empty for all actions.",
    )
    menu_id = fields.Many2one(
        "ir.ui.menu", string="Menu", ondelete="cascade", index=True,
        help="Optional. Apply only when opened from this exact menu, not its parent or siblings.",
    )
    available_menu_ids = fields.Many2many("ir.ui.menu", compute="_compute_available_menu_ids")
    available_action_ids = fields.Many2many("ir.actions.act_window", compute="_compute_available_action_ids")
    scope = fields.Selection(
        [("global", "Global"), ("group", "Group"), ("user", "User")],
        string="Apply To", required=True, default="global", index=True,
        help="Global applies to everyone. Group and User apply only to the selected targets. "
             "For the same field and Priority, User overrides Group, then Global.",
    )
    user_ids = fields.Many2many("res.users", string="Users")
    group_ids = fields.Many2many("res.groups", string="Groups")
    search_mode = fields.Selection(
        [("none", "No restriction"), ("allow", "Only selected fields"), ("deny", "Block selected fields")],
        required=True, default="none", string="Search policy",
    )
    search_field_ids = fields.Many2many(
        "ir.model.fields", "field_restriction_rule_search_field_rel", "rule_id", "field_id",
        string="Search fields",
    )
    group_by_mode = fields.Selection(
        [("none", "No restriction"), ("allow", "Only selected fields"), ("deny", "Block selected fields")],
        required=True, default="none", string="Group By policy",
    )
    group_by_field_ids = fields.Many2many(
        "ir.model.fields", "field_restriction_rule_group_field_rel", "rule_id", "field_id",
        string="Group By fields",
    )
    export_mode = fields.Selection(
        [("none", "No restriction"), ("allow", "Only selected fields"), ("deny", "Block selected fields")],
        required=True, default="none", string="Export / Import policy",
        help="The same policy and selected fields apply to both Export and Import. "
             "External ID and Database ID cannot be blocked by a rule. They are available for Import and import-compatible Export, and hidden in regular Export.",
    )
    export_field_ids = fields.Many2many(
        "ir.model.fields", "field_restriction_rule_export_field_rel", "rule_id", "field_id",
        string="Export / Import fields",
    )
    available_search_field_ids = fields.Many2many(
        "ir.model.fields", "field_restriction_rule_available_search_rel", "rule_id", "field_id",
        compute="_compute_available_fields",
    )
    available_group_by_field_ids = fields.Many2many(
        "ir.model.fields", "field_restriction_rule_available_group_rel", "rule_id", "field_id",
        compute="_compute_available_fields",
    )
    available_export_field_ids = fields.Many2many(
        "ir.model.fields", "field_restriction_rule_available_export_rel", "rule_id", "field_id",
        compute="_compute_available_fields",
    )
    notes = fields.Text()

    @api.model
    def _get_eligible_field_names(self, model_name):
        eligible = {"search": set(), "group_by": set(), "export": set()}
        if not model_name or model_name not in self.env:
            return eligible
        # Technical capabilities must not be narrowed by the very rule being
        # edited, by another rule, or by the administrator's field groups.
        metadata = self.env[model_name].sudo().fields_get(attributes=[
            "type", "searchable", "groupable", "exportable", "readonly",
        ])
        # Matches web/search/utils/misc.GROUPABLE_TYPES in Odoo 18.
        groupable_types = {"boolean", "char", "date", "datetime", "integer", "many2one", "many2many", "selection", "tags"}
        for name, description in metadata.items():
            field_type = description.get("type")
            if description.get("searchable") and field_type not in ("json", "separator"):
                eligible["search"].add(name)
            if name != "id" and (
                (description.get("groupable") and field_type in groupable_types)
                or field_type == "properties"
            ):
                # Properties are selected through their parent field's submenu.
                eligible["group_by"].add(name)
            importable = name not in models.MAGIC_COLUMNS and not description.get("readonly")
            if name != "id" and (
                description.get("exportable", True) or importable or field_type == "properties"
            ):
                eligible["export"].add(name)
        return eligible

    @api.depends("model_id")
    def _compute_available_fields(self):
        by_model = {}
        for rule in self:
            model_name = rule.model_name
            if model_name not in by_model:
                names = rule._get_eligible_field_names(model_name)
                records = self.env["ir.model.fields"].search([
                    ("model_id", "=", rule.model_id.id),
                    ("name", "in", list(set().union(*names.values()))),
                ])
                by_model[model_name] = {
                    operation: records.filtered(lambda field: field.name in allowed)
                    for operation, allowed in names.items()
                }
            for operation, records in by_model[model_name].items():
                rule[f"available_{operation}_field_ids"] = records

    @api.onchange("model_id")
    def _onchange_model_id(self):
        self.search_field_ids = False
        self.group_by_field_ids = False
        self.export_field_ids = False
        self.action_id = False
        self.menu_id = False

    @api.depends("model_id")
    def _compute_available_menu_ids(self):
        for rule in self:
            actions = self.env["ir.actions.act_window"].search([
                ("res_model", "=", rule.model_name),
            ]) if rule.model_id else self.env["ir.actions.act_window"]
            rule.available_menu_ids = self.env["ir.ui.menu"].search([
                ("action", "in", [f"ir.actions.act_window,{action.id}" for action in actions]),
            ]) if actions else False

    @api.depends("model_id", "menu_id.action")
    def _compute_available_action_ids(self):
        for rule in self:
            actions = self.env["ir.actions.act_window"]
            if rule.model_id:
                if rule.menu_id:
                    action = rule.menu_id.action
                    if action and action._name == "ir.actions.act_window" and action.res_model == rule.model_name:
                        actions = action
                else:
                    actions = actions.search([("res_model", "=", rule.model_name)])
            rule.available_action_ids = actions

    @api.onchange("menu_id")
    def _onchange_menu_id(self):
        if self.menu_id and self.menu_id.action and self.menu_id.action._name == "ir.actions.act_window":
            self.action_id = self.menu_id.action

    @api.onchange("action_id")
    def _onchange_action_id(self):
        if self.menu_id and self.action_id and self.menu_id.action != self.action_id:
            self.menu_id = False

    @api.constrains("model_id", "action_id", "menu_id")
    def _check_ui_targets(self):
        for rule in self:
            if rule.action_id and rule.action_id.res_model != rule.model_name:
                raise ValidationError(_("The action must use the configured model."))
            if rule.menu_id:
                action = rule.menu_id.action
                if not action or action._name != "ir.actions.act_window" or action.res_model != rule.model_name:
                    raise ValidationError(_("Select a menu that opens a window action for the configured model."))
                if rule.action_id and rule.action_id != action:
                    raise ValidationError(_("The menu must open the selected action."))

    @api.model
    def _get_ui_scope(self):
        def record_id(value):
            return int(value) if str(value).isdigit() and int(value) > 0 else False

        action_id = record_id(self.env.context.get("oy_field_action_id"))
        menu_id = record_id(self.env.context.get("oy_field_menu_id"))
        if menu_id:
            menu = self.env["ir.ui.menu"].sudo().browse(menu_id).exists()
            if not menu or not menu.action or menu.action._name != "ir.actions.act_window" or menu.action.id != action_id:
                menu_id = False
        return action_id, menu_id

    @api.constrains("model_id", "search_field_ids", "group_by_field_ids", "export_field_ids")
    def _check_field_models(self):
        for rule in self:
            selected = rule.search_field_ids | rule.group_by_field_ids | rule.export_field_ids
            if any(field.model_id != rule.model_id for field in selected):
                raise ValidationError(_("All selected fields must belong to the configured model."))

    @api.model
    def _get_blocked_fields(self, model_name, operations=("search", "group_by")):
        """Combine distinct fields and resolve explicit allow/deny conflicts per field."""
        if self.env.su:
            return tuple(set() for operation in operations)
        uid = self.env.uid
        group_ids = self.env.user.groups_id.ids
        action_id, menu_id = self._get_ui_scope()
        rules = self.sudo().search([
            ("active", "=", True), ("model_id.model", "=", model_name),
            "|", ("action_id", "=", False), ("action_id", "=", action_id),
            "|", ("menu_id", "=", False), ("menu_id", "=", menu_id),
            "|", "|",
            ("scope", "=", "global"),
            "&", ("scope", "=", "user"), ("user_ids", "in", [uid]),
            "&", ("scope", "=", "group"), ("group_ids", "in", group_ids),
        ])
        scope_rank = {"user": 0, "group": 1, "global": 2}
        all_names = set(self.env[model_name]._fields)
        result = []
        for operation in operations:
            # Reuse the existing Export configuration for both transfer flows.
            policy = "export" if operation == "import" else operation
            decisions = {}
            has_allowlist = False
            for rule in rules:
                mode = rule[f"{policy}_mode"]
                if mode == "none":
                    continue
                has_allowlist |= mode == "allow"
                # Omission from one allowlist must not compete with an explicit
                # selection in another rule. Only selected fields have a rank.
                rank = (rule.priority, scope_rank[rule.scope])
                for name in rule[f"{policy}_field_ids"].mapped("name"):
                    previous = decisions.get(name)
                    if previous is None or rank < previous[0] or (rank == previous[0] and mode == "deny"):
                        decisions[name] = (rank, mode)
            # Any matching allowlist limits unspecified fields. Empty allowlists
            # add no explicit decisions and do not erase other rules' selections.
            blocked = all_names.copy() if has_allowlist else set()
            for name, (_rank, mode) in decisions.items():
                if mode == "deny":
                    blocked.add(name)
                else:
                    blocked.discard(name)
            if operation in ("export", "import"):
                # Odoo uses id for External ID and .id for Database ID.
                # Both resolve to the technical id field in transfer paths.
                blocked.discard("id")
            result.append(blocked)
        return tuple(result)

    @api.model
    def _filter_transfer_paths(self, model_name, paths, operation):
        """Filter export/import paths without changing the business model's ORM methods."""
        if operation not in ("export", "import"):
            raise ValueError("Unsupported transfer operation")
        blocked_by_model = {}

        def allowed(path):
            current_model = model_name
            parts = models.fix_import_export_id_paths(path)
            for index, part in enumerate(parts):
                name = "id" if part == ".id" else part.split(".", 1)[0]
                if current_model not in blocked_by_model:
                    blocked_by_model[current_model] = self._get_blocked_fields(current_model, (operation,))[0]
                if name in blocked_by_model[current_model]:
                    return False
                field = self.env[current_model]._fields.get(name)
                if not field or index == len(parts) - 1 or field.type == "properties":
                    # Unknown paths are left to Odoo's own validation. Dynamic properties
                    # follow the policy of their parent field.
                    return True
                if not field.relational:
                    return True
                current_model = field.comodel_name
            return True

        return [path for path in paths if path and allowed(path)]
