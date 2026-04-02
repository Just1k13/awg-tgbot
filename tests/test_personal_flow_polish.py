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

    async def test_profile_keyboard_has_activation_status_short_label_and_no_generic_reissue(self):
        from keyboards import get_profile_inline_kb

        kb = get_profile_inline_kb(subscription_active=False)
        labels = [button.text for row in kb.inline_keyboard for button in row]
        self.assertIn("⏱ Статус активации", labels)
        self.assertIn("🆘 Помощь и поддержка", labels)
        self.assertNotIn("♻️ Перевыпустить устройство", labels)

        kb_active = get_profile_inline_kb(subscription_active=True)
        active_labels = [button.text for row in kb_active.inline_keyboard for button in row]
        self.assertNotIn("♻️ Перевыпустить устройство", active_labels)

    async def test_support_center_keyboard_contains_main_help_actions(self):
        from keyboards import get_support_center_kb

        labels = [button.text for row in get_support_center_kb().inline_keyboard for button in row]
        self.assertIn("💳 Помощь с оплатой", labels)
        self.assertIn("🔌 Помощь с подключением", labels)
        self.assertIn("📄 Краткие условия", labels)
        self.assertIn("⬅️ К меню", labels)

    async def test_config_result_keyboard_has_reissue_action(self):
        from keyboards import get_config_result_kb

        labels = [button.text for row in get_config_result_kb(5).inline_keyboard for button in row]
        self.assertIn("♻️ Перевыпустить это устройство", labels)

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

    async def test_send_selected_device_conf_does_not_repeat_action_keyboard(self):
        import handlers_user

        await handlers_user.ensure_user_exists(101)
        original_find = handlers_user._find_user_config_by_key_id

        async def fake_find(_uid: int, _key_id: int):
            return (1, 2, "[Interface]\nPrivateKey=x", "vpn://key")

        handlers_user._find_user_config_by_key_id = fake_find
        try:
            class DummyMessage:
                def __init__(self):
                    self.answer_calls = []
                    self.document_calls = []

                async def answer(self, text, **kwargs):
                    self.answer_calls.append((text, kwargs))

                async def answer_document(self, document, **kwargs):
                    self.document_calls.append((document, kwargs))

            class DummyCb:
                def __init__(self):
                    self.from_user = type("U", (), {"id": 101, "username": None, "first_name": "U"})()
                    self.data = "config_conf_1"
                    self.message = DummyMessage()

                async def answer(self, *args, **kwargs):
                    return None

            cb = DummyCb()
            await handlers_user.send_selected_device_conf(cb)  # type: ignore[arg-type]
        finally:
            handlers_user._find_user_config_by_key_id = original_find

        self.assertEqual(len(cb.message.document_calls), 1)
        self.assertTrue(cb.message.answer_calls)
        last_text, last_kwargs = cb.message.answer_calls[-1]
        self.assertIn("Файл отправлен", last_text)
        self.assertIsNone(last_kwargs.get("reply_markup"))

    async def test_support_policy_commands_return_text(self):
        import handlers_user

        class DummyMsg:
            def __init__(self):
                self.answers = []
                self.from_user = type("U", (), {"id": 100, "username": "u", "first_name": "N"})()

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        support_msg = DummyMsg()
        await handlers_user.support_cmd(support_msg)  # type: ignore[arg-type]
        self.assertTrue(support_msg.answers)
        self.assertIn("Поддержка", support_msg.answers[-1])

        pay_msg = DummyMsg()
        await handlers_user.paysupport_cmd(pay_msg)  # type: ignore[arg-type]
        self.assertIn("user_id", pay_msg.answers[-1])

        terms_msg = DummyMsg()
        await handlers_user.terms_cmd(terms_msg)  # type: ignore[arg-type]
        self.assertIn("7 / 30 / 90", terms_msg.answers[-1])

    async def test_support_callback_payment_and_terms_are_reachable(self):
        import handlers_user

        class DummyMessage:
            def __init__(self):
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append((text, kwargs))

        class DummyCb:
            def __init__(self, data: str):
                self.data = data
                self.from_user = type("U", (), {"id": 100, "username": "u", "first_name": "N"})()
                self.message = DummyMessage()

            async def answer(self, *args, **kwargs):
                return None

        cb_pay = DummyCb("support_payment")
        await handlers_user.support_payment_callback(cb_pay)  # type: ignore[arg-type]
        self.assertIn("Поддержка по оплате", cb_pay.message.answers[-1][0])

        cb_terms = DummyCb("support_terms")
        await handlers_user.support_terms_callback(cb_terms)  # type: ignore[arg-type]
        self.assertIn("Краткие условия", cb_terms.message.answers[-1][0])

    async def test_reissue_is_reachable_from_profile_and_device_buttons(self):
        import handlers_user

        await handlers_user.ensure_user_exists(1002)

        original_start = handlers_user._start_user_reissue_flow
        seen: list[int | None] = []

        async def fake_start(_target, _user, *, key_id=None):
            seen.append(key_id)

        handlers_user._start_user_reissue_flow = fake_start
        try:
            class DummyMessage:
                async def answer(self, text, **kwargs):
                    return None

            class DummyCb:
                def __init__(self, data: str):
                    self.data = data
                    self.from_user = type("U", (), {"id": 1002, "username": "u1002", "first_name": "User"})()
                    self.message = DummyMessage()

                async def answer(self, *args, **kwargs):
                    return None

            await handlers_user.user_reissue_from_button(DummyCb("user_reissue_device_0"))  # type: ignore[arg-type]
            await handlers_user.user_reissue_from_button(DummyCb("user_reissue_device_7"))  # type: ignore[arg-type]
        finally:
            handlers_user._start_user_reissue_flow = original_start

        self.assertEqual(seen, [None, 7])

    async def test_send_without_args_enters_interactive_broadcast_mode(self):
        import handlers_admin
        import database
        handlers_admin.admin_command_rate_limit.clear()

        class DummyMsg:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append((text, kwargs))

        msg = DummyMsg()
        await handlers_admin.broadcast_prepare(msg, type("C", (), {"args": None})())  # type: ignore[arg-type]
        pending = await database.get_pending_admin_action(1, handlers_admin.BROADCAST_INPUT_ACTION_KEY)
        self.assertIsNotNone(pending)
        self.assertTrue(msg.answers)

    async def test_finduser_by_numeric_id_opens_card(self):
        import handlers_admin
        import database

        await database.ensure_user_exists(222, "alpha", "A")

        class DummyMsg:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        msg = DummyMsg()
        await handlers_admin.find_user_cmd(msg, type("C", (), {"args": "222"})())  # type: ignore[arg-type]
        self.assertTrue(any("Управление пользователем" in line for line in msg.answers))

    async def test_finduser_by_username_substring_returns_short_list(self):
        import handlers_admin
        import database

        await database.ensure_user_exists(301, "john_alpha", "John")
        await database.ensure_user_exists(302, "john_beta", "John")

        class DummyMsg:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1})()
                self.answers = []
                self.kwargs = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)
                self.kwargs.append(kwargs)

        msg = DummyMsg()
        await handlers_admin.find_user_cmd(msg, type("C", (), {"args": "john"})())  # type: ignore[arg-type]
        self.assertIn("Найдено несколько пользователей", msg.answers[-1])
        self.assertIn("reply_markup", msg.kwargs[-1])

    async def test_user_reset_device_happy_path_and_cooldown_guard(self):
        import handlers_user
        import database

        future = "2099-01-01T00:00:00"
        await database.ensure_user_exists(1001, "u1001", "User")
        await database.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (future, 1001))

        class DummyMsg:
            def __init__(self):
                self.from_user = type("U", (), {"id": 1001, "username": "u1001", "first_name": "User"})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        original_get_keys = handlers_user.get_user_keys
        async def fake_get_user_keys(_user_id: int):
            return [(10, 1, "conf", "vpn://new")]
        handlers_user.get_user_keys = fake_get_user_keys
        msg = DummyMsg()
        await handlers_user.reset_device_cmd(msg)  # type: ignore[arg-type]
        self.assertIn("Перевыпуск доступа", msg.answers[-1])

        original_reissue = handlers_user.reissue_user_device
        calls = {"count": 0}

        async def fake_reissue(uid: int, device_num: int):
            calls["count"] += 1
            return {"status": "reissued", "uid": uid, "device_num": device_num}

        handlers_user.reissue_user_device = fake_reissue
        try:
            class DummyCb:
                def __init__(self):
                    self.from_user = type("U", (), {"id": 1001})()
                    self.message = DummyMsg()

                async def answer(self, *args, **kwargs):
                    return None

            cb = DummyCb()
            await handlers_user.user_reissue_confirm(cb)  # type: ignore[arg-type]
            await handlers_user.set_pending_admin_action(1001, "user_reissue_device", {"action": "user_reissue_device", "device_num": 1})
            await handlers_user.user_reissue_confirm(cb)  # type: ignore[arg-type]
        finally:
            handlers_user.reissue_user_device = original_reissue
            handlers_user.get_user_keys = original_get_keys
        self.assertEqual(calls["count"], 1)

    async def test_90_day_tariff_is_available(self):
        import handlers_user
        import payments
        from keyboards import get_buy_inline_kb

        self.assertIn("sub_90", payments.TARIFFS)
        self.assertEqual(handlers_user._format_last_payment_tariff("sub_90"), "90 дней")
        labels = [button.text for row in get_buy_inline_kb().inline_keyboard for button in row]
        self.assertTrue(any("90 дней" in label for label in labels))


if __name__ == "__main__":
    unittest.main()
