import base64
import json
import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


def _decode_vpn_payload(vpn_key: str) -> dict:
    encoded = vpn_key.removeprefix("vpn://")
    padding = "=" * (-len(encoded) % 4)
    blob = base64.urlsafe_b64decode(encoded + padding)
    payload_len = int.from_bytes(blob[:4], "big")
    data = zlib.decompress(blob[4:])
    assert len(data) == payload_len
    return json.loads(data.decode("utf-8"))


class VpnPayloadDeviceNameTests(unittest.IsolatedAsyncioTestCase):
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

    def test_payload_description_includes_device_num_1(self):
        import awg_backend

        original_name = awg_backend.SERVER_NAME
        awg_backend.SERVER_NAME = "Poland by just1kbot"
        try:
            payload = awg_backend.build_vpn_payload("priv", "pub", "10.8.1.11", "psk", device_num=1)
        finally:
            awg_backend.SERVER_NAME = original_name

        self.assertEqual(payload["description"], "Poland by just1kbot #1")

    def test_payload_description_includes_device_num_2(self):
        import awg_backend

        original_name = awg_backend.SERVER_NAME
        awg_backend.SERVER_NAME = "Poland by just1kbot"
        try:
            payload = awg_backend.build_vpn_payload("priv", "pub", "10.8.1.12", "psk", device_num=2)
        finally:
            awg_backend.SERVER_NAME = original_name

        self.assertEqual(payload["description"], "Poland by just1kbot #2")

    def test_payload_description_fallback_without_device_num(self):
        import awg_backend

        original_name = awg_backend.SERVER_NAME
        awg_backend.SERVER_NAME = "Poland by just1kbot"
        try:
            payload = awg_backend.build_vpn_payload("priv", "pub", "10.8.1.13", "psk", device_num=None)
        finally:
            awg_backend.SERVER_NAME = original_name

        self.assertEqual(payload["description"], "Poland by just1kbot")

    async def test_get_user_keys_flow_emits_numbered_vpn_profile_name(self):
        import database
        from security_utils import encrypt_text

        db = await database.open_db()
        try:
            await db.execute(
                "INSERT INTO users (user_id, sub_until, created_at) VALUES (?, ?, ?)",
                (900, "2099-01-01T00:00:00", "2026-01-01T00:00:00"),
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, psk_key, client_private_key)
                VALUES (?, ?, ?, '', ?, '2026-01-01T00:00:00', 'active', ?, ?)
                """,
                (900, 2, "PUB_900_2", "10.8.1.92", encrypt_text("psk-900"), encrypt_text("priv-900")),
            )
            await db.commit()
        finally:
            await db.close()

        rows = await database.get_user_keys(900)
        self.assertEqual(len(rows), 1)
        _, device_num, _config_text, vpn_key = rows[0]
        self.assertEqual(device_num, 2)

        payload = _decode_vpn_payload(vpn_key)
        self.assertTrue(payload["description"].endswith(" #2"))


if __name__ == "__main__":
    unittest.main()
