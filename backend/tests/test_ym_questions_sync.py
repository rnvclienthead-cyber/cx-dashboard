import unittest
from unittest.mock import patch

import ym_worker


class YmQuestionsSyncTests(unittest.TestCase):
    def test_sync_requests_only_questions_needing_answer(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, dict(body)))
            return {"result": {"questions": [], "paging": {}}}

        with (
            patch.object(ym_worker, "_post", side_effect=fake_post),
            patch.object(ym_worker, "_log_db"),
            patch.object(ym_worker, "engine") as engine,
        ):
            conn = engine.begin.return_value.__enter__.return_value
            conn.execute.return_value.rowcount = 0
            ym_worker.sync_questions()

        self.assertEqual(len(calls), 1)
        path, body = calls[0]
        self.assertIn("/goods-questions", path)
        self.assertTrue(body.get("needAnswer"))
        self.assertEqual(body.get("pageSize"), 100)

    def test_sync_upserts_open_questions_and_closes_missing(self):
        pages = [
            {
                "result": {
                    "questions": [{
                        "questionIdentifiers": {"id": 111, "offerId": "SKU-1"},
                        "createdAt": "2026-08-19T10:00:00+03:00",
                        "text": "Есть в чёрном?",
                        "answers": [],
                    }],
                    "paging": {},
                }
            }
        ]

        with (
            patch.object(ym_worker, "_post", side_effect=pages),
            patch.object(ym_worker, "_log_db") as log_db,
            patch.object(ym_worker, "engine") as engine,
        ):
            conn = engine.begin.return_value.__enter__.return_value
            conn.execute.return_value.rowcount = 2
            ym_worker.sync_questions()

        executed = [str(c.args[0]) for c in conn.execute.call_args_list]
        self.assertTrue(any("INSERT INTO ym_questions" in sql for sql in executed))
        self.assertTrue(any("NOT (question_id = ANY(:ids))" in sql for sql in executed))
        close_params = next(
            c.args[1] for c in conn.execute.call_args_list
            if "NOT (question_id = ANY(:ids))" in str(c.args[0])
        )
        self.assertEqual(close_params["ids"], ["111"])
        log_db.assert_called_once()
        self.assertIn("1 без ответа", log_db.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
