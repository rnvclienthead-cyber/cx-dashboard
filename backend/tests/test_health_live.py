import unittest

from backend.access import classify_path, PUBLIC
from backend.loop_watch import (
    classify_disk,
    classify_http_probe,
    classify_lag,
    classify_ram,
    note_lag_ms,
    snapshot,
)


class HealthLiveAccessTests(unittest.TestCase):
    def test_live_is_public(self):
        self.assertEqual(classify_path("/api/v1/health/live"), (PUBLIC, None))
        self.assertEqual(classify_path("/api/v1/health/db"), (PUBLIC, None))


class LoopLagTests(unittest.TestCase):
    def test_lag_buckets(self):
        self.assertEqual(classify_lag(10), "ok")
        self.assertEqual(classify_lag(500), "warn")
        self.assertEqual(classify_lag(2500), "critical")

    def test_snapshot_records_lag(self):
        note_lag_ms(12.4)
        self.assertEqual(snapshot()["loop_lag_ms"], 12.4)


class MonitorClassifyTests(unittest.TestCase):
    def test_http_probe(self):
        self.assertEqual(classify_http_probe(False, 100), "down")
        self.assertEqual(classify_http_probe(True, 400), "ok")
        self.assertEqual(classify_http_probe(True, 3000), "slow")

    def test_ram_and_disk(self):
        self.assertFalse(classify_ram(80))
        self.assertTrue(classify_ram(90))
        self.assertFalse(classify_disk(10))
        self.assertTrue(classify_disk(1.5))


class BackgroundSplitTests(unittest.TestCase):
    def test_lock_id_stable(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1].joinpath("bg_runtime.py").read_text(encoding="utf-8")
        self.assertIn("LEADER_LOCK_ID = 87442201", src)

    def test_sse_channel(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1].joinpath("sse_bridge.py").read_text(encoding="utf-8")
        self.assertIn('CHANNEL = "cx_frontend"', src)

    def test_api_lifespan_does_not_start_syncs(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        self.assertIn("sse_bridge.listen_forever", src)
        self.assertNotIn("start_mini_sync_worker(get_db)", src)
        self.assertNotIn("start_chat_bot_worker(get_db)", src)

    def test_leader_lock_autocommits(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1].joinpath("bg_runtime.py").read_text(encoding="utf-8")
        self.assertIn('isolation_level="AUTOCOMMIT"', src)
        self.assertIn("pg_try_advisory_lock", src)

    def test_cron_lock_autocommits(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[1].joinpath("cron_lock.py").read_text(encoding="utf-8")
        self.assertIn('isolation_level="AUTOCOMMIT"', src)
        self.assertGreaterEqual(src.count('isolation_level="AUTOCOMMIT"'), 2)


class DigestAiFilterTests(unittest.TestCase):
    def _filter(self):
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "system_monitor.py"
        spec = importlib.util.spec_from_file_location("system_monitor_digest", path)
        # Не импортируем файл целиком — там sqlalchemy/engine.
        src = path.read_text(encoding="utf-8")
        start = src.index("def _is_ai_tag_digest_action")
        end = src.index("\ndef _label(")
        ns = {"_re": __import__("re")}
        # Паттерн объявлен выше функции — вырежем оба.
        pat_start = src.index("_AI_TAG_DIGEST_RE")
        exec(src[pat_start:end], ns)
        return ns["_is_ai_tag_digest_action"]

    def test_digest_hides_ai_tag_actions(self):
        fn = self._filter()
        self.assertTrue(fn("ИИ Разметка WB"))
        self.assertTrue(fn("ИИ Отзывы пачка 3"))
        self.assertTrue(fn("Бэкенд ИИ"))
        self.assertTrue(fn("AI-тегировщик"))
        self.assertTrue(fn("ozon_rule_tags"))
        self.assertFalse(fn("ym_sync_ratings"))
        self.assertFalse(fn("Синхронизатор Отзывов"))

    def test_digest_no_ai_lag_block(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[2].joinpath("system_monitor.py").read_text(encoding="utf-8")
        self.assertNotIn("ИИ-разметка WB отстаёт", src)


if __name__ == "__main__":
    unittest.main()
