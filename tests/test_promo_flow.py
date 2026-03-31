import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")
os.environ.setdefault("AWG_HELPER_POLICY_PATH", str(ROOT / "tests" / "helper-policy.json"))


class PromoFlowTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_create_promo_code(self):
        import database

        created = await database.create_promo_code("WELCOME7", 7, 10, created_by=1)
        self.assertTrue(created)
        row = await database.fetchone(
            "SELECT code, bonus_days, max_activations, used_count, is_active FROM promo_codes WHERE code = ?",
            ("WELCOME7",),
        )
        self.assertEqual(row[0], "WELCOME7")
        self.assertEqual(row[1], 7)
        self.assertEqual(row[2], 10)
        self.assertEqual(row[3], 0)
        self.assertEqual(row[4], 1)

    async def test_activate_valid_promo_via_user_command(self):
        import database
        import handlers_user

        await database.create_promo_code("FRIEND14", 14, 2, created_by=1)

        class FakeMessage:
            def __init__(self):
                self.from_user = type("u", (), {"id": 2001, "username": "user2001", "first_name": "User"})()
                self.answers: list[str] = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        async def fake_issue_subscription(user_id, days, silent=False, operation_id=None):
            self.assertEqual(user_id, 2001)
            self.assertEqual(days, 14)
            self.assertEqual(operation_id, "promo-FRIEND14-2001")
            return datetime(2026, 4, 20, 10, 0, 0)

        msg = FakeMessage()
        original_issue = handlers_user.issue_subscription
        handlers_user.issue_subscription = fake_issue_subscription
        try:
            await handlers_user.promo_cmd(msg, type("C", (), {"args": "friend14"})())  # type: ignore[arg-type]
        finally:
            handlers_user.issue_subscription = original_issue

        self.assertIn("Промокод применён", msg.answers[-1])
        used_count = await database.fetchval("SELECT used_count FROM promo_codes WHERE code = ?", ("FRIEND14",), default=-1)
        self.assertEqual(used_count, 1)
        action = await database.fetchval(
            "SELECT action FROM audit_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (2001,),
            default="",
        )
        self.assertEqual(action, "promo_activated")

    async def test_activate_inactive_promo(self):
        import database

        await database.create_promo_code("OFF5", 5, 3, created_by=1)
        await database.disable_promo_code("OFF5")
        activation = await database.activate_promo_code(3001, "OFF5")
        self.assertEqual(activation["status"], "inactive")

    async def test_activate_exhausted_promo(self):
        import database

        await database.create_promo_code("ONE", 3, 1, created_by=1)
        first = await database.activate_promo_code(4001, "ONE")
        second = await database.activate_promo_code(4002, "ONE")
        self.assertEqual(first["status"], "reserved")
        self.assertEqual(second["status"], "exhausted")

    async def test_extend_active_subscription_correctly(self):
        import awg_backend
        import database

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
        original_used_ips = awg_backend.get_used_ips_from_awg
        awg_backend.generate_keypair = fake_keypair
        awg_backend.generate_psk = fake_psk
        awg_backend.add_peer_to_awg = fake_add_peer
        awg_backend.get_used_ips_from_awg = lambda: asyncio.sleep(0, result=set())  # type: ignore[assignment]
        try:
            first_until = await awg_backend.issue_subscription(5001, 7, operation_id="seed-sub")
            await database.create_promo_code("PLUS5", 5, 10, created_by=1)
            activation = await database.activate_promo_code(5001, "PLUS5")
            self.assertEqual(activation["status"], "reserved")
            promo_until = await awg_backend.issue_subscription(5001, 5, operation_id="promo-PLUS5-5001")
        finally:
            awg_backend.generate_keypair = original_keypair
            awg_backend.generate_psk = original_psk
            awg_backend.add_peer_to_awg = original_add
            awg_backend.get_used_ips_from_awg = original_used_ips

        self.assertGreaterEqual(promo_until - first_until, timedelta(days=5))

    async def test_failed_activation_is_audited(self):
        import database
        import handlers_user

        class FakeMessage:
            def __init__(self):
                self.from_user = type("u", (), {"id": 7001, "username": "u7001", "first_name": "Fail"})()
                self.answers: list[str] = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        msg = FakeMessage()
        await handlers_user.promo_cmd(msg, type("C", (), {"args": "missing"})())  # type: ignore[arg-type]
        self.assertIn("не найден", msg.answers[-1].lower())
        action = await database.fetchval(
            "SELECT action FROM audit_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (7001,),
            default="",
        )
        self.assertEqual(action, "promo_activation_failed")
