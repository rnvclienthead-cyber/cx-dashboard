import os
import unittest
from unittest.mock import patch

from backend.llm import complete_sync, is_yandex_lite, qwen_model_name
from backend.qwen_quota import FREE_QWEN_MODELS, reset_quota_state


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
            self.assertEqual(qwen_model_name("qwen-claims"), "qwen-plus")
            self.assertEqual(qwen_model_name("qwen-free"), "qwen-plus")


class LlmQuotaFailoverTests(unittest.TestCase):
    def setUp(self):
        os.environ["QWEN_QUOTA_PERSIST"] = "0"
        reset_quota_state()

    def tearDown(self):
        reset_quota_state()

    def test_complete_sync_tries_next_model_on_quota(self):
        calls = []

        def fake_chat(self, **_kwargs):
            calls.append(self.model)
            if self.model == FREE_QWEN_MODELS[0]:
                raise RuntimeError('Qwen HTTP 403: {"code":"insufficient_quota"}')
            return "готово"

        with patch("backend.llm.QwenClient.chat_sync", fake_chat):
            text = complete_sync(system="s", user="u", model_key="qwen-plus", mask_user_text=False)
        self.assertEqual(text, "готово")
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0], FREE_QWEN_MODELS[0])

    def test_complete_sync_skips_api_when_all_paused(self):
        import time
        from backend.qwen_quota import FREE_QWEN_MODELS, note_exhausted

        far = time.time() + 3600
        for model in FREE_QWEN_MODELS:
            note_exhausted(model, until=far)
        with patch("backend.llm.QwenClient.chat_sync") as fake:
            with self.assertRaises(RuntimeError) as ctx:
                complete_sync(system="s", user="u", model_key="qwen-plus", mask_user_text=False)
        fake.assert_not_called()
        self.assertIn("паузе", str(ctx.exception))

    def test_complete_sync_respects_max_models(self):
        calls = []

        def fake_chat(self, **_kwargs):
            calls.append(self.model)
            raise RuntimeError('Qwen HTTP 403: {"code":"insufficient_quota"}')

        with patch("backend.llm.QwenClient.chat_sync", fake_chat):
            with self.assertRaises(RuntimeError):
                complete_sync(
                    system="s", user="u", model_key="qwen-plus",
                    mask_user_text=False, max_models=1, timeout=20,
                )
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
