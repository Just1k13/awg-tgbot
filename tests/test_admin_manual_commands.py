import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")


class AdminManualCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_keyboard_has_manual_commands_entrypoint(self):
        from keyboards import get_admin_inline_kb

        kb = get_admin_inline_kb()
        labels = [button.text for row in kb.inline_keyboard for button in row]
        self.assertIn("⌨️ Команды", labels)

    async def test_manual_commands_text_contains_expected_and_excludes_legacy(self):
        import handlers_admin

        text = handlers_admin.build_admin_manual_commands_text()
        self.assertIn("/health", text)
        self.assertIn("/sync_awg", text)
        self.assertIn("/stats", text)
        self.assertIn("/audit", text)
        self.assertIn("/send TEXT", text)
        self.assertIn("/give USER_ID DAYS", text)

        self.assertNotIn("/set_rate", text)
        self.assertNotIn("/rate", text)
        self.assertNotIn("/text_set", text)
        self.assertNotIn("/setting_set", text)
        self.assertNotIn("/clean_orphans_force", text)
        self.assertNotIn("/force_delete", text)

    async def test_manual_commands_callback_returns_text_and_back_button(self):
        import handlers_admin
        from ui_constants import CB_ADMIN_BACK_MAIN

        answers: list[dict] = []
        callback_answers: list[str] = []

        class DummyMessage:
            async def answer(self, text, **kwargs):
                answers.append({"text": text, "kwargs": kwargs})

        class DummyCb:
            data = "admin_manual_commands"
            from_user = SimpleNamespace(id=1)
            message = DummyMessage()

            async def answer(self, text, **kwargs):
                callback_answers.append(text)

        await handlers_admin.admin_manual_commands(DummyCb())

        self.assertEqual(len(answers), 1)
        self.assertIn("Ручные admin-команды", answers[0]["text"])
        markup = answers[0]["kwargs"]["reply_markup"]
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data, CB_ADMIN_BACK_MAIN)
        self.assertIn("Готово", callback_answers[-1])


if __name__ == "__main__":
    unittest.main()
