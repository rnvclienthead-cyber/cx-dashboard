import unittest
from datetime import datetime, timedelta

from jose import jwt

from backend.access import (
    decide_access,
    classify_path,
    session_touch_requested,
    sliding_token_for,
    PUBLIC,
    ANY_AUTH,
    ADMIN,
    MODULES,
    DENY,
)
from backend.routers.auth import ALGORITHM, SECRET_KEY


class ClassifyPathTests(unittest.TestCase):
    def test_login_is_public(self):
        self.assertEqual(classify_path("/api/auth/login"), (PUBLIC, None))

    def test_return_scanner_is_public(self):
        self.assertEqual(classify_path("/api/return/lookup")[0], PUBLIC)
        self.assertEqual(classify_path("/api/return/intake")[0], PUBLIC)
        self.assertEqual(classify_path("/api/return/12.abc.3.0")[0], PUBLIC)

    def test_telegram_webapp_login_is_public(self):
        self.assertEqual(classify_path("/api/auth/telegram-webapp"), (PUBLIC, None))

    def test_tshopping_requires_module(self):
        kind, needed = classify_path("/api/v1/tshopping/chats")
        self.assertEqual(kind, MODULES)
        self.assertEqual(needed, frozenset({"tshopping"}))

    def test_tshopping_webhook_stays_public(self):
        self.assertEqual(classify_path("/api/v1/tshopping/telegram-webhook"), (PUBLIC, None))

    def test_shop_delivery_quote_is_public(self):
        self.assertEqual(classify_path("/api/v1/shop/delivery/quote")[0], PUBLIC)

    def test_shop_orders_ingest_is_public(self):
        self.assertEqual(classify_path("/api/v1/shop/orders/ingest")[0], PUBLIC)

    def test_shop_orders_list_needs_module(self):
        kind, needed = classify_path("/api/v1/shop/orders")
        self.assertEqual(kind, MODULES)
        self.assertEqual(needed, frozenset({"shop_orders"}))

    def test_faq_reshipment_list_uses_faq_module(self):
        kind, needed = classify_path("/api/v1/faq/reshipment-skus")
        self.assertEqual(kind, MODULES)
        self.assertEqual(needed, frozenset({"faq"}))

    def test_finances_and_chats_are_separate_modules(self):
        self.assertEqual(classify_path("/api/v1/finances/loss-analytics")[1], frozenset({"finances"}))
        self.assertEqual(classify_path("/api/v1/cx-responses/queue")[1], frozenset({"cx_responses"}))

    def test_role_catalog_is_admin_only(self):
        self.assertEqual(classify_path("/api/v1/admin/roles")[0], ADMIN)
        self.assertEqual(classify_path("/api/v1/admin/roles/modules")[0], ADMIN)
        self.assertEqual(classify_path("/api/v1/admin/roles/my-permissions")[0], ANY_AUTH)

    def test_unknown_api_is_closed(self):
        self.assertEqual(classify_path("/api/v1/secret-dump")[0], DENY)


class TshoppingOnlyRoleTests(unittest.TestCase):
    role = "tshopping_partner"
    modules = {"tshopping"}

    def test_own_section_allowed(self):
        self.assertIsNone(decide_access("/api/v1/tshopping/chats", self.role, self.modules))
        self.assertIsNone(decide_access("/api/v1/tshopping/stats", self.role, self.modules))
        self.assertIsNone(decide_access("/api/v1/admin/roles/my-permissions", self.role, self.modules))
        self.assertIsNone(decide_access("/api/auth/change-password", self.role, self.modules))

    def test_other_sections_forbidden(self):
        blocked = [
            "/api/v1/finances/loss-analytics",
            "/api/v1/dashboard/executive",
            "/api/v1/cx-responses/queue",
            "/api/v1/reshipment/requests",
            "/api/v1/analytics/production-claims",
            "/api/v1/system/logs",
            "/api/auth/users",
            "/api/v1/admin/roles",
            "/api/v1/cx-control/queues",
        ]
        for path in blocked:
            self.assertEqual(decide_access(path, self.role, self.modules), 403, path)

    def test_no_token_cannot_read_tshopping(self):
        self.assertEqual(decide_access("/api/v1/tshopping/chats", None, set()), 401)

    def test_admin_still_sees_everything(self):
        self.assertIsNone(decide_access("/api/v1/finances/loss-analytics", "admin", set()))
        self.assertIsNone(decide_access("/api/v1/tshopping/chats", "admin", set()))

    def test_cs_manager_keeps_own_modules(self):
        modules = {"cx_responses", "tshopping", "reshipment", "faq"}
        self.assertIsNone(decide_access("/api/v1/cx-responses/queue", "cs_manager", modules))
        self.assertEqual(decide_access("/api/v1/finances/loss-analytics", "cs_manager", modules), 403)


class SlidingSessionTests(unittest.TestCase):
    def test_background_poll_does_not_extend(self):
        self.assertFalse(session_touch_requested({}))
        self.assertIsNone(sliding_token_for({"username": "a@b.c", "role": "admin"}, {}))

    def test_human_action_extends_for_one_day(self):
        token = sliding_token_for(
            {"username": "a@b.c", "role": "cs_manager"},
            {b"x-session-touch": b"1"},
        )
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        self.assertEqual(payload["sub"], "a@b.c")
        self.assertEqual(payload["role"], "cs_manager")
        exp = datetime.utcfromtimestamp(payload["exp"])
        left = exp - datetime.utcnow()
        self.assertGreater(left, timedelta(hours=23, minutes=50))
        self.assertLess(left, timedelta(hours=24, minutes=10))
