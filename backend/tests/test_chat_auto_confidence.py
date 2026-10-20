import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.chat_bot import (
    AUTO_SEND_MIN_CONF,
    _correction_jaccard,
    _correction_overlap,
    _is_product_question_wrapper_only,
    _looks_like_missing_parts,
    _needs_greeting,
    _rule_classify,
    _unwrap_client_question,
    classify,
    gate_auto_by_confidence,
    record_correction,
)


class AutoConfidenceGateTests(unittest.TestCase):
    def test_auto_below_threshold_goes_to_draft(self):
        with patch("backend.chat_bot._auto_min_confidence", return_value=0.9):
            zone, reason, held = gate_auto_by_confidence(
                "auto", "vozvrat", 0.74, "ИИ", None, "wb",
            )
        self.assertEqual(zone, "draft")
        self.assertTrue(held)
        self.assertIn("согласование", reason)

    def test_auto_at_threshold_stays_auto(self):
        with patch("backend.chat_bot._auto_min_confidence", return_value=0.9):
            zone, reason, held = gate_auto_by_confidence(
                "auto", "sborka", 0.91, "правило", None, "wb",
            )
        self.assertEqual(zone, "auto")
        self.assertFalse(held)
        self.assertEqual(reason, "правило")

    def test_menu_and_human_are_not_gated(self):
        with patch("backend.chat_bot._auto_min_confidence", return_value=0.9):
            zone, _, held = gate_auto_by_confidence(
                "auto", "utochnenie", 0.7, "меню", None, "wb",
            )
            zone2, _, held2 = gate_auto_by_confidence(
                "human", "dengi", 0.4, "человек", None, "wb",
            )
        self.assertEqual(zone, "auto")
        self.assertFalse(held)
        self.assertEqual(zone2, "human")
        self.assertFalse(held2)

    def test_manager_correction_never_auto_sends(self):
        with patch("backend.chat_bot._auto_min_confidence", return_value=0.9):
            zone, reason, held = gate_auto_by_confidence(
                "auto", "nekomplekt", 0.95,
                "исправление менеджера → nekomplekt", None, "ym",
            )
        self.assertEqual(zone, "draft")
        self.assertTrue(held)
        self.assertIn("без автоотправки", reason)

    def test_default_threshold_is_90(self):
        self.assertEqual(AUTO_SEND_MIN_CONF, 0.9)


class CorrectionOverlapTests(unittest.TestCase):
    def test_overlap_counts_shared_words(self):
        score = _correction_overlap(
            "хочу вернуть товар, не подошёл размер",
            "хочу вернуть этот товар размер не подошёл",
        )
        self.assertGreaterEqual(score, 3)

    def test_jaccard_needs_real_similarity(self):
        similar = _correction_jaccard(
            "хочу вернуть товар, не подошёл размер",
            "хочу вернуть этот товар размер не подошёл",
        )
        generic = _correction_jaccard(
            "сломалась трубка при сборке не хватает детали",
            "хочу вернуть этот товар размер не подошёл",
        )
        self.assertGreaterEqual(similar, 0.38)
        self.assertLess(generic, 0.38)

    def test_same_chat_correction_is_ignored(self):
        class _Db:
            def execute(self, *_a, **_k):
                class _R:
                    def mappings(self):
                        class _M:
                            def all(self):
                                return [{
                                    "chosen_theme": "nekomplekt",
                                    "chosen_branch": "sku_denied",
                                    "client_message": "не хватает трубки А в комплекте",
                                    "chat_id": "17953267",
                                }]
                        return _M()
                return _R()

        from backend.chat_bot import _best_correction
        hit = _best_correction(
            _Db(), "не хватает трубки А в комплекте", "ym", chat_id="17953267",
        )
        self.assertIsNone(hit)
        hit_other = _best_correction(
            _Db(), "не хватает трубки А в комплекте", "ym", chat_id="other",
        )
        self.assertEqual(hit_other["chosen_theme"], "nekomplekt")

    def test_record_saves_confirmation_without_toast(self):
        class _Db:
            def __init__(self):
                self.commits = 0

            def execute(self, *_a, **_k):
                class _R:
                    def fetchone(self):
                        return None
                return _R()

            def commit(self):
                self.commits += 1

        db = _Db()
        result = record_correction(
            db,
            chat_id="1",
            mp="wb",
            client_message="текст",
            suggested_theme="sborka",
            suggested_branch="",
            chosen_theme="sborka",
            chosen_branch="",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["saved"])
        self.assertTrue(result["learned"])
        self.assertEqual(db.commits, 1)


class GreetingWindowTests(unittest.TestCase):
    def test_client_hello_does_not_skip_ours(self):
        self.assertTrue(_needs_greeting({}, None, client_msg="Здравствуйте!"))

    def test_skips_within_twelve_hours(self):
        at = datetime.now(timezone.utc) - timedelta(hours=3)
        self.assertFalse(_needs_greeting(
            {"last_greeted_at": at.replace(tzinfo=None)},
            None,
            client_msg="Здравствуйте",
        ))

    def test_greets_again_after_twelve_hours(self):
        at = datetime.now(timezone.utc) - timedelta(hours=13)
        self.assertTrue(_needs_greeting({"last_greeted_at": at.replace(tzinfo=None)}))


class OzonProductQuestionUnwrapTests(unittest.TestCase):
    SAMPLE = (
        'Здравствуйте! У меня вопрос по вашему товару '
        '"Вешалка напольная для одежды, гардеробную, металлическая, '
        'открытая стойка для хранения, белая, Видовито", артикул BO-White-1. '
        'Как мне получить скидку?'
    )
    TUBE = (
        'Здравствуйте! У меня вопрос по вашему товару '
        '"Вешалка напольная для одежды, гардеробную, металлическая, '
        'открытая стойка для хранения, белая, Видовито", артикул BO-White-1. '
        'Не хватает трубки'
    )

    def test_keeps_real_question(self):
        self.assertEqual(
            _unwrap_client_question(self.SAMPLE),
            "Как мне получить скидку?",
        )

    def test_wrapper_only_is_empty(self):
        wrap = (
            'Здравствуйте! У меня вопрос по вашему товару '
            '"Вешалка напольная", артикул BO-White-1.'
        )
        self.assertEqual(_unwrap_client_question(wrap), "")
        self.assertTrue(_is_product_question_wrapper_only(wrap))

    def test_wrapper_only_is_utochnenie(self):
        wrap = (
            'Здравствуйте! У меня вопрос по вашему товару '
            '"Вешалка напольная", артикул BO-White-1.'
        )
        theme, conf, _, reason = classify(None, wrap, "new", [], "BO-White-1", mp="ozon")
        self.assertEqual(theme, "utochnenie")
        self.assertGreaterEqual(conf, 0.85)
        self.assertIn("шапка", reason)

    def test_discount_goes_to_manager(self):
        theme, _, need, reason = _rule_classify(
            _unwrap_client_question(self.SAMPLE), "new", mp="ozon",
        )
        self.assertEqual(theme, "dengi")
        self.assertTrue(need)
        self.assertIn("скидк", reason)

    def test_same_card_does_not_match_missing_part(self):
        score = _correction_jaccard(
            _unwrap_client_question(self.SAMPLE),
            _unwrap_client_question(self.TUBE),
        )
        wrapped = _correction_jaccard(self.SAMPLE, self.TUBE)
        self.assertGreaterEqual(wrapped, 0.38)
        self.assertLess(score, 0.38)


class YmProductQuestionUnwrapTests(unittest.TestCase):
    SHELF = (
        "Здравствуйте, у меня вопрос по товару Стеллаж напольный металлический "
        "Видовито, белый, 5 полок, для кухни, ванной и хранения вещей, 173×43×30 см."
    )
    SHELF_OTHER = (
        "Здравствуйте, у меня вопрос по товару Стеллаж напольный металлический "
        "Видовито, чёрный, 5 полок, для кухни, ванной и хранения вещей, 173×43×30 см."
    )
    SHELF_PLUS = SHELF + "\nНе хватает трубки"

    def test_wrapper_only_is_empty(self):
        self.assertEqual(_unwrap_client_question(self.SHELF), "")
        self.assertTrue(_is_product_question_wrapper_only(self.SHELF))

    def test_keeps_real_question(self):
        self.assertEqual(_unwrap_client_question(self.SHELF_PLUS), "Не хватает трубки")
        self.assertFalse(_is_product_question_wrapper_only(self.SHELF_PLUS))

    def test_wrappers_do_not_jaccard_as_nekomplekt(self):
        score = _correction_jaccard(
            _unwrap_client_question(self.SHELF),
            _unwrap_client_question(self.SHELF_OTHER),
        )
        wrapped = _correction_jaccard(self.SHELF, self.SHELF_OTHER)
        self.assertGreaterEqual(wrapped, 0.38)
        self.assertLess(score, 0.38)

    def test_real_question_stays_nekomplekt(self):
        unwrapped = _unwrap_client_question(self.SHELF_PLUS)
        self.assertEqual(unwrapped, "Не хватает трубки")
        theme, _, _, reason = _rule_classify(unwrapped, "new", mp="ym")
        self.assertEqual(theme, "nekomplekt")
        self.assertIn("некомплект", reason)

    def test_wrapper_is_utochnenie_not_nekomplekt(self):
        theme, conf, need, reason = classify(
            None, self.SHELF, "new", [], "Shelf-white-5/A3", mp="ym",
        )
        self.assertEqual(theme, "utochnenie")
        self.assertGreaterEqual(conf, 0.85)
        self.assertFalse(need)
        self.assertIn("шапка", reason)
        self.assertFalse(_looks_like_missing_parts(self.SHELF))

    def test_rule_classify_wrapper_is_utochnenie(self):
        theme, _, _, reason = _rule_classify(self.SHELF, "new", mp="ym")
        self.assertEqual(theme, "utochnenie")
        self.assertIn("шапка", reason)

    def test_does_not_learn_wrapper(self):
        class _Db:
            def execute(self, *_a, **_k):
                raise AssertionError("wrapper must not be stored")

            def commit(self):
                raise AssertionError("wrapper must not be stored")

        result = record_correction(
            _Db(),
            chat_id="1",
            mp="ym",
            client_message=self.SHELF,
            suggested_theme="nekomplekt",
            suggested_branch="offer_buy",
            chosen_theme="utochnenie",
            chosen_branch="",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["learned"])
        self.assertIn("шаблон", result["reason"])


class PaintHolesClassifyTests(unittest.TestCase):
    def test_shelf_holes_are_tech_theme(self):
        theme, conf, need, reason = _rule_classify(
            "Для чего отверстия на полке?", "new", mp="wb",
        )
        self.assertEqual(theme, "tech_otverstiya")
        self.assertFalse(need)
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("отверст", reason)

    def test_tray_hole_and_photo_complaint(self):
        theme, *_ = _rule_classify("Зачем дырочка в подносе?", "new")
        self.assertEqual(theme, "tech_otverstiya")
        theme2, *_ = _rule_classify(
            "На полке четыре отверстия, на фото их нет", "new", mp="ozon",
        )
        self.assertEqual(theme2, "tech_otverstiya")

    def test_missing_bolt_hole_is_not_paint_holes(self):
        ruled = _rule_classify("нет отверстия под болт", "new")
        self.assertTrue(ruled is None or ruled[0] != "tech_otverstiya")
        theme2, *_ = _rule_classify("отверстия не совпадают, болт не входит", "new")
        self.assertNotEqual(theme2, "tech_otverstiya")

    def test_drain_plug_stays_krepezh(self):
        theme, *_ = _rule_classify("где купить заглушки для сливных отверстий", "new")
        self.assertEqual(theme, "krepezh")

    def test_missing_caps_and_screws_are_nekomplekt(self):
        theme, *_ = _rule_classify("Не положили в набор шурупы и заглушки", "new")
        self.assertEqual(theme, "nekomplekt")
        theme2, *_ = _rule_classify("Не положили заглушки", "new")
        self.assertEqual(theme2, "nekomplekt")
        theme3, *_ = _rule_classify("нет шурупов", "new")
        self.assertEqual(theme3, "nekomplekt")


if __name__ == "__main__":
    unittest.main()
