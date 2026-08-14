import unittest
from unittest.mock import patch

from backend.llm import is_yandex_lite, qwen_model_name


class LlmRoutingTests(unittest.TestCase):
    def test_yandex_lite_keys(self):
        self.assertTrue(is_yandex_lite("yandex-lite"))
        self.assertTrue(is_yandex_lite("yandexgpt-lite"))
        self.assertFalse(is_yandex_lite("yandexgpt"))
        self.assertFalse(is_yandex_lite("yandex-pro"))
        self.assertFalse(is_yandex_lite("qwen-plus"))
        self.assertFalse(is_yandex_lite("claude-haiku"))
        self.assertFalse(is_yandex_lite(""))

    def test_pro_and_claude_route_to_qwen_plus(self):
        with patch.dict("os.environ", {"QWEN_MODEL": "qwen-plus"}, clear=False):
            self.assertEqual(qwen_model_name("yandex-pro"), "qwen-plus")
            self.assertEqual(qwen_model_name("yandexgpt"), "qwen-plus")
            self.assertEqual(qwen_model_name("claude-haiku"), "qwen-plus")
            self.assertEqual(qwen_model_name(""), "qwen-plus")
            self.assertEqual(qwen_model_name(None), "qwen-plus")

    def test_explicit_qwen_models_kept(self):
        self.assertEqual(qwen_model_name("qwen-plus"), "qwen-plus")
        self.assertEqual(qwen_model_name("qwen-turbo"), "qwen-turbo")
        self.assertEqual(qwen_model_name("qwen-max"), "qwen-max")
        self.assertEqual(qwen_model_name("qwen3.7-plus"), "qwen3.7-plus")
        self.assertEqual(qwen_model_name("qwen3.5-plus-2026-02-15"), "qwen3.5-plus-2026-02-15")

    def test_reviews_alias_does_not_hit_api_name(self):
        with patch.dict("os.environ", {"QWEN_MODEL": "qwen-plus"}, clear=False):
            self.assertEqual(qwen_model_name("qwen-reviews"), "qwen-plus")


if __name__ == "__main__":
    unittest.main()
