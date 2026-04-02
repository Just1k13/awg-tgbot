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


class AdminFindpayFlowTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_findpay_exact_match_returns_summary_and_user_card_button(self):
        import database
        import handlers_admin

        await database.ensure_user_exists(42, "alice", "Alice")
        await database.save_payment(
            telegram_payment_charge_id="tg_charge_42",
            provider_payment_charge_id="prov_42",
            user_id=42,
            payload="plan_30",
            amount=199,
            currency="XTR",
            payment_method="telegram_stars",
            status="applied",
        )

        class DummyMsg:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append((text, kwargs))

        msg = DummyMsg()
        await handlers_admin.findpay_cmd(msg, type("C", (), {"args": "  tg_charge_42  "})())  # type: ignore[arg-type]

        self.assertEqual(len(msg.answers), 1)
        text, kwargs = msg.answers[0]
        self.assertIn("Платёж найден", text)
        self.assertIn("user_id: <code>42</code>", text)
        self.assertIn("telegram_payment_charge_id: <code>tg_charge_42</code>", text)
        self.assertIn("status: <b>applied</b>", text)
        self.assertIn("amount: <b>199 XTR</b>", text)
        self.assertIn("payload: <code>plan_30</code>", text)
        markup = kwargs.get("reply_markup")
        self.assertIsNotNone(markup)
        self.assertEqual(markup.inline_keyboard[0][0].text, "Открыть карточку пользователя")
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "admin_manage_user_42_0")

    async def test_findpay_not_found_returns_short_message(self):
        import handlers_admin

        class DummyMsg:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append((text, kwargs))

        msg = DummyMsg()
        await handlers_admin.findpay_cmd(msg, type("C", (), {"args": "missing_charge"})())  # type: ignore[arg-type]

        self.assertTrue(msg.answers)
        self.assertIn("не найден", msg.answers[-1][0])

    async def test_findpay_button_uses_existing_user_card_flow(self):
        import database
        import handlers_admin

        await database.ensure_user_exists(77, "bob", "Bob")
        await database.save_payment(
            telegram_payment_charge_id="tg_charge_77",
            provider_payment_charge_id="prov_77",
            user_id=77,
            payload="plan_7",
            amount=99,
            currency="XTR",
            payment_method="telegram_stars",
            status="received",
        )

        class DummyMsg:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append((text, kwargs))

        msg = DummyMsg()
        await handlers_admin.findpay_cmd(msg, type("C", (), {"args": "tg_charge_77"})())  # type: ignore[arg-type]
        callback_data = msg.answers[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data

        seen: list[tuple[int, int]] = []
        original_send = handlers_admin._send_user_manage_card

        async def fake_send_user_manage_card(_message, uid: int, page: int):
            seen.append((uid, page))

        handlers_admin._send_user_manage_card = fake_send_user_manage_card
        try:
            class DummyCb:
                def __init__(self, data: str):
                    self.data = data
                    self.from_user = type("U", (), {"id": 1})()
                    self.message = DummyMsg()

                async def answer(self, *_args, **_kwargs):
                    return None

            await handlers_admin.admin_manage_user(DummyCb(callback_data))  # type: ignore[arg-type]
        finally:
            handlers_admin._send_user_manage_card = original_send

        self.assertEqual(seen, [(77, 0)])

    async def test_non_admin_is_rejected_by_admin_filter_for_findpay(self):
        import handlers_admin

        is_admin = handlers_admin.IsAdmin()

        class DummyMsg:
            from_user = type("U", (), {"id": 999})()

        allowed = await is_admin(DummyMsg())
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
