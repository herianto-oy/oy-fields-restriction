{
    "name": "Fields Restriction",
    "version": "20.0.1.0.0",
    "summary": "Control Export and Import fields, plus Filters and Group By, by user or group",
    "author": "OY",
    "website": "https://www.linkedin.com/in/herianto-oy/",
    "support": "herianto.oy@gmail.com",
    "description": "Control web Export / Import fields with one shared policy, plus separate Filters and Group By settings. "
                   "Apply rules globally, by user or group, with optional Menu / Action scopes and priorities.",
    "category": "Tools",
    "license": "LGPL-3",
    "images": ["static/description/cover_fields_restriction.png"],
    "depends": ["web", "base_import"],
    "data": [
        "security/ir.access.csv",
        "views/field_restriction_rule_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oy_fields_restriction/static/src/ui_scope.js",
            "oy_fields_restriction/static/src/import_scope.js",
            "oy_fields_restriction/static/src/fields_restriction.js",
        ],
        "web.assets_unit_tests": [
            "oy_fields_restriction/static/tests/fields_restriction.test.js",
            "oy_fields_restriction/static/tests/ui_scope.test.js",
            "oy_fields_restriction/static/tests/import_scope.test.js",
            "oy_fields_restriction/static/tests/export_scope.test.js",
        ],
    },
    "installable": True,
}
