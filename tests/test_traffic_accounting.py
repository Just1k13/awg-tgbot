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


class TrafficAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self.tmp.name) / "test.db")
        await database.close_shared_db()
        await database.init_db()

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (700, '2099-01-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (700, 1, 'PUBKEY1', '', '10.8.1.11', '2026-01-01T00:00:00', 'active')
                """
            )
            await db.commit()
        finally:
            await db.close()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_db_migration_adds_traffic_columns(self):
        import database

        db = await database.open_db()
        try:
            async with db.execute("PRAGMA table_info(keys)") as cursor:
                cols = {row[1] for row in await cursor.fetchall()}
        finally:
            await db.close()

        self.assertIn("rx_bytes_total", cols)
        self.assertIn("tx_bytes_total", cols)
        self.assertIn("rx_bytes_last", cols)
        self.assertIn("tx_bytes_last", cols)
        self.assertIn("traffic_updated_at", cols)

    async def test_delta_accumulation_and_reset_behavior(self):
        import database

        touched = await database.sync_traffic_counters_from_runtime_peers(
            [{"public_key": "PUBKEY1", "rx_bytes": 1000, "tx_bytes": 500}]
        )
        self.assertEqual(touched, 1)

        row = await database.fetchone(
            "SELECT rx_bytes_total, tx_bytes_total, rx_bytes_last, tx_bytes_last FROM keys WHERE user_id = 700 AND device_num = 1"
        )
        self.assertEqual((row[0], row[1]), (0, 0))
        self.assertEqual((row[2], row[3]), (1000, 500))

        await database.sync_traffic_counters_from_runtime_peers(
            [{"public_key": "PUBKEY1", "rx_bytes": 1900, "tx_bytes": 1200}]
        )
        row = await database.fetchone(
            "SELECT rx_bytes_total, tx_bytes_total, rx_bytes_last, tx_bytes_last FROM keys WHERE user_id = 700 AND device_num = 1"
        )
        self.assertEqual((row[0], row[1]), (900, 700))

        await database.sync_traffic_counters_from_runtime_peers(
            [{"public_key": "PUBKEY1", "rx_bytes": 200, "tx_bytes": 300}]
        )
        row = await database.fetchone(
            "SELECT rx_bytes_total, tx_bytes_total, rx_bytes_last, tx_bytes_last FROM keys WHERE user_id = 700 AND device_num = 1"
        )
        self.assertEqual((row[0], row[1]), (900, 700))
        self.assertEqual((row[2], row[3]), (200, 300))

    async def test_missing_runtime_peer_keeps_totals(self):
        import database

        await database.execute(
            "UPDATE keys SET rx_bytes_total = 500, tx_bytes_total = 250, rx_bytes_last = 100, tx_bytes_last = 50 WHERE user_id = 700 AND device_num = 1"
        )
        await database.sync_traffic_counters_from_runtime_peers([])
        row = await database.fetchone(
            "SELECT rx_bytes_total, tx_bytes_total FROM keys WHERE user_id = 700 AND device_num = 1"
        )
        self.assertEqual((row[0], row[1]), (500, 250))


class TrafficParsingAndFormattingTests(unittest.TestCase):
    def test_parser_extracts_transfer_counters(self):
        import awg_backend

        sample = """
interface: awg0
peer: PUBKEY1
  latest handshake: 10 minutes ago
  transfer: 1.5 MiB received, 512 KiB sent
  allowed ips: 10.8.1.11/32
"""
        peers = awg_backend.parse_awg_show_output(sample)
        self.assertEqual(peers[0]["rx_bytes"], 1572864)
        self.assertEqual(peers[0]["tx_bytes"], 524288)

    def test_byte_formatter(self):
        from traffic import format_bytes_compact

        self.assertEqual(format_bytes_compact(0), "0 B")
        self.assertEqual(format_bytes_compact(950), "950 B")
        self.assertEqual(format_bytes_compact(12 * 1024 + 400), "12.4 KB")
        self.assertEqual(format_bytes_compact(532 * 1024 * 1024), "532 MB")


if __name__ == "__main__":
    unittest.main()
