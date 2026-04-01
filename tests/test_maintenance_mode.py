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


class MaintenanceModeTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_maintenance_on_blocks_buy_flow(self):
        import database
        import handlers_user

        await database.set_app_setting("MAINTENANCE_MODE", "1", updated_by=1)

        class DummyMessage:
            def __init__(self):
                self.from_user = SimpleNamespace(id=101, username="u101", first_name="User")
                self.answers: list[str] = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        msg = DummyMessage()
        await handlers_user.buy(msg)  # type: ignore[arg-type]
        self.assertIn("техработы", msg.answers[-1].lower())

    async def test_maintenance_off_restores_buy_flow(self):
        import database
        import handlers_user

        await database.set_app_setting("MAINTENANCE_MODE", "0", updated_by=1)

        class DummyMessage:
            def __init__(self):
                self.from_user = SimpleNamespace(id=102, username="u102", first_name="User")
                self.answers: list[str] = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        msg = DummyMessage()
        await handlers_user.buy(msg)  # type: ignore[arg-type]
        self.assertIn("выберите срок доступа", msg.answers[-1].lower())

    async def test_profile_and_configs_still_work_in_maintenance(self):
        import database
        import handlers_user
        from ui_constants import BTN_CONFIGS, BTN_PROFILE

        await database.set_app_setting("MAINTENANCE_MODE", "1", updated_by=1)

        class DummyMessage:
            def __init__(self, text: str):
                self.text = text
                self.from_user = SimpleNamespace(id=103, username="u103", first_name="User")
                self.answers: list[str] = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        profile_msg = DummyMessage(BTN_PROFILE)
        await handlers_user.profile(profile_msg)  # type: ignore[arg-type]
        self.assertIn("профиль", profile_msg.answers[-1].lower())
        self.assertNotIn("покупка временно недоступна", profile_msg.answers[-1].lower())

        config_msg = DummyMessage(BTN_CONFIGS)
        await handlers_user.my_keys(config_msg)  # type: ignore[arg-type]
        self.assertIn("подключение", config_msg.answers[-1].lower())

    async def test_admin_controls_toggle_and_audit(self):
        import database
        import handlers_admin

        class DummyMessage:
            def __init__(self):
                self.from_user = SimpleNamespace(id=1)
                self.answers: list[str] = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        on_msg = DummyMessage()
        await handlers_admin.maintenance_on_cmd(on_msg)  # type: ignore[arg-type]
        self.assertEqual(await database.get_app_setting("MAINTENANCE_MODE"), "1")

        status_msg = DummyMessage()
        await handlers_admin.maintenance_status_cmd(status_msg)  # type: ignore[arg-type]
        self.assertIn("ON", status_msg.answers[-1])

        off_msg = DummyMessage()
        await handlers_admin.maintenance_off_cmd(off_msg)  # type: ignore[arg-type]
        self.assertEqual(await database.get_app_setting("MAINTENANCE_MODE"), "0")

        rows = await database.get_recent_audit(limit=10)
        actions = [row[2] for row in rows]
        self.assertIn("maintenance_enabled", actions)
        self.assertIn("maintenance_disabled", actions)


if __name__ == "__main__":
    unittest.main()
