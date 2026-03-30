import asyncio
import base64
import hashlib
import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet

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

    async def test_delete_user_everywhere_retry_from_delete_pending(self):
        import awg_backend, database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (77, '0', '2026-01-01T00:00:00')")
            await db.execute(
                "INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state) VALUES (77, 1, 'pub-retry', '', '10.8.1.77', '2026-01-01T00:00:00', 'delete_pending')"
            )
            await db.commit()
        finally:
            await db.close()

        state = {"attempt": 0}

        async def flaky_remove(pub):
            state["attempt"] += 1
            if state["attempt"] == 1:
                raise RuntimeError("temporary failure")

        async def peers_with_key():
            return [{"public_key": "pub-retry", "ip": "10.8.1.77"}]

        original_remove = awg_backend.remove_peer_from_awg
        original_peers = awg_backend.get_awg_peers
        awg_backend.remove_peer_from_awg = flaky_remove
        awg_backend.get_awg_peers = peers_with_key
        try:
            with self.assertRaises(RuntimeError):
                await awg_backend.delete_user_everywhere(77)
            row = await database.fetchone("SELECT COUNT(*) FROM users WHERE user_id = 77")
            self.assertEqual(row[0], 1)

            awg_backend.get_awg_peers = lambda: asyncio.sleep(0, result=[])  # type: ignore[assignment]
            removed, _ = await awg_backend.delete_user_everywhere(77)
            self.assertEqual(removed, 1)
            row = await database.fetchone("SELECT COUNT(*) FROM users WHERE user_id = 77")
            self.assertEqual(row[0], 0)
        finally:
            awg_backend.remove_peer_from_awg = original_remove
            awg_backend.get_awg_peers = original_peers

    async def test_save_payment_is_atomic_with_job(self):
        import database

        real_open_db = database.open_db

        class FailingConn:
            def __init__(self, inner):
                self._inner = inner

            async def execute(self, sql, params=()):
                if "INSERT INTO provisioning_jobs" in sql:
                    raise RuntimeError("inject failure")
                return await self._inner.execute(sql, params)

            async def commit(self):
                return await self._inner.commit()

            async def rollback(self):
                return await self._inner.rollback()

            async def close(self):
                return await self._inner.close()

        async def failing_open_db():
            return FailingConn(await real_open_db())

        database.open_db = failing_open_db  # type: ignore[assignment]
        try:
            with self.assertRaises(RuntimeError):
                await database.save_payment(
                    telegram_payment_charge_id="tg_atomic",
                    provider_payment_charge_id="prov_atomic",
                    user_id=123,
                    payload="sub_7",
                    amount=1,
                    currency="XTR",
                    payment_method="stars",
                    status="received",
                    raw_payload_json="{}",
                )
        finally:
            database.open_db = real_open_db  # type: ignore[assignment]
        row = await database.fetchone("SELECT COUNT(*) FROM payments WHERE telegram_payment_charge_id='tg_atomic'")
        self.assertEqual(row[0], 0)

    async def test_decrypt_backward_compatibility_v1(self):
        import security_utils

        secret = os.environ["ENCRYPTION_SECRET"]
        legacy_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        legacy_token = Fernet(legacy_key).encrypt(b"legacy-value").decode("utf-8")
        encrypted = f"enc:v1:{legacy_token}"
        self.assertEqual(security_utils.decrypt_text(encrypted), "legacy-value")
        new_value = security_utils.encrypt_text("new-value")
        self.assertTrue(new_value.startswith("enc:v2:"))
        self.assertEqual(security_utils.decrypt_text(new_value), "new-value")

    async def test_reconciliation_activates_pending_key_if_peer_exists(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (700, '0', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (700, 1, 'PENDINGPUB=', '', '10.8.1.70', ?, 'pending')
                """,
                ((awg_backend.utc_now_naive() - timedelta(minutes=1)).isoformat(),),
            )
            await db.commit()
        finally:
            await db.close()

        original_peers = awg_backend.get_awg_peers
        awg_backend.get_awg_peers = lambda: asyncio.sleep(0, result=[{"public_key": "PENDINGPUB=", "ip": "10.8.1.70"}])  # type: ignore[assignment]
        try:
            stats = await awg_backend.reconcile_pending_awg_state()
        finally:
            awg_backend.get_awg_peers = original_peers
        self.assertEqual(stats["activated"], 1)
        row = await database.fetchone("SELECT state FROM keys WHERE user_id=700 AND device_num=1")
        self.assertEqual(row[0], "active")

    async def test_reconciliation_finishes_delete_pending_when_peer_absent(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (701, '2026-06-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, delete_reason)
                VALUES (701, 1, 'DELETEPUB=', '', '10.8.1.71', '2026-01-01T00:00:00', 'delete_pending', 'user_delete')
                """
            )
            await db.commit()
        finally:
            await db.close()
        original_peers = awg_backend.get_awg_peers
        awg_backend.get_awg_peers = lambda: asyncio.sleep(0, result=[])  # type: ignore[assignment]
        try:
            stats = await awg_backend.reconcile_pending_awg_state()
        finally:
            awg_backend.get_awg_peers = original_peers
        self.assertEqual(stats["deleted"], 1)
        row = await database.fetchone("SELECT COUNT(*) FROM keys WHERE user_id=701")
        self.assertEqual(row[0], 0)

    async def test_reconciliation_does_not_finalize_user_if_active_key_exists(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (702, '2026-06-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, delete_reason)
                VALUES (702, 1, 'DELETE-702', '', '10.8.1.72', '2026-01-01T00:00:00', 'delete_pending', 'user_delete')
                """
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (702, 2, 'ACTIVE-702', '', '10.8.1.73', '2026-01-01T00:00:00', 'active')
                """
            )
            await db.commit()
        finally:
            await db.close()
        original_peers = awg_backend.get_awg_peers
        awg_backend.get_awg_peers = lambda: asyncio.sleep(0, result=[])  # type: ignore[assignment]
        try:
            await awg_backend.reconcile_pending_awg_state()
        finally:
            awg_backend.get_awg_peers = original_peers
        count_keys = await database.fetchone("SELECT COUNT(*) FROM keys WHERE user_id=702")
        self.assertEqual(count_keys[0], 2)
        sub = await database.fetchone("SELECT sub_until FROM users WHERE user_id=702")
        self.assertEqual(sub[0], "2026-06-01T00:00:00")

    async def test_reconciliation_does_not_finalize_with_manual_repair_state(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (703, '2026-06-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, delete_reason)
                VALUES (703, 1, 'REVOKE-703', '', '10.8.1.74', '2026-01-01T00:00:00', 'revoke_pending', 'revoke_expired_or_admin')
                """
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, delete_reason)
                VALUES (703, 2, 'MANUAL-703', '', '10.8.1.75', '2026-01-01T00:00:00', 'needs_manual_repair', 'pending_stuck')
                """
            )
            await db.commit()
        finally:
            await db.close()
        original_peers = awg_backend.get_awg_peers
        awg_backend.get_awg_peers = lambda: asyncio.sleep(0, result=[])  # type: ignore[assignment]
        try:
            await awg_backend.reconcile_pending_awg_state()
        finally:
            awg_backend.get_awg_peers = original_peers
        sub = await database.fetchone("SELECT sub_until FROM users WHERE user_id=703")
        self.assertEqual(sub[0], "2026-06-01T00:00:00")
        rows = await database.fetchall("SELECT state FROM keys WHERE user_id=703 ORDER BY device_num")
        self.assertEqual([r[0] for r in rows], ["deleted", "needs_manual_repair"])

    async def test_reconciliation_mixed_partial_cleanup_keeps_data(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (704, '2026-06-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, delete_reason)
                VALUES (704, 1, 'DELETE-704', '', '10.8.1.76', '2026-01-01T00:00:00', 'delete_pending', 'user_delete')
                """
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (704, 2, 'PENDING-704', '', '10.8.1.77', '2026-01-01T00:00:00', 'pending')
                """
            )
            await db.commit()
        finally:
            await db.close()
        original_peers = awg_backend.get_awg_peers
        awg_backend.get_awg_peers = lambda: asyncio.sleep(0, result=[])  # type: ignore[assignment]
        try:
            await awg_backend.reconcile_pending_awg_state()
        finally:
            awg_backend.get_awg_peers = original_peers
        count_keys = await database.fetchone("SELECT COUNT(*) FROM keys WHERE user_id=704")
        self.assertEqual(count_keys[0], 2)
        sub = await database.fetchone("SELECT sub_until FROM users WHERE user_id=704")
        self.assertEqual(sub[0], "2026-06-01T00:00:00")
        rows = await database.fetchall("SELECT state FROM keys WHERE user_id=704 ORDER BY device_num")
        self.assertEqual([r[0] for r in rows], ["deleted", "needs_manual_repair"])

    async def test_decrypt_log_does_not_leak_ciphertext(self):
        import security_utils

        leaked = {"msg": ""}
        original = security_utils.logger.error

        def fake_error(msg, *args, **kwargs):
            leaked["msg"] = msg % args if args else msg

        security_utils.logger.error = fake_error  # type: ignore[assignment]
        try:
            with self.assertRaises(RuntimeError):
                security_utils.decrypt_text("enc:v2:invalid:tokenvalue")
        finally:
            security_utils.logger.error = original  # type: ignore[assignment]
        self.assertNotIn("tokenvalue", leaked["msg"])

    async def test_config_import_without_autodetect_side_effects(self):
        import subprocess

        os.environ["CONFIG_AUTODETECT_ON_IMPORT"] = "0"
        for key, value in {
            "API_TOKEN": "123:test",
            "ADMIN_ID": "1",
            "SERVER_PUBLIC_KEY": "A" * 44,
            "SERVER_IP": "1.1.1.1:51820",
            "ENCRYPTION_SECRET": "test-secret",
        }.items():
            os.environ[key] = value

        original_run = subprocess.run

        def guarded_run(*args, **kwargs):
            raise AssertionError("subprocess.run should not be called during config import")

        subprocess.run = guarded_run  # type: ignore[assignment]
        try:
            if "config" in sys.modules:
                del sys.modules["config"]
            importlib.import_module("config")
        finally:
            subprocess.run = original_run  # type: ignore[assignment]

    async def test_parse_awg_show_output(self):
        import awg_backend

        sample = """
interface: awg0
  peer: PUBKEY1
    allowed ips: 10.8.1.11/32, fd00::/128
peer: PUBKEY2
  allowed ips: 10.8.1.12/32
"""
        peers = awg_backend.parse_awg_show_output(sample)
        self.assertEqual(peers[0]["public_key"], "PUBKEY1")
        self.assertEqual(peers[0]["ip"], "10.8.1.11")
        self.assertEqual(peers[1]["public_key"], "PUBKEY2")
        self.assertEqual(peers[1]["ip"], "10.8.1.12")

    async def test_parse_awg_show_output_handles_non_32_networks(self):
        import awg_backend

        sample = """
interface: awg0
peer: PUBKEY_A
  allowed ips: 10.8.1.11/32, 10.8.1.0/24
peer: PUBKEY_B
  allowed ips: 10.8.1.0/24, 10.8.1.12/32
"""
        peers = awg_backend.parse_awg_show_output(sample)
        self.assertEqual(peers[0]["ip"], "10.8.1.11")
        self.assertEqual(peers[1]["ip"], "10.8.1.12")

    async def test_reserved_ips_ignore_deleted_but_keep_delete_pending(self):
        import database

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (201, '0', '2026-01-01T00:00:00')")
            await db.execute(
                "INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state) VALUES (201, 1, 'k-del', '', '10.8.1.31', '2026-01-01T00:00:00', 'deleted')"
            )
            await db.execute(
                "INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state) VALUES (201, 2, 'k-pending-del', '', '10.8.1.32', '2026-01-01T00:00:00', 'delete_pending')"
            )
            await db.commit()
        finally:
            await db.close()

        reserved = await database.get_reserved_ips_from_db()
        self.assertNotIn(31, reserved)
        self.assertIn(32, reserved)

    async def test_reconcile_active_awg_state_restores_missing_peer(self):
        import awg_backend
        import database

        enc_psk = awg_backend.encrypt_text("psk-value")
        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (301, '2099-01-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, psk_key, client_private_key)
                VALUES (301, 1, ?, '', '10.8.1.33', '2026-01-01T00:00:00', 'active', ?, ?)
                """,
                ("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", enc_psk, awg_backend.encrypt_text("priv")),
            )
            await db.commit()
        finally:
            await db.close()

        restored: list[tuple[str, str, str]] = []

        async def fake_get_awg_peers():
            return []

        async def fake_add_peer(public_key, ip, psk):
            restored.append((public_key, ip, psk))

        async def fake_qos_set(*args, **kwargs):
            return None

        original_get = awg_backend.get_awg_peers
        original_add = awg_backend.add_peer_to_awg
        original_qos = awg_backend.qos_set
        awg_backend.get_awg_peers = fake_get_awg_peers
        awg_backend.add_peer_to_awg = fake_add_peer
        awg_backend.qos_set = fake_qos_set
        try:
            stats = await awg_backend.reconcile_active_awg_state()
        finally:
            awg_backend.get_awg_peers = original_get
            awg_backend.add_peer_to_awg = original_add
            awg_backend.qos_set = original_qos

        self.assertEqual(stats["restored"], 1)
        self.assertEqual(restored[0][1], "10.8.1.33")
        self.assertEqual(restored[0][2], "psk-value")

    async def test_build_client_config_keeps_i2_i5_even_without_i1(self):
        import awg_backend

        original_values = (
            awg_backend.AWG_I1,
            awg_backend.AWG_I2,
            awg_backend.AWG_I3,
            awg_backend.AWG_I4,
            awg_backend.AWG_I5,
        )
        awg_backend.AWG_I1 = ""
        awg_backend.AWG_I2 = "should-appear-2"
        awg_backend.AWG_I3 = "should-appear-3"
        awg_backend.AWG_I4 = "should-appear-4"
        awg_backend.AWG_I5 = "should-appear-5"
        try:
            cfg = awg_backend.build_client_config("priv", "10.8.1.10", "psk")
        finally:
            (
                awg_backend.AWG_I1,
                awg_backend.AWG_I2,
                awg_backend.AWG_I3,
                awg_backend.AWG_I4,
                awg_backend.AWG_I5,
            ) = original_values
        self.assertNotIn("I1 =", cfg)
        self.assertIn("I2 = should-appear-2", cfg)
        self.assertIn("I3 = should-appear-3", cfg)
        self.assertIn("I4 = should-appear-4", cfg)
        self.assertIn("I5 = should-appear-5", cfg)

    async def test_force_cleanup_candidates_limited_to_quarantine_managed_not_in_db(self):
        import awg_backend

        async def fake_get_awg_peers():
            return [
                {"public_key": "candidate-ok", "ip": "10.8.1.10"},
                {"public_key": "in-db", "ip": "10.8.1.11"},
                {"public_key": "not-owned", "ip": "10.8.1.14"},
                {"public_key": "missing-ip", "ip": None},
                {"public_key": "outside-range", "ip": "192.168.1.10"},
                {"public_key": "not-quarantined", "ip": "10.8.1.12"},
                {"public_key": "ignored", "ip": "10.8.1.13"},
            ]

        async def fake_db_keys():
            return {"in-db"}

        async def fake_bot_managed_keys():
            return {"candidate-ok", "in-db", "missing-ip", "outside-range", "ignored"}

        async def fake_quarantined():
            return {"candidate-ok", "in-db", "missing-ip", "outside-range", "ignored", "not-owned"}

        original_get = awg_backend.get_awg_peers
        original_db = awg_backend.get_valid_db_public_keys
        original_bot_managed = awg_backend.get_bot_managed_known_public_keys
        original_quarantined = awg_backend._get_quarantined_public_keys
        original_ignore = awg_backend.IGNORE_PEERS
        awg_backend.get_awg_peers = fake_get_awg_peers
        awg_backend.get_valid_db_public_keys = fake_db_keys
        awg_backend.get_bot_managed_known_public_keys = fake_bot_managed_keys
        awg_backend._get_quarantined_public_keys = fake_quarantined
        awg_backend.IGNORE_PEERS = set(original_ignore) | {"ignored"}
        try:
            candidates = await awg_backend.list_orphan_delete_candidates_force()
        finally:
            awg_backend.get_awg_peers = original_get
            awg_backend.get_valid_db_public_keys = original_db
            awg_backend.get_bot_managed_known_public_keys = original_bot_managed
            awg_backend._get_quarantined_public_keys = original_quarantined
            awg_backend.IGNORE_PEERS = original_ignore
        self.assertEqual(candidates, [{"public_key": "candidate-ok", "ip": "10.8.1.10"}])

    async def test_get_orphan_awg_peers_requires_bot_managed_ownership(self):
        import awg_backend

        async def fake_get_awg_peers():
            return [
                {"public_key": "managed-orphan", "ip": "10.8.1.30"},
                {"public_key": "foreign-orphan", "ip": "10.8.1.31"},
            ]

        original_get = awg_backend.get_awg_peers
        original_db = awg_backend.get_valid_db_public_keys
        original_bot_managed = awg_backend.get_bot_managed_known_public_keys
        original_protected = awg_backend.get_protected_public_keys
        awg_backend.get_awg_peers = fake_get_awg_peers
        awg_backend.get_valid_db_public_keys = lambda: asyncio.sleep(0, result=set())  # type: ignore[assignment]
        awg_backend.get_bot_managed_known_public_keys = lambda: asyncio.sleep(0, result={"managed-orphan"})  # type: ignore[assignment]
        awg_backend.get_protected_public_keys = lambda: asyncio.sleep(0, result=set())  # type: ignore[assignment]
        try:
            orphans = await awg_backend.get_orphan_awg_peers()
        finally:
            awg_backend.get_awg_peers = original_get
            awg_backend.get_valid_db_public_keys = original_db
            awg_backend.get_bot_managed_known_public_keys = original_bot_managed
            awg_backend.get_protected_public_keys = original_protected
        self.assertEqual(orphans, [{"public_key": "managed-orphan", "ip": "10.8.1.30"}])

    async def test_validate_helper_policy_fails_on_permission_error(self):
        import config_validate

        class DummyLogger:
            def __init__(self):
                self.messages = []

            def error(self, msg, *args):
                self.messages.append(msg % args if args else msg)

            def warning(self, msg, *args):
                self.messages.append(msg % args if args else msg)

        logger = DummyLogger()
        with (
            patch.object(config_validate.Path, "exists", return_value=True),
            patch.object(config_validate.Path, "is_symlink", return_value=False),
            patch.object(config_validate.Path, "read_text", side_effect=PermissionError("denied")),
        ):
            with self.assertRaises(RuntimeError):
                config_validate.validate_helper_policy(
                    policy_path="/etc/awg-bot-helper.json",
                    docker_container="amnezia-awg",
                    wg_interface="awg0",
                    logger=logger,
                )


class InstallerAndHelperHardeningTests(unittest.TestCase):
    def _extract_shell_function(self, script: str, fn_name: str) -> str:
        marker = f"{fn_name}() {{"
        start = script.find(marker)
        self.assertNotEqual(start, -1, f"function {fn_name} not found")
        end = script.find("\n}\n", start)
        self.assertNotEqual(end, -1, f"function {fn_name} end not found")
        return script[start:end]

    def test_installer_menu_safe_fails_without_tty(self):
        if os.geteuid() != 0:
            self.skipTest("requires root to pass installer preflight")
        result = subprocess.run(
            ["bash", "awg-tgbot.sh"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Интерактивное меню требует TTY", result.stdout)
        self.assertIn("stdin pipe", result.stdout)


    def test_status_action_works_without_tty(self):
        if os.geteuid() != 0:
            self.skipTest("requires root to pass installer preflight")
        result = subprocess.run(
            ["bash", "awg-tgbot.sh", "status"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Проект:", result.stdout)

    def test_installer_tty_check_uses_tty_fd3(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertIn('has_tty() { [[ -t 3 ]]; }', script)

    def test_installer_remove_default_safe_fails_without_tty(self):
        if os.geteuid() != 0:
            self.skipTest("requires root to pass installer preflight")
        result = subprocess.run(
            ["bash", "awg-tgbot.sh", "remove-default"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Невозможно запросить ввод без TTY", result.stdout)

    def test_installer_removes_bot_from_docker_group(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertIn('gpasswd -d "$BOT_USER" docker', script)
        self.assertIn('не в группе docker', script)
        self.assertIn('install -o root -g "$BOT_USER" -m 640 "$tmp" "$AWG_HELPER_POLICY"', script)

    def test_installer_preserves_existing_db_path_on_update(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertIn('db_path="$(get_env_value DB_PATH)"', script)
        self.assertIn('if [[ -n "$db_path" ]]; then', script)
        self.assertIn('set_env_value DB_PATH "$db_path"', script)

    def test_installer_update_requires_pinned_sha(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertIn('Безопасное обновление требует pinned commit SHA', script)
        self.assertIn('Небезопасный update по mutable ветке отключён', script)
        self.assertIn('requested_ref="${REPO_UPDATE_REF:-}"', script)
        self.assertIn('if ! is_full_sha "$requested_ref"; then', script)
        self.assertIn('tmp_dir="$(download_repo "$requested_ref")"', script)

    def test_installer_update_has_no_implicit_target_fallback_from_state(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertNotIn("UPDATE_REF_FILE=", script)
        self.assertNotIn('UPDATE_REF="${REPO_UPDATE_REF:-$(cat', script)
        self.assertIn('printf \'%s\\n\' "$requested_ref" > "$VERSION_FILE"', script)

    def test_installer_update_explicit_noop_when_target_equals_current(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ -n "$local_sha" && "$requested_ref" == "$local_sha" ]]; then', script)
        self.assertIn('Запрошенный SHA уже установлен', script)

    def test_installer_update_does_not_gate_explicit_requested_sha_by_branch_status(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        update_body = self._extract_shell_function(script, "update_bot")
        self.assertIn('requested_ref="${REPO_UPDATE_REF:-}"', update_body)
        self.assertIn('if [[ -n "$local_sha" && "$requested_ref" == "$local_sha" ]]; then', update_body)
        self.assertNotIn('if [[ "$UPDATE_STATUS" == "current" ]]; then', update_body)

    def test_installer_update_has_rollback_path_after_deploy(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertIn("create_update_backup()", script)
        self.assertIn("rollback_update_backup()", script)
        update_body = self._extract_shell_function(script, "update_bot")
        self.assertIn('update_backup_dir="$(create_update_backup)"', update_body)
        self.assertIn('rollback_update_backup "$update_backup_dir"', update_body)

    def test_clean_orphans_command_does_not_promise_physical_delete(self):
        admin_handler = (ROOT / "bot" / "handlers_admin.py").read_text(encoding="utf-8")
        self.assertIn("quarantine", admin_handler)
        self.assertNotIn("Будет удалено: <b>{len(orphans)}</b>", admin_handler)

    def test_helper_rejects_invalid_policy_json(self):
        import awg_helper

        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.json"
            policy_path.write_text("not-json", encoding="utf-8")
            fake_mode = stat.S_IFREG | 0o640
            with patch.object(awg_helper, "POLICY_PATH", policy_path), patch.object(Path, "lstat") as mock_lstat:
                mock_lstat.return_value = os.stat_result((fake_mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
                with self.assertRaises(RuntimeError):
                    awg_helper._load_policy()

    def test_helper_rejects_group_writable_policy(self):
        import awg_helper

        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.json"
            policy_path.write_text(json.dumps({"container": "allowed-c", "interface": "awg0"}), encoding="utf-8")
            fake_mode = stat.S_IFREG | 0o666
            with patch.object(awg_helper, "POLICY_PATH", policy_path), patch.object(Path, "lstat") as mock_lstat:
                mock_lstat.return_value = os.stat_result((fake_mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
                with self.assertRaises(RuntimeError):
                    awg_helper._load_policy()

    def test_helper_rejects_symlink_policy(self):
        import awg_helper

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            policy_path = Path(tmp) / "policy.json"
            target.write_text(json.dumps({"container": "allowed-c", "interface": "awg0"}), encoding="utf-8")
            policy_path.symlink_to(target)
            with patch.object(awg_helper, "POLICY_PATH", policy_path):
                with self.assertRaises(RuntimeError):
                    awg_helper._load_policy()

    def test_validate_helper_policy_mismatch_is_hard_failure(self):
        import config_validate

        class DummyLogger:
            def error(self, *_args, **_kwargs):
                return None

            def warning(self, *_args, **_kwargs):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.json"
            policy_path.write_text(json.dumps({"container": "amnezia-awg2", "interface": "awg0"}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                config_validate.validate_helper_policy(
                    policy_path=str(policy_path),
                    docker_container="different-container",
                    wg_interface="awg0",
                    logger=DummyLogger(),
                )

    def test_helper_parser_has_no_external_target_args(self):
        import awg_helper

        parser = awg_helper.build_parser()
        add_peer = parser._subparsers._group_actions[0].choices["add-peer"]
        arg_dests = {action.dest for action in add_peer._actions}
        self.assertNotIn("container", arg_dests)
        self.assertNotIn("interface", arg_dests)
        self.assertIn("qos-set", parser._subparsers._group_actions[0].choices)
        self.assertIn("qos-clear", parser._subparsers._group_actions[0].choices)
        self.assertIn("qos-sync", parser._subparsers._group_actions[0].choices)
        self.assertIn("denylist-sync", parser._subparsers._group_actions[0].choices)

    def test_helper_public_key_validation_requires_real_base64_wireguard_key(self):
        import awg_helper

        valid = base64.b64encode(b"k" * 32).decode("ascii")
        self.assertEqual(awg_helper._safe_public_key(valid), valid)
        with self.assertRaises(ValueError):
            awg_helper._safe_public_key("A" * 44)
        with self.assertRaises(ValueError):
            awg_helper._safe_public_key("AA==")

    def test_command_exists_does_not_invoke_shell(self):
        import config_detect

        with patch.object(config_detect.shutil, "which", return_value=None) as mock_which:
            result = config_detect.command_exists("docker;rm -rf /")
        self.assertFalse(result)
        mock_which.assert_called_once_with("docker;rm -rf /")

    def test_content_settings_placeholder_validation(self):
        import asyncio
        from content_settings import validate_text_template

        ok, err = asyncio.run(validate_text_template("support_contact", "Contact: {support_username}"))
        self.assertTrue(ok)
        self.assertEqual(err, "")
        ok2, err2 = asyncio.run(validate_text_template("support_contact", "Contact: nope"))
        self.assertFalse(ok2)
        self.assertIn("missing placeholders", err2)

    def test_network_policy_parsing(self):
        from network_policy import parse_cidrs

        parsed = parse_cidrs("10.0.0.1/32, 10.10.0.0/16")
        self.assertEqual(parsed, ["10.0.0.1/32", "10.10.0.0/16"])

    def test_denylist_soft_mode_does_not_raise(self):
        import asyncio
        import network_policy

        async def fake_get_setting(key, cast=None):
            values = {
                "EGRESS_DENYLIST_ENABLED": "1",
                "EGRESS_DENYLIST_MODE": "soft",
                "EGRESS_DENYLIST_DOMAINS": "",
                "EGRESS_DENYLIST_CIDRS": "10.10.0.0/16",
                "VPN_SUBNET_PREFIX": "10.8.1.",
            }
            return cast(values[key]) if cast else values[key]

        async def fail_run(*args, **kwargs):
            raise RuntimeError("nft fail")

        original_get_setting = network_policy.get_setting
        original_inc = network_policy.increment_metric
        original_set = network_policy.set_metric
        network_policy.get_setting = fake_get_setting
        network_policy.increment_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        network_policy.set_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        try:
            asyncio.run(network_policy.denylist_sync(fail_run))
        finally:
            network_policy.get_setting = original_get_setting
            network_policy.increment_metric = original_inc
            network_policy.set_metric = original_set

    def test_denylist_strict_mode_raises(self):
        import asyncio
        import network_policy

        async def fake_get_setting(key, cast=None):
            values = {
                "EGRESS_DENYLIST_ENABLED": "1",
                "EGRESS_DENYLIST_MODE": "strict",
                "EGRESS_DENYLIST_DOMAINS": "",
                "EGRESS_DENYLIST_CIDRS": "10.10.0.0/16",
                "VPN_SUBNET_PREFIX": "10.8.1.",
            }
            return cast(values[key]) if cast else values[key]

        async def fail_run(*args, **kwargs):
            raise RuntimeError("nft fail")

        original_get_setting = network_policy.get_setting
        original_inc = network_policy.increment_metric
        original_set = network_policy.set_metric
        network_policy.get_setting = fake_get_setting
        network_policy.increment_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        network_policy.set_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        try:
            with self.assertRaises(RuntimeError):
                asyncio.run(network_policy.denylist_sync(fail_run))
        finally:
            network_policy.get_setting = original_get_setting
            network_policy.increment_metric = original_inc
            network_policy.set_metric = original_set

    def test_denylist_soft_mode_dns_error_does_not_raise(self):
        import asyncio
        import network_policy

        async def fake_get_setting(key, cast=None):
            values = {
                "EGRESS_DENYLIST_ENABLED": "1",
                "EGRESS_DENYLIST_MODE": "soft",
                "EGRESS_DENYLIST_DOMAINS": "bad_domain",
                "EGRESS_DENYLIST_CIDRS": "",
                "VPN_SUBNET_PREFIX": "10.8.1.",
            }
            return cast(values[key]) if cast else values[key]

        original_get_setting = network_policy.get_setting
        original_resolve = network_policy.resolve_domains
        original_inc = network_policy.increment_metric
        original_set = network_policy.set_metric
        network_policy.get_setting = fake_get_setting
        network_policy.resolve_domains = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dns fail"))  # type: ignore[assignment]
        network_policy.increment_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        network_policy.set_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        try:
            asyncio.run(network_policy.denylist_sync(lambda *_args, **_kwargs: asyncio.sleep(0)))
        finally:
            network_policy.get_setting = original_get_setting
            network_policy.resolve_domains = original_resolve
            network_policy.increment_metric = original_inc
            network_policy.set_metric = original_set

    def test_denylist_strict_mode_parse_error_raises(self):
        import asyncio
        import network_policy

        async def fake_get_setting(key, cast=None):
            values = {
                "EGRESS_DENYLIST_ENABLED": "1",
                "EGRESS_DENYLIST_MODE": "strict",
                "EGRESS_DENYLIST_DOMAINS": "",
                "EGRESS_DENYLIST_CIDRS": "bad-cidr",
                "VPN_SUBNET_PREFIX": "10.8.1.",
            }
            return cast(values[key]) if cast else values[key]

        original_get_setting = network_policy.get_setting
        original_inc = network_policy.increment_metric
        original_set = network_policy.set_metric
        network_policy.get_setting = fake_get_setting
        network_policy.increment_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        network_policy.set_metric = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[assignment]
        try:
            with self.assertRaises(Exception):
                asyncio.run(network_policy.denylist_sync(lambda *_args, **_kwargs: asyncio.sleep(0)))
        finally:
            network_policy.get_setting = original_get_setting
            network_policy.increment_metric = original_inc
            network_policy.set_metric = original_set

    def test_domain_to_ascii_converts_idn(self):
        import network_policy

        self.assertEqual(network_policy._domain_to_ascii("госуслуги.рф"), "xn--c1aapkosapc.xn--p1ai")
        self.assertEqual(network_policy._domain_to_ascii("gosuslugi.ru"), "gosuslugi.ru")

    def test_qos_rate_zero_means_unlimited_override(self):
        import network_policy
        self.assertEqual(asyncio.run(network_policy.qos_rate_for_key(0)), 0)

    def test_awg_settings_validation_rejects_invalid_numeric_ranges(self):
        import config_validate

        with self.assertRaises(RuntimeError):
            config_validate.validate_awg_obfuscation_settings(
                awg_jc="5",
                awg_jmin="800",
                awg_jmax="200",
                awg_s1="1",
                awg_s2="2",
                awg_s3="3",
                awg_s4="4",
                awg_h1="1-2",
                awg_h2="1-2",
                awg_h3="1-2",
                awg_h4="1-2",
                awg_i1="",
                awg_i2="112233",
                awg_i3="",
                awg_i4="",
                awg_i5="",
            )
        with self.assertRaises(RuntimeError):
            config_validate.validate_awg_obfuscation_settings(
                awg_jc="x",
                awg_jmin="0",
                awg_jmax="0",
                awg_s1="1",
                awg_s2="2",
                awg_s3="3",
                awg_s4="4",
                awg_h1="1-2",
                awg_h2="1-2",
                awg_h3="1-2",
                awg_h4="1-2",
                awg_i1="",
                awg_i2="",
                awg_i3="",
                awg_i4="",
                awg_i5="",
            )

    def test_awg_settings_validation_allows_i2_without_i1(self):
        import config_validate

        config_validate.validate_awg_obfuscation_settings(
            awg_jc="6",
            awg_jmin="10",
            awg_jmax="50",
            awg_s1="37",
            awg_s2="98",
            awg_s3="47",
            awg_s4="14",
            awg_h1="1486401722-1692300209",
            awg_h2="1696990121-1817276760",
            awg_h3="1841833217-1995591429",
            awg_h4="2109962185-2145796739",
            awg_i1="",
            awg_i2="112233",
            awg_i3="",
            awg_i4="",
            awg_i5="",
        )

        with self.assertRaises(RuntimeError):
            config_validate.validate_awg_obfuscation_settings(
                awg_jc="6",
                awg_jmin="10",
                awg_jmax="50",
                awg_s1="70000",
                awg_s2="98",
                awg_s3="47",
                awg_s4="14",
                awg_h1="1486401722-1692300209",
                awg_h2="1696990121-1817276760",
                awg_h3="1841833217-1995591429",
                awg_h4="2109962185-2145796739",
                awg_i1="",
                awg_i2="",
                awg_i3="",
                awg_i4="",
                awg_i5="",
            )
        with self.assertRaises(RuntimeError):
            config_validate.validate_awg_obfuscation_settings(
                awg_jc="6",
                awg_jmin="10",
                awg_jmax="50",
                awg_s1="37",
                awg_s2="98",
                awg_s3="47",
                awg_s4="14",
                awg_h1="bad",
                awg_h2="1696990121-1817276760",
                awg_h3="1841833217-1995591429",
                awg_h4="2109962185-2145796739",
                awg_i1="",
                awg_i2="",
                awg_i3="",
                awg_i4="",
                awg_i5="",
            )

    def test_keepalive_validation(self):
        import config_validate

        self.assertEqual(config_validate.validate_persistent_keepalive("off"), "0")
        self.assertEqual(config_validate.validate_persistent_keepalive("25"), "25")
        with self.assertRaises(RuntimeError):
            config_validate.validate_persistent_keepalive("-1")
        with self.assertRaises(RuntimeError):
            config_validate.validate_persistent_keepalive("70000")

    def test_client_allowed_ips_validation(self):
        import config_validate

        self.assertEqual(
            config_validate.validate_client_allowed_ips("0.0.0.0/0, ::/0"),
            "0.0.0.0/0, ::/0",
        )
        with self.assertRaises(RuntimeError):
            config_validate.validate_client_allowed_ips("not-a-cidr")


if __name__ == "__main__":
    unittest.main()
