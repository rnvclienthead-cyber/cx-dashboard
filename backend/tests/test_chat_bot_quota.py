import os
import unittest
from unittest.mock import MagicMock, Mock, patch

from backend import chat_bot
from backend.qwen_quota import (
    FREE_QWEN_MODELS,
    note_exhausted,
    qwen_wait_sec,
    reset_quota_state,
)


class ChatBotQuotaPauseTests(unittest.TestCase):
    def setUp(self):
        os.environ["QWEN_QUOTA_PERSIST"] = "0"
        reset_quota_state()

    def tearDown(self):
        reset_quota_state()

    def test_one_model_quota_does_not_freeze_bot(self):
        note_exhausted(FREE_QWEN_MODELS[0])
        self.assertFalse(chat_bot._quota_paused())
        self.assertEqual(qwen_wait_sec(), 0.0)

    def test_all_models_exhausted_pauses_until_next(self):
        import time
        far = time.time() + 900
        for model in FREE_QWEN_MODELS:
            note_exhausted(model, until=far)
        self.assertTrue(chat_bot._quota_paused())
        wait = chat_bot._quota_wait_sec()
        self.assertGreater(wait, 800)
        self.assertLess(wait, 910)

    def test_timeout_does_not_pause(self):
        chat_bot._mark_quota_pause("timeout")
        self.assertFalse(chat_bot._quota_paused())

    def test_manager_draft_ignores_bot_ai_draft_off(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = {
            "name": "Где мой заказ", "when_text": "",
        }
        with patch("backend.chat_bot.chat_playbook.get_setting_mp", return_value="false") as setting:
            with patch("backend.chat_bot.chat_playbook.get_setting", return_value="Ты менеджер Видовито."):
                with patch("backend.chat_bot._similar_seller_replies", return_value=[]):
                    with patch("backend.chat_bot._correction_hints_for_prompt", return_value=""):
                        with patch(
                            "backend.chat_bot._llm_complete",
                            return_value="Сейчас уточним статус заказа и напишем вам сразу.",
                        ) as llm:
                            out = chat_bot.generate_ai_draft(
                                db,
                                client_msg="где мой заказ",
                                theme="dostavka_wb",
                                article="BO",
                                history=[],
                                for_manager=True,
                            )
        self.assertTrue(out)
        self.assertGreater(len(out), 15)
        setting.assert_not_called()
        self.assertEqual(llm.call_args.kwargs.get("timeout"), 20)
        self.assertEqual(llm.call_args.kwargs.get("max_models"), 2)

    def test_autobot_draft_respects_bot_ai_draft_off(self):
        with patch("backend.chat_bot.chat_playbook.get_setting_mp", return_value="false"):
            out = chat_bot.generate_ai_draft(
                Mock(),
                client_msg="где заказ",
                theme="dostavka_wb",
                article="BO",
                history=[],
                for_manager=False,
            )
        self.assertIsNone(out)

    def test_classify_does_not_set_half_hour_pause(self):
        with patch("backend.llm.complete_sync", side_effect=RuntimeError("Free quota exhausted")):
            self.assertIsNone(chat_bot._llm_classify("привет", [], [], "", "new"))
        self.assertFalse(chat_bot._quota_paused())

    def test_dostavka_branch_follows_fbs(self):
        db = MagicMock()
        with patch.object(
            chat_bot.chat_fulfillment, "resolve_chat_fulfillment",
            return_value={"code": "FBS"},
        ):
            with patch.object(chat_bot.classify, "_forced_branch", None, create=True):
                out = chat_bot._dostavka_branch_from_fulfillment(
                    db, "dostavka_wb", "", "wb", "1:x",
                )
        self.assertEqual(out, "nasha_zaderzhka")

    def test_dostavka_branch_follows_fbo(self):
        db = MagicMock()
        with patch.object(
            chat_bot.chat_fulfillment, "resolve_chat_fulfillment",
            return_value={"code": "FBO"},
        ):
            with patch.object(chat_bot.classify, "_forced_branch", None, create=True):
                out = chat_bot._dostavka_branch_from_fulfillment(
                    db, "dostavka_wb", "nasha_zaderzhka", "wb", "1:x",
                )
        self.assertEqual(out, "")

    def test_dostavka_fbs_overrides_empty_correction(self):
        db = MagicMock()
        with patch.object(
            chat_bot.chat_fulfillment, "resolve_chat_fulfillment",
            return_value={"code": "FBS"},
        ):
            with patch.object(chat_bot.classify, "_forced_branch", "", create=True):
                out = chat_bot._dostavka_branch_from_fulfillment(
                    db, "dostavka_wb", "", "wb", "1:x",
                )
        self.assertEqual(out, "nasha_zaderzhka")

    def test_nekomplekt_chip_branch_uses_sku_list(self):
        db = MagicMock()
        with patch.object(chat_bot.reshipment_skus, "is_allowed", return_value=True):
            self.assertEqual(chat_bot.nekomplekt_chip_branch(db, "wb", "2-СБ-Black"), "sku_allowed")
        with patch.object(chat_bot.reshipment_skus, "is_allowed", return_value=False):
            self.assertEqual(chat_bot.nekomplekt_chip_branch(db, "wb", "Mop-1"), "sku_denied")
        with patch.object(chat_bot.reshipment_skus, "is_allowed", return_value=True):
            self.assertEqual(chat_bot.nekomplekt_chip_branch(db, "ozon", "2-СБ-Black"), "sku_allowed")
        with patch.object(chat_bot.reshipment_skus, "is_allowed", return_value=False):
            self.assertEqual(chat_bot.nekomplekt_chip_branch(db, "ozon", "Mop-1"), "sku_denied")
        self.assertEqual(chat_bot.nekomplekt_chip_branch(db, "ozon", ""), "")
        self.assertEqual(chat_bot.nekomplekt_chip_branch(db, "wb", ""), "")


if __name__ == "__main__":
    unittest.main()
