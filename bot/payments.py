import json
import uuid
from datetime import datetime
from aiogram import Bot, F, Router, types
from aiogram.types import LabeledPrice, PreCheckoutQuery

from awg_backend import has_capacity_for_user, issue_subscription
from config import PURCHASE_CLICK_COOLDOWN_SECONDS, PURCHASE_RATE_LIMIT_TTL_SECONDS, STARS_PRICE_7_DAYS, STARS_PRICE_30_DAYS, logger
from database import (
    bump_payment_retry,
    claim_payment_for_provisioning,
    ensure_user_exists,
    get_payment_processing_info,
    get_payment_status,
    list_incomplete_payments,
    payment_already_processed,
    save_payment,
    set_payment_operation,
    update_payment_status,
    write_audit_log,
)
from helpers import utc_now_naive
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


async def _ensure_capacity_or_notify(target, user_id: int) -> bool:
    has_capacity, needed, free = await has_capacity_for_user(user_id)
    if has_capacity:
        return True
    text = (
        "🚫 <b>Сейчас нет свободных слотов для новых конфигов</b>\n\n"
        f"Нужно слотов: <b>{needed}</b>\n"
        f"Свободно сейчас: <b>{free}</b>\n\n"
        "Попробуйте позже или напишите в поддержку."
    )
    try:
        await target.answer(text, parse_mode="HTML", show_alert=True)
    except TypeError:
        await target.answer(text, parse_mode="HTML")
    return False


async def process_payment_by_id(user_id: int, payment_id: str) -> tuple[str, object | None]:
    info = await get_payment_processing_info(payment_id)
    if not info:
        return "failed_final", None
    if info["status"] == "applied":
        until = info["provisioned_until"]
        if isinstance(until, str) and until:
            try:
                return "applied", datetime.fromisoformat(until)
            except ValueError:
                return "applied", None
        return "applied", None
    tariff = TARIFFS.get(info["payload"])
    if not tariff:
        await update_payment_status(payment_id, "failed_final", error_message="unknown tariff payload")
        return "failed_final", None
    has_capacity, needed, free = await has_capacity_for_user(user_id)
    if not has_capacity:
        await update_payment_status(
            payment_id,
            "failed_retriable",
            error_message=f"capacity unavailable: needed={needed}, free={free}",
        )
        return "failed_retriable", None

    op_id = info["provisioning_op_id"] or f"payop-{uuid.uuid4().hex}"
    await set_payment_operation(payment_id, op_id)
    try:
        new_until = await issue_subscription(
            user_id,
            tariff["days"],
            payment_id=payment_id,
            operation_id=op_id,
        )
        await update_payment_status(payment_id, "applied", provisioned_until=new_until.isoformat(), error_message=None)
        return "applied", new_until
    except Exception as e:
        await bump_payment_retry(payment_id, str(e))
        await write_audit_log(user_id, "payment_provision_failed", f"{payment_id}: {str(e)[:500]}")
        raise


async def recover_incomplete_payments() -> dict[str, int]:
    stats = {"checked": 0, "applied": 0, "failed": 0}
    for item in await list_incomplete_payments():
        stats["checked"] += 1
        pid = item["payment_id"]
        if not await claim_payment_for_provisioning(pid):
            continue
        try:
            status, _ = await process_payment_by_id(item["user_id"], pid)
            if status == "applied":
                stats["applied"] += 1
            else:
                stats["failed"] += 1
        except Exception:
            stats["failed"] += 1
            logger.exception("Recovery payment failed: %s", pid)
    return stats


@router.callback_query(F.data == CB_BUY_7)
async def buy_7_days(cb: types.CallbackQuery, bot: Bot):
    limited, wait_seconds = is_purchase_rate_limited(cb.from_user.id)
    if limited:
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    if not await _ensure_capacity_or_notify(cb, cb.from_user.id):
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_7", "Свободный Интернет на 7 дней", "7 дней доступа", STARS_PRICE_7_DAYS)


@router.callback_query(F.data == CB_BUY_30)
async def buy_30_days(cb: types.CallbackQuery, bot: Bot):
    limited, wait_seconds = is_purchase_rate_limited(cb.from_user.id)
    if limited:
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    if not await _ensure_capacity_or_notify(cb, cb.from_user.id):
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_30", "Свободный Интернет на 30 дней", "30 дней доступа", STARS_PRICE_30_DAYS)


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, bot: Bot):
    if q.invoice_payload not in TARIFFS:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="Некорректный платеж.")
        return
    has_capacity, _, _ = await has_capacity_for_user(q.from_user.id)
    if not has_capacity:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="Слоты сейчас закончились. Повторите позже.")
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

    payment_id = payment.telegram_payment_charge_id
    current_status = await get_payment_status(payment_id)
    if current_status == "applied" or await payment_already_processed(payment_id):
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
            telegram_payment_charge_id=payment_id,
            provider_payment_charge_id=payment.provider_payment_charge_id,
            user_id=message.from_user.id,
            payload=payment.invoice_payload,
            amount=payment.total_amount,
            currency=payment.currency,
            payment_method=tariff["method"],
            status="received",
            raw_payload_json=json.dumps(raw_payload, ensure_ascii=False),
        )
        claimed = await claim_payment_for_provisioning(payment_id)
        if not claimed:
            current_status = await get_payment_status(payment_id)
            if current_status == "applied":
                await message.answer("✅ Этот платёж уже был обработан.")
            else:
                await message.answer("⏳ Платёж уже обрабатывается. Подождите немного и проверьте профиль или конфиги.")
            return
        await write_audit_log(
            message.from_user.id,
            "payment_successful_payment",
            json.dumps({**raw_payload, "method": tariff["method"]}, ensure_ascii=False),
        )
        status, new_until = await process_payment_by_id(message.from_user.id, payment_id)
        if status != "applied" or new_until is None:
            await message.answer("⏳ Платёж принят, но свободные слоты заняты. Выполним выдачу автоматически при освобождении слотов.")
            return
        await message.answer(
            (
                "🎉 <b>Подписка активирована / продлена</b>\n\n"
                f"📅 <b>Действует до:</b> {new_until.strftime('%d.%m.%Y %H:%M')}\n"
                "🔑 В разделе <b>Конфиги</b> доступны ключ доступа и <b>.conf</b>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Ошибка обработки оплаты: %s", e)
        await update_payment_status(payment_id, "failed_retriable", error_message=str(e)[:500])
        await write_audit_log(message.from_user.id, "payment_provision_failed", str(e)[:500])
        await message.answer(
            "❌ Платёж получен, но возникла ошибка при активации доступа. Администратор увидит это в журнале и сможет повторно выдать доступ."
        )
