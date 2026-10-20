import inspect
import unittest

from backend.routers import ai
from backend import feedback_tagger_job


def _before_llm(src: str) -> str:
    after_fetch = src.split("fetchall()", 1)[1]
    before_http, _, rest = after_fetch.partition("fetch_ai_tags_rotating")
    if not rest:
        raise AssertionError("fetch_ai_tags_rotating not found after fetchall")
    return before_http


class TaggingReleasesTxBeforeLlmTests(unittest.TestCase):
    def test_ym_commits_before_qwen(self):
        src = inspect.getsource(ai.process_ym_tagging_task)
        self.assertIn("_end_tx(db)", _before_llm(src))
        self.assertIn("_log_tagging_fresh", src)

    def test_wb_commits_before_qwen(self):
        src = inspect.getsource(ai.process_tagging_task)
        self.assertIn("_end_tx(db)", _before_llm(src))
        self.assertIn("_log_tagging_fresh", src)

    def test_ozon_commits_before_qwen(self):
        src = inspect.getsource(ai.process_ozon_tagging_task)
        self.assertIn("_end_tx(db)", _before_llm(src))
        self.assertIn("_log_tagging_fresh", src)

    def test_feedback_commits_before_qwen(self):
        src = inspect.getsource(feedback_tagger_job._loop_table)
        chunk = src.split("if not rows:", 1)[1]
        before_http, _, rest = chunk.partition("fetch_ai_feedback_tags")
        self.assertTrue(rest)
        self.assertIn("db.commit()", before_http)

    def test_end_tx_commits(self):
        src = inspect.getsource(ai._end_tx)
        self.assertIn("db.commit()", src)
        self.assertIn("idle-in-transaction", src)
