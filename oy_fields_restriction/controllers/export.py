import json

from odoo import http, models
from odoo.addons.web.controllers.export import CSVExport, ExcelExport, Export
from odoo.exceptions import UserError
from odoo.http import request


def ui_scope(context):
    return {key: (context or {}).get(key, False) for key in ("oy_field_action_id", "oy_field_menu_id")}


def _export_path_visible(path, import_compat):
    return bool(import_compat) or models.fix_import_export_id_paths(path)[-1] not in ("id", ".id")


class FieldsRestrictionExport(Export):
    @http.route()
    def get_fields(self, model, domain, prefix="", parent_name="", import_compat=True,
                   parent_field_type=None, parent_field=None, exclude=None, context=None):
        if context is not None:
            request.update_context(**ui_scope(context))
        fields = super().get_fields(
            model, domain, prefix=prefix, parent_name=parent_name, import_compat=import_compat,
            parent_field_type=parent_field_type, parent_field=parent_field, exclude=exclude,
        )
        database_id = f"{prefix}/.id" if prefix else ".id"
        if import_compat and not any(field["id"] == database_id for field in fields) and request.env[model]._is_an_ordinary_table():
            label = request.env._("Database ID")
            fields.append({
                "id": database_id, "value": database_id,
                "string": f"{parent_name}/{label}" if parent_name else label,
                "children": False, "field_type": "integer", "required": False,
                "relation_field": False, "default_export": False,
            })
        # At each expansion, model is the current related model, not the root.
        local_paths = [field["id"][len(prefix) + 1:] if prefix else field["id"] for field in fields]
        allowed = set(request.env["field.restriction.rule"]._filter_transfer_paths(model, local_paths, "export"))
        return [field for field, path in zip(fields, local_paths)
                if path in allowed and _export_path_visible(path, import_compat)]

    @http.route()
    def namelist(self, model, export_id, context=None, import_compat=False):
        if context is not None:
            request.update_context(**ui_scope(context))
        result = super().namelist(model, export_id)
        allowed = set(request.env["field.restriction.rule"]._filter_transfer_paths(
            model, [field["id"] for field in result["fields"]], "export",
        ))
        return {**result, "fields": [field for field in result["fields"]
                if field["id"] in allowed and _export_path_visible(field["id"], import_compat)]}


class FieldsRestrictionExportMixin:
    def base(self, data):
        params = json.loads(data)
        import_compat = params.get("import_compat", False)
        policy = request.env["field.restriction.rule"].with_context(**ui_scope(params.get("context")))
        paths = [field["name"] for field in params["fields"]]
        allowed = set(policy._filter_transfer_paths(params["model"], paths, "export"))
        params["fields"] = [field for field in params["fields"]
                            if field["name"] in allowed and _export_path_visible(field["name"], import_compat)]
        if not params["fields"]:
            raise UserError(request.env._("No fields are available for export with your current configuration."))
        # Group headers also contain field values. Export flat data when a grouping
        # field is restricted, while retaining the selected records and domain.
        group_paths = [name.split(":", 1)[0] for name in params.get("groupby") or []]
        allowed_groups = set(policy._filter_transfer_paths(params["model"], group_paths, "export"))
        if any(path not in allowed_groups or not _export_path_visible(path, import_compat) for path in group_paths):
            params.pop("groupby", None)
        return super().base(json.dumps(params))


class FieldsRestrictionCSVExport(FieldsRestrictionExportMixin, CSVExport):
    @http.route()
    def web_export_csv(self, data):
        return super().web_export_csv(data)


class FieldsRestrictionExcelExport(FieldsRestrictionExportMixin, ExcelExport):
    @http.route()
    def web_export_xlsx(self, data):
        return super().web_export_xlsx(data)
