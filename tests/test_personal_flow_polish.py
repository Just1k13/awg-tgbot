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
os.environ.setdefault("SUPPORT_USERNAME", "@support_test")


class PersonalFlowPolishTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_profile_screen_template_has_practical_next_step_fields(self):
        import content_settings

        template = content_settings.TEXT_DEFAULTS["profile_screen"]
        self.assertIn("{connection_status}", template)
        self.assertIn("{next_step}", template)
        self.assertIn("{support_line}", template)

    async def test_referral_text_explains_first_payment_rule(self):
        import content_settings

        referral = content_settings.TEXT_DEFAULTS["referral_screen"]
        self.assertIn("первый доступ", referral.lower())
        self.assertIn("начисляется бонус", referral.lower())

    async def test_profile_keyboard_has_activation_status_short_label(self):
        from keyboards import get_profile_inline_kb

        kb = get_profile_inline_kb(subscription_active=False)
        labels = [button.text for row in kb.inline_keyboard for button in row]
        self.assertIn("⏱ Статус активации", labels)

    async def test_admin_user_manage_keyboard_has_refresh(self):
        import handlers_admin

        kb = handlers_admin._user_manage_kb(101, 0)
        labels = [button.text for row in kb.inline_keyboard for button in row]
        self.assertIn("🔄 Обновить карточку", labels)
        self.assertIn("⬅️ К списку", labels)

    async def test_admin_operator_step_hint_for_stuck_payment(self):
        import handlers_admin

        hint = handlers_admin._operator_next_step("stuck_manual", "stuck_manual", False)
        self.assertIn("investigate", hint)

    async def test_broadcast_prepare_shows_recipients_and_trimmed_preview(self):
        import database
        import handlers_admin

        await database.ensure_user_exists(1)
        await database.ensure_user_exists(2)

        long_text = "A" * 550

        class DummyMessage:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        msg = DummyMessage()
        await handlers_admin.broadcast_prepare(msg, type("C", (), {"args": long_text})())  # type: ignore[arg-type]

        self.assertTrue(msg.answers)
        out = msg.answers[-1]
        self.assertIn("Получателей", out)
        self.assertIn("<b>2</b>", out)
        self.assertIn("…", out)


if __name__ == "__main__":
    unittest.main()
