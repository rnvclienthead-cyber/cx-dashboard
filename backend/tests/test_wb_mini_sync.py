import unittest

from backend.wb_mini_sync import (
    _feedback_answer_text,
    review_request_chain_done,
    sync_all_answered_wb,
)


class FeedbackAnswerTextTests(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertIsNone(_feedback_answer_text(None))
        self.assertIsNone(_feedback_answer_text({}))
        self.assertIsNone(_feedback_answer_text({"answer": None}))
        self.assertIsNone(_feedback_answer_text({"answer": {"text": "  "}}))

    def test_dict_and_string(self):
        self.assertEqual(_feedback_answer_text({"answer": {"text": "Спасибо!"}}), "Спасибо!")
        self.assertEqual(_feedback_answer_text({"answer": "Ок"}), "Ок")


class ReviewRequestChainDoneTests(unittest.TestCase):
    def test_new_five_star_after_delivery_is_done(self):
        self.assertTrue(review_request_chain_done({
            "review_before_rating": None,
            "review_after_rating": 5,
        }))

    def test_any_new_review_without_old_one_is_done(self):
        self.assertTrue(review_request_chain_done({
            "review_before_rating": None,
            "review_after_rating": 4,
        }))

    def test_live_five_star_without_snapshot_is_done(self):
        self.assertTrue(review_request_chain_done({
            "review_before_rating": None,
            "review_after_rating": None,
            "current_rating": 5,
        }))

    def test_excellent_before_does_not_ask(self):
        self.assertTrue(review_request_chain_done({
            "review_before_rating": 5,
            "review_after_rating": None,
            "current_rating": 5,
        }))

    def test_rating_grew_is_done(self):
        self.assertTrue(review_request_chain_done({
            "review_before_rating": 2,
            "review_after_rating": 4,
        }))

    def test_same_low_rating_keeps_asking(self):
        self.assertFalse(review_request_chain_done({
            "review_before_rating": 2,
            "review_after_rating": None,
            "current_rating": 2,
        }))

    def test_wrong_or_broken_part_looks_like_complaint(self):
        from backend.wb_mini_sync import text_looks_like_part_complaint
        self.assertTrue(text_looks_like_part_complaint("пришла не та деталь"))
        self.assertTrue(text_looks_like_part_complaint("ролик сломан"))
        self.assertFalse(text_looks_like_part_complaint("спасибо, всё отлично"))

    def test_review_without_marketplace_chat_is_skipped(self):
        from backend.wb_mini_sync import review_has_marketplace_chat
        self.assertFalse(review_has_marketplace_chat({
            "salebot_client_id": "1", "marketplace": "wb",
        }))
        self.assertTrue(review_has_marketplace_chat({
            "wb_chat_id": "1:abc", "marketplace": "wb",
        }))

    def test_review_nags_skip_salebot(self):
        import inspect
        from backend.wb_mini_sync import _send_review_msg_to_client
        src = inspect.getsource(_send_review_msg_to_client)
        self.assertIn("promo_mode and has_salebot", src)


class SyncAllAnsweredSourceTests(unittest.TestCase):
    def test_saves_answer_text_when_wb_has_one(self):
        import inspect
        src = inspect.getsource(sync_all_answered_wb)
        self.assertIn("_feedback_answer_text", src)
        self.assertIn("answer_text", src)
        self.assertIn("COALESCE(NULLIF(TRIM(answer_text), ''), :ans)", src)


class MergeClaimsBySridTests(unittest.TestCase):
    def test_active_claim_wins_over_archived_same_srid(self):
        from backend.wb_mini_sync import _merge_claims_by_srid
        old = {"id": "old-id", "srid": "same.srid"}
        new = {"id": "new-id", "srid": "same.srid"}
        merged = _merge_claims_by_srid([new], [old])
        item, is_arch = merged["same.srid"]
        self.assertEqual(item["id"], "new-id")
        self.assertFalse(is_arch)

    def test_archive_kept_when_no_active(self):
        from backend.wb_mini_sync import _merge_claims_by_srid
        old = {"id": "old-id", "srid": "only.arch"}
        merged = _merge_claims_by_srid([], [old])
        item, is_arch = merged["only.arch"]
        self.assertEqual(item["id"], "old-id")
        self.assertTrue(is_arch)


class UpsertClaimConflictTests(unittest.TestCase):
    def test_upsert_does_not_let_archive_replace_other_active_claim(self):
        import inspect
        from backend.wb_mini_sync import _upsert_claim_row
        src = inspect.getsource(_upsert_claim_row)
        self.assertIn("EXCLUDED.is_archive = true", src)
        self.assertIn("wb_claims.claim_id IS DISTINCT FROM EXCLUDED.claim_id", src)

    def test_upsert_does_not_downgrade_terminal_status(self):
        import inspect
        from backend.wb_mini_sync import _upsert_claim_row
        src = inspect.getsource(_upsert_claim_row)
        self.assertIn("THEN wb_claims.status", src)
        self.assertIn("EXCLUDED.status IN ('На рассмотрении'", src)

    def test_upsert_fills_empty_supplier_article(self):
        import inspect
        from backend.wb_mini_sync import _upsert_claim_row
        src = inspect.getsource(_upsert_claim_row)
        self.assertIn("lookup_wb_articles_by_nm", src)
        self.assertIn("NULLIF(TRIM(EXCLUDED.supplier_article), '')", src)

    def test_upsert_skips_purged_claims(self):
        import inspect
        from backend.wb_mini_sync import _upsert_claim_row
        src = inspect.getsource(_upsert_claim_row)
        self.assertIn("is_purged_claim", src)


class LookupWbArticleByNmTests(unittest.TestCase):
    def test_uses_small_tables_not_logistics(self):
        import inspect
        from backend.wb_mini_sync import lookup_wb_articles_by_nm
        src = inspect.getsource(lookup_wb_articles_by_nm)
        self.assertNotIn('"wb_logistics"', src)
        self.assertNotIn("'wb_logistics'", src)
        self.assertIn("wb_claims", src)
        self.assertIn("wb_feedbacks", src)
        self.assertLess(src.find("wb_claims"), src.find("wb_feedbacks"))
        self.assertIn("nm_id = ANY", src)

    def test_nm_key_strips_float_suffix(self):
        from backend.wb_mini_sync import _nm_id_key
        self.assertEqual(_nm_id_key("243758346.0"), "243758346")
        self.assertEqual(_nm_id_key(243758346), "243758346")


class MapClaimStatusTests(unittest.TestCase):
    def test_status_ex_10_is_approved(self):
        from backend.wb_mini_sync import _map_claim_status
        self.assertEqual(
            _map_claim_status("0", "10", "Заявка одобрена — товар нужно вернуть"),
            "Одобрено",
        )
        self.assertEqual(_map_claim_status("2", "10", ""), "Одобрено")

    def test_stale_refresh_starts_with_oldest(self):
        import inspect
        from backend.wb_mini_sync import refresh_stale_claim_statuses
        src = inspect.getsource(refresh_stale_claim_statuses)
        self.assertIn("dt ASC NULLS LAST", src)

    def test_persist_live_writes_status(self):
        import inspect
        from backend.wb_mini_sync import persist_live_claim_status
        src = inspect.getsource(persist_live_claim_status)
        self.assertIn("SET status = :st", src)
        self.assertIn("is_archive = CASE WHEN :arch THEN true", src)
        self.assertIn("CAST(:overwrite AS boolean)", src)


if __name__ == "__main__":
    unittest.main()
