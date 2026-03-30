import asyncio
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bot"))

os.environ.setdefault("ENCRYPTION_SECRET", "test-secret")
os.environ.setdefault("API_TOKEN", "123:test")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("SERVER_PUBLIC_KEY", "A" * 44)
os.environ.setdefault("SERVER_IP", "1.1.1.1:51820")
os.environ.setdefault("AWG_HELPER_POLICY_PATH", str(ROOT / "tests" / "helper-policy.json"))

(Path(ROOT) / "tests" / "helper-policy.json").write_text(
    '{"container":"amnezia-awg2","interface":"awg0"}',
    encoding="utf-8",
)


class BetaBlockersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import database

        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test.db")
        database.DB_PATH = self.db_path
        await database.close_shared_db()
        await database.init_db()

    async def asyncTearDown(self):
        import database

        await database.close_shared_db()
        self.tmp.cleanup()

    async def test_retry_delay_persisted_to_job(self):
        import database
        import payments

        await database.save_payment(
            telegram_payment_charge_id="tg_retry",
            provider_payment_charge_id="prov_retry",
            user_id=100,
            payload="sub_7",
            amount=1,
            currency="XTR",
            payment_method="stars",
            status="received",
            raw_payload_json="{}",
        )

        async def fail_issue_subscription(*args, **kwargs):
            raise RuntimeError("awg temporary down")

        original_issue = payments.issue_subscription
        payments.issue_subscription = fail_issue_subscription
        try:
            with self.assertRaises(RuntimeError):
                await payments.process_payment_provisioning("tg_retry", 100, "sub_7", 7)
        finally:
            payments.issue_subscription = original_issue

        row = await database.fetchone(
            "SELECT status, next_retry_at, lease_expires_at FROM provisioning_jobs WHERE payment_id = ?",
            ("tg_retry",),
        )
        self.assertEqual(row[0], "needs_repair")
        self.assertIsNotNone(row[1])
        self.assertIsNone(row[2])

    async def test_pre_checkout_rejects_amount_currency_mismatch(self):
        import payments

        class DummyBot:
            def __init__(self):
                self.calls = []

            async def answer_pre_checkout_query(self, qid, ok, error_message=None):
                self.calls.append((qid, ok, error_message))

        class DummyQuery:
            def __init__(self, payload, amount, currency):
                self.id = "q1"
                self.invoice_payload = payload
                self.total_amount = amount
                self.currency = currency

        bot = DummyBot()
        original_readiness = payments.checkout_readiness
        payments.checkout_readiness = lambda: asyncio.sleep(0, result=(True, ""))  # type: ignore[assignment]
        try:
            await payments.pre_checkout(DummyQuery("sub_7", payments.STARS_PRICE_7_DAYS + 1, "XTR"), bot)
            self.assertEqual(bot.calls[-1][1], False)
            await payments.pre_checkout(DummyQuery("sub_7", payments.STARS_PRICE_7_DAYS, "USD"), bot)
            self.assertEqual(bot.calls[-1][1], False)
            await payments.pre_checkout(DummyQuery("sub_7", payments.STARS_PRICE_7_DAYS, "XTR"), bot)
            self.assertEqual(bot.calls[-1][1], True)
        finally:
            payments.checkout_readiness = original_readiness

    async def test_pre_checkout_rejects_unknown_payload(self):
        import payments

        class DummyBot:
            def __init__(self):
                self.calls = []

            async def answer_pre_checkout_query(self, qid, ok, error_message=None):
                self.calls.append((qid, ok, error_message))

        class DummyQuery:
            id = "q-unknown"
            invoice_payload = "sub_999"
            total_amount = payments.STARS_PRICE_7_DAYS
            currency = "XTR"

        bot = DummyBot()
        await payments.pre_checkout(DummyQuery(), bot)
        self.assertFalse(bot.calls[-1][1])

    async def test_pre_checkout_rejects_when_readiness_degraded(self):
        import payments

        class DummyBot:
            def __init__(self):
                self.calls = []

            async def answer_pre_checkout_query(self, qid, ok, error_message=None):
                self.calls.append((qid, ok, error_message))

        class DummyQuery:
            id = "q-degraded"
            invoice_payload = "sub_7"
            total_amount = payments.STARS_PRICE_7_DAYS
            currency = "XTR"

        bot = DummyBot()
        original_readiness = payments.checkout_readiness
        payments.checkout_readiness = lambda: asyncio.sleep(0, result=(False, "db down"))  # type: ignore[assignment]
        try:
            await payments.pre_checkout(DummyQuery(), bot)
        finally:
            payments.checkout_readiness = original_readiness
        self.assertFalse(bot.calls[-1][1])
        self.assertIn("временно", bot.calls[-1][2])

    async def test_retries_stop_at_max_attempts_and_mark_stuck(self):
        import database
        import payments

        await database.save_payment(
            telegram_payment_charge_id="tg_stuck",
            provider_payment_charge_id="prov_stuck",
            user_id=101,
            payload="sub_7",
            amount=1,
            currency="XTR",
            payment_method="stars",
            status="received",
            raw_payload_json="{}",
        )

        async def fail_issue_subscription(*args, **kwargs):
            raise RuntimeError("provisioning hard failure")

        original_issue = payments.issue_subscription
        payments.issue_subscription = fail_issue_subscription
        original_max = payments.PAYMENT_MAX_ATTEMPTS
        payments.PAYMENT_MAX_ATTEMPTS = 2
        try:
            await payments.payment_recovery_worker()
            await database.execute("UPDATE provisioning_jobs SET next_retry_at = ? WHERE payment_id = ?", (payments.utc_now_naive().isoformat(), "tg_stuck"))
            await payments.payment_recovery_worker()
            await database.execute("UPDATE provisioning_jobs SET next_retry_at = ? WHERE payment_id = ?", (payments.utc_now_naive().isoformat(), "tg_stuck"))
            await payments.payment_recovery_worker()
        finally:
            payments.issue_subscription = original_issue
            payments.PAYMENT_MAX_ATTEMPTS = original_max

        status = await database.get_payment_status("tg_stuck")
        self.assertEqual(status, "stuck_manual")

    async def test_stale_provisioning_job_is_recoverable(self):
        import database
        import payments

        await database.save_payment(
            telegram_payment_charge_id="tg_stale",
            provider_payment_charge_id="prov_stale",
            user_id=200,
            payload="sub_7",
            amount=1,
            currency="XTR",
            payment_method="stars",
            status="received",
            raw_payload_json="{}",
        )
        past = (payments.utc_now_naive() - timedelta(minutes=10)).isoformat()
        await database.update_payment_status("tg_stale", "provisioning")
        db = await database.open_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE provisioning_jobs
                SET status='provisioning', lock_token='stale-lock', lease_expires_at=?, updated_at=?
                WHERE payment_id=?
                """,
                (past, past, "tg_stale"),
            )
            await db.commit()
        finally:
            await db.close()

        async def fake_issue_subscription(user_id, days, silent=False, operation_id=None):
            from datetime import datetime
            return datetime.fromisoformat("2026-04-01T00:00:00")

        original_issue = payments.issue_subscription
        payments.issue_subscription = fake_issue_subscription
        try:
            repaired = await payments.payment_recovery_worker()
        finally:
            payments.issue_subscription = original_issue

        self.assertEqual(repaired, 1)
        status = await database.get_payment_status("tg_stale")
        self.assertEqual(status, "applied")

    async def test_revoke_only_if_expired_does_not_touch_renewed_user(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute(
                "INSERT INTO users (user_id, sub_until, created_at) VALUES (300, ?, '2026-01-01T00:00:00')",
                ((awg_backend.utc_now_naive() + timedelta(days=5)).isoformat(),),
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (300, 1, ?, '', '10.8.1.30', '2026-01-01T00:00:00', 'active')
                """,
                ("B" * 44,),
            )
            await db.commit()
        finally:
            await db.close()

        called = {"remove": 0}

        async def fail_remove(_):
            called["remove"] += 1
            raise AssertionError("remove should not be called for renewed user")

        original_remove = awg_backend.remove_peer_from_awg
        awg_backend.remove_peer_from_awg = fail_remove
        try:
            removed = await awg_backend.revoke_user_access(300, only_if_expired=True)
        finally:
            awg_backend.remove_peer_from_awg = original_remove

        self.assertEqual(removed, 0)
        self.assertEqual(called["remove"], 0)

    async def test_issue_subscription_reuses_deleted_slot_and_creates_missing_key(self):
        import awg_backend
        import database

        db = await database.open_db()
        try:
            await db.execute(
                "INSERT INTO users (user_id, sub_until, created_at) VALUES (400, '0', '2026-01-01T00:00:00')"
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state)
                VALUES (400, 1, ?, '', '10.8.1.40', '2026-01-01T00:00:00', 'deleted')
                """,
                ("C" * 44,),
            )
            await db.execute(
                """
                INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, state, client_private_key, psk_key)
                VALUES (400, 2, ?, '', '10.8.1.41', '2026-01-01T00:00:00', 'active', 'plain', 'plain')
                """,
                ("D" * 44,),
            )
            await db.commit()
        finally:
            await db.close()

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
            new_until = await awg_backend.issue_subscription(400, 7, operation_id="test-op")
        finally:
            awg_backend.generate_keypair = original_keypair
            awg_backend.generate_psk = original_psk
            awg_backend.add_peer_to_awg = original_add
            awg_backend.get_used_ips_from_awg = original_used_ips

        self.assertIsNotNone(new_until)
        rows = await database.fetchall(
            "SELECT device_num, state FROM keys WHERE user_id = ? ORDER BY device_num",
            (400,),
        )
        active_device_nums = [device_num for device_num, state in rows if state == "active"]
        self.assertIn(1, active_device_nums)
        self.assertIn(2, active_device_nums)


    async def test_issue_subscription_operation_id_is_idempotent(self):
        import awg_backend
        import database

        state = {"idx": 0}

        async def fake_keypair():
            state["idx"] += 1
            suffix = str(state["idx"]).rjust(43, "E")
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
            first_until = await awg_backend.issue_subscription(500, 7, operation_id="same-op")
            second_until = await awg_backend.issue_subscription(500, 7, operation_id="same-op")
        finally:
            awg_backend.generate_keypair = original_keypair
            awg_backend.generate_psk = original_psk
            awg_backend.add_peer_to_awg = original_add
            awg_backend.get_used_ips_from_awg = original_used_ips

        self.assertEqual(first_until.isoformat(), second_until.isoformat())

    async def test_broadcast_confirm_queues_job(self):
        import handlers_admin
        import database

        await database.ensure_user_exists(1)
        await database.set_pending_broadcast(1, "hello users")

        class FakeMessage:
            def __init__(self):
                self.calls = 0

            async def answer(self, *args, **kwargs):
                self.calls += 1

        class FakeCb:
            def __init__(self):
                self.from_user = type("u", (), {"id": 1})
                self.message = FakeMessage()
                self.bot = None

            async def answer(self, *args, **kwargs):
                return None

        cb = FakeCb()
        await handlers_admin.broadcast_confirm(cb)  # type: ignore[arg-type]
        row = await database.fetchone("SELECT COUNT(*) FROM broadcast_jobs WHERE status='queued'")
        self.assertEqual(row[0], 1)

    async def test_broadcast_targets_are_snapshotted_at_claim_time(self):
        import database

        await database.ensure_user_exists(10)
        await database.ensure_user_exists(11)
        job_id = await database.create_broadcast_job(1, "snapshot")
        claimed = await database.claim_next_broadcast_job()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed[0], job_id)

        await database.ensure_user_exists(12)
        recipients = await database.get_broadcast_recipients(job_id, 0, 50)
        self.assertEqual(recipients, [10, 11])

    async def test_backup_requires_explicit_opt_in_for_insecure_send(self):
        import config
        import handlers_admin

        class DummyMessage:
            def __init__(self):
                self.answers = []
                self.documents = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

            async def answer_document(self, document, caption=None, **kwargs):
                self.documents.append((document, caption))

        original_secure = handlers_admin.BACKUP_SECURE_MODE
        original_allow = handlers_admin.BACKUP_ALLOW_INSECURE_SEND
        handlers_admin.BACKUP_SECURE_MODE = False
        handlers_admin.BACKUP_ALLOW_INSECURE_SEND = False
        original_config_db_path = config.DB_PATH
        config.DB_PATH = self.db_path
        try:
            msg = DummyMessage()
            await handlers_admin.backup_db(msg)  # type: ignore[arg-type]
            self.assertTrue(any("Небезопасная отправка backup отключена" in text for text in msg.answers))
            self.assertEqual(msg.documents, [])
        finally:
            handlers_admin.BACKUP_SECURE_MODE = original_secure
            handlers_admin.BACKUP_ALLOW_INSECURE_SEND = original_allow
            config.DB_PATH = original_config_db_path

    async def test_unknown_slash_command_gets_user_feedback(self):
        import handlers_user

        class DummyMessage:
            def __init__(self):
                self.text = "/unknown"
                self.from_user = type("U", (), {"id": 101})()
                self.answers = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        message = DummyMessage()
        await handlers_user.fallback_message(message)  # type: ignore[arg-type]
        self.assertTrue(message.answers)
        self.assertIn("Неизвестная команда", message.answers[-1])

    async def test_recovery_ready_notification_is_idempotent(self):
        import database
        import payments

        await database.save_payment(
            telegram_payment_charge_id="tg_notify",
            provider_payment_charge_id="prov_notify",
            user_id=555,
            payload="sub_7",
            amount=payments.STARS_PRICE_7_DAYS,
            currency="XTR",
            payment_method="stars",
            status="needs_repair",
            raw_payload_json="{}",
        )
        await database.execute("UPDATE provisioning_jobs SET status='needs_repair', next_retry_at=NULL WHERE payment_id='tg_notify'")

        async def fake_process(payment_id, user_id, payload, days):
            await database.update_payment_status(payment_id, "applied")
            return True

        class DummyBot:
            def __init__(self):
                self.sent = []

            async def send_message(self, user_id, text, **kwargs):
                self.sent.append((user_id, text))

        bot = DummyBot()
        original_process = payments.process_payment_provisioning
        payments.process_payment_provisioning = fake_process
        try:
            await payments.payment_recovery_worker(bot)  # type: ignore[arg-type]
            await payments.payment_recovery_worker(bot)  # type: ignore[arg-type]
        finally:
            payments.process_payment_provisioning = original_process

        self.assertEqual(len(bot.sent), 1)

    async def test_referral_capture_first_wins_and_self_referral_rejected(self):
        import referrals
        from database import get_referral_attribution

        code_1 = await referrals.ensure_user_referral_code(1000)
        code_2 = await referrals.ensure_user_referral_code(1001)
        self_ref = await referrals.capture_referral_start(1000, f"ref_{code_1}")
        self.assertFalse(self_ref)
        first = await referrals.capture_referral_start(1002, f"ref_{code_1}")
        second = await referrals.capture_referral_start(1002, f"ref_{code_2}")
        self.assertTrue(first)
        self.assertFalse(second)
        attribution = await get_referral_attribution(1002)
        self.assertEqual(attribution[0], 1000)

    async def test_referral_reward_is_idempotent(self):
        import referrals
        from database import get_referral_attribution, get_referral_summary

        code = await referrals.ensure_user_referral_code(2000)
        await referrals.capture_referral_start(2001, f"ref_{code}")
        self.assertIsNotNone(await get_referral_attribution(2001))

        async def fake_issue_subscription(*args, **kwargs):
            from datetime import datetime
            return datetime.fromisoformat("2026-04-01T00:00:00")

        original_issue = referrals.issue_subscription
        referrals.issue_subscription = fake_issue_subscription
        try:
            first = await referrals.apply_referral_rewards_on_first_payment(2001, "pay-1")
            second = await referrals.apply_referral_rewards_on_first_payment(2001, "pay-1")
        finally:
            referrals.issue_subscription = original_issue
        self.assertTrue(first)
        self.assertFalse(second)
        summary = await get_referral_summary(2000)
        self.assertGreaterEqual(summary["inviter_bonus_days"], 3)

    async def test_referral_second_successful_paid_subscription_has_no_second_reward(self):
        import referrals

        code = await referrals.ensure_user_referral_code(3000)
        await referrals.capture_referral_start(3001, f"ref_{code}")

        async def fake_issue_subscription(*args, **kwargs):
            from datetime import datetime
            return datetime.fromisoformat("2026-05-01T00:00:00")

        original_issue = referrals.issue_subscription
        referrals.issue_subscription = fake_issue_subscription
        try:
            first = await referrals.apply_referral_rewards_on_first_payment(3001, "pay-a")
            second = await referrals.apply_referral_rewards_on_first_payment(3001, "pay-b")
        finally:
            referrals.issue_subscription = original_issue
        self.assertTrue(first)
        self.assertFalse(second)

    async def test_admin_give_path_does_not_trigger_referral_rewards(self):
        import handlers_admin
        import referrals

        called = {"count": 0}

        async def fake_apply(*args, **kwargs):
            called["count"] += 1
            return True

        original_apply = referrals.apply_referral_rewards_on_first_payment
        referrals.apply_referral_rewards_on_first_payment = fake_apply
        try:
            class DummyMessage:
                from_user = type("U", (), {"id": 1})()
                answers = []

                async def answer(self, text, **kwargs):
                    self.answers.append(text)

            msg = DummyMessage()
            await handlers_admin.give_manual(msg, type("C", (), {"args": "4000 7"})())  # type: ignore[arg-type]
        except Exception:
            pass
        finally:
            referrals.apply_referral_rewards_on_first_payment = original_apply
        self.assertEqual(called["count"], 0)

    async def test_ref_stats_returns_global_summary(self):
        import handlers_admin
        import database

        await database.ensure_referral_code(7000, "ABC7000")
        await database.set_referral_attribution(7001, 7000, "ABC7000")

        class DummyMessage:
            from_user = type("U", (), {"id": 1})()
            answers = []

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        msg = DummyMessage()
        await handlers_admin.ref_stats_cmd(msg)  # type: ignore[arg-type]
        self.assertTrue(msg.answers)
        self.assertIn("pending", msg.answers[-1])

    async def test_qos_soft_mode_does_not_raise(self):
        import network_policy

        async def fail_run(*args, **kwargs):
            raise RuntimeError("tc fail")

        async def fake_get_setting(key, cast=None):
            values = {"QOS_ENABLED": "1", "QOS_STRICT": "0"}
            return cast(values[key]) if cast else values[key]

        original_get_setting = network_policy.get_setting
        network_policy.get_setting = fake_get_setting
        try:
            await network_policy.qos_set(fail_run, "10.8.1.10", 100, 1)
        finally:
            network_policy.get_setting = original_get_setting

    async def test_qos_strict_mode_raises(self):
        import network_policy

        async def fail_run(*args, **kwargs):
            raise RuntimeError("tc fail")

        async def fake_get_setting(key, cast=None):
            values = {"QOS_ENABLED": "1", "QOS_STRICT": "1"}
            return cast(values[key]) if cast else values[key]

        original_get_setting = network_policy.get_setting
        network_policy.get_setting = fake_get_setting
        try:
            with self.assertRaises(RuntimeError):
                await network_policy.qos_set(fail_run, "10.8.1.10", 100, 1)
        finally:
            network_policy.get_setting = original_get_setting

    async def test_content_text_defaults_cover_user_and_payment_flow(self):
        import content_settings

        required_keys = {
            "start",
            "buy_menu",
            "renew_menu",
            "instruction_body",
            "support_contact",
            "profile_screen",
            "configs_empty",
            "configs_menu",
            "payment_success",
            "payment_pending",
            "payment_error",
            "payment_next_step",
            "activation_status_ready",
            "activation_status_pending",
            "activation_status_delayed",
            "unknown_slash",
            "unknown_message",
            "unknown_callback_action",
        }
        self.assertTrue(required_keys.issubset(set(content_settings.TEXT_DEFAULTS)))

    async def test_instruction_text_contains_dynamic_download_url(self):
        import config
        import texts

        original = config.DOWNLOAD_URL
        config.DOWNLOAD_URL = "https://example.com/client"
        try:
            rendered = await texts.get_instruction_text()
        finally:
            config.DOWNLOAD_URL = original
        self.assertIn("example.com/client", rendered)


if __name__ == "__main__":
    unittest.main()
