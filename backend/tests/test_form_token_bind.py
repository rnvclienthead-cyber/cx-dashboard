import unittest
from unittest.mock import MagicMock, patch

from backend import chat_bot
from backend import ym_chat_sync


class ExtractFormTokenTests(unittest.TestCase):
    def test_box_url(self):
        text = (
            "Заполните форму:\n"
            "https://box.vidovito.com/reshipment/form?t=glg_K_KXWpV6y58G"
        )
        self.assertEqual(chat_bot.extract_form_tokens(text), ["glg_K_KXWpV6y58G"])

    def test_empty(self):
        self.assertEqual(chat_bot.extract_form_tokens(""), [])
        self.assertEqual(chat_bot.extract_form_tokens(None), [])


class BindFormTokenTests(unittest.TestCase):
    def test_rebinds_unused_token_to_sent_chat(self):
        db = MagicMock()
        n = chat_bot.bind_form_tokens_to_chat(
            db,
            "https://box.vidovito.com/reshipment/form?t=abc12-XYZ",
            "18054100",
            "ym",
        )
        self.assertEqual(n, 1)
        _sql, params = db.execute.call_args[0]
        self.assertIn("used_at IS NULL", str(_sql))
        self.assertEqual(params["cid"], "18054100")
        self.assertEqual(params["token"], "abc12-XYZ")
        self.assertEqual(params["mp"], "ym")

    def test_no_url_is_noop(self):
        db = MagicMock()
        self.assertEqual(chat_bot.bind_form_tokens_to_chat(db, "привет", "1", "ym"), 0)
        db.execute.assert_not_called()


class ChatIdForFormTokenTests(unittest.TestCase):
    def test_prefers_ym_hit(self):
        db = MagicMock()
        ym = MagicMock()
        ym.mappings.return_value.first.return_value = {"chat_id": "18054100"}
        db.execute.return_value = ym
        self.assertEqual(chat_bot.chat_id_for_form_token(db, "glg_K_KXWpV6y58G"), "18054100")
        pat = db.execute.call_args[0][1]["pat"]
        self.assertEqual(pat, "%t=glg_K_KXWpV6y58G%")

    def test_looks_in_ozon_messages(self):
        import inspect
        src = inspect.getsource(chat_bot.chat_id_for_form_token)
        self.assertIn("ozon_chat_messages", src)
        self.assertLess(src.find("ym_chat_messages"), src.find("ozon_chat_messages"))
        self.assertLess(src.find("ozon_chat_messages"), src.find("wb_chat_messages"))


class SubmitPrefersSentChatTests(unittest.TestCase):
    def test_submit_looks_up_chat_where_link_was_sent(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "backend/routers/reshipment.py").read_text()
        submit = src.split("def submit_reshipment(", 1)[1].split("\n@public_router", 1)[0]
        self.assertIn("chat_id_for_form_token", submit)
        self.assertIn('ctx in ("ORDER", "RETURN")', submit)
        lookup = submit.split("if clean_token:", 1)[1]
        self.assertLess(
            lookup.find("chat_id_for_form_token"),
            lookup.find("SELECT chat_id, mp FROM reshipment_chat_tokens"),
        )


class YmLinkRebindTests(unittest.TestCase):
    def test_linker_rebinds_wrong_chat(self):
        src = ym_chat_sync.link_reshipment_tokens.__doc__ or ""
        self.assertIn("перепривязываем", src)
        import inspect
        body = inspect.getsource(ym_chat_sync.link_reshipment_tokens)
        self.assertIn("token rebound", body)
        self.assertIn("_ym_chat_where_form_token_sent", body)


class ApprovedReturnKillsFormTokenTests(unittest.TestCase):
    def test_block_reason_when_claim_approved(self):
        db = MagicMock()
        db.execute.return_value.first.return_value = (1,)
        self.assertEqual(
            chat_bot.form_token_block_reason(db, None, chat_id="1:abc"),
            "return_approved",
        )

    def test_no_block_without_chat(self):
        self.assertIsNone(chat_bot.form_token_block_reason(MagicMock(), None, chat_id=""))

    def test_expire_only_unused_tokens(self):
        import inspect
        src = inspect.getsource(chat_bot.expire_form_tokens_for_approved_return)
        self.assertIn("expired = TRUE", src)
        self.assertIn("used_at IS NULL", src)

    def test_check_token_rejects_approved_return(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "backend/routers/reshipment.py").read_text()
        chunk = src.split("def check_token(", 1)[1].split("\n# ──", 1)[0]
        self.assertIn("form_token_block_reason", chunk)
        self.assertIn("if blocked:", chunk)

    def test_ozon_plain_send_binds_form_token(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "backend/routers/cx_responses.py").read_text()
        chunk = src.split("def send_ozon_chat_message(", 1)[1].split("\n@router.post", 1)[0]
        self.assertIn("bind_form_tokens_to_chat", chunk)
        self.assertIn('"ozon"', chunk)

    def test_upsert_expires_tokens_on_new_approve(self):
        import inspect
        from backend.wb_mini_sync import _upsert_claim_row
        src = inspect.getsource(_upsert_claim_row)
        self.assertIn("expire_form_tokens_for_approved_return", src)
        self.assertIn("prev_status", src)


class DraftDoesNotCreateFormTokenTests(unittest.TestCase):
    def test_make_form_url_preview_does_not_insert(self):
        db = MagicMock()
        with patch("backend.chat_bot.chat_has_approved_return", return_value=False):
            url = chat_bot._make_form_url(db, "1:abc", mp="wb", persist=False)
        self.assertEqual(url, "")
        db.execute.assert_not_called()

    def test_assemble_only_persists_when_commit_form(self):
        import inspect
        src = inspect.getsource(chat_bot.assemble_reply)
        self.assertIn("commit_form: bool = False", src)
        self.assertIn("persist=commit_form", src)

    def test_process_one_commits_form_only_on_auto_send(self):
        import inspect
        src = inspect.getsource(chat_bot.process_one)
        self.assertIn("commit_form=(zone == \"auto\" and not shadow)", src)
        self.assertIn("expire_unused_form_tokens_in_text", src)

    def test_draft_keeps_form_placeholder(self):
        text = chat_bot._fill_placeholders("Заполните форму\n{form_url}")
        self.assertIn("{form_url}", text)

    def test_expire_unused_tokens_in_unsent_text(self):
        db = MagicMock()
        db.execute.return_value.rowcount = 1
        n = chat_bot.expire_unused_form_tokens_in_text(
            db, "https://box.vidovito.com/reshipment/form?t=abc12-XYZ",
        )
        self.assertEqual(n, 1)
        sql = str(db.execute.call_args[0][0])
        self.assertIn("expired = TRUE", sql)
        self.assertIn("used_at IS NULL", sql)
