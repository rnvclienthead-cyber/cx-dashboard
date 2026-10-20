import unittest
from datetime import datetime
from unittest.mock import Mock, patch

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

    def test_client_reply_after_sent_auto_is_detected_from_sent_log_only(self):
        db = Mock()
        db.execute.return_value.fetchone.return_value = (1_000,)

        self.assertTrue(chat_bot._client_replied_after_sent_auto(db, "chat-1", 1_001, "wb"))
        self.assertFalse(chat_bot._client_replied_after_sent_auto(db, "chat-1", 1_000, "wb"))
        sql = str(db.execute.call_args.args[0])
        self.assertIn("sent = true", sql)
        self.assertIn("zone = 'auto'", sql)

    def test_reply_after_auto_handoffs_without_second_auto_answer(self):
        db = Mock()
        db.execute.return_value.fetchone.return_value = None
        state = {
            "stage": "awaiting_return_reason",
            "last_theme": "vozvrat",
            "last_bot_at": datetime(2024, 1, 1),
            "last_greeted_date": None,
        }
        chat = {
            "chat_id": "chat-1",
            "last_message": "Не подошёл размер",
            "last_ts": 1_735_689_600_000,
            "nm_id": 1,
            "client_name": "Покупатель",
        }
        def mp_setting(_db, key, _mp, default=""):
            return "" if key == "bot_test_chat_ids" else default

        with (
            patch.object(chat_bot.chat_playbook, "get_setting_mp", side_effect=mp_setting),
            patch.object(chat_bot.chat_playbook, "get_setting", return_value=""),
            patch.object(chat_bot, "_has_human_seller_reply", return_value=False),
            patch.object(chat_bot, "_has_seller_reply", return_value=False),
            patch.object(chat_bot, "_best_client_text", return_value="Не подошёл размер"),
            patch.object(chat_bot, "_bot_state", return_value=state),
            patch.object(chat_bot, "_client_replied_after_sent_auto", return_value=True),
            patch.object(chat_bot, "_get_article", return_value="Сушилка"),
            patch.object(chat_bot, "_get_reshipment", return_value=None),
            patch.object(chat_bot, "_last_seller_ts", return_value=None),
            patch.object(chat_bot, "_needs_greeting", return_value=False),
            patch.object(chat_bot, "_notify_manager") as notify,
            patch.object(chat_bot, "_set_state") as set_state,
            patch.object(chat_bot, "_log") as log,
            patch.object(chat_bot, "classify") as classify,
            patch.object(chat_bot, "assemble_reply") as assemble,
            patch.object(chat_bot, "_send_to_client") as send,
        ):
            result = chat_bot.process_one(db, chat, shadow=False, mp="wb")

        self.assertEqual(result["zone"], "human")
        self.assertFalse(result["sent"])
        self.assertIn("передано менеджеру", result["reason"])
        classify.assert_not_called()
        assemble.assert_not_called()
        send.assert_not_called()
        notify.assert_called_once()
        self.assertEqual(set_state.call_args.kwargs["stage"], "handoff")
        self.assertEqual(log.call_args.kwargs["action"], "handoff_to_manager")

    def test_classify_does_not_call_llm_without_a_rule(self):
        with patch.object(chat_bot, "_llm_classify") as llm:
            theme, _, _, reason = chat_bot.classify(
                None, "случайный текст без темы xyz", "new", [], "", mp="wb"
            )
        llm.assert_not_called()
        self.assertEqual(theme, "utochnenie")
        self.assertIn("без ИИ", reason)

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

    def test_ym_seed_offers_buy_then_free_reshipment(self):
        """ЯМ: сначала купить за 1 ₽ с промо, потом бесплатная доотправка, не как Ozon."""
        ym_keys = {(item[0], item[1] or "") for item in chat_bot.chat_playbook.SEED_YM_VARIANTS}
        self.assertIn(("nekomplekt", "offer_buy"), ym_keys)
        self.assertIn(("nekomplekt", "offer_free_slow"), ym_keys)
        self.assertIn(("nekomplekt", "to_return"), ym_keys)
        self.assertIn(("kupit_detal", ""), ym_keys)
        self.assertIn(("soglasie", ""), ym_keys)
        self.assertIn(("nekomplekt", "sku_allowed"), ym_keys)
        self.assertNotIn(("nekomplekt", "no_reship_return"), ym_keys)
        self.assertNotIn(("nekomplekt", "check_tubes"), ym_keys)
        for item in chat_bot.chat_playbook.SEED_YM_VARIANTS:
            if item[0] == "nekomplekt" and item[1] == "offer_buy":
                self.assertIn("1 ₽", item[3])
                self.assertIn("{promo_code}", item[3])
            if item[0] == "kupit_detal":
                self.assertIn("{promo_code}", item[3])
                self.assertIn("1 ₽", item[3])
                self.assertTrue(
                    "{part_article}" in item[3] or "{part_list}" in item[3]
                )
                self.assertNotIn("{form_url}", item[3])
            if item[0] == "soglasie":
                self.assertIn("{form_url}", item[3])
            body = item[3].lower()
            self.assertNotIn("ozon", body)
            self.assertNotIn("wildberries", body)
            self.assertNotIn("«покупки»", body)


    def test_ym_non_2sb_allowed_sku_gets_reshipment(self):
        db = Mock()
        with (
            patch.object(chat_bot.reshipment_skus, "is_allowed", return_value=True),
            patch("backend.ym_parts.is_2sb_hanger", return_value=False),
        ):
            self.assertEqual(
                chat_bot._resolve_branch(
                    db, "nekomplekt", "BO-White-2", None, "нет колёс", mp="ym",
                ),
                "sku_allowed",
            )
            self.assertEqual(
                chat_bot.nekomplekt_chip_branch(db, "ym", "BO-White-2"),
                "sku_allowed",
            )

    def test_ym_2sb_still_offers_buy(self):
        db = Mock()
        with patch("backend.ym_parts.is_2sb_hanger", return_value=True):
            self.assertEqual(
                chat_bot._resolve_branch(
                    db, "nekomplekt", "2-СБ-Black", None, "нет детали", mp="ym",
                ),
                "offer_buy",
            )

    def test_ozon_missing_part_branches(self):
        self.assertEqual(
            chat_bot._resolve_branch_ozon("nekomplekt", "не хватает колёс"),
            "wheels",
        )
        self.assertEqual(
            chat_bot._resolve_branch_ozon("nekomplekt", "нет болтов в пакете"),
            "bolts",
        )
        self.assertEqual(
            chat_bot._resolve_branch_ozon("nekomplekt", "не хватает ножек"),
            "legs",
        )
        oz_keys = {(item[0], item[1] or "") for item in chat_bot.chat_playbook.SEED_OZON_VARIANTS}
        self.assertIn(("nekomplekt", "wheels"), oz_keys)
        self.assertIn(("nekomplekt", "bolts"), oz_keys)
        self.assertIn(("nekomplekt", "legs"), oz_keys)
        self.assertIn(("nekomplekt", "sku_allowed"), oz_keys)
        self.assertIn(("soglasie", ""), oz_keys)
        for item in chat_bot.chat_playbook.SEED_OZON_VARIANTS:
            if item[0] == "soglasie":
                self.assertIn("{form_url}", item[3])

    def test_ozon_allowed_sku_offers_part(self):
        db = Mock()
        with patch.object(chat_bot.reshipment_skus, "is_allowed", return_value=True):
            self.assertEqual(
                chat_bot._resolve_branch(
                    db, "nekomplekt", "BO-White-2", None, "нет детали", mp="ozon",
                ),
                "sku_allowed",
            )
            self.assertEqual(
                chat_bot.nekomplekt_chip_branch(db, "ozon", "BO-White-2"),
                "sku_allowed",
            )
            theme, _reason = chat_bot._remap_theme_for_mp("soglasie", "ozon")
            self.assertEqual(theme, "soglasie")


if __name__ == "__main__":
    unittest.main()
