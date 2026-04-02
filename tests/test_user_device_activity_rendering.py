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


class UserDeviceActivityRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self.tmp.name) / "test.db")
        await database.close_shared_db()
        await database.init_db()

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (601, '2099-01-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, rx_bytes_total, tx_bytes_total)
                VALUES (601, 1, 'PUBKEY1', '', '10.8.1.11', '2026-01-01T00:00:00', 'active', 1073741824, 314572800)
                """
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, rx_bytes_total, tx_bytes_total)
                VALUES (601, 2, 'PUBKEY2', '', '10.8.1.12', '2026-01-01T00:00:00', 'active', 0, 0)
                """
            )
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (602, '2099-01-01T00:00:00', '2026-01-01T00:00:00')")
            await db.commit()
        finally:
            await db.close()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_user_device_activity_recent_and_stale(self):
        import handlers_user

        async def fake_get_awg_peers():
            return [
                {"public_key": "PUBKEY1", "latest_handshake_at": datetime(2026, 3, 31, 11, 55, 0)},
                {"public_key": "PUBKEY2", "latest_handshake_at": datetime(2026, 3, 29, 12, 0, 0)},
            ]

        original_get_peers = handlers_user.get_awg_peers
        original_now = handlers_user.utc_now_naive
        handlers_user.get_awg_peers = fake_get_awg_peers
        handlers_user.utc_now_naive = lambda: datetime(2026, 3, 31, 12, 0, 0)
        try:
            lines = await handlers_user._build_user_device_activity_lines(601)
        finally:
            handlers_user.get_awg_peers = original_get_peers
            handlers_user.utc_now_naive = original_now

        self.assertEqual(len(lines), 2)
        self.assertIn("Устройство 1", lines[0])
        self.assertIn("активно недавно", lines[0])
        self.assertIn("Устройство 2", lines[1])
        self.assertIn("давно не подключалось", lines[1])

    async def test_user_device_traffic_block_lines(self):
        import handlers_user

        lines = await handlers_user._build_user_traffic_lines(601)

        self.assertIn("Устройство 1", lines[0])
        self.assertIn("↓ 1.0 GB", lines[0])
        self.assertIn("↑ 300 MB", lines[0])
        self.assertIn("Всего трафика", lines[-1])

    async def test_user_device_activity_no_runtime_data(self):
        import handlers_user

        async def fake_get_awg_peers():
            return []

        original_get_peers = handlers_user.get_awg_peers
        original_now = handlers_user.utc_now_naive
        handlers_user.get_awg_peers = fake_get_awg_peers
        handlers_user.utc_now_naive = lambda: datetime(2026, 3, 31, 12, 0, 0)
        try:
            lines = await handlers_user._build_user_device_activity_lines(601)
        finally:
            handlers_user.get_awg_peers = original_get_peers
            handlers_user.utc_now_naive = original_now

        self.assertEqual(len(lines), 2)
        self.assertIn("нет данных", lines[0])
        self.assertIn("нет данных", lines[1])

    async def test_user_device_activity_without_active_devices(self):
        import handlers_user

        lines = await handlers_user._build_user_device_activity_lines(602)
        traffic_lines = await handlers_user._build_user_traffic_lines(602)

        self.assertEqual(lines, ["• нет данных"])
        self.assertEqual(traffic_lines[-1], "• Всего трафика — 0 B")


if __name__ == "__main__":
    unittest.main()
