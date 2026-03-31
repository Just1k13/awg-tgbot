import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")
os.environ.setdefault("SUPPORT_USERNAME", "@support_test")


class _DummyMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class _DummyCallback:
    def __init__(self, user_id: int):
        self.from_user = type("U", (), {"id": user_id})()
        self.message = _DummyMessage()

    async def answer(self, *args, **kwargs):
        return None


class ActivationReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self.tmp.name) / "test.db")
        await database.close_shared_db()
        await database.init_db()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_check_activation_status_handles_no_payment(self):
        import handlers_user

        cb = _DummyCallback(101)
        original_get_user_subscription = handlers_user.get_user_subscription
        original_get_latest_user_payment_summary = handlers_user.get_latest_user_payment_summary
        original_get_user_keys = handlers_user.get_user_keys
        try:
            async def _sub(_uid):
                return "0"
            handlers_user.get_user_subscription = _sub
            async def _pay(_uid):
                return None
            handlers_user.get_latest_user_payment_summary = _pay
            async def _keys(_uid):
                return []
            handlers_user.get_user_keys = _keys
            await handlers_user.check_activation_status(cb)
        finally:
            handlers_user.get_user_subscription = original_get_user_subscription
            handlers_user.get_latest_user_payment_summary = original_get_latest_user_payment_summary
            handlers_user.get_user_keys = original_get_user_keys

        self.assertTrue(cb.message.answers)
        text, _kwargs = cb.message.answers[-1]
        self.assertIn("платежей пока нет", text.lower())

    async def test_check_activation_status_ready_without_config_is_explicit(self):
        import handlers_user

        cb = _DummyCallback(102)
        original_get_user_subscription = handlers_user.get_user_subscription
        original_get_latest_user_payment_summary = handlers_user.get_latest_user_payment_summary
        original_get_user_keys = handlers_user.get_user_keys
        try:
            async def _sub(_uid):
                return "2099-01-01T00:00:00"
            handlers_user.get_user_subscription = _sub
            async def _pay(_uid):
                return {
                    "status": "applied",
                    "last_provision_status": "ready",
                }
            handlers_user.get_latest_user_payment_summary = _pay
            async def _keys(_uid):
                return []
            handlers_user.get_user_keys = _keys
            await handlers_user.check_activation_status(cb)
        finally:
            handlers_user.get_user_subscription = original_get_user_subscription
            handlers_user.get_latest_user_payment_summary = original_get_latest_user_payment_summary
            handlers_user.get_user_keys = original_get_user_keys

        text, _kwargs = cb.message.answers[-1]
        self.assertIn("ключ ещё собирается", text.lower())

    async def test_check_activation_status_problem_state_is_practical(self):
        import handlers_user

        cb = _DummyCallback(103)
        original_get_user_subscription = handlers_user.get_user_subscription
        original_get_latest_user_payment_summary = handlers_user.get_latest_user_payment_summary
        original_get_user_keys = handlers_user.get_user_keys
        try:
            async def _sub(_uid):
                return "0"
            handlers_user.get_user_subscription = _sub
            async def _pay(_uid):
                return {
                    "status": "stuck_manual",
                    "last_provision_status": "provisioning",
                }
            handlers_user.get_latest_user_payment_summary = _pay
            async def _keys(_uid):
                return []
            handlers_user.get_user_keys = _keys
            await handlers_user.check_activation_status(cb)
        finally:
            handlers_user.get_user_subscription = original_get_user_subscription
            handlers_user.get_latest_user_payment_summary = original_get_latest_user_payment_summary
            handlers_user.get_user_keys = original_get_user_keys

        text, _kwargs = cb.message.answers[-1]
        self.assertIn("требует проверки", text.lower())


if __name__ == "__main__":
    unittest.main()
