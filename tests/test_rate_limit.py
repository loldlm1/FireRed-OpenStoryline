from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from open_storyline.mvp.rate_limit import PersistentRateLimiter, RateRule


class PersistentRateLimiterTests(unittest.TestCase):
    def test_minute_limit_persists_across_instances(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "limits.sqlite3"
            rule = RateRule(minute=2, day=10)
            first = PersistentRateLimiter(path)
            self.assertTrue(first.check("api:key", rule, now=120).allowed)
            self.assertTrue(first.check("api:key", rule, now=121).allowed)

            restarted = PersistentRateLimiter(path)
            denied = restarted.check("api:key", rule, now=122)
            rolled = restarted.check("api:key", rule, now=180)

        self.assertFalse(denied.allowed)
        self.assertEqual(denied.retry_after, 58)
        self.assertTrue(rolled.allowed)

    def test_daily_limit_survives_minute_rollover(self):
        with TemporaryDirectory() as tmpdir:
            limiter = PersistentRateLimiter(Path(tmpdir) / "limits.sqlite3")
            rule = RateRule(minute=5, day=2)
            self.assertTrue(limiter.check("jobs:key", rule, now=60).allowed)
            self.assertTrue(limiter.check("jobs:key", rule, now=120).allowed)
            denied = limiter.check("jobs:key", rule, now=180)

        self.assertFalse(denied.allowed)
        self.assertEqual(denied.remaining_day, 0)
        self.assertGreater(denied.retry_after, 60)

    def test_concurrent_checks_do_not_oversubscribe_quota(self):
        with TemporaryDirectory() as tmpdir:
            limiter = PersistentRateLimiter(Path(tmpdir) / "limits.sqlite3")
            rule = RateRule(minute=5, day=100)
            with ThreadPoolExecutor(max_workers=10) as pool:
                decisions = list(pool.map(
                    lambda _: limiter.check("concurrent:key", rule, now=300),
                    range(20),
                ))

        self.assertEqual(sum(item.allowed for item in decisions), 5)


if __name__ == "__main__":
    unittest.main()
