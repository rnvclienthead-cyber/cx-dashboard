import inspect
import unittest
from unittest.mock import Mock

from backend.database import add_column_if_missing, column_exists
from backend.schema import apply_schema
from backend import chat_bot, wb_chat
from backend.wb_mini_sync import refresh_stale_claim_statuses, sync_recent_claims


class ColumnExistsTests(unittest.TestCase):
    def test_skips_alter_when_column_present(self):
        conn = Mock()
        conn.execute.return_value.scalar.return_value = 1
        added = add_column_if_missing(
            conn, "wb_chats", "pre_purchase",
            "ALTER TABLE wb_chats ADD COLUMN pre_purchase BOOLEAN",
        )
        self.assertFalse(added)
        sql = str(conn.execute.call_args.args[0])
        self.assertIn("information_schema.columns", sql)
        self.assertNotIn("ALTER TABLE", sql)

    def test_alters_only_when_missing(self):
        conn = Mock()
        conn.execute.return_value.scalar.return_value = None
        added = add_column_if_missing(
            conn, "wb_chats", "pre_purchase",
            "ALTER TABLE wb_chats ADD COLUMN pre_purchase BOOLEAN",
        )
        self.assertTrue(added)
        self.assertEqual(conn.execute.call_count, 2)
        sql = str(conn.execute.call_args.args[0])
        self.assertIn("ALTER TABLE wb_chats ADD COLUMN pre_purchase", sql)
        self.assertNotIn("IF NOT EXISTS", sql)


class HotPathNoExclusiveAlterTests(unittest.TestCase):
    def test_ensure_does_not_use_if_not_exists_on_wb_chats(self):
        src = inspect.getsource(wb_chat.ensure_chat_order_rid_column)
        self.assertIn("add_column_if_missing", src)
        self.assertNotIn("ADD COLUMN IF NOT EXISTS", src)

    def test_startup_does_not_alter_wb_chats_if_not_exists(self):
        src = inspect.getsource(apply_schema)
        self.assertNotIn("ALTER TABLE wb_chats ADD COLUMN IF NOT EXISTS", src)
        self.assertIn("add_column_if_missing", src)

    def test_init_bot_tables_rollbacks_failed_ddl(self):
        src = inspect.getsource(chat_bot.init_bot_tables)
        self.assertGreaterEqual(src.count("conn.rollback()"), 5)
        self.assertIn("idx_chat_bot_log_sent_auto_lookup", src)

    def test_stale_claims_commit_before_wb_http(self):
        src = inspect.getsource(refresh_stale_claim_statuses)
        after_fetch = src.split("fetchall()", 1)[1]
        before_http, _, rest = after_fetch.partition("_find_claim_in_api")
        self.assertIn("db.commit()", before_http)
        self.assertTrue(rest)

    def test_missing_claims_commit_before_lookup(self):
        src = inspect.getsource(sync_recent_claims)
        chunk = src.split("claims left active list")[0]
        lookup_at = chunk.rfind("_find_claim_in_api")
        self.assertGreater(lookup_at, 0)
        self.assertIn("db.commit()", chunk[chunk.rfind("missing ="):lookup_at])


class ColumnExistsSqlTests(unittest.TestCase):
    def test_sql_is_metadata_only(self):
        conn = Mock()
        conn.execute.return_value.scalar.return_value = 1
        self.assertTrue(column_exists(conn, "wb_chats", "pre_purchase"))
        sql = str(conn.execute.call_args.args[0])
        self.assertIn("information_schema.columns", sql)
        self.assertNotIn("ALTER", sql)


if __name__ == "__main__":
    unittest.main()
