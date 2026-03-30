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


class TextLayerRefactorTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_activation_status_text_mapping(self):
        import texts

        ready = await texts.get_activation_status_text("ready")
        pending = await texts.get_activation_status_text("provisioning")
        delayed = await texts.get_activation_status_text("failed")

        self.assertIn("доступ готов", ready.lower())
        self.assertIn("выпускается", pending.lower())
        self.assertIn("задерж", delayed.lower())

    async def test_payment_result_text_consolidated(self):
        import texts

        ready = await texts.get_payment_result_text("ready")
        pending = await texts.get_payment_result_text("pending")

        self.assertIn("следующий шаг", ready.lower())
        self.assertIn("нажмите", pending.lower())

    async def test_ui_callback_scopes(self):
        from ui_constants import is_admin_callback_data, is_user_config_callback_data

        self.assertTrue(is_admin_callback_data("a:tx:p:0"))
        self.assertFalse(is_admin_callback_data("config_device_1"))
        self.assertTrue(is_user_config_callback_data("config_conf_1"))
        self.assertFalse(is_user_config_callback_data("a:st:k:1_0"))


if __name__ == "__main__":
    unittest.main()
