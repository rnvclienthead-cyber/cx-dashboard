import unittest
from datetime import datetime, timedelta

from backend.ym_chat_sync import last_human_message, needs_answer_from_messages


def _msg(sender, *, minutes=0, text="", mid="1"):
    return {
        "sender_type": sender,
        "created_at": datetime(2026, 9, 12, 18, 0, 0) + timedelta(minutes=minutes),
        "text": text,
        "message_id": mid,
    }


class YmLastHumanWaitingTests(unittest.TestCase):
    def test_customer_last_needs_answer(self):
        msgs = [
            _msg("seller", minutes=0, text="форма", mid="s1"),
            _msg("customer", minutes=10, text="Можете занова отправить", mid="c1"),
        ]
        last = last_human_message(msgs)
        self.assertEqual(last["role"], "customer")
        self.assertTrue(needs_answer_from_messages(msgs))

    def test_local_seller_after_customer_clears_waiting(self):
        msgs = [
            _msg("customer", minutes=0, text="Можете занова отправить", mid="c1"),
            _msg("system", minutes=1, text="не присылайте ссылки", mid="sys"),
            _msg("seller", minutes=120, text="https://box.vidovito.com/reshipment/form?t=x", mid="local-1"),
        ]
        last = last_human_message(msgs)
        self.assertEqual(last["role"], "seller")
        self.assertEqual(last["text"].startswith("https://"), True)
        self.assertFalse(needs_answer_from_messages(msgs))

    def test_system_after_customer_still_waiting(self):
        msgs = [
            _msg("customer", minutes=0, text="вопрос по товару", mid="c1"),
            _msg("system", minutes=1, text="не присылайте ссылки", mid="sys"),
        ]
        last = last_human_message(msgs)
        self.assertEqual(last["role"], "customer")
        self.assertTrue(needs_answer_from_messages(msgs))

    def test_api_order_does_not_matter(self):
        msgs = [
            _msg("seller", minutes=50, text="ссылка", mid="s1"),
            _msg("customer", minutes=0, text="привет", mid="c1"),
        ]
        self.assertFalse(needs_answer_from_messages(msgs))

    def test_sync_commits_before_history_http(self):
        import inspect
        from backend import ym_chat_sync
        src = inspect.getsource(ym_chat_sync.sync_chats)
        upsert_then_history = src.find("_upsert_chat")
        first_history = src.find("_sync_history")
        first_commit = src.find("conn.commit()")
        self.assertGreater(first_commit, upsert_then_history)
        self.assertGreater(first_history, first_commit)
