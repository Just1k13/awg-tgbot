import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


class AdminDeviceActivityRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self.tmp.name) / "test.db")
        await database.close_shared_db()
        await database.init_db()

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (501, '2099-01-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (501, 1, 'PUBKEY1', '', '10.8.1.11', '2026-01-01T00:00:00', 'active')
                """
            )
            await db.commit()
        finally:
            await db.close()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_build_admin_device_activity_lines_with_runtime_data(self):
        import handlers_admin

        async def fake_get_awg_peers():
            return [
                {
                    "public_key": "PUBKEY1",
                    "ip": "10.8.1.11",
                    "latest_handshake_at": datetime(2026, 3, 31, 11, 55, 0),
                }
            ]

        original = handlers_admin.get_awg_peers
        handlers_admin.get_awg_peers = fake_get_awg_peers
        try:
            lines = await handlers_admin._build_admin_device_activity_lines(501)
        finally:
            handlers_admin.get_awg_peers = original

        self.assertEqual(len(lines), 1)
        self.assertIn("Устройство 1", lines[0])
        self.assertIn("(31.03 11:55)", lines[0])


if __name__ == "__main__":
    unittest.main()
