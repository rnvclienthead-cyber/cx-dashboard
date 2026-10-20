import inspect
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import ozon_chat_sync, ozon_returns_api


ROOT = Path(__file__).resolve().parents[2]


class OzonFormTokenLinkTests(unittest.TestCase):
    def test_linker_rebinds_wrong_chat(self):
        src = ozon_chat_sync.link_reshipment_tokens.__doc__ or ""
        self.assertIn("перепривязываем", src)
        body = inspect.getsource(ozon_chat_sync.link_reshipment_tokens)
        self.assertIn("token rebound", body)
        self.assertIn("_ozon_chat_where_form_token_sent", body)

    def test_apply_sets_ozon_chat_and_article(self):
        body = inspect.getsource(ozon_chat_sync._apply_ozon_chat_to_reshipment)
        self.assertIn("ozon_chat_id = :cid", body)
        self.assertIn("marketplace = 'ozon'", body)
        self.assertIn("supplier_article", body)

    def test_sync_cycle_calls_linker(self):
        src = inspect.getsource(ozon_chat_sync.sync_chats)
        self.assertIn("link_reshipment_tokens", src)


class OzonDeclineReturnForReshipmentTests(unittest.TestCase):
    def test_skips_when_no_return(self):
        db = MagicMock()
        db.execute.return_value.mappings.return_value.first.return_value = None
        result = ozon_returns_api.decline_return_for_reshipment(db, order_id="86752379-0075")
        self.assertEqual(result["skipped"], "no_return")
        self.assertFalse(result["ok"])

    def test_skips_when_already_closed(self):
        db = MagicMock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "return_id": "123",
            "order_id": "86752379",
            "order_number": "86752379-0075",
            "status_sys": "Rejected",
            "status_ru": "Отказ",
        }
        result = ozon_returns_api.decline_return_for_reshipment(db, return_id="123")
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], "already_closed")

    def test_declines_waiting_return(self):
        db = MagicMock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "return_id": "123",
            "order_id": "86752379",
            "order_number": "86752379-0075",
            "status_sys": "OnSellerApproval",
            "status_ru": "На согласовании",
        }
        with patch.object(
            ozon_returns_api,
            "decision_capability",
            return_value={"actions": [{"action": "3", "kind": "reject"}]},
        ), patch.object(
            ozon_returns_api,
            "decide_return",
            return_value={"ok": True},
        ) as decide:
            result = ozon_returns_api.decline_return_for_reshipment(
                db, ozon_chat_id="db51bfb0-2e01-4bbb-bcef-62a74d974b93",
            )
        self.assertTrue(result["ok"])
        decide.assert_called_once()
        self.assertEqual(decide.call_args[0][2], "3")


class OzonSubmitAndNotifySourceTests(unittest.TestCase):
    def test_schema_adds_ozon_chat_id(self):
        src = (ROOT / "backend/schema.py").read_text()
        self.assertIn("ozon_chat_id TEXT", src)

    def test_approve_declines_ozon_return(self):
        src = (ROOT / "backend/routers/reshipment.py").read_text()
        chunk = src.split("def approve_request(", 1)[1].split("\nclass ", 1)[0]
        self.assertIn("decline_return_for_reshipment", chunk)
        self.assertIn("ozon_chat_id", chunk)

    def test_lookup_matches_ozon_chat(self):
        src = (ROOT / "backend/routers/cx_responses.py").read_text()
        chunk = src.split("def _lookup_reshipment_row(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("ozon_chat_id", chunk)
        self.assertIn("used_at IS NOT NULL", chunk)

    def test_offer_keys_ignore_unused_tokens(self):
        src = (ROOT / "backend/routers/cx_responses.py").read_text()
        chunk = src.split("def _collect_offer_key_sets(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("WHERE used_at IS NOT NULL", chunk)
        self.assertNotIn("SELECT chat_id FROM reshipment_chat_tokens\n", chunk)
