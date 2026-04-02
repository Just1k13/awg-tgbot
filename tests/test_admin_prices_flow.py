import os
import sys
import tempfile
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


class AdminPricesFlowTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_prices_screen_renders_values(self):
        import handlers_admin

        text = handlers_admin._render_admin_prices_text()
        self.assertIn("7 дней", text)
        self.assertIn("30 дней", text)
        self.assertIn("90 дней", text)

    async def test_admin_can_start_edit_each_tariff(self):
        import database
        import handlers_admin
        from ui_constants import CB_ADMIN_PRICE_EDIT_30, CB_ADMIN_PRICE_EDIT_7, CB_ADMIN_PRICE_EDIT_90

        await database.set_app_setting("MAINTENANCE_MODE", "1")
        for cb_data, expected_key in (
            (CB_ADMIN_PRICE_EDIT_7, "STARS_PRICE_7_DAYS"),
            (CB_ADMIN_PRICE_EDIT_30, "STARS_PRICE_30_DAYS"),
            (CB_ADMIN_PRICE_EDIT_90, "STARS_PRICE_90_DAYS"),
        ):
            answers = []

            class DummyMessage:
                async def answer(self, text, **kwargs):
                    answers.append(text)

            class DummyCb:
                from_user = SimpleNamespace(id=1)
                message = DummyMessage()
                data = cb_data

                async def answer(self, *_args, **_kwargs):
                    return None

            await handlers_admin.admin_prices_start_edit(DummyCb())
            action = await database.get_pending_admin_action(1, handlers_admin.PRICE_INPUT_ACTION_KEY)
            self.assertEqual(action["env_key"], expected_key)
            self.assertTrue(answers)

    async def test_start_edit_blocked_when_maintenance_off(self):
        import database
        import handlers_admin
        import content_settings
        from ui_constants import CB_ADMIN_PRICE_EDIT_7

        await database.set_app_setting("MAINTENANCE_MODE", "0")
        answers = []

        class DummyMessage:
            async def answer(self, text, **kwargs):
                answers.append(text)

        class DummyCb:
            from_user = SimpleNamespace(id=1)
            message = DummyMessage()
            data = CB_ADMIN_PRICE_EDIT_7

            async def answer(self, text, show_alert=False):
                answers.append((text, show_alert))

        await handlers_admin.admin_prices_start_edit(DummyCb())
        action = await database.get_pending_admin_action(1, handlers_admin.PRICE_INPUT_ACTION_KEY)
        self.assertIsNone(action)
        self.assertEqual(await content_settings.get_setting("MAINTENANCE_MODE", int), 0)
        self.assertEqual(answers[-1][0], "Сначала включите /maintenance_on, затем изменяйте цену.")
        self.assertTrue(answers[-1][1])

    async def test_invalid_input_rejected(self):
        import database
        import handlers_admin

        await database.set_pending_admin_action(
            1,
            handlers_admin.PRICE_INPUT_ACTION_KEY,
            {"env_key": "STARS_PRICE_7_DAYS", "label": "7 дней"},
        )
        answers = []

        class DummyMessage:
            from_user = SimpleNamespace(id=1)
            text = "abc"

            async def answer(self, text, **kwargs):
                answers.append(text)

        await handlers_admin.admin_prices_capture_input(DummyMessage())
        action = await database.get_pending_admin_action(1, handlers_admin.PRICE_INPUT_ACTION_KEY)
        self.assertIsNotNone(action)
        self.assertIn("положительное", answers[-1].lower())

    async def test_valid_input_reaches_confirm(self):
        import database
        import handlers_admin

        await database.set_pending_admin_action(
            1,
            handlers_admin.PRICE_INPUT_ACTION_KEY,
            {"env_key": "STARS_PRICE_7_DAYS", "label": "7 дней"},
        )
        answers = []

        class DummyMessage:
            from_user = SimpleNamespace(id=1)
            text = "77"

            async def answer(self, text, **kwargs):
                answers.append(text)

        await handlers_admin.admin_prices_capture_input(DummyMessage())
        action = await database.get_pending_admin_action(1, handlers_admin.PRICE_CONFIRM_ACTION_KEY)
        self.assertEqual(action["new"], 77)
        self.assertTrue(any("Станет: 77⭐" in text for text in answers))

    async def test_confirm_saves_new_value(self):
        import database
        import handlers_admin
        import content_settings
        from ui_constants import CB_ADMIN_PRICE_SAVE

        await database.set_app_setting("MAINTENANCE_MODE", "1")
        await database.set_pending_admin_action(
            1,
            handlers_admin.PRICE_CONFIRM_ACTION_KEY,
            {
                "env_key": "STARS_PRICE_30_DAYS",
                "label": "30 дней",
                "old": 50,
                "new": 88,
            },
        )
        captured = {}
        original_set = handlers_admin.set_stars_price
        handlers_admin.set_stars_price = lambda key, value: captured.setdefault("pair", (50, value))
        answers = []

        class DummyMessage:
            async def answer(self, text, **kwargs):
                answers.append(text)

        class DummyCb:
            from_user = SimpleNamespace(id=1)
            data = CB_ADMIN_PRICE_SAVE
            message = DummyMessage()

            async def answer(self, *_args, **_kwargs):
                return None

        try:
            await handlers_admin.admin_prices_save(DummyCb())
        finally:
            handlers_admin.set_stars_price = original_set
        self.assertEqual(captured["pair"], (50, 88))
        self.assertEqual(await content_settings.get_setting("MAINTENANCE_MODE", int), 1)
        self.assertFalse(any("Покупки снова доступны." in text for text in answers))
        self.assertTrue(any("50⭐ → 88⭐" in text for text in answers))

    async def test_cancel_does_not_save(self):
        import database
        import handlers_admin
        import content_settings
        from ui_constants import CB_ADMIN_PRICE_CANCEL

        await database.set_app_setting("MAINTENANCE_MODE", "1")
        await database.set_pending_admin_action(
            1,
            handlers_admin.PRICE_CONFIRM_ACTION_KEY,
            {
                "env_key": "STARS_PRICE_90_DAYS",
                "label": "90 дней",
                "old": 140,
                "new": 190,
            },
        )
        answers = []
        calls = {"count": 0}
        original_set = handlers_admin.set_stars_price
        handlers_admin.set_stars_price = lambda key, value: calls.update(count=calls["count"] + 1)

        class DummyMessage:
            async def answer(self, text, **_kwargs):
                answers.append(text)
                return None

        class DummyCb:
            from_user = SimpleNamespace(id=1)
            data = CB_ADMIN_PRICE_CANCEL
            message = DummyMessage()

            async def answer(self, *_args, **_kwargs):
                return None

        try:
            await handlers_admin.admin_prices_cancel(DummyCb())
        finally:
            handlers_admin.set_stars_price = original_set
        self.assertEqual(calls["count"], 0)
        self.assertEqual(await content_settings.get_setting("MAINTENANCE_MODE", int), 1)
        self.assertFalse(any("Покупки снова доступны." in text for text in answers))
        action = await database.get_pending_admin_action(1, handlers_admin.PRICE_CONFIRM_ACTION_KEY)
        self.assertIsNone(action)

    async def test_non_admin_cannot_access_price_flow(self):
        import database
        import handlers_admin
        from ui_constants import CB_ADMIN_PRICE_EDIT_7

        answers = []

        class DummyMessage:
            async def answer(self, _text, **_kwargs):
                return None

        class DummyCb:
            from_user = SimpleNamespace(id=2)
            message = DummyMessage()
            data = CB_ADMIN_PRICE_EDIT_7

            async def answer(self, text, show_alert=False):
                answers.append((text, show_alert))

        await handlers_admin.admin_prices_start_edit(DummyCb())
        action = await database.get_pending_admin_action(1, handlers_admin.PRICE_INPUT_ACTION_KEY)
        self.assertIsNone(action)
        self.assertEqual(answers[-1][0], "Нет доступа")

    async def test_user_tariffs_use_updated_runtime_values(self):
        import config
        import handlers_user
        import payments

        old_7, old_30, old_90 = config.STARS_PRICE_7_DAYS, config.STARS_PRICE_30_DAYS, config.STARS_PRICE_90_DAYS
        config.STARS_PRICE_7_DAYS = 71
        config.STARS_PRICE_30_DAYS = 131
        config.STARS_PRICE_90_DAYS = 221
        answers = []

        class DummyTarget:
            async def answer(self, text, **kwargs):
                answers.append(text)

        original_get_user_subscription = handlers_user.get_user_subscription
        original_get_text = handlers_user.get_text
        async def fake_get_user_subscription(_uid):
            return None

        async def fake_get_text(_key, **kwargs):
            return kwargs.get("price_lines", "")

        handlers_user.get_user_subscription = fake_get_user_subscription  # type: ignore[assignment]
        handlers_user.get_text = fake_get_text  # type: ignore[assignment]
        try:
            await handlers_user._send_buy_menu(DummyTarget(), 1)
            self.assertIn("7 дней — 71⭐", answers[-1])
            self.assertEqual(payments.get_tariffs()["sub_90"]["amount"], 221)
        finally:
            handlers_user.get_user_subscription = original_get_user_subscription
            handlers_user.get_text = original_get_text
            config.STARS_PRICE_7_DAYS = old_7
            config.STARS_PRICE_30_DAYS = old_30
            config.STARS_PRICE_90_DAYS = old_90


if __name__ == "__main__":
    unittest.main()
