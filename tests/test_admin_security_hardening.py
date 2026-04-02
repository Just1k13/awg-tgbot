import os
import sys
import tempfile
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
os.environ.setdefault("AWG_HELPER_POLICY_PATH", str(ROOT / "tests" / "helper-policy.json"))


class AdminSecurityHardeningTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_non_admin_cannot_cancel_admin_revoke_action(self):
        import database
        import handlers_admin

        await database.set_pending_admin_action(1, "revoke", {"action": "revoke", "target": 111})

        answers: list[tuple[str, bool]] = []

        class DummyCb:
            from_user = SimpleNamespace(id=999)
            message = SimpleNamespace(answer=lambda *_args, **_kwargs: None)

            async def answer(self, text: str, show_alert: bool = False):
                answers.append((text, show_alert))

        cb = DummyCb()
        await handlers_admin.cancel_revoke(cb)
        state = await database.pop_pending_admin_action(1, "revoke")
        self.assertIsNotNone(state)
        self.assertTrue(answers and answers[-1][0] == "Нет доступа")

    async def test_non_admin_cannot_cancel_admin_delete_action(self):
        import database
        import handlers_admin

        await database.set_pending_admin_action(1, "delete_user", {"action": "delete_user", "target": 111})

        answers: list[tuple[str, bool]] = []

        class DummyCb:
            from_user = SimpleNamespace(id=777)
            message = SimpleNamespace(answer=lambda *_args, **_kwargs: None)

            async def answer(self, text: str, show_alert: bool = False):
                answers.append((text, show_alert))

        cb = DummyCb()
        await handlers_admin.cancel_delete_user(cb)
        state = await database.pop_pending_admin_action(1, "delete_user")
        self.assertIsNotNone(state)
        self.assertTrue(answers and answers[-1][0] == "Нет доступа")

    async def test_admin_manage_keyboard_has_no_qos_buttons(self):
        import handlers_admin

        kb = handlers_admin._user_manage_kb(123, 0)
        serialized = str(kb.model_dump())
        self.assertNotIn("admin_set_rate_", serialized)
