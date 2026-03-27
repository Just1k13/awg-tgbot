import json
from aiogram import Bot, F, Router, types
from aiogram.types import LabeledPrice, PreCheckoutQuery

from awg_backend import count_free_ip_slots, get_required_new_ips_for_user, issue_subscription
from config import PURCHASE_CLICK_COOLDOWN_SECONDS, PURCHASE_RATE_LIMIT_TTL_SECONDS, STARS_PRICE_7_DAYS, STARS_PRICE_30_DAYS, logger
from database import claim_payment_for_provisioning, ensure_user_exists, get_payment_status, payment_already_processed, save_payment, update_payment_status, write_audit_log
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


async def _has_capacity_for_user(user_id: int) -> tuple[bool, int, int]:
    required_new_ips = await get_required_new_ips_for_user(user_id)
    if required_new_ips <= 0:
        return True, 0, await count_free_ip_slots()
    free_slots = await count_free_ip_slots()
    return free_slots >= required_new_ips, required_new_ips, free_slots


@router.callback_query(F.data == CB_BUY_7)
async def buy_7_days(cb: types.CallbackQuery, bot: Bot):
    limited, wait_seconds = is_purchase_rate_limited(cb.from_user.id)
    if limited:
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    has_capacity, required, free_slots = await _has_capacity_for_user(cb.from_user.id)
    if not has_capacity:
        await cb.answer(
            f"Свободные IP закончились: нужно {required}, доступно {free_slots}. Напишите в поддержку.",
            show_alert=True,
        )
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_7", "Свободный Интернет на 7 дней", "7 дней доступа", STARS_PRICE_7_DAYS)


@router.callback_query(F.data == CB_BUY_30)
async def buy_30_days(cb: types.CallbackQuery, bot: Bot):
    limited, wait_seconds = is_purchase_rate_limited(cb.from_user.id)
    if limited:
        await cb.answer(f"Подождите {wait_seconds} сек.", show_alert=True)
        return
    has_capacity, required, free_slots = await _has_capacity_for_user(cb.from_user.id)
    if not has_capacity:
        await cb.answer(
            f"Свободные IP закончились: нужно {required}, доступно {free_slots}. Напишите в поддержку.",
            show_alert=True,
        )
        return
    await cb.answer()
    await _send_stars_invoice(bot, cb.message.chat.id, "sub_30", "Свободный Интернет на 30 дней", "30 дней доступа", STARS_PRICE_30_DAYS)


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, bot: Bot):
    if q.invoice_payload not in TARIFFS:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="Некорректный платеж.")
        return
    has_capacity, required, free_slots = await _has_capacity_for_user(q.from_user.id)
    if not has_capacity:
        await bot.answer_pre_checkout_query(
            q.id,
            ok=False,
            error_message=f"Свободные IP закончились: нужно {required}, доступно {free_slots}.",
        )
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
        claimed = await claim_payment_for_provisioning(payment.telegram_payment_charge_id)
        if not claimed:
            current_status = await get_payment_status(payment.telegram_payment_charge_id)
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
        new_until = await issue_subscription(message.from_user.id, tariff["days"])
        await update_payment_status(payment.telegram_payment_charge_id, "applied", provisioned_until=new_until.isoformat())
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
        await update_payment_status(payment.telegram_payment_charge_id, "failed", error_message=str(e)[:500])
        await write_audit_log(message.from_user.id, "payment_provision_failed", str(e)[:500])
        await message.answer(
            "❌ Платёж получен, но возникла ошибка при активации доступа. Администратор увидит это в журнале и сможет повторно выдать доступ."
        )
