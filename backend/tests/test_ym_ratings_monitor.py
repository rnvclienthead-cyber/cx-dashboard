import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.digest_issues import still_failing
from backend.ym_ratings_freshness import ym_ratings_is_stale


class YmRatingsStaleTests(unittest.TestCase):
    def test_missing_success_is_stale(self):
        self.assertTrue(ym_ratings_is_stale(None))

    def test_fresh_success_is_ok(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        last = now - timedelta(hours=12)
        self.assertFalse(ym_ratings_is_stale(last, now=now))

    def test_two_weeks_gap_alerts(self):
        now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        last = datetime(2026, 8, 6, 4, 0, tzinfo=timezone.utc)
        self.assertTrue(ym_ratings_is_stale(last, now=now))

    def test_naive_timestamps_count_as_utc(self):
        now = datetime(2026, 8, 20, 12, 0)
        last = datetime(2026, 8, 6, 4, 0)
        self.assertTrue(ym_ratings_is_stale(last, now=now))


class YmWorkerLockIdsTests(unittest.TestCase):
    def test_ratings_lock_is_not_hourly(self):
        src = (Path(__file__).resolve().parents[1] / "cron_lock.py").read_text(encoding="utf-8")
        self.assertIn('"ym_worker": 87442211', src)
        self.assertIn('"ym_worker_ratings": 87442215', src)
        self.assertNotEqual("87442211", "87442215")

    def test_wb_ratings_lock_is_not_logistics(self):
        src = (Path(__file__).resolve().parents[1] / "cron_lock.py").read_text(encoding="utf-8")
        self.assertIn('"worker": 87442210', src)
        self.assertIn('"worker_ratings": 87442220', src)
        self.assertIn('"worker_logistics": 87442221', src)


class DigestCurrentFailuresTests(unittest.TestCase):
    def test_recovered_after_errors_is_not_current(self):
        err = datetime(2026, 8, 31, 10, 6, tzinfo=timezone.utc)
        ok = datetime(2026, 8, 31, 13, 59, tzinfo=timezone.utc)
        self.assertFalse(still_failing(err, ok))

    def test_error_after_success_is_current(self):
        ok = datetime(2026, 8, 30, 3, 0, tzinfo=timezone.utc)
        err = datetime(2026, 9, 1, 3, 25, tzinfo=timezone.utc)
        self.assertTrue(still_failing(err, ok))

    def test_error_without_success_is_current(self):
        self.assertTrue(still_failing(datetime(2026, 9, 1, 3, 25, tzinfo=timezone.utc), None))

    def test_no_error_is_not_current(self):
        self.assertFalse(still_failing(None, datetime(2026, 9, 1, 3, 25, tzinfo=timezone.utc)))


if __name__ == "__main__":
    unittest.main()
