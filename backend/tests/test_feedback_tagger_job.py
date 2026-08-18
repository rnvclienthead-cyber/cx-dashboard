import unittest

from backend.qwen_quota import (
    FREE_QWEN_MODELS,
    is_quota_or_model_error,
    resolve_free_qwen_models,
)


class FreeQwenQuotaTests(unittest.TestCase):
    def test_plus_remaps_to_free_chain(self):
        chain = resolve_free_qwen_models("qwen-plus")
        self.assertEqual(chain[0], "qwen3.5-122b-a10b")
        self.assertNotIn("qwen-plus", chain)
        self.assertEqual(tuple(chain), FREE_QWEN_MODELS)

    def test_aliases_use_free_chain(self):
        self.assertEqual(resolve_free_qwen_models("qwen-reviews")[0], "qwen3.5-122b-a10b")
        self.assertEqual(resolve_free_qwen_models("qwen-claims")[0], "qwen3.5-122b-a10b")

    def test_lite_stays_lite(self):
        self.assertEqual(resolve_free_qwen_models("yandex-lite"), ["yandex-lite"])

    def test_quota_error_rotates(self):
        self.assertTrue(is_quota_or_model_error(
            'Qwen HTTP 403: {"type":"insufficient_quota","code":"insufficient_quota"}'
        ))
        self.assertTrue(is_quota_or_model_error("Free quota exhausted"))
        self.assertFalse(is_quota_or_model_error("timeout"))


if __name__ == "__main__":
    unittest.main()
