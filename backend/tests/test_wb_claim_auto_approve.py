import unittest
from unittest.mock import Mock, patch

try:
    from backend.wb_claims_api import approve_return_for_reshipment
except ImportError:
    approve_return_for_reshipment = None


class ApproveReturnForReshipmentTests(unittest.TestCase):
    @unittest.skipUnless(approve_return_for_reshipment, "sqlalchemy not installed")
    def test_skips_when_already_approved(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "claim_id": "c1",
            "srid": "srid-1",
            "status": "Одобрено",
        }
        result = approve_return_for_reshipment(db, srid="srid-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], "already_approved")

    @unittest.skipUnless(approve_return_for_reshipment, "sqlalchemy not installed")
    def test_prefers_approve_with_return(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "claim_id": "c2",
            "srid": "srid-2",
            "status": "Отказ",
        }
        with patch("backend.wb_claims_api.fetch_claim_from_wb", return_value={"actions": ["autorefund1", "approve2"]}):
            with patch("backend.wb_claims_api.decide_claim", return_value={"ok": True, "action": "approve2"}) as decide:
                result = approve_return_for_reshipment(db, srid="srid-2")
        decide.assert_called_once()
        self.assertEqual(decide.call_args.args[2], "approve2")
        self.assertTrue(result["ok"])

    def test_reject_skips_when_already_rejected(self):
        from backend.wb_claims_api import reject_return_for_reshipment
        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "claim_id": "c3",
            "srid": "srid-3",
            "status": "Отказ",
        }
        result = reject_return_for_reshipment(db, srid="srid-3")
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], "already_rejected")

    def test_reject_uses_chat_rid(self):
        from backend.wb_claims_api import reject_return_for_reshipment
        db = Mock()

        def _execute(sql, params=None):
            q = str(sql)
            m = Mock()
            if "wb_chat_pending" in q:
                m.mappings.return_value.first.return_value = {"rid": "rid-from-chat"}
            elif "wb_chats" in q:
                m.mappings.return_value.first.return_value = None
            else:
                m.mappings.return_value.first.return_value = {
                    "claim_id": "c4",
                    "srid": "rid-from-chat",
                    "status": "На рассмотрении",
                }
            return m

        db.execute.side_effect = _execute
        with patch("backend.wb_claims_api.patch_claim", return_value={"ok": True}) as patch_fn:
            with patch("backend.wb_claims_api.apply_local_status") as apply:
                result = reject_return_for_reshipment(db, wb_chat_id="1:abc")
        self.assertTrue(result["ok"])
        patch_fn.assert_called_once()
        self.assertEqual(patch_fn.call_args.args[0], "c4")
        apply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
