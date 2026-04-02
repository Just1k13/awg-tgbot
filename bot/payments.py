import json
import io
import uuid
from datetime import timedelta
from pathlib import Path

from aiogram import Bot, F, Router, types
from aiogram.types import LabeledPrice, PreCheckoutQuery

from awg_backend import check_awg_container, issue_subscription
from config import (
    AWG_HELPER_POLICY_PATH,
    DOCKER_CONTAINER,
    ADMIN_ID,
    PAYMENT_MAX_ATTEMPTS,
    PAYMENT_PROVISIONING_LEASE_SECONDS,
    PAYMENT_RETRY_DELAY_SECONDS,
    PURCHASE_CLICK_COOLDOWN_SECONDS,
    PURCHASE_RATE_LIMIT_TTL_SECONDS,
    STARS_PRICE_7_DAYS,
    STARS_PRICE_30_DAYS,
    STARS_PRICE_90_DAYS,
    WG_INTERFACE,
    logger,
)
from config_validate import read_helper_policy
from database import (
    claim_payment_and_job_for_provisioning,
    db_health_info,
    ensure_user_exists,
    fetchone,
    finalize_payment_and_job,
    mark_ready_notification_sent,
    get_provisioning_attempt_count,
    get_payment_status,
    get_repairable_payments,
    update_last_provision_status,
    mark_payment_stuck_manual,
    payment_already_processed,
    persistent_guard_hit,
    save_payment,
    upsert_payment_precheck,
    mark_payment_precheck_status,
    update_payment_status,
    write_audit_log,
    get_user_keys,
)
from helpers import utc_now_naive
from keyboards import get_post_payment_kb
from content_settings import get_text
from referrals import (
    apply_referral_recurring_inviter_reward,
    apply_referral_rewards_on_first_payment,
    notify_inviter_about_referral_reward,
)
from texts import get_payment_result_text
from ui_constants import CB_BUY_30, CB_BUY_7, CB_BUY_90
from maintenance import get_purchase_maintenance_text, is_purchase_maintenance_enabled

router = Router()
purchase_rate_limit: dict[int, object] = {}
_checkout_readiness_cache = {"ok": True, "reason": "", "expires_at": None}
CHECKOUT_READINESS_TTL_SECONDS = 12
CRITICAL_ERRORS_LOG = Path("critical_errors.log")

try:
    import qrcode
except Exception:  # pragma: no cover - optional dependency
    qrcode = None


TARIFFS = {
    "sub_7": {"days": 7, "amount": STARS_PRICE_7_DAYS, "currency": "XTR", "method": "stars"},
    "sub_30": {"days": 30, "amount": STARS_PRICE_30_DAYS, "currency": "XTR", "method": "stars"},
    "sub_90": {"days": 90, "amount": STARS_PRICE_90_DAYS, "currency": "XTR", "method": "stars"},
}


def _cleanup_purchase_rate_limit(now):
    stale = [uid for uid, dt in purchase_rate_limit.items() if (now - dt).total_seconds() > PURCHASE_RATE_LIMIT_TTL_SECONDS]
    for uid in stale:
        purchase_rate_limit.pop(uid, None)


def is_purchase_rate_limited(user_id: int) -> tuple[bool, int]:
    now = utc_now_naive()
    _cleanup_purchase_rate_limit(now)
    last = purchase_rate_limit.get(user_id)
    if not last:
        purchase_rate_limit[user_id] = now
        return False, 0
    delta = (now - last).total_seconds()
    if delta < PURCHASE_CLICK_COOLDOWN_SECONDS:
        return True, int(PURCHASE_CLICK_COOLDOWN_SECONDS - delta) + 1
    purchase_rate_limit[user_id] = now
    return False, 0


async def is_purchase_rate_limited_persistent(user_id: int, action: str) -> tuple[bool, int]:
    hit = await persistent_guard_hit("purchase", user_id, action, PURCHASE_CLICK_COOLDOWN_SECONDS)
    if hit:
        return True, PURCHASE_CLICK_COOLDOWN_SECONDS
    return False, 0


async def _send_stars_invoice(bot: Bot, chat_id: int, payload: str, title: str, label: str, amount: int):
    await bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description="Доступ для 2 устройств",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=amount)],
    )


async def checkout_readiness() -> tuple[bool, str]:
    now_ts = utc_now_naive().timestamp()
    expires_at = _checkout_readiness_cache.get("expires_at")
    if isinstance(expires_at, (int, float)) and now_ts < expires_at:
        return bool(_checkout_readiness_cache["ok"]), str(_checkout_readiness_cache["reason"])
    ok = True
    reason = ""
    try:
        if not DOCKER_CONTAINER or not WG_INTERFACE:
            raise RuntimeError("missing_awg_target")
        policy_container, policy_interface, policy_error = read_helper_policy(Path(AWG_HELPER_POLICY_PATH))
        if policy_error:
            raise RuntimeError(policy_error)
        if policy_container != DOCKER_CONTAINER or policy_interface != WG_INTERFACE:
            raise RuntimeError("helper policy mismatch")
        db_info = await db_health_info()
        if not db_info.get("is_healthy"):
            raise RuntimeError("database_not_ready")
        await check_awg_container()
    except Exception as e:
        ok = False
        reason = str(e)[:200]
    _checkout_readiness_cache["ok"] = ok
    _checkout_readiness_cache["reason"] = reason
    _checkout_readiness_cache["expires_at"] = now_ts + CHECKOUT_READINESS_TTL_SECONDS
    return ok, reason


@router.callback_query(F.data == CB_BUY_7)
async def buy_7_days(cb: types.CallbackQuery, bot: Bot):
    if await is_purchase_maintenance_enabled():
        await cb.answer(await get_purchase_maintenance_text(), show_alert=True)
        return
    mem_limited, mem_wait = is_purchase_rate_limited(cb.from_user.id)
    persistent_limited, persistent_wait = await is_purchase_rate_limited_persistent(cb.from_user.id, CB_BUY_7)
    limited = persistent_limited or mem_limited
    if limited:
        wait_seconds = max(mem_wait, persistent_wait, 1)
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_7", "Свободный Интернет на 7 дней", "7 дней доступа", STARS_PRICE_7_DAYS)


@router.callback_query(F.data == CB_BUY_30)
async def buy_30_days(cb: types.CallbackQuery, bot: Bot):
    if await is_purchase_maintenance_enabled():
        await cb.answer(await get_purchase_maintenance_text(), show_alert=True)
        return
    mem_limited, mem_wait = is_purchase_rate_limited(cb.from_user.id)
    persistent_limited, persistent_wait = await is_purchase_rate_limited_persistent(cb.from_user.id, CB_BUY_30)
    limited = persistent_limited or mem_limited
    if limited:
        wait_seconds = max(mem_wait, persistent_wait, 1)
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_30", "Свободный Интернет на 30 дней", "30 дней доступа", STARS_PRICE_30_DAYS)


@router.callback_query(F.data == CB_BUY_90)
async def buy_90_days(cb: types.CallbackQuery, bot: Bot):
    if await is_purchase_maintenance_enabled():
        await cb.answer(await get_purchase_maintenance_text(), show_alert=True)
        return
    mem_limited, mem_wait = is_purchase_rate_limited(cb.from_user.id)
    persistent_limited, persistent_wait = await is_purchase_rate_limited_persistent(cb.from_user.id, CB_BUY_90)
    limited = persistent_limited or mem_limited
    if limited:
        wait_seconds = max(mem_wait, persistent_wait, 1)
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_90", "Свободный Интернет на 90 дней", "90 дней доступа", STARS_PRICE_90_DAYS)


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, bot: Bot):
    if await is_purchase_maintenance_enabled():
        await bot.answer_pre_checkout_query(
            q.id,
            ok=False,
            error_message=await get_purchase_maintenance_text(),
        )
        return
    tariff = TARIFFS.get(q.invoice_payload)
    if not tariff:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message=await get_text("payment_payload_error"))
        return
    if q.currency != tariff["currency"]:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message=await get_text("payment_currency_error"))
        return
    if q.total_amount != tariff["amount"]:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message=await get_text("payment_amount_error"))
        return
    ready, reason = await checkout_readiness()
    if not ready:
        logger.warning("pre_checkout rejected: readiness degraded: %s", reason)
        await bot.answer_pre_checkout_query(
            q.id,
            ok=False,
            error_message=await get_text("precheckout_unavailable"),
        )
        return
    try:
        q_user = getattr(q, "from_user", None)
        q_user_id = int(getattr(q_user, "id", 0) or 0)
        q_username = getattr(q_user, "username", None)
        q_first_name = getattr(q_user, "first_name", None)
        if q_user_id > 0:
            await ensure_user_exists(q_user_id, q_username, q_first_name)
            await upsert_payment_precheck(q.id, q_user_id, q.invoice_payload, status="precheck_passed")
            # Security & Reliability: helper path is checked before pre-checkout confirmation
            await check_awg_container()
    except Exception as e:
        logger.exception("pre_checkout precheck failed: %s", e)
        if "q_user_id" in locals() and q_user_id > 0:
            await upsert_payment_precheck(q.id, q_user_id, q.invoice_payload, status="failed", error_message=str(e)[:400])
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message=await get_text("precheckout_unavailable"))
        return
    await bot.answer_pre_checkout_query(q.id, ok=True)
    await mark_payment_precheck_status(q.id, "confirmed")


async def _log_critical_delivery_error(payment_id: str, user_id: int, error: str) -> None:
    line = f"{utc_now_naive().isoformat()} payment_id={payment_id} user_id={user_id} error={error}\n"
    CRITICAL_ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CRITICAL_ERRORS_LOG.open("a", encoding="utf-8") as fp:
        fp.write(line)


async def _send_user_active_config(message: types.Message, user_id: int) -> bool:
    configs = await get_user_keys(user_id)
    if not configs:
        return False
    key_id, device_num, cfg, vpn_key = configs[0]
    filename = f"config_{key_id}_device_{device_num}.conf"
    await message.answer_document(types.BufferedInputFile(cfg.encode("utf-8"), filename=filename))
    if vpn_key:
        await message.answer(f"<code>{vpn_key}</code>", parse_mode="HTML")
        if qrcode is not None:
            qr = qrcode.QRCode(border=1, box_size=8)
            qr.add_data(vpn_key)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            await message.answer_photo(types.BufferedInputFile(buffer.read(), filename=f"{filename}.png"), caption="QR для быстрого импорта")
    return True


@router.message(F.successful_payment)
async def success_pay(message: types.Message):
    payment = message.successful_payment
    tariff = TARIFFS.get(payment.invoice_payload)
    if not tariff:
        await message.answer(await get_text("payment_payload_error"))
        return
    if payment.currency != tariff["currency"]:
        await message.answer(await get_text("payment_currency_error"))
        return
    if payment.total_amount != tariff["amount"]:
        await message.answer(await get_text("payment_amount_error"))
        return

    current_status = await get_payment_status(payment.telegram_payment_charge_id)
    if current_status == "applied" or await payment_already_processed(payment.telegram_payment_charge_id):
        await message.answer(await get_text("payment_already_processed"))
        return
    if current_status == "provisioning":
        await message.answer(await get_text("payment_already_provisioning"))
        return

    raw_payload = {
        "invoice_payload": payment.invoice_payload,
        "currency": payment.currency,
        "total_amount": payment.total_amount,
        "telegram_payment_charge_id": payment.telegram_payment_charge_id,
        "provider_payment_charge_id": payment.provider_payment_charge_id,
    }
    try:
        await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await save_payment(
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            provider_payment_charge_id=payment.provider_payment_charge_id,
            user_id=message.from_user.id,
            payload=payment.invoice_payload,
            amount=payment.total_amount,
            currency=payment.currency,
            payment_method=tariff["method"],
            status="received",
            raw_payload_json=json.dumps(raw_payload, ensure_ascii=False),
        )
        await message.answer(await get_text("payment_received"))
        await update_last_provision_status(payment.telegram_payment_charge_id, "payment_received")
        await message.answer(await get_text("payment_provisioning_started"))
        await update_last_provision_status(payment.telegram_payment_charge_id, "provisioning")
        applied = await process_payment_provisioning(
            payment_id=payment.telegram_payment_charge_id,
            user_id=message.from_user.id,
            payload=payment.invoice_payload,
            days=tariff["days"],
            bot=message.bot,
        )
        if applied:
            result_status = "ready"
            await update_last_provision_status(payment.telegram_payment_charge_id, "ready")
            await mark_ready_notification_sent(payment.telegram_payment_charge_id)
            try:
                sent = await _send_user_active_config(message, message.from_user.id)
                if not sent:
                    result_status = "ready_config_pending"
                    await update_last_provision_status(payment.telegram_payment_charge_id, "ready_config_pending")
                    await write_audit_log(
                        message.from_user.id,
                        "payment_config_pending_after_apply",
                        f"payment_id={payment.telegram_payment_charge_id}",
                    )
                    raise RuntimeError("active config not found after applied payment")
            except Exception as delivery_error:
                result_status = "ready_config_pending"
                await _log_critical_delivery_error(payment.telegram_payment_charge_id, message.from_user.id, str(delivery_error)[:500])
                logger.exception("Критическая ошибка отправки конфига после оплаты: %s", delivery_error)
            await message.answer(
                await get_payment_result_text(result_status),
                parse_mode="HTML",
                reply_markup=get_post_payment_kb(),
            )
        else:
            await message.answer(
                await get_payment_result_text("pending"),
                reply_markup=get_post_payment_kb(),
            )
    except Exception as e:
        logger.exception("Ошибка обработки оплаты: %s", e)
        retry_at = (utc_now_naive() + timedelta(seconds=PAYMENT_RETRY_DELAY_SECONDS)).isoformat()
        await update_payment_status(
            payment.telegram_payment_charge_id,
            "needs_repair",
            error_message=str(e)[:500],
            next_retry_at=retry_at,
        )
        await write_audit_log(message.from_user.id, "payment_provision_failed", str(e)[:500])
        await message.answer(
            await get_text("payment_error")
        )


async def process_payment_provisioning(payment_id: str, user_id: int, payload: str, days: int, bot: Bot | None = None) -> bool:
    lock_token = str(uuid.uuid4())
    lease_expires_at = (utc_now_naive() + timedelta(seconds=PAYMENT_PROVISIONING_LEASE_SECONDS)).isoformat()
    claimed = await claim_payment_and_job_for_provisioning(payment_id, lock_token, lease_expires_at)
    if not claimed:
        current_status = await get_payment_status(payment_id)
        return current_status == "applied"

    try:
        await write_audit_log(user_id, "payment_provisioning_started", f"payment_id={payment_id}; payload={payload}")
        new_until = await issue_subscription(user_id, days, operation_id=payment_id)
        finalized = await finalize_payment_and_job(
            payment_id=payment_id,
            lock_token=lock_token,
            status="applied",
            provisioned_until=new_until.isoformat(),
        )
        if not finalized:
            raise RuntimeError("payment finalization lock lost")
        rewarded = await apply_referral_rewards_on_first_payment(user_id, payment_id)
        if rewarded:
            await notify_inviter_about_referral_reward(bot, user_id)
        else:
            await apply_referral_recurring_inviter_reward(
                invitee_user_id=user_id,
                payment_id=payment_id,
                purchased_days=days,
            )
        return True
    except Exception as e:
        retry_at = (utc_now_naive() + timedelta(seconds=PAYMENT_RETRY_DELAY_SECONDS)).isoformat()
        await finalize_payment_and_job(
            payment_id=payment_id,
            lock_token=lock_token,
            status="needs_repair",
            error_message=str(e)[:500],
            next_retry_at=retry_at,
        )
        attempts = await get_provisioning_attempt_count(payment_id)
        if attempts >= PAYMENT_MAX_ATTEMPTS:
            reason = f"max_attempts_exceeded attempts={attempts}; last_error={str(e)[:220]}"
            await mark_payment_stuck_manual(payment_id, reason)
            await write_audit_log(user_id, "payment_provisioning_stuck_manual", f"payment_id={payment_id}; {reason}")
        await write_audit_log(user_id, "payment_provisioning_failed", f"payment_id={payment_id}; retry_at={retry_at}; error={str(e)[:300]}")
        raise


async def _notify_admin_stuck(bot: Bot | None, payment_id: str, user_id: int, reason: str) -> None:
    if bot is None:
        return
    try:
        await bot.send_message(
            ADMIN_ID,
            (
                "⚠️ <b>Платёж требует ручной проверки</b>\n\n"
                f"payment_id=<code>{payment_id}</code>\n"
                f"user_id=<code>{user_id}</code>\n"
                f"reason={reason[:200]}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Не удалось отправить stuck alert администратору: %s", e)


async def payment_recovery_worker(bot: Bot | None = None) -> int:
    repaired = 0
    jobs = await get_repairable_payments(limit=25)
    for payment_id, user_id, payload in jobs:
        attempts = await get_provisioning_attempt_count(payment_id)
        if attempts >= PAYMENT_MAX_ATTEMPTS:
            reason = f"max_attempts_exceeded attempts={attempts}"
            await mark_payment_stuck_manual(payment_id, reason)
            await write_audit_log(user_id, "payment_recovery_stuck_manual", f"payment_id={payment_id}; {reason}")
            await _notify_admin_stuck(bot, payment_id, user_id, reason)
            continue
        tariff = TARIFFS.get(payload)
        if not tariff:
            await update_payment_status(payment_id, "failed", error_message="unknown payload in recovery")
            continue
        try:
            done = await process_payment_provisioning(payment_id, user_id, payload, tariff["days"], bot=bot)
            repaired += int(done)
            if done:
                await update_last_provision_status(payment_id, "ready")
                if bot is not None and await mark_ready_notification_sent(payment_id):
                    await bot.send_message(
                        user_id,
                        await get_text("payment_recovery_ready"),
                    )
        except Exception as e:
            logger.warning("Recovery failed for payment=%s: %s", payment_id, e)
            attempts = await get_provisioning_attempt_count(payment_id)
            if attempts >= PAYMENT_MAX_ATTEMPTS:
                reason = f"max_attempts_exceeded attempts={attempts}; last_error={str(e)[:180]}"
                await mark_payment_stuck_manual(payment_id, reason)
                await write_audit_log(user_id, "payment_recovery_stuck_manual", f"payment_id={payment_id}; {reason}")
                await _notify_admin_stuck(bot, payment_id, user_id, reason)
    return repaired


async def manual_retry_activation(payment_id: str, bot: Bot | None = None) -> dict[str, str]:
    row = await fetchone(
        """
        SELECT user_id, payload, status
        FROM payments
        WHERE telegram_payment_charge_id = ?
        """,
        (payment_id,),
    )
    if not row:
        return {"result": "no_payment", "message": "Платёж не найден."}

    user_id = int(row[0])
    payload = str(row[1] or "")
    status = str(row[2] or "")
    if status == "applied":
        return {"result": "already_applied", "message": "Платёж уже применён, повтор не требуется."}
    if status == "provisioning":
        return {"result": "in_progress", "message": "Активация уже выполняется recovery-процессом."}
    if status not in {"received", "needs_repair", "failed", "stuck_manual"}:
        return {"result": "not_retryable", "message": f"Текущий статус не подходит для retry: {status}"}

    tariff = TARIFFS.get(payload)
    if not tariff:
        return {"result": "unknown_payload", "message": f"Неизвестный payload={payload}. Нужна ручная проверка."}

    try:
        done = await process_payment_provisioning(payment_id, user_id, payload, tariff["days"], bot=bot)
        if done:
            await update_last_provision_status(payment_id, "ready")
            return {"result": "succeeded", "message": "Retry выполнен успешно, доступ выдан."}
        current_status = await get_payment_status(payment_id)
        if current_status == "applied":
            return {"result": "already_applied", "message": "Платёж уже применён."}
        if current_status == "provisioning":
            return {"result": "in_progress", "message": "Активация уже в процессе, повтор не запущен."}
        return {"result": "no_op", "message": "Нечего повторять: кейс не перешёл в provisioning."}
    except Exception as e:
        logger.warning("manual retry failed for payment=%s: %s", payment_id, e)
        return {"result": "failed", "message": f"Retry завершился ошибкой: {str(e)[:200]}"}
