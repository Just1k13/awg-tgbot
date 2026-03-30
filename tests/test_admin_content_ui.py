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


class DummyUser:
    def __init__(self, user_id: int):
        self.id = user_id


class DummyMessage:
    def __init__(self):
        self.sent = []
        self.text = ""
        self.from_user = DummyUser(1)

    async def answer(self, text, **kwargs):
        self.sent.append((text, kwargs))




class DummyNoUserMessage:
    def __init__(self):
        self.from_user = None


class DummyCallback:
    def __init__(self, user_id: int, data: str):
        self.from_user = DummyUser(user_id)
        self.data = data
        self.message = DummyMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class AdminContentUiTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_pending_edit_filter_only_matches_when_state_exists(self):
        import database
        import handlers_admin

        msg = DummyMessage()
        msg.from_user = DummyUser(1)

        filt = handlers_admin.HasPendingAdminEdit()
        self.assertFalse(await filt(msg))

        await database.set_pending_admin_action(1, "edit_text", {"key": "start", "started_at": "2026-01-01T00:00:00"})
        self.assertTrue(await filt(msg))

        await database.clear_pending_admin_action(1, "edit_text")
        self.assertFalse(await filt(msg))

        self.assertFalse(await filt(DummyNoUserMessage()))

    async def test_admin_only_access_for_texts_menu(self):
        import handlers_admin
        from ui_constants import CB_ADMIN_TEXTS

        cb = DummyCallback(2, CB_ADMIN_TEXTS)
        await handlers_admin.admin_texts_menu(cb)
        self.assertEqual(cb.answers[-1], ("Нет доступа", True))

    async def test_open_texts_and_settings_screens(self):
        import handlers_admin
        from ui_constants import CB_ADMIN_SETTINGS, CB_ADMIN_TEXTS

        cb_texts = DummyCallback(1, CB_ADMIN_TEXTS)
        await handlers_admin.admin_texts_menu(cb_texts)
        self.assertIn("Тексты", cb_texts.message.sent[-1][0])

        cb_settings = DummyCallback(1, CB_ADMIN_SETTINGS)
        await handlers_admin.admin_settings_menu(cb_settings)
        self.assertIn("Настройки", cb_settings.message.sent[-1][0])

    async def test_select_text_key_and_setting_key(self):
        import handlers_admin
        from ui_constants import CB_ADMIN_SETTING_KEY_PREFIX, CB_ADMIN_TEXT_KEY_PREFIX

        cb_text = DummyCallback(1, f"{CB_ADMIN_TEXT_KEY_PREFIX}0_0")
        await handlers_admin.admin_text_key_detail(cb_text)
        self.assertIn("Карточка текста", cb_text.message.sent[-1][0])

        cb_setting = DummyCallback(1, f"{CB_ADMIN_SETTING_KEY_PREFIX}0_0")
        await handlers_admin.admin_setting_key_detail(cb_setting)
        self.assertIn("Карточка настройки", cb_setting.message.sent[-1][0])

    async def test_successful_text_update(self):
        import database
        import handlers_admin
        from ui_constants import CB_ADMIN_TEXT_EDIT_PREFIX

        key = handlers_admin._chunk_keys(handlers_admin._all_text_keys(), 0)[0][0]
        idx = 0
        cb = DummyCallback(1, f"{CB_ADMIN_TEXT_EDIT_PREFIX}{idx}_0")
        await handlers_admin.admin_text_edit_start(cb)

        msg = DummyMessage()
        msg.from_user = DummyUser(1)
        msg.text = "Новый start текст"
        await handlers_admin.admin_pending_edit_consumer(msg)

        value = await database.get_text_override(key)
        self.assertEqual(value, "Новый start текст")

    async def test_failed_placeholder_validation_does_not_save(self):
        import database
        import handlers_admin
        from ui_constants import CB_ADMIN_TEXT_EDIT_PREFIX

        keys = handlers_admin._all_text_keys()
        idx = keys.index("support_unavailable") if "support_unavailable" in keys else 0
        # force validation path with required placeholder key
        await database.set_pending_admin_action(1, "edit_text", {"key": "support_contact", "index": idx, "page": 0})

        msg = DummyMessage()
        msg.from_user = DummyUser(1)
        msg.text = "без плейсхолдера"
        await handlers_admin.admin_pending_edit_consumer(msg)

        value = await database.get_text_override("support_contact")
        self.assertIsNone(value)

    async def test_reset_text_works(self):
        import database
        import handlers_admin
        from ui_constants import CB_ADMIN_TEXT_RESET_PREFIX

        key = handlers_admin._chunk_keys(handlers_admin._all_text_keys(), 0)[0][0]
        await database.set_text_override(key, "custom", updated_by=1)
        idx = 0
        cb = DummyCallback(1, f"{CB_ADMIN_TEXT_RESET_PREFIX}{idx}_0")
        await handlers_admin.admin_text_reset_btn(cb)
        value = await database.get_text_override(key)
        self.assertIsNone(value)

    async def test_successful_setting_update(self):
        import database
        import handlers_admin
        from ui_constants import CB_ADMIN_SETTING_EDIT_PREFIX

        idx = handlers_admin._all_setting_keys().index("REFERRAL_ENABLED")
        cb = DummyCallback(1, f"{CB_ADMIN_SETTING_EDIT_PREFIX}{idx}_0")
        await handlers_admin.admin_setting_edit_start(cb)

        msg = DummyMessage()
        msg.from_user = DummyUser(1)
        msg.text = "0"
        await handlers_admin.admin_pending_edit_consumer(msg)

        value = await database.get_app_setting("REFERRAL_ENABLED")
        self.assertEqual(value, "0")

    async def test_cancel_edit_flow(self):
        import database
        import handlers_admin
        from ui_constants import CB_ADMIN_CANCEL_EDIT, CB_ADMIN_SETTING_EDIT_PREFIX

        idx = handlers_admin._all_setting_keys().index("REFERRAL_ENABLED")
        cb_start = DummyCallback(1, f"{CB_ADMIN_SETTING_EDIT_PREFIX}{idx}_0")
        await handlers_admin.admin_setting_edit_start(cb_start)

        cb_cancel = DummyCallback(1, CB_ADMIN_CANCEL_EDIT)
        await handlers_admin.admin_cancel_edit(cb_cancel)

        msg = DummyMessage()
        msg.from_user = DummyUser(1)
        msg.text = "1"
        await handlers_admin.admin_pending_edit_consumer(msg)
        value = await database.get_app_setting("REFERRAL_ENABLED")
        self.assertIsNone(value)

    async def test_health_button_path(self):
        import handlers_admin
        from ui_constants import CB_ADMIN_HEALTH

        cb = DummyCallback(1, CB_ADMIN_HEALTH)
        await handlers_admin.admin_health_summary(cb)
        text = cb.message.sent[-1][0]
        self.assertIn("qos_last_sync_ok", text)
        self.assertIn("denylist_entries", text)

    async def test_referral_summary_button_path(self):
        import handlers_admin
        from ui_constants import CB_ADMIN_REFERRALS

        cb = DummyCallback(1, CB_ADMIN_REFERRALS)
        await handlers_admin.admin_referrals_summary(cb)
        self.assertIn("Referral admin summary", cb.message.sent[-1][0])


if __name__ == "__main__":
    unittest.main()
