import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "backend/routers/reshipment.py").read_text()
SUBMIT = SRC.split("def submit_reshipment(", 1)[1].split("\n@public_router", 1)[0]


class SubmitDupeGuardSourceTests(unittest.TestCase):
    def test_token_is_claimed_before_pickup_lookup(self):
        claim_at = SUBMIT.find("claim_submit_token(db, _token_clean)")
        candidates_at = SUBMIT.find("get_both_candidates(")
        self.assertGreater(claim_at, 0)
        self.assertGreater(candidates_at, claim_at)

    def test_second_open_request_check_before_insert(self):
        lock_at = SUBMIT.find("pg_advisory_xact_lock")
        insert_at = SUBMIT.find("INSERT INTO reshipment_requests")
        self.assertGreater(lock_at, 0)
        self.assertGreater(insert_at, lock_at)

    def test_token_chat_id_survives_after_claim(self):
        """После занятия ссылки чат берём из уже прочитанного токена, не требуя used_at IS NULL."""
        self.assertIn("clean_chat_id  = (wb_chat_id or \"\").strip() or _chat_clean or None", SUBMIT)
        lookup = SUBMIT.split("if not clean_chat_id and clean_token:", 1)[1]
        self.assertNotIn("used_at IS NULL", lookup.split("if clean_chat_id:", 1)[0])

    def test_prefers_chat_where_form_link_was_sent(self):
        self.assertIn("chat_id_for_form_token", SUBMIT)
        self.assertIn('ctx in ("ORDER", "RETURN")', SUBMIT)
        self.assertIn("ozon_chats", SUBMIT)
        self.assertIn("_get_article", SUBMIT)
        self.assertIn('mp == "ozon"', SUBMIT)
        self.assertIn("ozon_chat_id", SUBMIT)

    def test_blocks_submit_when_return_already_approved(self):
        self.assertIn("form_token_block_reason", SUBMIT)
        self.assertIn("возврат уже одобрен", SUBMIT)

    def test_does_not_reject_wb_claim_on_form_submit(self):
        self.assertNotIn("auto-reject on form submit", SUBMIT)
        after_insert = SUBMIT.split("INSERT INTO reshipment_requests", 1)[1]
        self.assertNotIn("reject_return_for_reshipment", after_insert)
