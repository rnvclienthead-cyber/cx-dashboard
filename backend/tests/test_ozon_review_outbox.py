import inspect
import unittest

from backend.routers import cx_responses


class OzonReviewOutboxTests(unittest.TestCase):
    def test_cabinet_queue_is_a_success(self):
        src = inspect.getsource(cx_responses.answer_ozon_review)
        self.assertIn('"status": "queued", "via": "cabinet_bot"', src)
        self.assertNotIn("очередь на отправку через кабинет", src)
        self.assertIn("if code != 403:", src)

    def test_repeat_updates_the_waiting_text(self):
        src = inspect.getsource(cx_responses._queue_ozon_review_reply)
        self.assertIn("UPDATE ozon_review_outbox", src)
        self.assertIn("status = 'pending'", src)

    def test_waiting_reply_leaves_the_manager_queue(self):
        sql = cx_responses._UNIFIED_QUEUE_VIEW_SQL
        self.assertIn("ozon_review_outbox", sql)
        self.assertIn("o.status = 'pending'", sql)


if __name__ == "__main__":
    unittest.main()
