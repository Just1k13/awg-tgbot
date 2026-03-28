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


class InstallerAndHelperHardeningTests(unittest.TestCase):
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

    def test_installer_preserves_existing_db_path_on_update(self):
        script = (ROOT / "awg-tgbot.sh").read_text(encoding="utf-8")
        self.assertIn('db_path="$(get_env_value DB_PATH)"', script)
        self.assertIn('if [[ -n "$db_path" ]]; then', script)
        self.assertIn('set_env_value DB_PATH "$db_path"', script)

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

    def test_helper_parser_has_no_external_target_args(self):
        import awg_helper

        parser = awg_helper.build_parser()
        add_peer = parser._subparsers._group_actions[0].choices["add-peer"]
        arg_dests = {action.dest for action in add_peer._actions}
        self.assertNotIn("container", arg_dests)
        self.assertNotIn("interface", arg_dests)


if __name__ == "__main__":
    unittest.main()
