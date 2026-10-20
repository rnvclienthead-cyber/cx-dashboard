import unittest

from backend.telegram_proxy import strip_tg_html


class StripTgHtmlTests(unittest.TestCase):
    def test_monitor_alert_looks_plain(self):
        raw = (
            "🚨 <b>CX-Dashboard: СБОЙ</b>\n"
            "<b>Фоновые задачи (синк, чаты)</b>\n"
            "Фоновые задачи (cx-bg) не запущены.\n"
            "<i>Длится: 30 мин. Проверьте систему.</i>"
        )
        out = strip_tg_html(raw)
        self.assertNotIn("<b>", out)
        self.assertNotIn("</b>", out)
        self.assertNotIn("<i>", out)
        self.assertIn("CX-Dashboard: СБОЙ", out)
        self.assertIn("Длится: 30 мин", out)


class TelegramProxySendMessageTests(unittest.TestCase):
    def test_worker_body_keeps_parse_mode(self):
        import inspect
        from backend import telegram_proxy
        src = inspect.getsource(telegram_proxy.call)
        self.assertIn('body["parse_mode"]', src)


if __name__ == "__main__":
    unittest.main()
