import inspect
import unittest
from unittest.mock import patch

import ym_worker


class YmFeedbackQueueSyncTests(unittest.TestCase):
    def test_queue_asks_cabinet_by_existing_ids(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, dict(body)))
            return {"result": {"feedbacks": []}}

        with (
            patch.object(ym_worker, "_post", side_effect=fake_post),
            patch.object(ym_worker, "_log_db"),
            patch.object(ym_worker, "engine") as engine,
        ):
            sel = engine.connect.return_value.__enter__.return_value
            sel.execute.return_value.fetchall.return_value = [("458799242",)]
            engine.begin.return_value.__enter__.return_value.execute.return_value.rowcount = 0
            ym_worker.sync_feedback_queue()

        self.assertEqual(len(calls), 1)
        path, body = calls[0]
        self.assertIn("/goods-feedback", path)
        self.assertEqual(body.get("feedbackIds"), [458799242])

    def test_queue_marks_read_or_commented_as_answered(self):
        pages = [{
            "result": {
                "feedbacks": [{
                    "feedbackId": 458799242,
                    "statistics": {"rating": 5, "commentsCount": 1},
                    "needReaction": False,
                }],
            }
        }]

        with (
            patch.object(ym_worker, "_post", side_effect=pages),
            patch.object(ym_worker, "_log_db") as log_db,
            patch.object(ym_worker, "engine") as engine,
        ):
            sel = engine.connect.return_value.__enter__.return_value
            sel.execute.return_value.fetchall.return_value = [("458799242",)]
            upd = engine.begin.return_value.__enter__.return_value
            upd.execute.return_value.rowcount = 1
            ym_worker.sync_feedback_queue()

        executed = [str(c.args[0]) for c in upd.execute.call_args_list]
        self.assertTrue(any("SET answered_at = now()" in sql for sql in executed))
        self.assertTrue(any("feedback_id = :fid" in sql for sql in executed))
        log_db.assert_called_once()
        self.assertIn("закрыто 1", log_db.call_args.args[2])

    def test_queue_does_not_close_ids_missing_from_cabinet(self):
        with (
            patch.object(ym_worker, "_post", return_value={"status": "OK", "result": {"feedbacks": []}}),
            patch.object(ym_worker, "_log_db"),
            patch.object(ym_worker, "engine") as engine,
        ):
            sel = engine.connect.return_value.__enter__.return_value
            sel.execute.return_value.fetchall.return_value = [("450019891",)]
            upd = engine.begin.return_value.__enter__.return_value
            upd.execute.return_value.rowcount = 1
            ym_worker.sync_feedback_queue()

        executed = [str(c.args[0]) for c in upd.execute.call_args_list]
        self.assertFalse(any("SET answered_at = now()" in sql for sql in executed))

    def test_comments_count_marks_answered(self):
        fb = {"statistics": {"commentsCount": 1, "rating": 5}, "needReaction": True}
        self.assertIsNotNone(ym_worker._feedback_answered_at(fb))
        open_fb = {"statistics": {"commentsCount": 0, "rating": 5}, "needReaction": True}
        self.assertIsNone(ym_worker._feedback_answered_at(open_fb))
        read_fb = {"statistics": {"commentsCount": 0, "rating": 5}, "needReaction": False}
        self.assertIsNone(ym_worker._feedback_answered_at(read_fb))

    def test_sync_asks_all_reviews_in_date_windows(self):
        src = inspect.getsource(ym_worker.sync_feedbacks) + inspect.getsource(ym_worker._sync_feedbacks_window)
        self.assertIn("reactionStatus", src)
        self.assertIn("dateTimeFrom", src)
        self.assertLessEqual(ym_worker._YM_FEEDBACK_PAGE, 50)

    def test_page_token_goes_in_query_not_body(self):
        calls = []

        def fake_post(path, body, **kwargs):
            calls.append((path, dict(body or {}), kwargs.get("params")))
            if len(calls) == 1:
                return {
                    "result": {
                        "feedbacks": [{
                            "feedbackId": 1,
                            "statistics": {"commentsCount": 0, "rating": 5},
                            "description": {},
                            "identifiers": {},
                        }],
                        "paging": {"nextPageToken": "NEXT"},
                    }
                }
            return {
                "result": {
                    "feedbacks": [{
                        "feedbackId": 2,
                        "statistics": {"commentsCount": 0, "rating": 4},
                        "description": {},
                        "identifiers": {},
                    }],
                    "paging": {},
                }
            }

        with (
            patch.object(ym_worker.ym_client, "post", side_effect=fake_post),
            patch.object(ym_worker.time, "sleep"),
            patch.object(ym_worker, "_log_db"),
            patch.object(ym_worker, "engine"),
        ):
            n = ym_worker.sync_feedback_need_reaction()

        self.assertEqual(n, 2)
        self.assertNotIn("pageToken", calls[0][1])
        self.assertNotIn("pageToken", calls[1][1])
        self.assertIsNone((calls[0][2] or {}).get("pageToken"))
        self.assertEqual(calls[1][2].get("pageToken"), "NEXT")
        self.assertEqual(calls[0][1].get("reactionStatus"), "NEED_REACTION")

    def test_repeated_page_does_not_loop(self):
        def fake_post(path, body, **kwargs):
            return {
                "result": {
                    "feedbacks": [{
                        "feedbackId": 9,
                        "statistics": {"commentsCount": 0, "rating": 5},
                        "description": {},
                        "identifiers": {},
                    }],
                    "paging": {"nextPageToken": "SAME"},
                }
            }

        with (
            patch.object(ym_worker.ym_client, "post", side_effect=fake_post),
            patch.object(ym_worker.time, "sleep"),
            patch.object(ym_worker, "_log_db"),
            patch.object(ym_worker, "engine"),
        ):
            n = ym_worker.sync_feedback_need_reaction()
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
