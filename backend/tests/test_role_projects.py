import unittest

from backend.routers import roles


class RoleProjectCatalogTests(unittest.TestCase):
    def test_projects_and_inner_sections(self):
        keys = [m["key"] for m in roles.MODULES]
        self.assertIn("project_cx", keys)
        self.assertIn("project_stock", keys)
        self.assertIn("project_navigator", keys)
        self.assertIn("nav_sales", keys)
        self.assertIn("nav_prices", keys)
        self.assertIn("dashboard", keys)
        self.assertIn("stock_queue", keys)
        self.assertIn("stock_fbs", keys)
        self.assertIn("project_cx", roles.ASSIGNABLE_MODULE_KEYS)
        self.assertIn("project_stock", roles.ASSIGNABLE_MODULE_KEYS)
        self.assertIn("project_navigator", roles.ASSIGNABLE_MODULE_KEYS)
        self.assertNotIn("warehouse_intake", roles.ASSIGNABLE_MODULE_KEYS)
        self.assertEqual(next(m for m in roles.MODULES if m["key"] == "dashboard")["project"], "cx")
        self.assertEqual(
            next(m for m in roles.MODULES if m["key"] == "stock_queue")["project"],
            "stock",
        )
        self.assertEqual(
            next(m for m in roles.MODULES if m["key"] == "stock_fbs")["project"],
            "stock",
        )

    def test_infer_keeps_legacy_cx_access(self):
        perms = roles.infer_project_permissions({"cx_responses": "all", "faq": "wb"})
        self.assertEqual(perms["project_cx"], "all")
        self.assertNotIn("project_stock", perms)

    def test_stock_section_implies_stock_project(self):
        perms = roles.infer_project_permissions({"stock_finance": "all"})
        self.assertEqual(perms["project_stock"], "all")

    def test_has_project_access(self):
        self.assertTrue(roles.has_project_access({"cx_responses": "all"}, "cx"))
        self.assertFalse(roles.has_project_access({"cx_responses": "all"}, "stock"))
        self.assertTrue(roles.has_project_access({"project_stock": "all"}, "stock"))
        self.assertTrue(roles.has_project_access({"project_navigator": "all"}, "navigator"))
        self.assertTrue(roles.has_project_access({"nav_prices": "all"}, "navigator"))
        self.assertFalse(roles.has_project_access({"cx_responses": "all"}, "navigator"))
        self.assertFalse(roles.has_project_access({}, "cx"))
        nav = roles.infer_project_permissions({"nav_plan": "all"})
        self.assertEqual(nav["project_navigator"], "all")


if __name__ == "__main__":
    unittest.main()
