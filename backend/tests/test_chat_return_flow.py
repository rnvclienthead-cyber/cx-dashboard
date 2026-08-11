import unittest
from unittest.mock import patch

from backend import chat_bot


class ReturnFlowTests(unittest.TestCase):
    def test_return_without_reason_asks_and_waits(self):
        with (
            patch.object(chat_bot.nonreturnable_cats, "is_nonreturnable", return_value=False),
            patch.object(chat_bot.chat_playbook, "get_setting_mp", return_value="0.55"),
        ):
            theme, _, need_human, _ = chat_bot.classify(
                None, "Хочу вернуть товар", "new", [], "", mp="wb"
            )
        self.assertEqual(theme, "vozvrat")
        self.assertFalse(need_human)
        self.assertEqual(
            chat_bot._resolve_branch(None, theme, "", None, "Хочу вернуть товар"),
            "ask_reason",
        )
        self.assertEqual(
            chat_bot._next_stage(theme, "ask_reason", "new"),
            "awaiting_return_reason",
        )

    def test_return_with_reason_handoffs(self):
        message = "Хочу вернуть товар, потому что не подошёл размер"
        with (
            patch.object(chat_bot.nonreturnable_cats, "is_nonreturnable", return_value=False),
            patch.object(chat_bot.chat_playbook, "get_setting_mp", return_value="0.55"),
        ):
            theme, _, need_human, reason = chat_bot.classify(
                None, message, "new", [], "", mp="wb"
            )
        self.assertEqual(theme, "vozvrat")
        self.assertTrue(need_human)
        self.assertIn("менеджер", reason)
        self.assertEqual(
            chat_bot._next_stage(theme, "handoff_to_manager", "new"),
            "handoff",
        )

    def test_any_reply_while_awaiting_reason_handoffs(self):
        theme, _, need_human, reason = chat_bot._rule_classify(
            "Мне просто не понравился", "awaiting_return_reason"
        )
        self.assertEqual(theme, "vozvrat")
        self.assertTrue(need_human)
        self.assertIn("причин", reason)
        self.assertEqual(
            chat_bot._next_stage(theme, "handoff_to_manager", "awaiting_return_reason"),
            "handoff",
        )

    def test_wb_nonreturnable_category_stays_separate(self):
        with (
            patch.object(chat_bot.nonreturnable_cats, "is_nonreturnable", return_value=True),
            patch.object(chat_bot.chat_playbook, "get_setting_mp", return_value="0.55"),
        ):
            theme, _, need_human, _ = chat_bot.classify(
                None, "Хочу вернуть товар", "new", [], "Сушилка", nm_id=123, mp="wb"
            )
        self.assertEqual(theme, "nevozvratny")
        self.assertFalse(need_human)

    def test_platform_questions_use_correct_sections_without_return_steps(self):
        wb = chat_bot._return_reason_text("wb")
        ozon = chat_bot._return_reason_text("ozon")
        ym = chat_bot._return_reason_text("ym")
        self.assertIn("почему решили вернуть", wb.lower())
        self.assertIn("«Покупки»", wb)
        self.assertIn("«Заказы»", ozon)
        self.assertIn("«Заказы»", ym)
        for body in (wb, ozon, ym):
            self.assertNotIn("нажмите «Вернуть»", body)
            self.assertNotIn("оформите возврат", body.lower())

        seeded = {
            (mp, item[0], item[1]): item[3]
            for mp, variants in (
                ("wb", chat_bot.chat_playbook.SEED_VARIANTS),
                ("ozon", chat_bot.chat_playbook.SEED_OZON_VARIANTS),
                ("ym", chat_bot.chat_playbook.SEED_YM_VARIANTS),
            )
            for item in variants
        }
        for mp in ("wb", "ozon", "ym"):
            self.assertIn((mp, "vozvrat", "ask_reason"), seeded)


if __name__ == "__main__":
    unittest.main()
