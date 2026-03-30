import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


class RateLimitMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_sliding_window_allows_burst_and_then_limits(self):
        import middlewares

        limiter = middlewares.RateLimitMiddleware(ttl_seconds=10.0, max_hits=2)
        event = SimpleNamespace(from_user=SimpleNamespace(id=1001))
        calls = {"count": 0}

        async def handler(_event, _data):
            calls["count"] += 1
            return "ok"

        result1 = await limiter(handler, event, {})
        result2 = await limiter(handler, event, {})
        result3 = await limiter(handler, event, {})

        self.assertEqual(result1, "ok")
        self.assertEqual(result2, "ok")
        self.assertIsNone(result3)
        self.assertEqual(calls["count"], 2)
