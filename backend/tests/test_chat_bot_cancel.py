import unittest
from unittest.mock import MagicMock, patch

from backend import chat_bot
from backend.routers import chat_playbook as pb


NUDGE = "Здравствуйте! Пожалуйста, посмотрите моё сообщение — жду ответа"
NUDGE_NBSP = "Здравствуйте! Пожалуйста, посмотрите моё сообщение\u00a0— жду ответа"


class CancelClassifyTests(unittest.TestCase):
    def test_typo_otmetite_is_cancel(self):
        theme, _, need, reason = chat_bot._rule_classify(
            "Отметите заказ если е отправили еще", "new",
        )
        self.assertEqual(theme, "otmena_zakaza")
        self.assertFalse(need)
        self.assertIn("отмена", reason)

    def test_no_need_anymore_is_cancel(self):
        theme, *_ = chat_bot._rule_classify(
            "Очень долгая доставка, уже и нет в надобности его привозить!", "new",
        )
        self.assertEqual(theme, "otmena_zakaza")

    def test_otmetit_shipment_is_cancel(self):
        theme, *_ = chat_bot._rule_classify(
            "Отметить заказ на отправку от вас и все!", "new",
        )
        self.assertEqual(theme, "otmena_zakaza")

    def test_cancel_wins_over_handoff(self):
        theme, *_ = chat_bot._rule_classify(
            "Отметите заказ если не отправили еще", "handoff",
        )
        self.assertEqual(theme, "otmena_zakaza")

    def test_nudge_is_not_soglasie(self):
        self.assertTrue(chat_bot._is_marketplace_nudge(NUDGE))
        self.assertTrue(chat_bot._is_marketplace_nudge(NUDGE_NBSP))
        self.assertFalse(chat_bot._is_marketplace_nudge(
            "Я все еще жду ответа. Вы ответите или оформлять возврат?",
        ))
        ruled = chat_bot._rule_classify(NUDGE, "new")
        self.assertTrue(ruled is None or ruled[0] != "soglasie")


class BestClientTextTests(unittest.TestCase):
    def test_skips_nudge_and_prefers_cancel(self):
        hist = [
            {"sender": "client", "text": "Отметите заказ если е отправили еще"},
            {"sender": "client", "text": "Очень долгая доставка, уже и нет в надобности его привозить!"},
            {"sender": "client", "text": NUDGE},
        ]
        db = MagicMock()
        with patch.object(chat_bot, "_history", return_value=hist):
            msg = chat_bot._best_client_text(db, "1:x", "wb", NUDGE)
        self.assertIn("надобности", msg)
        self.assertFalse(chat_bot._is_marketplace_nudge(msg))


class CorrectionNudgeTests(unittest.TestCase):
    def test_nudge_is_not_learned(self):
        result = chat_bot.record_correction(
            MagicMock(),
            chat_id="1:x",
            mp="wb",
            client_message=NUDGE,
            suggested_theme="otmena_zakaza",
            chosen_theme="soglasie",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["learned"])

    def test_nudge_does_not_match_corrections(self):
        db = MagicMock()
        self.assertIsNone(chat_bot._best_correction(db, NUDGE, "wb"))
        db.execute.assert_not_called()


class ArticleForChipTests(unittest.TestCase):
    def test_wb_uses_supplier_article_not_card_title(self):
        db = MagicMock()
        with patch.object(chat_bot, "_get_article", return_value="2-СБ-Grey") as ga:
            art = chat_bot._article_for_chat(db, {
                "nm_id": 237307788,
                "good_name": "Сушилка для белья серая напольная на колесиках",
                "chat_id": "1:6c65651d",
            }, "wb")
        self.assertEqual(art, "2-СБ-Grey")
        ga.assert_called()

    def test_wb_without_article_does_not_fall_back_to_title(self):
        db = MagicMock()
        with patch.object(chat_bot, "_get_article", return_value=""):
            with patch.object(chat_bot, "_get_reshipment", return_value=None):
                art = chat_bot._article_for_chat(db, {
                    "nm_id": 237307788,
                    "good_name": "Сушилка для белья серая",
                    "chat_id": "1:x",
                }, "wb")
        self.assertEqual(art, "")
        self.assertEqual(chat_bot.nekomplekt_chip_branch(db, "wb", art), "")


class ThemeOrderTests(unittest.TestCase):
    def test_usage_order_starts_with_frequent_themes(self):
        self.assertEqual(pb.CHAT_THEME_USAGE_ORDER[:4], [
            "dostavka_wb", "nekomplekt", "brak", "soglasie",
        ])
        self.assertIn("otmena_zakaza", pb.CHAT_THEME_USAGE_ORDER)
        self.assertIn("promokody", pb.CHAT_THEME_USAGE_ORDER)
        self.assertIn("tech_otverstiya", pb.CHAT_THEME_USAGE_ORDER)


if __name__ == "__main__":
    unittest.main()
