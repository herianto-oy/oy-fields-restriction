from odoo import api, models


class Base(models.AbstractModel):
    _inherit = "base"

    @api.model
    def get_views(self, views, options=None):
        # Odoo's view service removes most context keys. Options are part of its
        # cache key and carry the scope through to the field metadata request.
        options = options or {}
        scoped = self.with_context(
            oy_field_action_id=options.get("oy_field_action_id", options.get("action_id", self.env.context.get("oy_field_action_id"))),
            oy_field_menu_id=options.get("oy_field_menu_id", self.env.context.get("oy_field_menu_id")),
        )
        return super(Base, scoped).get_views(views, options=options)

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        result = super().fields_get(allfields=allfields, attributes=attributes)
        relevant = {"searchable", "groupable", "exportable", "oy_search_allowed", "oy_group_by_allowed"}
        if self.env.su or (attributes and not relevant.intersection(attributes)):
            return result
        blocked_search, blocked_group, blocked_export = self.env["field.restriction.rule"]._get_blocked_fields(
            self._name, ("search", "group_by", "export"),
        )
        for name, description in result.items():
            for blocked, flag, capability in (
                (blocked_search, "oy_search_allowed", "searchable"),
                (blocked_group, "oy_group_by_allowed", "groupable"),
            ):
                if not attributes or flag in attributes:
                    description[flag] = name not in blocked
                if name in blocked and capability in description:
                    description[capability] = False
            if name in blocked_export and "exportable" in description:
                description["exportable"] = False
        return result

    @api.model
    def _get_view_field_attributes(self):
        return super()._get_view_field_attributes() + ["oy_search_allowed", "oy_group_by_allowed", "exportable"]
