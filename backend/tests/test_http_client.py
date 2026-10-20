import unittest
from unittest.mock import MagicMock, patch

import requests

from backend import http_client


def _resp(code, headers=None):
    r = MagicMock()
    r.status_code = code
    r.headers = headers or {}
    return r


class FakeCaller:
    """Подставной requests/session: отдаёт заранее заданную очередь ответов
    или бросает исключения (если элемент очереди — исключение)."""

    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class HttpRetryTests(unittest.TestCase):
    def setUp(self):
        # Не спим в тестах.
        p = patch("backend.http_client.time.sleep", lambda *_: None)
        p.start()
        self.addCleanup(p.stop)

    def test_retries_5xx_then_success(self):
        caller = FakeCaller([_resp(503), _resp(200)])
        r = http_client.request("GET", "http://x", session=caller)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(caller.calls, 2)

    def test_retries_connection_error_then_success(self):
        caller = FakeCaller([requests.ConnectionError("boom"), _resp(200)])
        r = http_client.request("GET", "http://x", session=caller)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(caller.calls, 2)

    def test_no_retry_on_400(self):
        caller = FakeCaller([_resp(400), _resp(200)])
        r = http_client.request("GET", "http://x", session=caller)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(caller.calls, 1)

    def test_422_returned_as_is(self):
        # WB отдаёт 422 как «конец пагинации» — не ошибка, повторять нельзя.
        caller = FakeCaller([_resp(422)])
        r = http_client.request("GET", "http://x", session=caller)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(caller.calls, 1)

    def test_exhausts_and_returns_last_5xx(self):
        caller = FakeCaller([_resp(500), _resp(500), _resp(500)])
        r = http_client.request("GET", "http://x", session=caller, retries=2)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(caller.calls, 3)

    def test_429_retried_by_default(self):
        caller = FakeCaller([_resp(429), _resp(200)])
        r = http_client.request("GET", "http://x", session=caller)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(caller.calls, 2)

    def test_429_not_retried_when_caller_handles_it(self):
        caller = FakeCaller([_resp(429), _resp(200)])
        r = http_client.request("GET", "http://x", session=caller, retry_on_429=False)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(caller.calls, 1)

    def test_non_idempotent_does_not_retry_read_timeout(self):
        # POST-отправка: таймаут чтения не повторяем — сервер мог всё выполнить,
        # иначе клиент получит дубль.
        caller = FakeCaller([requests.ReadTimeout("slow"), _resp(200)])
        with self.assertRaises(requests.ReadTimeout):
            http_client.request("POST", "http://x", session=caller, idempotent=False)
        self.assertEqual(caller.calls, 1)

    def test_non_idempotent_retries_connection_error(self):
        # Обрыв соединения = запрос не ушёл: повторить безопасно даже для отправки.
        caller = FakeCaller([requests.ConnectionError("no route"), _resp(200)])
        r = http_client.request("POST", "http://x", session=caller, idempotent=False)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(caller.calls, 2)

    def test_non_idempotent_does_not_retry_5xx(self):
        # 5xx на отправке неоднозначен — не повторяем.
        caller = FakeCaller([_resp(502), _resp(200)])
        r = http_client.request("POST", "http://x", session=caller, idempotent=False)
        self.assertEqual(r.status_code, 502)
        self.assertEqual(caller.calls, 1)

    def test_idempotent_retries_read_timeout(self):
        caller = FakeCaller([requests.ReadTimeout("slow"), _resp(200)])
        r = http_client.request("GET", "http://x", session=caller)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(caller.calls, 2)

    def test_respects_retry_after_header(self):
        sleeps = []
        with patch("backend.http_client.time.sleep", lambda s: sleeps.append(s)):
            caller = FakeCaller([_resp(429, {"Retry-After": "5"}), _resp(200)])
            http_client.request("GET", "http://x", session=caller)
        self.assertEqual(sleeps, [5.0])

    def test_post_helper_defaults_non_idempotent(self):
        caller = FakeCaller([requests.ReadTimeout("x"), _resp(200)])
        with self.assertRaises(requests.ReadTimeout):
            http_client.post("http://x", session=caller)
        self.assertEqual(caller.calls, 1)


if __name__ == "__main__":
    unittest.main()
