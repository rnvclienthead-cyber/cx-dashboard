import unittest
from unittest.mock import patch

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.mp_api_watch import (
    extract_paths,
    format_alert,
    format_digest,
    is_relevant,
    parse_channel_html,
    seconds_until_daily,
)

HTML = """
<div class="tgme_widget_message" data-post="OzonSellerAPI/683">
<div class="tgme_widget_message_text js-message_text" dir="auto">
<b>Изменения в методах</b><br/>отключены /v3/finance/transaction/list
</div></div>
<div class="tgme_widget_message" data-post="OzonSellerAPI/682">
<div class="tgme_widget_message_text js-message_text" dir="auto">
Пройти опрос про модули Маркета
</div></div>
<div class="tgme_widget_message" data-post="wb_api_notifications/10">
<div class="tgme_widget_message_text js-message_text" dir="auto">
чужой канал
</div></div>
"""


class ParseTests(unittest.TestCase):
    def test_parses_own_channel_posts(self):
        posts = parse_channel_html(HTML, "OzonSellerAPI")
        self.assertEqual([p["post_id"] for p in posts], [682, 683])
        self.assertIn("/v3/finance/transaction/list", posts[1]["text"])
        self.assertEqual(posts[1]["url"], "https://t.me/OzonSellerAPI/683")

    def test_skips_survey_keeps_method_post(self):
        self.assertFalse(is_relevant("Пройти опрос про модули Маркета"))
        self.assertTrue(is_relevant("отключены /v3/finance/transaction/list"))
        self.assertTrue(is_relevant("Изменения в WB API чатов"))

    def test_extracts_paths(self):
        paths = extract_paths("GET /api/v3/orders и /v3/finance/transaction/list")
        self.assertTrue(any("orders" in p for p in paths))
        self.assertTrue(any("finance" in p for p in paths))

    def test_alert_mentions_usage(self):
        channel = {"mp": "Ozon", "label": "Ozon Seller API"}
        post = {"text": "Метод /v1/chat/list устарел", "url": "https://t.me/OzonSellerAPI/1"}
        msg = format_alert(channel, post, ["ozon_client.py"])
        self.assertIn("⚠️ у нас в коде: ozon_client.py", msg)
        empty = format_alert(channel, post, [])
        self.assertIn("не нашёл", empty)


class ScheduleTests(unittest.TestCase):
    def test_waits_until_moscow_1500(self):
        msk = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 9, 24, 14, 0, tzinfo=msk)
        wait = seconds_until_daily(now, hour=15, minute=0)
        self.assertEqual(int(wait), 3600)
        after = datetime(2026, 9, 24, 15, 1, tzinfo=msk)
        wait2 = seconds_until_daily(after, hour=15, minute=0)
        self.assertGreater(wait2, 20 * 3600)

    def test_digest_empty_and_with_posts(self):
        msk = ZoneInfo("Europe/Moscow")
        empty = format_digest({"items": [], "errors": []}, now=datetime(2026, 9, 24, 15, 0, tzinfo=msk))
        self.assertIn("15:00 МСК", empty)
        self.assertIn("новых постов про методы нет", empty)
        stats = {
            "items": [{
                "channel": {"mp": "Ozon", "label": "Ozon Seller API"},
                "post": {"text": "Метод /v1/chat/list устарел", "url": "https://t.me/OzonSellerAPI/1"},
                "used_in": ["ozon_client.py"],
            }],
            "errors": [],
        }
        msg = format_digest(stats, now=datetime(2026, 9, 24, 15, 0, tzinfo=msk))
        self.assertIn("Ozon:", msg)
        self.assertIn("есть у нас", msg)
        self.assertIn("https://t.me/OzonSellerAPI/1", msg)


class TickSeedTests(unittest.TestCase):
    def test_first_run_marks_without_sending(self):
        from backend import mp_api_watch

        posts = parse_channel_html(HTML, "OzonSellerAPI")
        sent = []

        class FakeConn:
            def __init__(self):
                self.ids = set()
                self.commits = 0

            def execute(self, q, params=None):
                sql = str(q)
                if "SELECT post_id" in sql:
                    class R:
                        def fetchall(inner):
                            return [(i,) for i in self.ids]
                    return R()
                if "INSERT" in sql:
                    self.ids.add(int(params["id"]))
                    class R:
                        pass
                    return R()
                return type("R", (), {"fetchall": lambda *_: []})()

            def commit(self):
                self.commits += 1

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake = FakeConn()
        with patch.object(mp_api_watch, "fetch_html", return_value=HTML), \
             patch.object(mp_api_watch, "init_tables"), \
             patch.object(mp_api_watch.engine, "connect", return_value=fake), \
             patch.object(mp_api_watch, "CHANNELS", ({"slug": "OzonSellerAPI", "mp": "Ozon", "label": "Ozon"},)), \
             patch.dict("os.environ", {"TG_ALERT_CHAT_ID": "1"}, clear=False):
            stats = mp_api_watch.tick(send_fn=lambda *a, **k: sent.append(a) or True)
        self.assertEqual(stats["seeded"], 2)
        self.assertEqual(stats["sent"], 0)
        self.assertFalse(sent)
        self.assertEqual(len(posts), 2)


if __name__ == "__main__":
    unittest.main()
