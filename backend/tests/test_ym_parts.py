import unittest
from unittest.mock import Mock, patch

from backend import chat_bot, ym_parts


class YmPartsCatalogTests(unittest.TestCase):
    def test_only_2sb_hanger_gets_parts(self):
        catalog = ym_parts.DEFAULT_CATALOG
        self.assertTrue(ym_parts.is_2sb_hanger("2-СБ-Black"))
        self.assertTrue(ym_parts.is_2sb_hanger("2СБ White"))
        self.assertFalse(ym_parts.is_2sb_hanger("Mop-grey"))
        self.assertFalse(ym_parts.is_2sb_hanger("2СБ-TRUB-A-677-10-White"))
        self.assertEqual(ym_parts.lookup_part("Mop-grey", catalog)[1], "")
        name, arts = ym_parts.lookup_part("2-СБ-Black", catalog)
        self.assertIn("2СБ-TRUB-A-677-10-White", arts)
        self.assertIn("2СБ-KOL-ST-Black", arts)
        self.assertIn("детал", name.lower())

    def test_guess_tube_a_from_message(self):
        parts = ym_parts.guess_parts("2-СБ-White", "не хватает трубки А")
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].sku, "2СБ-TRUB-A-677-10-White")

    def test_tube_c_not_confused_with_sovsem(self):
        parts = ym_parts.guess_parts("2-СБ-Black", "трубки совсем нет, колесо тоже")
        skus = {p.sku for p in parts}
        self.assertIn("2СБ-KOL-ST-Black", skus)
        self.assertNotIn("2СБ-TRUB-С-520-16-White", skus)

    def test_fill_placeholders_one_part(self):
        with patch.object(ym_parts, "settings", return_value={
            "promo_code": "VIDO50PART",
            "promo_pct": "50",
            "catalog": ym_parts.DEFAULT_CATALOG,
            "promo_id": "",
        }):
            body = ym_parts.fill_part_placeholders(
                "{part_list}\nкод {promo_code} {promo_pct}%",
                product_article="2-СБ-Grey",
                client_msg="нет колеса",
                db=Mock(),
            )
        self.assertIn("2СБ-KOL-ST-Black", body)
        self.assertIn("VIDO50PART", body)
        self.assertNotIn("2СБ-TRUB-A-677-10-White", body)


class YmPartsBuyFlowTests(unittest.TestCase):
    def test_agree_after_buy_offer(self):
        theme, _, need, reason = chat_bot._rule_classify("да", "awaiting_buy", mp="ym")
        self.assertEqual(theme, "kupit_detal")
        self.assertFalse(need)
        self.assertIn("купить", reason.lower())

    def test_decline_buy_goes_to_free(self):
        theme, _, need, reason = chat_bot._rule_classify(
            "нет, не надо", "awaiting_buy", mp="ym",
        )
        self.assertEqual(theme, "nekomplekt")
        self.assertFalse(need)
        self.assertIn("бесплатн", reason.lower())

    def test_want_return_skips_free(self):
        theme, _, need, _ = chat_bot._rule_classify(
            "хочу вернуть товар", "awaiting_buy", mp="ym",
        )
        self.assertEqual(theme, "nekomplekt")
        self.assertFalse(need)

    def test_decline_free_goes_to_return_not_manager(self):
        theme, _, need, reason = chat_bot._rule_classify(
            "не актуально", "awaiting_client", mp="ym",
        )
        self.assertEqual(theme, "nekomplekt")
        self.assertFalse(need)
        self.assertIn("возврат", reason.lower())
        self.assertEqual(
            chat_bot._next_stage("nekomplekt", "explain_return", "awaiting_client", branch="to_return"),
            "closing",
        )

    def test_wb_decline_still_handoffs(self):
        theme, _, need, _ = chat_bot._rule_classify(
            "не актуально", "awaiting_client", mp="wb",
        )
        self.assertEqual(theme, "otkaz_dootpravki")
        self.assertTrue(need)

    def test_branches_and_stages(self):
        db = Mock()
        def allowed(_db, article):
            return article != "Mop-1"
        with patch.object(chat_bot.reshipment_skus, "is_allowed", side_effect=allowed):
            self.assertEqual(
                chat_bot._resolve_branch(db, "nekomplekt", "2-СБ-Black", None, "нет трубки", mp="ym"),
                "offer_buy",
            )
            self.assertEqual(
                chat_bot._resolve_branch(
                    db, "nekomplekt", "Mop-1", None, "нет насадки", mp="ym",
                ),
                "sku_denied",
            )
            self.assertEqual(
                chat_bot._resolve_branch(
                    db, "nekomplekt", "2-СБ-Black", None, "нет", mp="ym", stage="awaiting_buy",
                ),
                "offer_free_slow",
            )
        self.assertEqual(
            chat_bot._next_stage("nekomplekt", "offer_buy", "new", branch="offer_buy"),
            "awaiting_buy",
        )
        self.assertEqual(
            chat_bot._next_stage("kupit_detal", "send_buy_link", "awaiting_buy"),
            "buy_sent",
        )

    def test_ym_continue_stages_not_handoff(self):
        self.assertTrue(chat_bot._ym_parts_dialog_open("ym", "awaiting_buy"))
        self.assertTrue(chat_bot._ym_parts_dialog_open("ym", "awaiting_client"))
        self.assertFalse(chat_bot._ym_parts_dialog_open("wb", "awaiting_client"))
        self.assertFalse(chat_bot._ym_parts_dialog_open("ym", "form_sent"))


if __name__ == "__main__":
    unittest.main()
