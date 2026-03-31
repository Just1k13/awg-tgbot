import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


class AdminManualRetryTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_manual_retry_action_available_only_for_repairable_case(self):
        import handlers_admin

        repairable = {"status": "needs_repair", "last_provision_status": "provisioning"}
        applied = {"status": "applied", "last_provision_status": "ready"}
        self.assertTrue(handlers_admin._is_retry_activation_relevant(repairable, has_keys=False))
        self.assertFalse(handlers_admin._is_retry_activation_relevant(applied, has_keys=True))

    async def test_manual_retry_succeeds_for_repairable_payment(self):
        import database
        import payments
        from helpers import utc_now_naive

        now = utc_now_naive()
        await database.execute(
            "INSERT INTO users (user_id, sub_until, created_at) VALUES (501, '0', ?)",
            (now.isoformat(),),
        )
        await database.execute(
            """
            INSERT INTO payments (telegram_payment_charge_id, user_id, payload, amount, currency, payment_method, status, last_provision_status, created_at, updated_at)
            VALUES ('manual_ok', 501, 'sub_7', 100, 'XTR', 'stars', 'needs_repair', 'provisioning', ?, ?)
            """,
            (now.isoformat(), now.isoformat()),
        )
        await database.execute(
            """
            INSERT INTO provisioning_jobs (payment_id, user_id, payload, status, created_at, updated_at)
            VALUES ('manual_ok', 501, 'sub_7', 'needs_repair', ?, ?)
            """,
            (now.isoformat(), now.isoformat()),
        )

        original_issue_subscription = payments.issue_subscription
        original_apply_ref = payments.apply_referral_rewards_on_first_payment
        original_notify_ref = payments.notify_inviter_about_referral_reward
        try:
            async def _fake_issue_subscription(_uid, days, operation_id=None):
                self.assertEqual(operation_id, "manual_ok")
                return utc_now_naive() + timedelta(days=days)

            async def _fake_apply(_uid, _payment_id):
                return False

            async def _fake_notify(_bot, _uid):
                return None

            payments.issue_subscription = _fake_issue_subscription
            payments.apply_referral_rewards_on_first_payment = _fake_apply
            payments.notify_inviter_about_referral_reward = _fake_notify
            result = await payments.manual_retry_activation("manual_ok")
        finally:
            payments.issue_subscription = original_issue_subscription
            payments.apply_referral_rewards_on_first_payment = original_apply_ref
            payments.notify_inviter_about_referral_reward = original_notify_ref

        self.assertEqual(result["result"], "succeeded")
        status = await database.get_payment_status("manual_ok")
        self.assertEqual(status, "applied")

    async def test_manual_retry_noop_for_missing_or_applied_case(self):
        import payments

        missing = await payments.manual_retry_activation("unknown_payment")
        self.assertEqual(missing["result"], "no_payment")

    async def test_manual_retry_does_not_duplicate_already_applied_case(self):
        import database
        import payments
        from helpers import utc_now_naive

        now = utc_now_naive().isoformat()
        await database.execute(
            "INSERT INTO users (user_id, sub_until, created_at) VALUES (777, ?, ?)",
            (now, now),
        )
        await database.execute(
            """
            INSERT INTO payments (telegram_payment_charge_id, user_id, payload, amount, currency, payment_method, status, last_provision_status, created_at, updated_at)
            VALUES ('manual_applied', 777, 'sub_30', 200, 'XTR', 'stars', 'applied', 'ready', ?, ?)
            """,
            (now, now),
        )
        await database.execute(
            """
            INSERT INTO provisioning_jobs (payment_id, user_id, payload, status, created_at, updated_at)
            VALUES ('manual_applied', 777, 'sub_30', 'applied', ?, ?)
            """,
            (now, now),
        )
        result = await payments.manual_retry_activation("manual_applied")
        self.assertEqual(result["result"], "already_applied")

    async def test_admin_retry_writes_audit_log(self):
        import database
        import handlers_admin

        answers = []

        class DummyMessage:
            async def answer(self, text, **kwargs):
                answers.append(text)

        class DummyCb:
            data = "admin_retry_activation_321_0"
            from_user = SimpleNamespace(id=1)
            message = DummyMessage()
            bot = SimpleNamespace()

            async def answer(self, *_args, **_kwargs):
                return None

        original_get_latest = handlers_admin.get_latest_user_payment_summary
        original_retry = handlers_admin.manual_retry_activation
        try:
            async def _fake_get_latest(_uid):
                return {"payment_id": "manual_audit"}

            async def _fake_retry(_payment_id, bot=None):
                return {"result": "succeeded", "message": "ok"}

            handlers_admin.get_latest_user_payment_summary = _fake_get_latest
            handlers_admin.manual_retry_activation = _fake_retry
            await handlers_admin.admin_retry_activation_btn(DummyCb())
        finally:
            handlers_admin.get_latest_user_payment_summary = original_get_latest
            handlers_admin.manual_retry_activation = original_retry

        audit_rows = await database.fetchall(
            "SELECT action FROM audit_log WHERE action LIKE 'manual_retry_%' ORDER BY id ASC"
        )
        actions = [row[0] for row in audit_rows]
        self.assertEqual(actions, ["manual_retry_requested", "manual_retry_succeeded"])
        self.assertTrue(any("Retry succeeded" in text for text in answers))


if __name__ == "__main__":
    unittest.main()
