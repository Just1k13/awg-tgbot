import asyncio
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


class CriticalFlowsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        database.DB_PATH = self.db_path
        await database.close_shared_db()
        await database.init_db()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_ip_reservation_counts_pending(self):
        import database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (1, '0', '2026-01-01T00:00:00')")
            await db.execute(
                "INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state) VALUES (1, 1, 'pending:test', '', '10.8.1.10', '2026-01-01T00:00:00', 'pending')"
            )
            await db.commit()
        finally:
            await db.close()

        reserved = await database.get_reserved_ips_from_db()
        self.assertIn(10, reserved)

    async def test_payment_recovery_worker_repairs_job(self):
        import database, payments

        await database.save_payment(
            telegram_payment_charge_id="tg_1",
            provider_payment_charge_id="prov_1",
            user_id=123,
            payload="sub_7",
            amount=1,
            currency="XTR",
            payment_method="stars",
            status="received",
            raw_payload_json="{}",
        )

        async def fake_issue_subscription(user_id, days, silent=False, operation_id=None):
            from datetime import datetime
            return datetime.fromisoformat("2026-04-01T00:00:00")

        original_issue = payments.issue_subscription
        payments.issue_subscription = fake_issue_subscription
        try:
            repaired = await payments.payment_recovery_worker()
        finally:
            payments.issue_subscription = original_issue

        self.assertEqual(repaired, 1)
        status = await database.get_payment_status("tg_1")
        self.assertEqual(status, "applied")

    async def test_safe_delete_stops_on_awg_failure(self):
        import awg_backend, database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (50, '0', '2026-01-01T00:00:00')")
            await db.execute(
                "INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state) VALUES (50, 1, 'pub-1', '', '10.8.1.51', '2026-01-01T00:00:00', 'active')"
            )
            await db.commit()
        finally:
            await db.close()

        async def fail_remove(_):
            raise RuntimeError("awg down")

        original_remove = awg_backend.remove_peer_from_awg
        awg_backend.remove_peer_from_awg = fail_remove
        try:
            with self.assertRaises(RuntimeError):
                await awg_backend.delete_user_everywhere(50)
        finally:
            awg_backend.remove_peer_from_awg = original_remove

        row = await database.fetchone("SELECT COUNT(*) FROM users WHERE user_id = 50")
        self.assertEqual(row[0], 1)

    async def test_orphan_cleanup_quarantine_only_without_force(self):
        import awg_backend

        async def fake_orphans():
            return [{"public_key": "orphan-1", "ip": "10.8.1.9"}]

        protected_calls = []

        async def fake_add_protected(pub, reason):
            protected_calls.append((pub, reason))

        async def fail_remove(_):
            raise AssertionError("remove should not be called in non-force mode")

        original_get = awg_backend.get_orphan_awg_peers
        original_add = awg_backend.add_protected_peer
        original_remove = awg_backend.remove_peer_from_awg
        awg_backend.get_orphan_awg_peers = fake_orphans
        awg_backend.add_protected_peer = fake_add_protected
        awg_backend.remove_peer_from_awg = fail_remove
        try:
            removed = await awg_backend.clean_orphan_awg_peers(force=False)
        finally:
            awg_backend.get_orphan_awg_peers = original_get
            awg_backend.add_protected_peer = original_add
            awg_backend.remove_peer_from_awg = original_remove

        self.assertEqual(removed, 0)
        self.assertEqual(protected_calls, [("orphan-1", "orphan-quarantine")])

    async def test_issue_subscription_promotes_pending_keys_to_active(self):
        import awg_backend, database

        await database.ensure_user_exists(700)

        async def fake_generate_keypair():
            fake_generate_keypair.i += 1
            return (f"priv-{fake_generate_keypair.i}", "A" * 43 + chr(65 + fake_generate_keypair.i))

        fake_generate_keypair.i = 0

        async def fake_generate_psk():
            return "psk-1"

        async def fake_add_peer(_public_key, _ip, _psk):
            return None

        async def fake_used_ips():
            return set()

        original_generate_keypair = awg_backend.generate_keypair
        original_generate_psk = awg_backend.generate_psk
        original_add_peer = awg_backend.add_peer_to_awg
        original_used_ips = awg_backend.get_used_ips_from_awg
        awg_backend.generate_keypair = fake_generate_keypair
        awg_backend.generate_psk = fake_generate_psk
        awg_backend.add_peer_to_awg = fake_add_peer
        awg_backend.get_used_ips_from_awg = fake_used_ips
        try:
            await awg_backend.issue_subscription(700, 30)
        finally:
            awg_backend.generate_keypair = original_generate_keypair
            awg_backend.generate_psk = original_generate_psk
            awg_backend.add_peer_to_awg = original_add_peer
            awg_backend.get_used_ips_from_awg = original_used_ips

        rows = await database.fetchall("SELECT state FROM keys WHERE user_id = 700")
        self.assertTrue(rows)
        self.assertTrue(all(state == "active" for (state,) in rows))


if __name__ == "__main__":
    unittest.main()
