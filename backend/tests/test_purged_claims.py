import inspect
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from backend.purged_claims import is_purged_claim, purged_srid_sql
from backend.return_scan import _NOT_PURGED_SQL, fetch_wb_claim
from backend.schema import apply_schema


class PurgedClaimsTests(unittest.TestCase):
    def test_empty_ids_are_not_purged(self):
        db = MagicMock()
        self.assertFalse(is_purged_claim(db, srid="", claim_id=""))
        db.execute.assert_not_called()

    def test_lookup_uses_srid_or_claim_id(self):
        db = MagicMock()
        db.execute.return_value.first.return_value = (1,)
        self.assertTrue(is_purged_claim(db, srid="ebA.x", claim_id="abc"))
        sql = str(db.execute.call_args.args[0])
        self.assertIn("cx_purged_claims", sql)
        self.assertEqual(db.execute.call_args.args[1]["srid"], "ebA.x")
        self.assertEqual(db.execute.call_args.args[1]["cid"], "abc")

    def test_schema_creates_purge_table_before_view(self):
        src = inspect.getsource(apply_schema)
        table_at = src.find("init_purged_claims_table")
        view_at = src.find("init_claims_enriched_view")
        self.assertGreater(table_at, 0)
        self.assertGreater(view_at, table_at)

    def test_return_scan_skips_purged(self):
        self.assertIn("cx_purged_claims", _NOT_PURGED_SQL)
        src = inspect.getsource(fetch_wb_claim)
        self.assertIn("_NOT_PURGED_SQL", src)
        self.assertIn("cx_purged_claims", purged_srid_sql("c"))

    def test_worker_upsert_skips_purged(self):
        src = (Path(__file__).resolve().parents[2] / "worker.py").read_text()
        self.assertIn("cx_purged_claims", src)
