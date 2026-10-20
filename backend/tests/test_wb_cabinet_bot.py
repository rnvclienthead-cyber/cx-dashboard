import unittest
from pathlib import Path

from backend.wb_cabinet_bot import chat_id_from_cabinet_url


class WbCabinetBotUrlTest(unittest.TestCase):
    def test_chat_id_from_cabinet_url(self):
        url = "https://seller.wildberries.ru/chat-with-clients?chatId=2d0a6c8f-0bd8-59f0-4f3c-acc3d83a1e9c"
        self.assertEqual(
            chat_id_from_cabinet_url(url),
            "1:2d0a6c8f-0bd8-59f0-4f3c-acc3d83a1e9c",
        )

    def test_empty(self):
        self.assertEqual(chat_id_from_cabinet_url(""), "")
        self.assertEqual(chat_id_from_cabinet_url("https://seller.wildberries.ru/chat-with-clients"), "")


class WbCabinetBotOpenChatScriptTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.script = (root / "scripts" / "wb_bot_open_chat.py").read_text(encoding="utf-8")
        self.bot = (Path(__file__).resolve().parents[1] / "wb_cabinet_bot.py").read_text(encoding="utf-8")
        self.router = (Path(__file__).resolve().parents[1] / "routers" / "cx_responses.py").read_text(encoding="utf-8")

    def test_active_skips_archive_search(self):
        self.assertIn("archive: bool = False", self.script)
        self.assertIn('need = "archive" if archive else "under-consideration"', self.script)
        self.assertNotIn("page.goto(ARCHIVE_URL, timeout=60000, wait_until=\"domcontentloaded\")\n            _fill_search", self.script)
        self.assertIn('заявка не нашлась в {where}', self.script)

    def test_runner_passes_active_or_archive_flag(self):
        self.assertIn('"--archive" if archive else "--active"', self.bot)
        self.assertIn("archive: bool = False", self.bot)
        self.assertIn("wait_lock: bool = False", self.bot)
        self.assertIn('cmd.append("--wait")', self.bot)

    def test_open_chat_uses_shared_ensure(self):
        self.assertIn("ensure_wb_claim_chat(db.bind, claim_id, wait_lock=True)", self.router)
        self.assertIn("start_wb_claim_chat_worker", self.bot)
        self.assertNotIn("detail = _detail_wb(db, claim_id)", self.router.split("def open_claim_chat")[1][:1800])

    def test_prefetch_skips_closed_and_existing_chats(self):
        self.assertIn("NOT COALESCE(c.is_archive, false)", self.bot)
        self.assertIn("ch.order_rid = c.srid", self.bot)
        self.assertIn("wait_lock=False", self.bot)

    def test_background_worker_is_registered(self):
        runtime = (Path(__file__).resolve().parents[1] / "bg_runtime.py").read_text(encoding="utf-8")
        self.assertIn("wb_claim_chat", runtime)
        self.assertIn("start_wb_claim_chat_worker", runtime)

    def test_script_waits_for_lock_when_asked(self):
        self.assertIn("wait_lock = False", self.script)
        self.assertIn('elif a == "--wait":', self.script)
        self.assertIn('"busy": True', self.script)
        self.assertIn("time.time() + 90", self.script)


if __name__ == "__main__":
    unittest.main()
