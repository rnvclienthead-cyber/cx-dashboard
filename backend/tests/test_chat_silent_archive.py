import inspect
import unittest
from pathlib import Path

from backend import ozon_chat_sync, ym_client
from backend.routers import cx_responses


ROOT = Path(__file__).resolve().parents[2]


class SilentChatArchiveTests(unittest.TestCase):
    def test_queue_hides_only_when_seller_spoke_last(self):
        src = cx_responses._UNIFIED_QUEUE_VIEW_SQL + cx_responses._CHATS_QUEUE_VIEW_SQL
        self.assertIn("lower(COALESCE(c.last_sender, '')) = 'seller'", src)
        self.assertNotIn("cl.sender IN ('client', 'customer', 'buyer')", src)
        self.assertNotIn("cl.sender_type, '')) IN ('customer', 'buyer', 'client')", src)

    def test_workers_archive_only_seller_last(self):
        wb = (ROOT / "backend" / "wb_chat_sync.py").read_text(encoding="utf-8")
        oz = (ROOT / "backend" / "ozon_chat_sync.py").read_text(encoding="utf-8")
        ym = (ROOT / "backend" / "ym_chat_sync.py").read_text(encoding="utf-8")
        for src in (wb, oz, ym):
            self.assertIn("Авто: нет общения >3 дн после нашего ответа", src)
            self.assertNotIn("cl.add_ts >", src)
            self.assertNotIn("cl.created_at >", src)

    def test_unread_always_gets_history(self):
        src = inspect.getsource(ozon_chat_sync.sync_chats)
        self.assertIn("unread_n > 0", src)
        self.assertNotIn("history_done < MAX_HISTORY_CHATS\n                    and (", src)

    def test_unread_forces_needs_answer(self):
        src = inspect.getsource(ozon_chat_sync._upsert_chat)
        self.assertIn("EXCLUDED.unread_count > 0 THEN true", src)

    def test_restore_helper_is_wired(self):
        schema = (ROOT / "backend" / "schema.py").read_text(encoding="utf-8")
        self.assertTrue(callable(cx_responses.restore_client_waiting_chats))
        self.assertIn("restore_client_waiting_chats", schema)
        self.assertIn("Авто: нет общения%", inspect.getsource(cx_responses.restore_client_waiting_chats))

    def test_ym_chat_list_paginates(self):
        src = Path(ym_client.__file__).read_text(encoding="utf-8")
        self.assertIn("pageToken", src)
        self.assertIn("nextPageToken", src)


if __name__ == "__main__":
    unittest.main()
