from odoo import api, models, _
from odoo.addons.base_import.models.base_import import FIELDS_RECURSION_LIMIT


class BaseImport(models.TransientModel):
    _inherit = "base_import.import"

    @api.model
    def get_fields_tree(self, model, depth=FIELDS_RECURSION_LIMIT):
        tree = super().get_fields_tree(model, depth=depth)
        if not any(node["name"] == ".id" for node in tree):
            tree.append({
                "id": ".id", "name": ".id", "string": _("Database ID"),
                "required": False, "fields": [], "type": "id", "model_name": model,
            })
        policy = self.env["field.restriction.rule"]

        def all_paths(nodes, prefix=""):
            for node in nodes:
                path = f"{prefix}/{node['name']}" if prefix else node["name"]
                yield path
                yield from all_paths(node.get("fields", []), path)

        allowed = set(policy._filter_transfer_paths(model, list(all_paths(tree)), "import"))

        def filter_tree(nodes, prefix=""):
            result = []
            seen = set()
            for node in nodes:
                path = f"{prefix}/{node['name']}" if prefix else node["name"]
                # Odoo can append .id again to one2many children in debug mode.
                if path in allowed and path not in seen:
                    seen.add(path)
                    result.append({**node, "fields": filter_tree(node.get("fields", []), path)})
            return result

        return filter_tree(tree)

    def parse_preview(self, options, count=10):
        preview = super().parse_preview(options, count=count)
        if preview.get("matches"):
            paths = {index: "/".join(parts) for index, parts in preview["matches"].items()}
            allowed = set(self.env["field.restriction.rule"]._filter_transfer_paths(
                self.res_model, list(paths.values()), "import",
            ))
            preview["matches"] = {
                index: parts for index, parts in preview["matches"].items() if paths[index] in allowed
            } or False
        return preview

    def execute_import(self, fields, columns, options, dryrun=False):
        self.ensure_one()
        # Recheck at submission for mappings kept open before a rule was changed.
        paths = [path for path in fields if path]
        paths += list(options.get("import_set_empty_fields") or [])
        paths += list(options.get("fallback_values") or {})
        allowed = set(self.env["field.restriction.rule"]._filter_transfer_paths(self.res_model, paths, "import"))
        blocked = sorted(set(paths) - allowed)
        if blocked:
            return {"messages": [{
                "type": "error", "record": False,
                "message": _("These fields are not available for import: %s", ", ".join(blocked)),
            }]}
        return super().execute_import(fields, columns, options, dryrun=dryrun)
