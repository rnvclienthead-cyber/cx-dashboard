import unittest

from backend.review_policy import (
    MODE_AI_ONLY,
    MODE_APPROVE_TEMPLATE,
    MODE_AUTO_SORRY,
    MODE_AUTO_THANKS,
    PAINT_HOLES_REPLY,
    apply_article_hints,
    article_facts_for_ai,
    auto_send_scenario,
    classify_review_reply,
    detect_review_topic,
    is_paint_hole_question,
    needs_lived_reply,
    no_text_answer,
    should_auto_send,
    should_ground_constructor,
    topic_answer,
    TOPIC_INCOMPLETE,
    TOPIC_DEFECT,
    TOPIC_DELIVERY,
    TOPIC_RETURNED,
    TOPIC_ASSEMBLY,
    TOPIC_LOAD,
    TOPIC_UNSUITABLE,
    TOPIC_SIZE,
    TOPIC_FOUR_ASK,
    TOPIC_PAINT_HOLES,
)


class ClassifyReviewReplyTests(unittest.TestCase):
    def test_high_rating_no_text_no_media(self):
        self.assertEqual(classify_review_reply(5, "", None), MODE_AUTO_THANKS)
        self.assertEqual(classify_review_reply(4, "—", []), MODE_AUTO_THANKS)

    def test_high_rating_with_text_or_media(self):
        self.assertEqual(classify_review_reply(5, "спасибо", None), MODE_APPROVE_TEMPLATE)
        self.assertEqual(classify_review_reply(4, "", ["http://p.jpg"]), MODE_APPROVE_TEMPLATE)

    def test_low_rating_no_text(self):
        self.assertEqual(classify_review_reply(1, "", None), MODE_AUTO_SORRY)
        self.assertEqual(classify_review_reply(3, "  ", None), MODE_AUTO_SORRY)

    def test_low_rating_with_text_or_media(self):
        self.assertEqual(classify_review_reply(2, "сломалась", None), MODE_AI_ONLY)
        self.assertEqual(classify_review_reply(1, "", ["http://p.jpg"]), MODE_AI_ONLY)

    def test_no_text_answer_does_not_offer_return(self):
        for stars in (1, 2, 3):
            text = no_text_answer(stars).lower()
            self.assertNotIn("возврат", text)
            self.assertNotIn("замен", text)
            self.assertIn("чат", text)

    def test_five_star_thanks_is_short(self):
        text = no_text_answer(5).lower()
        self.assertIn("приятно", text)
        self.assertNotIn("отзыв", text)

    def test_four_star_no_text_asks_what_was_missing(self):
        text = no_text_answer(4)
        self.assertIn("пятёрк", text.lower())
        self.assertIn("чат", text.lower())
        self.assertNotIn("прихож", text.lower())
        self.assertNotIn("возврат", text.lower())
        self.assertNotRegex(text, r"\bВ[аы]ш")

    def test_customer_address_is_not_capitalized(self):
        from pathlib import Path
        from backend.review_policy import REVIEW_AI_SYSTEM
        pack = topic_answer(TOPIC_DELIVERY, "BO-White-2", 2)
        self.assertNotRegex(pack, r"\bВ[аы]ш")
        self.assertIn("вы/вас/ваш", REVIEW_AI_SYSTEM)
        src = Path(__file__).resolve().parents[1] / "routers" / "cx_responses.py"
        self.assertNotIn("Вы получили", src.read_text())


class AutoSendScenarioTests(unittest.TestCase):
    def test_maps_modes_to_toggles(self):
        self.assertEqual(auto_send_scenario(MODE_AUTO_THANKS, 5), "thanks_5")
        self.assertEqual(auto_send_scenario(MODE_AUTO_THANKS, 4), "thanks_4")
        self.assertEqual(auto_send_scenario(MODE_AUTO_SORRY, 2), "sorry")
        self.assertIsNone(auto_send_scenario(MODE_APPROVE_TEMPLATE, 5))
        self.assertIsNone(auto_send_scenario(MODE_AI_ONLY, 1))

    def test_missing_keys_mean_on(self):
        self.assertTrue(should_auto_send(MODE_AUTO_THANKS, 5, {}))
        self.assertTrue(should_auto_send(MODE_AUTO_SORRY, 1, None))

    def test_respects_off_toggle(self):
        flags = {"thanks_5": False, "thanks_4": True, "sorry": False}
        self.assertFalse(should_auto_send(MODE_AUTO_THANKS, 5, flags))
        self.assertTrue(should_auto_send(MODE_AUTO_THANKS, 4, flags))
        self.assertFalse(should_auto_send(MODE_AUTO_SORRY, 2, flags))
        self.assertFalse(should_auto_send(MODE_APPROVE_TEMPLATE, 5, flags))


class TopicAndAiGroundingTests(unittest.TestCase):
    def test_incomplete_and_defect(self):
        self.assertEqual(detect_review_topic("не хватает болтов"), TOPIC_INCOMPLETE)
        self.assertEqual(detect_review_topic("нет колёс к сушилке"), TOPIC_INCOMPLETE)
        self.assertEqual(detect_review_topic("бракованная, поломанная пришла"), TOPIC_DEFECT)
        self.assertEqual(detect_review_topic("коробка порвана, курьер"), TOPIC_DELIVERY)
        self.assertEqual(detect_review_topic("отправила возврат и заново закажу"), TOPIC_RETURNED)

    def test_sho_incomplete_offers_return_not_reship(self):
        ans = topic_answer(TOPIC_INCOMPLETE, "ШО-White", 3)
        self.assertIn("возврат", ans.lower())
        self.assertNotIn("чат", ans.lower())

    def test_non_sho_incomplete_invites_chat_not_reship(self):
        ans = topic_answer(TOPIC_INCOMPLETE, "BO-White-2", 3)
        self.assertIn("чат", ans.lower())
        self.assertNotIn("возврат", ans.lower())
        self.assertNotIn("дошл", ans.lower())

    def test_packaging_invites_seller_chat(self):
        ans = topic_answer(TOPIC_DELIVERY, "BO-White-2", 2)
        self.assertIn("упаков", ans.lower())
        self.assertIn("чат", ans.lower())
        self.assertNotIn("скорректируете оценку", ans.lower())
        self.assertNotIn("поддержк", ans.lower())

    def test_defect_offers_return_and_chat(self):
        ans = topic_answer(TOPIC_DEFECT, "BO-White-2", 1)
        self.assertIn("брак", ans.lower())
        self.assertIn("возврат", ans.lower())
        self.assertIn("чат", ans.lower())

    def test_ai_only_skips_constructor_grounding(self):
        self.assertFalse(should_ground_constructor(MODE_AI_ONLY))
        self.assertTrue(should_ground_constructor(MODE_APPROVE_TEMPLATE))
        long_praise = (
            "Взяла в качестве временного варианта для детских вещей. "
            "Думаю их он выдержит спокойно. Видела в отзывах сравнение с Ридбергом — "
            "лично мне этот вариант понравился больше. Подробная инструкция на русском, "
            "детали отсортированы. Размеры идентичны. Соединители туже. Рекомендую."
        )
        self.assertTrue(needs_lived_reply(long_praise, 4))
        self.assertFalse(should_ground_constructor(MODE_APPROVE_TEMPLATE, long_praise, 4))
        self.assertIsNone(detect_review_topic(long_praise, 4))
        self.assertNotEqual(detect_review_topic(long_praise, 4), TOPIC_SIZE)

    def test_positive_assembly_is_not_a_problem(self):
        self.assertIsNone(detect_review_topic(
            "Собрала без проблем, в сборке лёгкая, собрал ребёнок", 5,
        ))

    def test_broke_after_use_is_load_not_defect(self):
        self.assertEqual(
            detect_review_topic("сломалась спустя 2 месяца, хлипкая"),
            TOPIC_LOAD,
        )
        ans = topic_answer(TOPIC_LOAD, "BO-Black-2", 2)
        self.assertNotIn("возврат", ans.lower())
        self.assertNotIn("замен", ans.lower())
        self.assertNotIn("35 кг", ans)
        self.assertIn("инструкц", ans.lower())

    def test_load_spec_only_for_sho(self):
        sho = topic_answer(TOPIC_LOAD, "ШО-White", 2)
        bo = topic_answer(TOPIC_LOAD, "BO-White-2", 2)
        self.assertIn("35 кг", sho)
        self.assertNotIn("35 кг", bo)
        facts_bo = article_facts_for_ai("BO-White-2")
        self.assertIn("НЕ артикул ШО", facts_bo)
        self.assertNotIn("35 кг", apply_article_hints(
            "BB-Black",
            "Нагрузка на всю вешалку до 35 кг, рейл до 5 кг.",
        ))

    def test_short_leg_is_defect(self):
        self.assertEqual(
            detect_review_topic("Ножка одна короче, или отверстие сделано не там", 4),
            TOPIC_DEFECT,
        )

    def test_paint_holes_on_shelf_are_not_defect(self):
        self.assertTrue(is_paint_hole_question("Для чего отверстия на полке?"))
        self.assertTrue(is_paint_hole_question("Зачем дырочка в подносе"))
        self.assertTrue(is_paint_hole_question(
            "На полке четыре отверстия, на фото карточки их нет",
        ))
        self.assertFalse(is_paint_hole_question("нет отверстия под болт"))
        self.assertFalse(is_paint_hole_question("отверстие сделано не там"))
        self.assertEqual(
            detect_review_topic("Дырки на полке, на фото их нет", 4),
            TOPIC_PAINT_HOLES,
        )
        self.assertIsNone(detect_review_topic("просто не понравилось качество", 2))
        self.assertNotEqual(
            detect_review_topic("сломалась полка, качество ужасное", 1),
            TOPIC_PAINT_HOLES,
        )
        self.assertEqual(
            detect_review_topic("нет отверстия под болт, не собрать", 3),
            TOPIC_ASSEMBLY,
        )
        ans = topic_answer(TOPIC_PAINT_HOLES, "Flower-stand-black-5", 4)
        self.assertIn(PAINT_HOLES_REPLY, ans)
        self.assertIn("покраск", ans.lower())
        self.assertNotIn("возврат", ans.lower())

    def test_four_star_unsuitable_and_unclear(self):
        self.assertEqual(
            detect_review_topic("Не довольна этой покупкой, не подошло", 4),
            TOPIC_UNSUITABLE,
        )
        self.assertEqual(
            detect_review_topic("только временное решение, долго не прослужит", 4),
            TOPIC_UNSUITABLE,
        )
        self.assertEqual(
            detect_review_topic("забрала потому что для организации пространства", 4),
            TOPIC_FOUR_ASK,
        )
        uns = topic_answer(TOPIC_UNSUITABLE, "BB-White", 4)
        self.assertNotIn("возврат", uns.lower())
        ask = topic_answer(TOPIC_FOUR_ASK, "BO-White", 4)
        self.assertIn("пятёрк", ask.lower())
        self.assertIn("чат", ask.lower())

    def test_size_stays_in_card_without_chat(self):
        self.assertEqual(detect_review_topic("платья стелятся, размер маленький", 3), TOPIC_SIZE)
        ans = topic_answer(TOPIC_SIZE, "ШО-White", 3)
        self.assertIn("карточк", ans.lower())
        self.assertNotIn("чат", ans.lower())

    def test_fast_delivery_praise_is_not_delivery_topic(self):
        self.assertIsNone(detect_review_topic("доставка быстрая, всё пришло целое, спасибо", 5))

    def test_ai_prompt_sends_packaging_to_seller_chat(self):
        from backend.review_policy import REVIEW_AI_SYSTEM
        self.assertIn("Чат с продавцом", REVIEW_AI_SYSTEM)
        self.assertIn("Порванная или помятая упаковка", REVIEW_AI_SYSTEM)
        self.assertIn("технологические отверстия", REVIEW_AI_SYSTEM.lower())
        self.assertNotIn("Не зови в чат «решать доставку»", REVIEW_AI_SYSTEM)
        self.assertIn("без приглашения в чат", REVIEW_AI_SYSTEM)
        self.assertIn("Длинный живой отзыв", REVIEW_AI_SYSTEM)

    def test_wobbly_bolts_only_for_floor_hangers(self):
        bo = topic_answer(TOPIC_ASSEMBLY, "BO-White-2", 3)
        bb = topic_answer(TOPIC_ASSEMBLY, "BB-White", 3)
        self.assertIn("болт", bo.lower())
        self.assertIn("на полу", bo.lower())
        self.assertNotIn("болт", bb.lower())

    def test_banned_constructor_advice_is_stripped(self):
        cleaned = apply_article_hints(
            "ШО-White",
            "Можно расширить систему. Вещи на верхних ярусах удобнее. Поставьте в прихожей.",
        )
        low = cleaned.lower()
        self.assertNotIn("расширить", low)
        self.assertNotIn("ярус", low)
        self.assertNotIn("прихож", low)

    def test_shelf_has_no_wheels(self):
        from backend.review_policy import article_facts_for_ai, is_shelf_article
        self.assertTrue(is_shelf_article("Shelf-black-5/43"))
        self.assertFalse(is_shelf_article("BB-White"))
        cleaned = apply_article_hints(
            "Shelf-black-5/43",
            "Благодарим за пятёрку! Рады, что мобильность стеллажа пришлась по душе. "
            "Чтобы колёса вращались плавно, периодически очищайте их от пыли. "
            "Порядок в доме — порядок в мыслях!",
        )
        low = cleaned.lower()
        self.assertNotIn("колёс", low)
        self.assertNotIn("колес", low)
        self.assertNotIn("мобильн", low)
        facts = article_facts_for_ai("Shelf-black-5/43")
        self.assertIn("колёс нет", facts.lower())
        rack = apply_article_hints(
            "BB-White",
            "Чтобы колёсики всегда вращались плавно, периодически очищайте их от пыли.",
        )
        self.assertIn("колёс", rack.lower())
        five = apply_article_hints(
            "BB-White",
            "Чтобы колёсики всегда вращались плавно, периодически очищайте их от пыли.",
            rating=5,
        )
        self.assertNotIn("колёс", five.lower())
        self.assertNotIn("очища", five.lower())

    def test_five_star_drops_shelf_and_care_tips(self):
        text = apply_article_hints(
            "SH-Black-4",
            "Спасибо за пять звёзд! Регулируемые полки удобно подстраивать под сезонную обувь. "
            "Полки можно просто убрать, если они не нужны. Возвращайтесь за новинками!",
            rating=5,
        )
        low = text.lower()
        self.assertNotIn("полки", low)
        self.assertNotIn("убрать", low)
        self.assertNotIn("регулируем", low)
        self.assertIn("новинк", low)
        facts = article_facts_for_ai("SH-Black-4")
        self.assertNotIn("можно убрать", facts.lower())

    def test_hanger_does_not_say_wear_it(self):
        from backend.review_policy import _furniture_wording
        text = apply_article_hints(
            "POLKO-White-2",
            "Рады, что вешалка и красивая, и надёжная. Носите с удовольствием и возвращайтесь за новинками для дома!",
            rating=5,
        )
        low = text.lower()
        self.assertNotIn("носит", low)
        self.assertIn("пользуйтесь с удовольствием", low)
        self.assertIn("пользуйтесь", _furniture_wording("Носите любимые образы с удовольствием и заглядывайте снова!").lower())
        self.assertNotIn("носит", _furniture_wording("Носите любимые образы с удовольствием!").lower())


if __name__ == "__main__":
    unittest.main()
