import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")
os.environ.setdefault("AWG_HELPER_POLICY_PATH", str(ROOT / "tests" / "helper-policy.json"))

(Path(ROOT) / "tests" / "helper-policy.json").write_text(
    '{"container":"amnezia-awg2","interface":"awg0"}',
    encoding="utf-8",
)


class BetaBlockersTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_retry_delay_persisted_to_job(self):
        import database
        import payments

        await database.save_payment(
            telegram_payment_charge_id="tg_retry",
            provider_payment_charge_id="prov_retry",
            user_id=100,
            payload="sub_7",
            amount=1,
            currency="XTR",
            payment_method="stars",
            status="received",
            raw_payload_json="{}",
        )

        async def fail_issue_subscription(*args, **kwargs):
            raise RuntimeError("awg temporary down")

        original_issue = payments.issue_subscription
        payments.issue_subscription = fail_issue_subscription
        try:
            with self.assertRaises(RuntimeError):
                await payments.process_payment_provisioning("tg_retry", 100, "sub_7", 7)
        finally:
            payments.issue_subscription = original_issue

        row = await database.fetchone(
            "SELECT status, next_retry_at, lease_expires_at FROM provisioning_jobs WHERE payment_id = ?",
            ("tg_retry",),
        )
        self.assertEqual(row[0], "needs_repair")
        self.assertIsNotNone(row[1])
        self.assertIsNone(row[2])

    async def test_stale_provisioning_job_is_recoverable(self):
        import database
        import payments

        await database.save_payment(
            telegram_payment_charge_id="tg_stale",
            provider_payment_charge_id="prov_stale",
            user_id=200,
            payload="sub_7",
            amount=1,
            currency="XTR",
            payment_method="stars",
            status="received",
            raw_payload_json="{}",
        )
        past = (payments.utc_now_naive() - timedelta(minutes=10)).isoformat()
        await database.update_payment_status("tg_stale", "provisioning")
        db = await database.open_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE provisioning_jobs
                SET status='provisioning', lock_token='stale-lock', lease_expires_at=?, updated_at=?
                WHERE payment_id=?
                """,
                (past, past, "tg_stale"),
            )
            await db.commit()
        finally:
            await db.close()

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
        status = await database.get_payment_status("tg_stale")
        self.assertEqual(status, "applied")

    async def test_revoke_only_if_expired_does_not_touch_renewed_user(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute(
                "INSERT INTO users (user_id, sub_until, created_at) VALUES (300, ?, '2026-01-01T00:00:00')",
                ((awg_backend.utc_now_naive() + timedelta(days=5)).isoformat(),),
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (300, 1, ?, '', '10.8.1.30', '2026-01-01T00:00:00', 'active')
                """,
                ("B" * 44,),
            )
            await db.commit()
        finally:
            await db.close()

        called = {"remove": 0}

        async def fail_remove(_):
            called["remove"] += 1
            raise AssertionError("remove should not be called for renewed user")

        original_remove = awg_backend.remove_peer_from_awg
        awg_backend.remove_peer_from_awg = fail_remove
        try:
            removed = await awg_backend.revoke_user_access(300, only_if_expired=True)
        finally:
            awg_backend.remove_peer_from_awg = original_remove

        self.assertEqual(removed, 0)
        self.assertEqual(called["remove"], 0)

    async def test_issue_subscription_reuses_deleted_slot_and_creates_missing_key(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute(
                "INSERT INTO users (user_id, sub_until, created_at) VALUES (400, '0', '2026-01-01T00:00:00')"
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (400, 1, ?, '', '10.8.1.40', '2026-01-01T00:00:00', 'deleted')
                """,
                ("C" * 44,),
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, client_private_key, psk_key)
                VALUES (400, 2, ?, '', '10.8.1.41', '2026-01-01T00:00:00', 'active', 'plain', 'plain')
                """,
                ("D" * 44,),
            )
            await db.commit()
        finally:
            await db.close()

        state = {"idx": 0}

        async def fake_keypair():
            state["idx"] += 1
            suffix = str(state["idx"]).rjust(43, "K")
            return "priv-key", suffix + "="

        async def fake_psk():
            return "psk-key"

        async def fake_add_peer(*args, **kwargs):
            return None

        original_keypair = awg_backend.generate_keypair
        original_psk = awg_backend.generate_psk
        original_add = awg_backend.add_peer_to_awg
        awg_backend.generate_keypair = fake_keypair
        awg_backend.generate_psk = fake_psk
        awg_backend.add_peer_to_awg = fake_add_peer
        try:
            new_until = await awg_backend.issue_subscription(400, 7, operation_id="test-op")
        finally:
            awg_backend.generate_keypair = original_keypair
            awg_backend.generate_psk = original_psk
            awg_backend.add_peer_to_awg = original_add

        self.assertIsNotNone(new_until)
        rows = await database.fetchall(
            "SELECT device_num, state FROM keys WHERE user_id = ? ORDER BY device_num",
            (400,),
        )
        active_device_nums = [device_num for device_num, state in rows if state == "active"]
        self.assertIn(1, active_device_nums)
        self.assertIn(2, active_device_nums)


    async def test_issue_subscription_operation_id_is_idempotent(self):
        import awg_backend
        import database

        state = {"idx": 0}

        async def fake_keypair():
            state["idx"] += 1
            suffix = str(state["idx"]).rjust(43, "E")
            return "priv-key", suffix + "="

        async def fake_psk():
            return "psk-key"

        async def fake_add_peer(*args, **kwargs):
            return None

        original_keypair = awg_backend.generate_keypair
        original_psk = awg_backend.generate_psk
        original_add = awg_backend.add_peer_to_awg
        awg_backend.generate_keypair = fake_keypair
        awg_backend.generate_psk = fake_psk
        awg_backend.add_peer_to_awg = fake_add_peer
        try:
            first_until = await awg_backend.issue_subscription(500, 7, operation_id="same-op")
            second_until = await awg_backend.issue_subscription(500, 7, operation_id="same-op")
        finally:
            awg_backend.generate_keypair = original_keypair
            awg_backend.generate_psk = original_psk
            awg_backend.add_peer_to_awg = original_add

        self.assertEqual(first_until.isoformat(), second_until.isoformat())
        row = await database.fetchone("SELECT sub_until FROM users WHERE user_id = ?", (500,))
        self.assertEqual(row[0], first_until.isoformat())
        op = await database.fetchone(
            "SELECT status, new_until FROM subscription_operations WHERE operation_id = ?",
            ("same-op",),
        )
        self.assertEqual(op[0], "applied")
        self.assertEqual(op[1], first_until.isoformat())


if __name__ == "__main__":
    unittest.main()
