import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


class ExpiryRemindersTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_expiring_subscriptions_sends_once_per_kind(self):
        import app

        sent_marks: set[tuple[int, str, str]] = set()
        sent_messages: list[tuple[int, str]] = []

        async def fake_get_subscriptions_expiring_within(hours: int = 24):
            if hours == 72:
                return [(10, "2099-01-04T00:00:00")]
            if hours == 24:
                return [(10, "2099-01-04T00:00:00")]
            return []

        async def fake_has_notification(user_id: int, sub_until: str, kind: str):
            return (user_id, sub_until, kind) in sent_marks

        async def fake_mark_sent(user_id: int, sub_until: str, kind: str):
            sent_marks.add((user_id, sub_until, kind))

        class DummyBot:
            async def send_message(self, user_id, text, **kwargs):
                sent_messages.append((user_id, text))

        original_get = app.get_subscriptions_expiring_within
        original_has = app.has_subscription_notification
        original_mark = app.mark_subscription_notification_sent
        app.get_subscriptions_expiring_within = fake_get_subscriptions_expiring_within
        app.has_subscription_notification = fake_has_notification
        app.mark_subscription_notification_sent = fake_mark_sent
        try:
            bot = DummyBot()
            await app._notify_expiring_subscriptions(bot)  # type: ignore[arg-type]
            await app._notify_expiring_subscriptions(bot)  # type: ignore[arg-type]
        finally:
            app.get_subscriptions_expiring_within = original_get
            app.has_subscription_notification = original_has
            app.mark_subscription_notification_sent = original_mark

        self.assertEqual(len(sent_messages), 2)
        self.assertIn("3 дня", sent_messages[0][1])
        self.assertIn("1 день", sent_messages[1][1])


if __name__ == "__main__":
    unittest.main()
