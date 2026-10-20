import inspect
import unittest

from backend.review_updates import (
    KIND_LABELS,
    OZON_MARK,
    WB_UNANSWERED_MARK,
    YM_MARK,
    update_label,
)


class ReviewUpdateLabelTests(unittest.TestCase):
    def test_known_kinds(self):
        self.assertEqual(update_label("edited"), "Обновлён")
        self.assertEqual(update_label("reopened"), "Снова в очереди")
        self.assertEqual(update_label("added_text"), "Добавил текст")
        self.assertEqual(update_label(""), "")
        self.assertEqual(update_label(None), "")
        self.assertEqual(set(KIND_LABELS), {"edited", "reopened", "added_text"})


class ReviewUpdateSqlTests(unittest.TestCase):
    def test_wb_unanswered_marks_reopen_and_keeps_prev(self):
        self.assertIn("wb_feedbacks.state IN ('answered', 'archive')", WB_UNANSWERED_MARK)
        self.assertIn("prev_text", WB_UNANSWERED_MARK)
        self.assertIn("buyer_updated_at", WB_UNANSWERED_MARK)
        self.assertIn("'reopened'", WB_UNANSWERED_MARK)
        self.assertIn("'added_text'", WB_UNANSWERED_MARK)

    def test_ym_reopens_when_incoming_unanswered(self):
        self.assertIn("ym_feedbacks.answered_at IS NOT NULL", YM_MARK)
        self.assertIn("EXCLUDED.answered_at IS NULL", YM_MARK)

    def test_ozon_does_not_reopen_on_every_sync(self):
        self.assertNotIn("answered_at IS NOT NULL AND EXCLUDED.answered_at IS NULL", OZON_MARK)

    def test_mini_sync_uses_mark(self):
        from backend import wb_mini_sync
        src = inspect.getsource(wb_mini_sync.sync_recent_feedbacks)
        self.assertIn("WB_UNANSWERED_MARK", src)

    def test_queue_attaches_update_fields(self):
        from backend.routers import cx_responses
        src = inspect.getsource(cx_responses.get_queue)
        self.assertIn("_attach_review_updates", src)
        card = inspect.getsource(cx_responses.review_card)
        self.assertIn("update_kind", card)
        self.assertIn("linked_reshipment", card)

    def test_schema_adds_columns(self):
        from backend import schema
        src = inspect.getsource(schema.apply_schema)
        self.assertIn("buyer_updated_at", src)
        self.assertIn("update_kind", src)
        self.assertIn("wb_feedback_snapshots", src)


if __name__ == "__main__":
    unittest.main()
