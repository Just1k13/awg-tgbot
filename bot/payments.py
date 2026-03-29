import json
import uuid
from datetime import timedelta

from aiogram import Bot, F, Router, types
from aiogram.types import LabeledPrice, PreCheckoutQuery

from awg_backend import issue_subscription
from config import (
    ADMIN_ID,
    PAYMENT_MAX_ATTEMPTS,
    PAYMENT_PROVISIONING_LEASE_SECONDS,
    PAYMENT_RETRY_DELAY_SECONDS,
    PURCHASE_CLICK_COOLDOWN_SECONDS,
    PURCHASE_RATE_LIMIT_TTL_SECONDS,
    STARS_PRICE_7_DAYS,
    STARS_PRICE_30_DAYS,
    logger,
)
from database import (
    claim_payment_and_job_for_provisioning,
    ensure_user_exists,
    finalize_payment_and_job,
    get_provisioning_attempt_count,
    get_payment_status,
    get_repairable_payments,
    mark_payment_stuck_manual,
    payment_already_processed,
    persistent_guard_hit,
    save_payment,
    update_payment_status,
    write_audit_log,
)
from helpers import utc_now_naive
from keyboards import get_post_payment_kb
from ui_constants import CB_BUY_30, CB_BUY_7

router = Router()
purchase_rate_limit: dict[int, object] = {}


TARIFFS = {
    "sub_7": {"days": 7, "amount": STARS_PRICE_7_DAYS, "currency": "XTR", "method": "stars"},
    "sub_30": {"days": 30, "amount": STARS_PRICE_30_DAYS, "currency": "XTR", "method": "stars"},
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


@router.callback_query(F.data == CB_BUY_7)
async def buy_7_days(cb: types.CallbackQuery, bot: Bot):
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
    mem_limited, mem_wait = is_purchase_rate_limited(cb.from_user.id)
    persistent_limited, persistent_wait = await is_purchase_rate_limited_persistent(cb.from_user.id, CB_BUY_30)
    limited = persistent_limited or mem_limited
    if limited:
        wait_seconds = max(mem_wait, persistent_wait, 1)
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_30", "Свободный Интернет на 30 дней", "30 дней доступа", STARS_PRICE_30_DAYS)


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, bot: Bot):
    tariff = TARIFFS.get(q.invoice_payload)
    if not tariff:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="Некорректный платеж.")
        return
    if q.currency != tariff["currency"]:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="Некорректная валюта платежа.")
        return
    if q.total_amount != tariff["amount"]:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="Некорректная сумма платежа.")
        return
    await bot.answer_pre_checkout_query(q.id, ok=True)


@router.message(F.successful_payment)
async def success_pay(message: types.Message):
    payment = message.successful_payment
    tariff = TARIFFS.get(payment.invoice_payload)
    if not tariff:
        await message.answer("Ошибка оплаты: неизвестный payload.")
        return
    if payment.currency != tariff["currency"]:
        await message.answer("Ошибка оплаты: неверная валюта.")
        return
    if payment.total_amount != tariff["amount"]:
        await message.answer("Ошибка оплаты: неверная сумма.")
        return

    current_status = await get_payment_status(payment.telegram_payment_charge_id)
    if current_status == "applied" or await payment_already_processed(payment.telegram_payment_charge_id):
        await message.answer("✅ Этот платёж уже был обработан.")
        return
    if current_status == "provisioning":
        await message.answer("⏳ Платёж уже обрабатывается. Подождите немного и проверьте профиль или конфиги.")
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
        applied = await process_payment_provisioning(
            payment_id=payment.telegram_payment_charge_id,
            user_id=message.from_user.id,
            payload=payment.invoice_payload,
            days=tariff["days"],
        )
        if applied:
            await message.answer(
                (
                    "🎉 <b>Оплата подтверждена</b>\n\n"
                    "Подписка активирована ✅\n"
                    "Следующий шаг — получите подключение и импортируйте его в Amnezia."
                ),
                parse_mode="HTML",
                reply_markup=get_post_payment_kb(),
            )
        else:
            await message.answer(
                "⏳ Платёж принят. Выдача доступа выполняется в фоне, это обычно занимает до минуты."
                "\n\nКогда всё будет готово, нажмите «🔑 Получить подключение».",
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
            "❌ Платёж получен, но возникла ошибка при активации доступа. Администратор увидит это в журнале и сможет повторно выдать доступ."
        )


async def process_payment_provisioning(payment_id: str, user_id: int, payload: str, days: int) -> bool:
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
            done = await process_payment_provisioning(payment_id, user_id, payload, tariff["days"])
            repaired += int(done)
        except Exception as e:
            logger.warning("Recovery failed for payment=%s: %s", payment_id, e)
            attempts = await get_provisioning_attempt_count(payment_id)
            if attempts >= PAYMENT_MAX_ATTEMPTS:
                reason = f"max_attempts_exceeded attempts={attempts}; last_error={str(e)[:180]}"
                await mark_payment_stuck_manual(payment_id, reason)
                await write_audit_log(user_id, "payment_recovery_stuck_manual", f"payment_id={payment_id}; {reason}")
                await _notify_admin_stuck(bot, payment_id, user_id, reason)
    return repaired
