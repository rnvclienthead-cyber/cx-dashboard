import os
import unittest
from unittest.mock import patch

from backend.feedback_tagger_job import unsaved_action
from backend.qwen_quota import (
    FREE_QWEN_MODELS,
    is_quota_or_model_error,
    note_exhausted,
    note_http_quota,
    reset_quota_state,
    resolve_free_qwen_models,
)


class UnsavedActionTests(unittest.TestCase):
    def test_clean_batch_closes_empty(self):
        # Пачка без ошибок: ИИ просто не дал тегов — закрываем пустым.
        self.assertEqual(unsaved_action(clean_batch=True), "empty")

    def test_errored_batch_defers_not_loses(self):
        # Пачка с ошибкой: запись НЕ хороним пустым, откладываем на потом.
        self.assertEqual(unsaved_action(clean_batch=False), "defer")

    def test_defer_filter_uses_text_ids(self):
        import inspect
        from backend import feedback_tagger_job
        src = inspect.getsource(feedback_tagger_job._loop_table)
        self.assertIn("CAST(:defer AS text[])", src)
        self.assertNotIn("CAST(:defer AS bigint[])", src)
        self.assertIn("id::text", src)


class FreeQwenQuotaTests(unittest.TestCase):
    def setUp(self):
        os.environ["QWEN_QUOTA_PERSIST"] = "0"
        reset_quota_state()

    def tearDown(self):
        reset_quota_state()

    def test_plus_remaps_to_free_chain(self):
        chain = resolve_free_qwen_models("qwen-plus")
        self.assertEqual(chain[0], FREE_QWEN_MODELS[0])
        self.assertEqual(tuple(chain), FREE_QWEN_MODELS)
        self.assertGreaterEqual(len(chain), 10)

    def test_aliases_use_free_chain(self):
        self.assertEqual(resolve_free_qwen_models("qwen-reviews")[0], FREE_QWEN_MODELS[0])
        self.assertEqual(resolve_free_qwen_models("qwen-claims")[0], FREE_QWEN_MODELS[0])

    def test_lite_stays_lite(self):
        self.assertEqual(resolve_free_qwen_models("yandex-lite"), ["yandex-lite"])

    def test_quota_error_rotates(self):
        self.assertTrue(is_quota_or_model_error(
            'Qwen HTTP 403: {"type":"insufficient_quota","code":"insufficient_quota"}'
        ))
        self.assertTrue(is_quota_or_model_error("Free quota exhausted"))
        self.assertFalse(is_quota_or_model_error("timeout"))
        from backend.qwen_quota import is_transient_network_error, should_rotate_qwen_model
        self.assertTrue(is_transient_network_error("Qwen сеть/таймаут: "))
        self.assertTrue(should_rotate_qwen_model("Qwen сеть/таймаут: "))
        self.assertFalse(is_quota_or_model_error("Qwen сеть/таймаут: "))

    def test_low_remaining_moves_model_to_the_end(self):
        first = resolve_free_qwen_models("qwen-plus")[0]
        note_http_quota(first, 200, {"x-ratelimit-remaining-requests": "2"})
        chain = resolve_free_qwen_models("qwen-plus")
        self.assertNotEqual(chain[0], first)
        self.assertIn(first, chain)

    def test_zero_remaining_skips_model(self):
        first = resolve_free_qwen_models("qwen-plus")[0]
        note_http_quota(first, 200, {"x-ratelimit-remaining-requests": "0"})
        chain = resolve_free_qwen_models("qwen-plus")
        self.assertNotIn(first, chain)

    def test_exhausted_skips_until_reset(self):
        first = resolve_free_qwen_models("qwen-plus")[0]
        note_exhausted(first, until=time_far_future())
        chain = resolve_free_qwen_models("qwen-plus")
        self.assertNotIn(first, chain)

    def test_named_exhausted_model_does_not_jump_the_queue(self):
        note_exhausted("qwen-plus-latest", until=time_far_future())
        chain = resolve_free_qwen_models("qwen-plus-latest")
        self.assertEqual(chain[0], FREE_QWEN_MODELS[0])
        self.assertNotIn("qwen-plus-latest", chain)

    def test_all_exhausted_does_not_probe(self):
        import time
        far = time.time() + 3600
        for model in FREE_QWEN_MODELS:
            note_exhausted(model, until=far)
        chain = resolve_free_qwen_models("qwen-plus")
        self.assertEqual(chain, [])
        from backend.qwen_quota import qwen_pause_message
        self.assertIn("YandexGPT Lite", qwen_pause_message() or "")

    def test_quota_http_pauses_minutes_not_until_night(self):
        import time
        first = resolve_free_qwen_models("qwen-plus")[0]
        note_http_quota(first, 403, {}, '{"code":"insufficient_quota"}')
        chain = resolve_free_qwen_models("qwen-plus")
        self.assertNotIn(first, chain)
        from backend.qwen_quota import _state
        until = float((_state.get(first) or {}).get("exhausted_until") or 0)
        wait = until - time.time()
        self.assertGreater(wait, 10 * 60)
        self.assertLess(wait, 25 * 60)

    def test_wait_sec_zero_when_any_model_free(self):
        first = resolve_free_qwen_models("qwen-plus")[0]
        note_exhausted(first)
        from backend.qwen_quota import qwen_wait_sec
        self.assertEqual(qwen_wait_sec(), 0.0)

    def test_wait_sec_when_all_paused(self):
        import time
        from backend.qwen_quota import qwen_wait_sec
        far = time.time() + 600
        for model in FREE_QWEN_MODELS:
            note_exhausted(model, until=far)
        wait = qwen_wait_sec()
        self.assertGreater(wait, 500)
        self.assertLess(wait, 610)

    def test_pause_error_counts_as_quota(self):
        self.assertTrue(is_quota_or_model_error(
            "Qwen: все модели на паузе (лимит), без запроса к API"
        ))


def time_far_future():
    import time
    return time.time() + 3600


if __name__ == "__main__":
    unittest.main()
