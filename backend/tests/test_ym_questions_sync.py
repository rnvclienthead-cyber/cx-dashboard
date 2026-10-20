import unittest
from pathlib import Path
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

        def fake_post(path, body=None):
            if "offer-mappings" in path:
                return {"result": {"offerMappings": [{
                    "offer": {"offerId": "SKU-1"},
                    "mapping": {"marketSku": 987654},
                }]}}
            return pages.pop(0)

        with (
            patch.object(ym_worker, "_post", side_effect=fake_post),
            patch.object(ym_worker, "_log_db") as log_db,
            patch.object(ym_worker, "engine") as engine,
        ):
            conn = engine.begin.return_value.__enter__.return_value
            conn.execute.return_value.rowcount = 2
            ym_worker.sync_questions()

        executed = [str(c.args[0]) for c in conn.execute.call_args_list]
        self.assertTrue(any("INSERT INTO ym_questions" in sql for sql in executed))
        self.assertTrue(any("market_sku" in sql and "INSERT INTO ym_questions" in sql for sql in executed))
        self.assertTrue(any("NOT (question_id = ANY(:ids))" in sql for sql in executed))
        insert_params = next(
            c.args[1] for c in conn.execute.call_args_list
            if "INSERT INTO ym_questions" in str(c.args[0])
        )
        self.assertEqual(insert_params["article"], "SKU-1")
        close_params = next(
            c.args[1] for c in conn.execute.call_args_list
            if "NOT (question_id = ANY(:ids))" in str(c.args[0])
        )
        self.assertEqual(close_params["ids"], ["111"])
        log_db.assert_called_once()
        self.assertIn("1 без ответа", log_db.call_args.args[2])


class YmQuestionsCliTests(unittest.TestCase):
    def test_questions_task_is_registered(self):
        with open(ym_worker.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"questions": "ym_worker_questions"', src)
        self.assertIn('elif task == "questions":', src)
        self.assertIn("run(sync_questions)", src)

        lock_src = (
            Path(__file__).resolve().parents[1] / "cron_lock.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"ym_worker_questions": 87442223', lock_src)


class YmReturnsSyncTests(unittest.TestCase):
    def test_returns_task_is_registered(self):
        with open(ym_worker.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"returns": "ym_worker_returns"', src)
        self.assertIn("run(sync_recent_returns)", src)
        self.assertIn('"sync_recent_returns": "ym_sync_returns"', src)
        self.assertIn("max_pages", src)

        lock_src = (
            Path(__file__).resolve().parents[1] / "cron_lock.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"ym_worker_returns": 87442224', lock_src)

    def test_recent_sync_stops_after_max_pages(self):
        calls = []

        def fake_get(path, params=None):
            calls.append((path, dict(params or {})))
            token = "next" if not (params or {}).get("page_token") else None
            return {
                "result": {
                    "returns": [],
                    "paging": {"nextPageToken": token} if token else {},
                }
            }

        with (
            patch.object(ym_worker, "_get", side_effect=fake_get),
            patch.object(ym_worker, "_log_db"),
            patch.object(ym_worker.ym_client, "available_campaigns",
                         return_value=[{"id": 1, "placementType": "FBY"}]),
            patch.object(ym_worker, "engine"),
            patch("backend.ym_returns_api.ensure_schema"),
        ):
            ym_worker.sync_returns(max_pages=1)

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
