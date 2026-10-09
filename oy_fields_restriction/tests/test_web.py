from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestFieldsRestrictionWeb(HttpCase):
    def test_fields_restriction_js(self):
        self.browser_js(
            "/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&filter=oy_fields_restriction",
            "", login="admin", timeout=180,
            success_signal="[HOOT] Test suite succeeded",
        )
