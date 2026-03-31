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


class AdminDeviceManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self.tmp.name) / "test.db")
        await database.close_shared_db()
        await database.init_db()

        from security_utils import encrypt_text

        db = await database.open_db()
        try:
            await db.execute("INSERT INTO users (user_id, sub_until, created_at) VALUES (700, '2099-01-01T00:00:00', '2026-01-01T00:00:00')")
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, psk_key, client_private_key)
                VALUES (?, ?, ?, '', ?, '2026-01-01T00:00:00', 'active', ?, ?)
                """,
                (700, 1, "OLD_DEVICE_1", "10.8.1.11", encrypt_text("old-psk-1"), encrypt_text("old-private-1")),
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, psk_key, client_private_key)
                VALUES (?, ?, ?, '', ?, '2026-01-01T00:00:00', 'active', ?, ?)
                """,
                (700, 2, "OLD_DEVICE_2", "10.8.1.12", encrypt_text("old-psk-2"), encrypt_text("old-private-2")),
            )
            await db.commit()
        finally:
            await db.close()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_delete_single_device_success_and_idempotent_runtime_absent(self):
        import awg_backend
        import database

        removed = []
        qos_clears = []

        async def fake_remove_peer(public_key: str):
            removed.append(public_key)
            if public_key == "OLD_DEVICE_1":
                raise RuntimeError("already absent")

        async def fake_peers():
            return [{"public_key": "OLD_DEVICE_2", "ip": "10.8.1.12", "latest_handshake_at": None}]

        async def fake_qos_clear(_run, ip, user_id):
            qos_clears.append((ip, user_id))

        original_remove = awg_backend.remove_peer_from_awg
        original_peers = awg_backend.get_awg_peers
        original_qos_clear = awg_backend.qos_clear
        awg_backend.remove_peer_from_awg = fake_remove_peer
        awg_backend.get_awg_peers = fake_peers
        awg_backend.qos_clear = fake_qos_clear
        try:
            result = await awg_backend.delete_user_device(700, 1)
        finally:
            awg_backend.remove_peer_from_awg = original_remove
            awg_backend.get_awg_peers = original_peers
            awg_backend.qos_clear = original_qos_clear

        self.assertEqual(result["status"], "deleted")
        self.assertTrue(result["removed_runtime"])
        self.assertIn("OLD_DEVICE_1", removed)
        self.assertEqual(qos_clears, [])

        row_deleted = await database.fetchone("SELECT state FROM keys WHERE user_id = 700 AND device_num = 1")
        row_other = await database.fetchone("SELECT state, public_key FROM keys WHERE user_id = 700 AND device_num = 2")
        self.assertEqual(row_deleted[0], "deleted")
        self.assertEqual(row_other[0], "active")
        self.assertEqual(row_other[1], "OLD_DEVICE_2")

        audit_rows = await database.fetchall("SELECT action FROM audit_log WHERE user_id = 700 ORDER BY id DESC LIMIT 5")
        actions = {row[0] for row in audit_rows}
        self.assertIn("delete_user_device", actions)

    async def test_reissue_single_device_success_and_other_devices_untouched(self):
        import awg_backend
        import database

        removed = []
        added = []

        async def fake_remove_peer(public_key: str):
            removed.append(public_key)

        async def fake_add_peer(public_key: str, ip: str, _psk: str):
            added.append((public_key, ip))

        async def fake_generate_keypair():
            return "new-private", "NEW_DEVICE_1"

        async def fake_generate_psk():
            return "new-psk"

        async def fake_add_protected(_public_key: str, _reason: str):
            return None

        original_remove = awg_backend.remove_peer_from_awg
        original_add = awg_backend.add_peer_to_awg
        original_gen = awg_backend.generate_keypair
        original_psk = awg_backend.generate_psk
        original_add_protected = awg_backend.add_protected_peer
        awg_backend.remove_peer_from_awg = fake_remove_peer
        awg_backend.add_peer_to_awg = fake_add_peer
        awg_backend.generate_keypair = fake_generate_keypair
        awg_backend.generate_psk = fake_generate_psk
        awg_backend.add_protected_peer = fake_add_protected
        try:
            result = await awg_backend.reissue_user_device(700, 1)
        finally:
            awg_backend.remove_peer_from_awg = original_remove
            awg_backend.add_peer_to_awg = original_add
            awg_backend.generate_keypair = original_gen
            awg_backend.generate_psk = original_psk
            awg_backend.add_protected_peer = original_add_protected

        self.assertEqual(result["status"], "reissued")
        self.assertEqual(removed, ["OLD_DEVICE_1"])
        self.assertEqual(added, [("NEW_DEVICE_1", "10.8.1.11")])

        row_reissued = await database.fetchone("SELECT device_num, public_key, state FROM keys WHERE user_id = 700 AND device_num = 1")
        row_other = await database.fetchone("SELECT device_num, public_key, state FROM keys WHERE user_id = 700 AND device_num = 2")
        self.assertEqual((row_reissued[0], row_reissued[1], row_reissued[2]), (1, "NEW_DEVICE_1", "active"))
        self.assertEqual((row_other[0], row_other[1], row_other[2]), (2, "OLD_DEVICE_2", "active"))

        audit_rows = await database.fetchall("SELECT action FROM audit_log WHERE user_id = 700 ORDER BY id DESC LIMIT 5")
        actions = {row[0] for row in audit_rows}
        self.assertIn("reissue_user_device", actions)


if __name__ == "__main__":
    unittest.main()
