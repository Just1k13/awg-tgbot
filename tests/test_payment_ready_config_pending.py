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


class PaymentReadyConfigPendingTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_applied_with_missing_config_does_not_show_ready(self):
        import payments

        class DummyPayment:
            invoice_payload = "sub_7"
            currency = "XTR"
            total_amount = payments.STARS_PRICE_7_DAYS
            telegram_payment_charge_id = "pay_cfg_missing"
            provider_payment_charge_id = "prov_cfg_missing"

        class DummyMessage:
            def __init__(self):
                self.successful_payment = DummyPayment()
                self.from_user = type("U", (), {"id": 4001, "username": "u", "first_name": "User"})()
                self.bot = object()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        async def fake_process(*args, **kwargs):
            return True

        async def fake_send_config(*args, **kwargs):
            return False

        original_process = payments.process_payment_provisioning
        original_send = payments._send_user_active_config
        try:
            payments.process_payment_provisioning = fake_process
            payments._send_user_active_config = fake_send_config
            msg = DummyMessage()
            await payments.success_pay(msg)  # type: ignore[arg-type]
        finally:
            payments.process_payment_provisioning = original_process
            payments._send_user_active_config = original_send

        self.assertIn("ключ ещё собирается", msg.answers[-1].lower())
        self.assertNotIn("доступ готов", msg.answers[-1].lower())

    async def test_applied_with_config_keeps_ready_message(self):
        import payments

        class DummyPayment:
            invoice_payload = "sub_7"
            currency = "XTR"
            total_amount = payments.STARS_PRICE_7_DAYS
            telegram_payment_charge_id = "pay_cfg_ready"
            provider_payment_charge_id = "prov_cfg_ready"

        class DummyMessage:
            def __init__(self):
                self.successful_payment = DummyPayment()
                self.from_user = type("U", (), {"id": 4002, "username": "u2", "first_name": "User"})()
                self.bot = object()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        async def fake_process(*args, **kwargs):
            return True

        async def fake_send_config(*args, **kwargs):
            return True

        original_process = payments.process_payment_provisioning
        original_send = payments._send_user_active_config
        try:
            payments.process_payment_provisioning = fake_process
            payments._send_user_active_config = fake_send_config
            msg = DummyMessage()
            await payments.success_pay(msg)  # type: ignore[arg-type]
        finally:
            payments.process_payment_provisioning = original_process
            payments._send_user_active_config = original_send

        self.assertIn("доступ готов", msg.answers[-1].lower())


if __name__ == "__main__":
    unittest.main()
