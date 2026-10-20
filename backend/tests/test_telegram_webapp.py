import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

from backend.access import classify_path, PUBLIC
from backend.telegram_webapp import (
    chat_open_markup,
    encode_start_param,
    mini_app_link,
    parse_start_param,
    site_chat_url,
    validate_init_data,
)


def _sign(fields: dict, token: str) -> str:
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()


class StartParamTests(unittest.TestCase):
    def test_ozon_uuid_roundtrip(self):
        chat_id = "fdbb1be2-d564-4beb-9dad-78b0618a7341"
        param = encode_start_param("ozon", chat_id)
        self.assertLessEqual(len(param), 64)
        self.assertEqual(parse_start_param(param), ("ozon", chat_id))

    def test_wb_colon_id(self):
        param = encode_start_param("wb", "1:abc-def")
        self.assertNotIn(":", param)
        self.assertEqual(parse_start_param(param), ("wb", "1:abc-def"))

    def test_site_url_keeps_query(self):
        url = site_chat_url("ozon", "fdbb1be2-d564-4beb-9dad-78b0618a7341")
        self.assertIn("/chats?chat=fdbb1be2-d564-4beb-9dad-78b0618a7341", url)
        self.assertIn("mp=ozon", url)
        self.assertTrue("cxvo.ru" in url or "workers.dev" in url)


class MarkupTests(unittest.TestCase):
    def test_private_chat_uses_mini_app_button(self):
        markup = chat_open_markup("ozon", "abc", 123456)
        btn = markup["inline_keyboard"][0][0]
        self.assertEqual(btn["text"], "Открыть чат")
        self.assertNotIn("url", btn)
        self.assertIn("web_app", btn)
        self.assertIn("chats?chat=abc", btn["web_app"]["url"])

    def test_group_uses_https_button(self):
        markup = chat_open_markup("wb", "1:uuid", -100123)
        btn = markup["inline_keyboard"][0][0]
        self.assertIn("url", btn)
        self.assertNotIn("web_app", btn)
        self.assertIn("/chats?", btn["url"])


class InitDataTests(unittest.TestCase):
    def test_valid_signature(self):
        token = "123:ABC"
        now = int(time.time())
        user = json.dumps({"id": 99, "first_name": "R"})
        fields = {"auth_date": str(now), "query_id": "AA", "user": user}
        fields_hash = _sign(fields, token)
        init = urlencode({**fields, "hash": fields_hash})
        parsed = validate_init_data(init, token, now=now)
        self.assertEqual(parsed["id"], 99)

    def test_bad_hash(self):
        with self.assertRaises(ValueError):
            validate_init_data("auth_date=1&user=%7B%22id%22%3A1%7D&hash=00", "token", now=1)

    def test_stale_auth_date(self):
        token = "123:ABC"
        fields = {"auth_date": "10", "user": json.dumps({"id": 1})}
        init = urlencode({**fields, "hash": _sign(fields, token)})
        with self.assertRaises(ValueError):
            validate_init_data(init, token, now=10 + 48 * 3600)


class AccessTests(unittest.TestCase):
    def test_telegram_webapp_login_is_public(self):
        self.assertEqual(classify_path("/api/auth/telegram-webapp"), (PUBLIC, None))


class MiniLinkTests(unittest.TestCase):
    @patch("backend.telegram_webapp.telegram_proxy.call", return_value=None)
    def test_fallback_bot_username(self, _call):
        with patch.dict("os.environ", {"TG_BOT_USERNAME": "cxvidovito_bot"}, clear=False):
            link = mini_app_link("ozon", "abc")
        self.assertTrue(link.startswith("https://t.me/cxvidovito_bot?startapp="))


class NotifyUsesMiniAppTests(unittest.TestCase):
    def test_chat_bot_sends_markup(self):
        from backend import chat_bot

        db = MagicMock()
        with patch.object(chat_bot.chat_playbook, "get_setting", side_effect=lambda *_a, **_k: "true" if _a[1] == "red_zone_notify_tg" else ""), \
             patch.dict("os.environ", {"TG_ALERT_CHAT_ID": "777", "TG_BOT_USERNAME": "cxvidovito_bot"}, clear=False), \
             patch.object(chat_bot.telegram_proxy, "send_text", return_value=True) as send, \
             patch.object(chat_bot.cx_alert, "send_tg") as fallback:
            chat_bot._notify_manager(db, "red", "Клиент", "SKU", "текст", "chat-1", mp="ozon")
        self.assertTrue(send.called)
        args, kwargs = send.call_args
        self.assertEqual(args[0], "777")
        self.assertNotIn("workers.dev", args[1])
        self.assertNotIn("cxvo.ru/chats", args[1])
        btn = kwargs["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(btn["text"], "Открыть чат")
        self.assertIn("web_app", btn)
        self.assertIn("workers.dev", btn["web_app"]["url"])
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
