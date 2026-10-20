import unittest

from backend.routers import cx_responses


class ChatSearchTokenTests(unittest.TestCase):
    def test_splits_keywords_and_drops_short(self):
        self.assertEqual(cx_responses._chat_search_tokens("  Иван   швабра "), ["Иван", "швабра"])
        self.assertEqual(cx_responses._chat_search_tokens("я"), [])
        self.assertEqual(cx_responses._chat_search_tokens(""), [])

    def test_escapes_ilike_wildcards(self):
        self.assertEqual(cx_responses._ilike_contains("100%"), "%100\\%%")
        self.assertEqual(cx_responses._ilike_contains("a_b"), "%a\\_b%")

    def test_all_tokens_must_match_somewhere(self):
        sql, params = cx_responses._token_match_sql(
            ["COALESCE(c.client_name,'')", "COALESCE(c.last_message,'')"],
            ["Иван", "брак"],
            "wb",
            extra_or="EXISTS (SELECT 1 FROM wb_chat_messages m WHERE m.text ILIKE :tok)",
        )
        self.assertIn("AND", sql)
        self.assertIn("wb_0", params)
        self.assertIn("wb_1", params)
        self.assertIn(":wb_0", sql)
        self.assertIn(":wb_1", sql)
        self.assertIn("EXISTS (SELECT 1 FROM wb_chat_messages m WHERE m.text ILIKE :wb_0)", sql)
        self.assertNotIn(":tok", sql)


class ChatQueueSeenSqlTests(unittest.TestCase):
    def test_queue_query_joins_shared_seen(self):
        import inspect
        src = inspect.getsource(cx_responses)
        self.assertIn("LEFT JOIN cx_chat_seen s ON s.mp = q.mp AND s.chat_id = q.id", src)
        self.assertIn("s.seen_ts", src)

    def test_search_endpoint_exists(self):
        self.assertTrue(hasattr(cx_responses, "search_chats"))
        self.assertTrue(hasattr(cx_responses, "mark_chat_seen"))
        self.assertTrue(hasattr(cx_responses, "mark_all_chats_seen"))
