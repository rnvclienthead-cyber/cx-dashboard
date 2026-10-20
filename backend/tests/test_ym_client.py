import unittest
from unittest.mock import Mock, patch

import requests

from backend import ym_client
from backend.ym_returns_api import _friendly_submit_error


class YmClientAnswerTests(unittest.TestCase):
    def test_answer_question_uses_goods_questions_update(self):
        with patch.object(ym_client, "post", return_value={"status": "OK"}) as post:
            ym_client.answer_question("28417577", "  Нагрузка до 20 кг.  ", biz="203")

        path, body = post.call_args.args[:2]
        self.assertEqual(path, "/v1/businesses/203/goods-questions/update")
        self.assertEqual(body["operationType"], "CREATE")
        self.assertEqual(body["parentEntityId"], {"id": 28417577, "type": "QUESTION"})
        self.assertEqual(body["text"], "Нагрузка до 20 кг.")

    def test_answer_feedback_raises_on_status_error(self):
        with patch.object(ym_client, "request") as req:
            resp = Mock()
            resp.json.return_value = {
                "status": "ERROR",
                "errors": [{"code": "BAD_REQUEST", "message": "Invalid comment"}],
            }
            resp.status_code = 200
            resp.raise_for_status = Mock()
            req.return_value = resp
            with self.assertRaises(requests.HTTPError) as caught:
                ym_client.answer_feedback("458799242", "Спасибо", biz="203")
        self.assertIn("Invalid comment", str(caught.exception))

    def test_answer_feedback_uses_comments_update(self):
        with patch.object(ym_client, "post", return_value={"status": "OK"}) as post:
            ym_client.answer_feedback("458799242", "Спасибо за отзыв!", biz="203")

        path, body = post.call_args.args[:2]
        self.assertEqual(path, "/v2/businesses/203/goods-feedback/comments/update")
        self.assertEqual(body, {
            "feedbackId": 458799242,
            "comment": {"text": "Спасибо за отзыв!"},
        })

    def test_feedback_gone_from_cabinet(self):
        class Resp:
            status_code = 404
            text = '{"status":"ERROR","errors":[{"code":"NOT_FOUND","message":"Feedbacks not found."}]}'

        err = requests.HTTPError()
        err.response = Resp()
        self.assertTrue(ym_client.feedback_gone_from_cabinet(err))
        self.assertFalse(ym_client.feedback_gone_from_cabinet(RuntimeError("nope")))

    def test_get_retries_transient_500(self):
        calls = {"n": 0}

        class Resp:
            def __init__(self, code):
                self.status_code = code
                self.content = b"{}"
                self.text = "{}"

            def raise_for_status(self):
                if self.status_code >= 400:
                    err = requests.HTTPError(str(self.status_code))
                    err.response = self
                    raise err

            def json(self):
                return {"ok": True}

        def fake_get(*_a, **_k):
            calls["n"] += 1
            if calls["n"] < 3:
                return Resp(500)
            return Resp(200)

        with patch.object(ym_client.time, "sleep"), patch.object(ym_client.requests, "get", side_effect=fake_get):
            data = ym_client.get("/v2/campaigns")
        self.assertEqual(data["ok"], True)
        self.assertEqual(calls["n"], 3)

    def test_write_post_does_not_retry_500(self):
        class Resp:
            status_code = 500
            text = "boom"
            content = b"boom"

            def raise_for_status(self):
                err = requests.HTTPError("500")
                err.response = self
                raise err

        with patch.object(ym_client.requests, "post", return_value=Resp()) as mocked:
            with self.assertRaises(requests.HTTPError):
                ym_client.post("/v2/x", {"a": 1}, mutate=True)
        self.assertEqual(mocked.call_count, 1)

    def test_answer_question_rejects_non_numeric_id(self):
        with self.assertRaises(ValueError):
            ym_client.answer_question("abc", "текст", biz="203")

    def test_returns_403_explains_missing_write_scope(self):
        msg = _friendly_submit_error(
            403,
            '{"errors":[{"code":"FORBIDDEN","message":"Token does not have any of the scopes '
            'to access the API method. Scopes for accessing the method: all-methods, '
            'inventory-and-order-processing."}],"status":"ERROR"}',
        )
        self.assertIn("Обработка заказов и остатков", msg)
        self.assertNotIn("FORBIDDEN", msg)

    def test_returns_internal_error_is_human(self):
        from backend.ym_returns_api import reason_label, normalize_decision_comment

        msg = _friendly_submit_error(
            500,
            '{"status":"ERROR","errors":[{"code":"INTERNAL_ERROR","message":"Internal error"}]}',
        )
        self.assertIn("250", msg)
        self.assertNotIn("INTERNAL_ERROR", msg)

        self.assertEqual(reason_label("DOES_NOT_FIT"), "Не подошёл")
        self.assertEqual(reason_label("DO_NOT_FIT"), "Не подошёл")
        self.assertNotIn("Does", reason_label("DOES_NOT_FIT"))

        ok, err = normalize_decision_comment("короткий отказ ок", decision_type="DECLINE_REFUND")
        self.assertIsNone(err)
        self.assertEqual(ok, "короткий отказ ок")
        long = "а" * 300
        _, err2 = normalize_decision_comment(long, decision_type="DECLINE_REFUND")
        self.assertIsNotNone(err2)


if __name__ == "__main__":
    unittest.main()
