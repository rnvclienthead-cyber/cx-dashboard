import unittest

from backend.feedback_tagger_job import (
    FEEDBACK_FREE_MODELS,
    resolve_feedback_models,
    should_rotate_feedback_model,
)


class FeedbackModelRotationTests(unittest.TestCase):
    def test_plus_is_not_used_for_reviews(self):
        chain = resolve_feedback_models("qwen-plus")
        self.assertEqual(chain[0], "qwen3.5-122b-a10b")
        self.assertNotIn("qwen-plus", chain)
        self.assertEqual(tuple(chain), FEEDBACK_FREE_MODELS)

    def test_reviews_alias_uses_free_chain(self):
        self.assertEqual(resolve_feedback_models("qwen-reviews")[0], "qwen3.5-122b-a10b")

    def test_lite_stays_lite(self):
        self.assertEqual(resolve_feedback_models("yandex-lite"), ["yandex-lite"])

    def test_quota_error_rotates(self):
        self.assertTrue(should_rotate_feedback_model(
            "Qwen HTTP 403: AllocationQuota.FreeTierOnly"
        ))
        self.assertFalse(should_rotate_feedback_model("timeout"))


if __name__ == "__main__":
    unittest.main()
