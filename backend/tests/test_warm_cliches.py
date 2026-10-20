import unittest

from backend.routers.kb import (
    _WARM_CLICHE_REPLACEMENTS,
    ensure_no_warm_cliches,
    ensure_no_stray_recommend_thanks,
    scrub_recommend_thanks,
)
from backend.review_policy import REVIEW_AI_SYSTEM


class WarmClicheTests(unittest.TestCase):
    def test_ai_rule_bans_warm_words(self):
        self.assertIn("тёплые слова", REVIEW_AI_SYSTEM)
        self.assertIn("порекомендовали", REVIEW_AI_SYSTEM)

    def test_replacements_cover_both_spellings(self):
        olds = [old for old, _new in _WARM_CLICHE_REPLACEMENTS]
        self.assertTrue(any("теплые слова" in old for old in olds))
        self.assertTrue(any("тёплые слова" in old for old in olds))
        self.assertTrue(all("тёпл" not in new and "тепл" not in new
                            for _old, new in _WARM_CLICHE_REPLACEMENTS))

    def test_ensure_helper_exists(self):
        self.assertTrue(callable(ensure_no_warm_cliches))
        self.assertTrue(callable(ensure_no_stray_recommend_thanks))

    def test_scrub_drops_recommend_thanks_without_recommend_word(self):
        raw = "Спасибо, что порекомендовали нас — это лучшая благодарность для команды. Рады, что удобно."
        out = scrub_recommend_thanks(raw, "Супер, собрала за 5 минут")
        self.assertNotIn("порекомендовали", out.lower())
        self.assertIn("Спасибо за отзыв", out)

    def test_scrub_keeps_recommend_thanks_when_review_recommends(self):
        raw = "Спасибо, что порекомендовали нас — это лучшая благодарность для команды."
        out = scrub_recommend_thanks(raw, "Отличная вешалка, рекомендую всем")
        self.assertIn("порекомендовали", out)


if __name__ == "__main__":
    unittest.main()
