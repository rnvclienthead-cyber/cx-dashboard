import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

import ozon_worker
from backend import ozon_client
from backend.routers import cx_responses


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _Connection:
    def __init__(self, question=None):
        self.question = question
        self.statements = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if "SELECT sku, status" in sql:
            return _Result(self.question)
        return _Result()

    def commit(self):
        self.commits += 1


class _Db:
    def __init__(self, connection):
        self.bind = Mock()
        self.bind.connect.return_value = connection


class OzonQuestionTests(unittest.TestCase):
    def test_client_uses_confirmed_answer_contract(self):
        with patch.object(ozon_client, "post", return_value={"answer_id": "a"}) as post:
            result = ozon_client.answer_question("q-1", 123456, "Полезный ответ")

        self.assertEqual(result, {"answer_id": "a"})
        post.assert_called_once_with(
            "/v1/question/answer/create",
            {"question_id": "q-1", "sku": 123456, "text": "Полезный ответ"},
            timeout=20,
        )

    def test_client_enforces_ozon_text_length(self):
        with self.assertRaises(ValueError):
            ozon_client.answer_question("q-1", 123456, "x")
        with self.assertRaises(ValueError):
            ozon_client.answer_question("q-1", 123456, "x" * 3001)

    def test_normalize_question_keeps_exact_sku_and_product_mapping(self):
        normalized = ozon_worker._normalize_question({
            "id": "q-1",
            "sku": 123456,
            "text": "Подойдёт ли размер?",
            "status": "VIEWED",
            "published_at": "2026-08-11T12:00:00Z",
            "answers_count": 0,
        }, {
            "supplier_article": "offer-77",
            "product_name": "Товар",
            "photo_url": "https://cdn.example/photo.jpg",
        })

        self.assertEqual(normalized["question_id"], "q-1")
        self.assertEqual(normalized["sku"], 123456)
        self.assertEqual(normalized["supplier_article"], "offer-77")
        self.assertEqual(normalized["status"], "VIEWED")

    def test_answer_route_publishes_and_updates_local_queue(self):
        connection = _Connection({"sku": 123456, "status": "VIEWED"})
        db = _Db(connection)
        with (
            patch.object(cx_responses, "_oz_key", return_value="test-key"),
            patch.object(cx_responses, "_oz_cid", return_value="test-client"),
            patch.object(cx_responses.ozon_client, "answer_question") as answer,
        ):
            result = cx_responses.answer_ozon_question(
                "q-1",
                cx_responses.AnswerPayload(text="Полезный ответ"),
                db,
                {"username": "manager@example.test", "role": "cs_manager"},
            )

        answer.assert_called_once_with("q-1", 123456, "Полезный ответ", timeout=20)
        self.assertEqual(result, {"status": "ok", "visibility": "public"})
        sql = "\n".join(statement for statement, _ in connection.statements)
        self.assertIn("UPDATE ozon_questions", sql)
        self.assertIn("INSERT INTO cx_response_log", sql)
        self.assertIn("DELETE FROM cx_pending_moderation", sql)
        self.assertEqual(connection.commits, 1)

    def test_answer_route_rejects_closed_question_without_api_call(self):
        db = _Db(_Connection({"sku": 123456, "status": "PROCESSED"}))
        with (
            patch.object(cx_responses, "_oz_key", return_value="test-key"),
            patch.object(cx_responses, "_oz_cid", return_value="test-client"),
            patch.object(cx_responses.ozon_client, "answer_question") as answer,
            self.assertRaises(HTTPException) as caught,
        ):
            cx_responses.answer_ozon_question(
                "q-1",
                cx_responses.AnswerPayload(text="Полезный ответ"),
                db,
                {"username": "manager@example.test", "role": "cs_manager"},
            )
        self.assertEqual(caught.exception.status_code, 409)
        answer.assert_not_called()

    def test_unified_queue_and_clarification_include_ozon(self):
        self.assertIn("FROM ozon_questions q", cx_responses._UNIFIED_QUEUE_VIEW_SQL)
        self.assertIn("q.status IN ('NEW', 'VIEWED', 'UNPROCESSED')", cx_responses._UNIFIED_QUEUE_VIEW_SQL)


if __name__ == "__main__":
    unittest.main()
