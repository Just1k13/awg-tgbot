import re
from datetime import datetime

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    ADMIN_ID,
    SERVER_NAME,
    STARS_PRICE_7_DAYS,
    STARS_PRICE_30_DAYS,
    STARS_PRICE_90_DAYS,
    USER_REISSUE_COOLDOWN_SECONDS,
    logger,
    get_support_username,
    maybe_set_support_username,
)
from awg_backend import get_awg_peers
from awg_backend import issue_subscription
from awg_backend import reissue_user_device
from database import (
    activate_promo_code,
    clear_pending_admin_action,
    ensure_user_exists,
    fetchall,
    get_latest_user_payment_summary,
    get_pending_admin_action,
    get_user_keys,
    get_user_subscription,
    normalize_promo_code,
    rollback_promo_activation_reservation,
    set_pending_admin_action,
    persistent_guard_hit,
    write_audit_log,
)
from device_activity import render_device_activity_line
from helpers import escape_html, format_remaining_time, format_tg_username, get_status_text, subscription_is_active, utc_now_naive
from keyboards import (
    get_buy_inline_kb,
    get_configs_empty_kb,
    get_config_result_kb,
    get_configs_devices_kb,
    get_instruction_inline_kb,
    get_main_menu,
    get_profile_inline_kb,
    get_support_back_kb,
    get_support_center_kb,
    get_user_reissue_confirm_kb,
)
from texts import (
    get_activation_status_text,
    get_instruction_with_policy_text,
    get_support_full_text,
    get_support_short_text,
)
from ui_constants import (
    BTN_BUY,
    BTN_CONFIGS,
    BTN_GUIDE,
    BTN_PROFILE,
    BTN_REFERRALS,
    BTN_SUPPORT,
    CB_CHECK_ACTIVATION_STATUS,
    CB_CONFIG_CONF_PREFIX,
    CB_CONFIG_DEVICE_PREFIX,
    CB_OPEN_CONFIGS,
    CB_OPEN_SUPPORT,
    CB_SHOW_BUY_MENU,
    CB_SHOW_INSTRUCTION,
    CB_SUPPORT_BACK,
    CB_SUPPORT_CONNECTION,
    CB_SUPPORT_PAYMENT,
    CB_SUPPORT_TERMS,
    CB_USER_REISSUE_DEVICE_PREFIX,
    CB_USER_REISSUE_CANCEL,
    CB_USER_REISSUE_CONFIRM,
)
from content_settings import get_text
from referrals import capture_referral_start, get_referral_screen_data
from maintenance import get_purchase_maintenance_text, is_purchase_maintenance_enabled

router = Router()


def _config_filename_prefix() -> str:
    base = re.sub(r"[^\w.-]+", "_", (SERVER_NAME or "configs").strip(), flags=re.UNICODE).strip("._")
    return base or "configs"


def _format_last_payment_status(status: str | None) -> str:
    mapped = {
        "applied": "успешно",
        "received": "в обработке",
        "provisioning": "в обработке",
        "needs_repair": "нужна проверка",
        "stuck_manual": "нужна проверка",
        "failed": "нужна проверка",
    }
    return mapped.get(str(status or "").strip(), "в обработке")


def _format_last_payment_tariff(payload: str | None) -> str:
    if payload == "sub_7":
        return "7 дней"
    if payload == "sub_30":
        return "30 дней"
    if payload == "sub_90":
        return "90 дней"
    return "—"


def _format_last_payment_date(created_at: str | None) -> str:
    if not created_at:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(created_at))
        return parsed.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(created_at).replace("T", " ")[:16]


def _build_last_payment_fields(payment_summary: dict | None) -> dict[str, str]:
    fields = {
        "payment_tariff": "нет данных",
        "payment_date": "—",
        "payment_amount": "—",
        "payment_status": "—",
    }
    if not payment_summary:
        return fields
    fields["payment_tariff"] = _format_last_payment_tariff(payment_summary.get("payload"))
    fields["payment_date"] = _format_last_payment_date(payment_summary.get("created_at"))
    fields["payment_amount"] = f"{payment_summary['amount']} {payment_summary['currency']}"
    fields["payment_status"] = _format_last_payment_status(str(payment_summary.get("status")))
    return fields


async def _build_user_device_activity_lines(user_id: int) -> list[str]:
    key_rows = await fetchall(
        """
        SELECT device_num, public_key
        FROM keys
        WHERE user_id = ?
          AND state = 'active'
          AND public_key NOT LIKE 'pending:%'
        ORDER BY device_num
        LIMIT 2
        """,
        (user_id,),
    )
    if not key_rows:
        return ["• нет данных"]

    runtime_available = True
    peer_by_public_key: dict[str, dict] = {}
    try:
        runtime_peers = await get_awg_peers()
        peer_by_public_key = {
            str(peer.get("public_key") or "").strip(): peer
            for peer in runtime_peers
            if str(peer.get("public_key") or "").strip()
        }
    except Exception:
        runtime_available = False

    now = utc_now_naive()
    lines: list[str] = []
    for device_num, public_key in key_rows:
        peer = peer_by_public_key.get(str(public_key).strip())
        lines.append(
            render_device_activity_line(
                device_num=int(device_num),
                has_runtime_peer=peer is not None,
                last_handshake_at=peer.get("latest_handshake_at") if peer else None,
                runtime_available=runtime_available,
                now=now,
            )
        )
    return lines


async def _send_buy_menu(target, user_id: int):
    if await is_purchase_maintenance_enabled():
        await target.answer(await get_purchase_maintenance_text())
        return
    sub_until = await get_user_subscription(user_id)
    price_lines = [
        f"• 7 дней — {STARS_PRICE_7_DAYS}⭐",
        f"• 30 дней — {STARS_PRICE_30_DAYS}⭐",
        f"• 90 дней — {STARS_PRICE_90_DAYS}⭐",
    ]
    if subscription_is_active(sub_until):
        remaining = format_remaining_time(sub_until)
        await target.answer(
            await get_text("renew_menu", remaining=remaining, price_lines="\n".join(price_lines)),
            parse_mode="HTML",
            reply_markup=get_buy_inline_kb(),
        )
        return
    await target.answer(
        await get_text("buy_menu", price_lines="\n".join(price_lines)),
        parse_mode="HTML",
        reply_markup=get_buy_inline_kb(),
    )


async def _send_configs_menu(target, user: types.User):
    configs = await get_user_keys(user.id)
    if not configs:
        await target.answer(
            await get_text("configs_empty"),
            parse_mode="HTML",
            reply_markup=get_configs_empty_kb(),
        )
        return

    await target.answer(
        await get_text("configs_menu"),
        parse_mode="HTML",
        reply_markup=get_configs_devices_kb(configs),
    )


async def _find_user_config_by_key_id(user_id: int, key_id: int):
    configs = await get_user_keys(user_id)
    return next((item for item in configs if item[0] == key_id), None)


def _terms_text() -> str:
    return (
        "📄 <b>Краткие условия</b>\n\n"
        "• Сервис выдаёт доступ AmneziaWG для личного использования (single-server MVP).\n"
        "• Оплата даёт доступ на 7 / 30 / 90 дней.\n"
        "• После успешной оплаты выдаётся цифровой доступ (vpn:// и .conf).\n"
        "• По вопросам поддержки и возвратов: через раздел помощи."
    )


def _payment_support_text() -> str:
    return (
        "💳 <b>Поддержка по оплате</b>\n\n"
        "По вопросам оплаты и активации после оплаты напишите в поддержку и укажите ваш "
        "<code>user_id</code> из профиля."
    )


async def _send_support_center(target) -> None:
    await target.answer(
        f"{await get_support_full_text()}\n\nВыберите, с чем нужна помощь:",
        parse_mode="HTML",
        reply_markup=get_support_center_kb(),
    )


async def _start_user_reissue_flow(target, user: types.User, *, key_id: int | None = None) -> None:
    sub_until = await get_user_subscription(user.id)
    if not subscription_is_active(sub_until):
        await target.answer("Сейчас активной подписки нет. Сначала оформите или продлите доступ.")
        return
    configs = await get_user_keys(user.id)
    if not configs:
        await target.answer(
            "Не найден активный конфиг для перевыпуска. Откройте «🔑 Подключение» или напишите в поддержку.",
            reply_markup=get_support_back_kb(),
        )
        return
    selected = configs[0]
    if key_id is not None:
        selected = next((item for item in configs if item[0] == key_id), configs[0])
    _, device_num, _, _ = selected
    await clear_pending_admin_action(user.id, "user_reissue_device")
    await set_pending_admin_action(
        user.id,
        "user_reissue_device",
        {"action": "user_reissue_device", "device_num": int(device_num)},
    )
    await target.answer(
        (
            "⚠️ <b>Перевыпуск доступа</b>\n\n"
            "Текущий конфиг устройства будет отключён.\n"
            "Старый vpn:// и .conf перестанут работать.\n\n"
            "Продолжить перевыпуск?"
        ),
        parse_mode="HTML",
        reply_markup=get_user_reissue_confirm_kb(),
    )


def _help_clients_kb() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📱 iOS", url="https://apps.apple.com/app/amnezia-vpn/id1600529902")
    kb.button(text="🤖 Android", url="https://play.google.com/store/apps/details?id=org.amnezia.vpn")
    kb.button(text="🪟 Windows", url="https://amnezia.org/downloads")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "noop")
async def noop_callback(cb: types.CallbackQuery):
    await cb.answer()


@router.message(Command("start"))
async def start(message: types.Message, command: CommandObject):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if command.args:
        await capture_referral_start(message.from_user.id, command.args.strip())
    if message.from_user.id == ADMIN_ID:
        maybe_set_support_username(message.from_user.username)
    await message.answer(await get_text("start"), parse_mode="HTML", reply_markup=get_main_menu(message.from_user.id, ADMIN_ID))


@router.message(Command("my_config"))
async def my_config_cmd(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    configs = await get_user_keys(message.from_user.id)
    if not configs:
        await message.answer(await get_text("configs_empty"), parse_mode="HTML", reply_markup=get_instruction_inline_kb())
        return
    key_id, device_num, cfg, vpn_key = configs[0]
    await message.answer_document(
        types.BufferedInputFile(
            cfg.encode("utf-8"),
            filename=f"{_config_filename_prefix()}_device_{device_num}.conf",
        ),
        caption=f"Ваш активный конфиг (device {device_num})",
    )
    if vpn_key:
        await message.answer(f"<code>{escape_html(vpn_key)}</code>", parse_mode="HTML")


@router.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "Выберите официальный клиент AmneziaWG для установки:",
        reply_markup=_help_clients_kb(),
    )


@router.message(Command("support"))
async def support_cmd(message: types.Message):
    await support(message)


@router.message(Command("paysupport"))
async def paysupport_cmd(message: types.Message):
    await message.answer(_payment_support_text(), parse_mode="HTML")


@router.message(Command("terms"))
async def terms_cmd(message: types.Message):
    await message.answer(_terms_text(), parse_mode="HTML")


@router.message(Command("promo"))
async def promo_cmd(message: types.Message, command: CommandObject):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    code = normalize_promo_code(command.args or "")
    if not code:
        await message.answer("Формат: <code>/promo CODE</code>", parse_mode="HTML")
        return
    try:
        activation = await activate_promo_code(message.from_user.id, code)
        status = activation["status"]
        if status == "not_found":
            await write_audit_log(message.from_user.id, "promo_activation_failed", f"code={code}; reason=not_found")
            await message.answer("❌ Промокод не найден.")
            return
        if status == "inactive":
            await write_audit_log(message.from_user.id, "promo_activation_failed", f"code={code}; reason=inactive")
            await message.answer("❌ Промокод выключен.")
            return
        if status == "exhausted":
            await write_audit_log(message.from_user.id, "promo_activation_failed", f"code={code}; reason=exhausted")
            await message.answer("❌ Лимит активаций исчерпан.")
            return
        if status == "already_used":
            await write_audit_log(message.from_user.id, "promo_activation_failed", f"code={code}; reason=already_used")
            await message.answer("❌ Этот промокод уже нельзя применить.")
            return

        bonus_days = int(activation["bonus_days"])
        operation_id = f"promo-{code}-{message.from_user.id}"
        new_until = await issue_subscription(message.from_user.id, bonus_days, operation_id=operation_id)
        await write_audit_log(
            message.from_user.id,
            "promo_activated",
            f"code={code}; days={bonus_days}; until={new_until.isoformat()}",
        )
        await message.answer(
            f"✅ Промокод применён: +{bonus_days} дней.\n📅 Доступ до: <b>{new_until.strftime('%d.%m.%Y %H:%M')}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Ошибка /promo: %s", e)
        await rollback_promo_activation_reservation(message.from_user.id, code)
        await write_audit_log(message.from_user.id, "promo_activation_failed", f"code={code}; reason=internal_error")
        await message.answer("❌ Не удалось применить промокод. Попробуйте позже.")


@router.message(F.text == BTN_PROFILE)
async def profile(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        maybe_set_support_username(message.from_user.username)
    sub_until = await get_user_subscription(message.from_user.id)
    status_text, until_text = get_status_text(sub_until)
    tg_username = format_tg_username(message.from_user.username)
    first_name = escape_html(message.from_user.first_name)
    is_active = subscription_is_active(sub_until)
    remaining = format_remaining_time(sub_until) if is_active else "—"
    configs = await get_user_keys(message.from_user.id)
    has_connection = bool(configs)
    connection_status = "готово ✅" if has_connection else "ещё не выдано"
    if has_connection:
        next_step = "Откройте «🔑 Подключение» и импортируйте vpn://"
    elif is_active:
        next_step = "Нажмите «⏱ Проверить статус активации» или откройте «🔑 Подключение»"
    else:
        next_step = "Нажмите «💳 Оплатить доступ»"
    payment_summary = await get_latest_user_payment_summary(message.from_user.id)
    payment_fields = _build_last_payment_fields(payment_summary)
    device_activity_lines = await _build_user_device_activity_lines(message.from_user.id)
    await message.answer(
        await get_text(
            "profile_screen",
            user_id=message.from_user.id,
            first_name=first_name,
            tg_username=escape_html(tg_username),
            status_text=status_text,
            until_text=until_text,
            remaining=remaining,
            connection_status=connection_status,
            **payment_fields,
            device_activity_block="\n".join(device_activity_lines),
            next_step=next_step,
            support_line=await get_support_short_text(),
        ),
        parse_mode="HTML",
        reply_markup=get_profile_inline_kb(is_active),
    )


@router.message(F.text == BTN_CONFIGS)
async def my_keys(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        maybe_set_support_username(message.from_user.username)
    await _send_configs_menu(message, message.from_user)


@router.callback_query(F.data.startswith(CB_CONFIG_DEVICE_PREFIX))
async def show_selected_device_config(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.answer()
    try:
        key_id = int(cb.data.removeprefix(CB_CONFIG_DEVICE_PREFIX))
    except ValueError:
        await cb.answer(await get_text("config_invalid_device"), show_alert=True)
        return

    selected = await _find_user_config_by_key_id(cb.from_user.id, key_id)
    if not selected:
        await cb.message.answer(
            await get_text("config_not_found"),
            reply_markup=get_instruction_inline_kb(),
        )
        return

    _, device_num, _cfg, vpn_key = selected
    if vpn_key and vpn_key.strip():
        await cb.message.answer(
            await get_text("config_vpn_ready", device_num=device_num, vpn_key=escape_html(vpn_key)),
            parse_mode="HTML",
            reply_markup=get_config_result_kb(key_id),
        )
    else:
        await cb.message.answer(
            await get_text("config_vpn_missing"),
            reply_markup=get_configs_empty_kb(),
        )


@router.callback_query(F.data.startswith(CB_CONFIG_CONF_PREFIX))
async def send_selected_device_conf(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.answer()
    try:
        key_id = int(cb.data.removeprefix(CB_CONFIG_CONF_PREFIX))
    except ValueError:
        await cb.answer(await get_text("config_invalid_conf_request"), show_alert=True)
        return

    selected = await _find_user_config_by_key_id(cb.from_user.id, key_id)
    if not selected:
        await cb.message.answer(
            await get_text("config_conf_not_found"),
            reply_markup=get_instruction_inline_kb(),
        )
        return

    _, device_num, cfg, _vpn_key = selected
    if cfg and cfg.strip():
        await cb.message.answer_document(
            types.BufferedInputFile(
                cfg.encode("utf-8"),
                filename=f"{_config_filename_prefix()}_device_{device_num}.conf",
            ),
            caption=await get_text("config_conf_caption", device_num=device_num),
            parse_mode="HTML",
        )
        await cb.message.answer(
            await get_text("config_conf_sent"),
        )
    else:
        await cb.message.answer(
            await get_text("config_conf_missing"),
            reply_markup=get_configs_empty_kb(),
        )


@router.callback_query(F.data == CB_OPEN_CONFIGS)
async def open_configs_from_profile(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if cb.from_user.id == ADMIN_ID:
        maybe_set_support_username(cb.from_user.username)
    await cb.answer()
    if not cb.message:
        await cb.answer(await get_text("callback_message_unavailable"), show_alert=True)
        return
    await _send_configs_menu(cb.message, cb.from_user)


@router.message(F.text == BTN_GUIDE)
async def guide(message: types.Message):
    await message.answer(await get_instruction_with_policy_text(), parse_mode="HTML", disable_web_page_preview=True)


@router.message(F.text == BTN_SUPPORT)
async def support(message: types.Message):
    support_username = get_support_username()
    if not support_username:
        logger.warning("SUPPORT_USERNAME is not configured; support contact hidden from user flow")
    await _send_support_center(message)


@router.message(Command("resetdevice"))
async def reset_device_cmd(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await _start_user_reissue_flow(message, message.from_user)


@router.callback_query(F.data.startswith(CB_USER_REISSUE_DEVICE_PREFIX))
async def user_reissue_from_button(cb: types.CallbackQuery):
    await cb.answer()
    key_id: int | None = None
    if cb.data != f"{CB_USER_REISSUE_DEVICE_PREFIX}0":
        try:
            key_id = int(cb.data.removeprefix(CB_USER_REISSUE_DEVICE_PREFIX))
        except ValueError:
            key_id = None
    if cb.message:
        await _start_user_reissue_flow(cb.message, cb.from_user, key_id=key_id)


@router.callback_query(F.data == CB_USER_REISSUE_CANCEL)
async def user_reissue_cancel(cb: types.CallbackQuery):
    await cb.answer()
    await clear_pending_admin_action(cb.from_user.id, "user_reissue_device")
    if cb.message:
        await cb.message.answer("❌ Перевыпуск отменён.")


@router.callback_query(F.data == CB_USER_REISSUE_CONFIRM)
async def user_reissue_confirm(cb: types.CallbackQuery):
    await cb.answer()
    action = await get_pending_admin_action(cb.from_user.id, "user_reissue_device")
    if not action or action.get("action") != "user_reissue_device":
        if cb.message:
            await cb.message.answer("Нет ожидающего запроса на перевыпуск. Используйте /resetdevice.")
        return
    cooldown_hit = await persistent_guard_hit("user_reissue", cb.from_user.id, "current_device", USER_REISSUE_COOLDOWN_SECONDS)
    if cooldown_hit:
        if cb.message:
            await cb.message.answer(f"⏳ Слишком часто. Повторите через {USER_REISSUE_COOLDOWN_SECONDS} сек.")
        return
    try:
        device_num = int(action.get("device_num", 1))
        result = await reissue_user_device(cb.from_user.id, device_num)
        await clear_pending_admin_action(cb.from_user.id, "user_reissue_device")
        if result.get("status") != "reissued":
            if cb.message:
                await cb.message.answer(
                    "Не удалось перевыпустить устройство. Попробуйте позже или напишите в поддержку.",
                    reply_markup=get_support_back_kb(),
                )
            return
        await write_audit_log(cb.from_user.id, "user_reissue_device", f"device_num={device_num}")
        if cb.message:
            await cb.message.answer("✅ Перевыпуск выполнен. Старый конфиг отключён, используйте новый в разделе «🔑 Подключение».")
            await _send_configs_menu(cb.message, cb.from_user)
    except Exception as error:
        logger.exception("Ошибка user_reissue_confirm: %s", error)
        if cb.message:
            await cb.message.answer("❌ Ошибка перевыпуска. Попробуйте позже или напишите в поддержку.", reply_markup=get_support_back_kb())


@router.callback_query(F.data == CB_CHECK_ACTIVATION_STATUS)
async def check_activation_status(cb: types.CallbackQuery):
    await cb.answer()
    sub_until = await get_user_subscription(cb.from_user.id)
    is_active = subscription_is_active(sub_until)
    payment_summary = await get_latest_user_payment_summary(cb.from_user.id)
    has_config = bool(await get_user_keys(cb.from_user.id))
    if not payment_summary:
        await cb.message.answer(await get_text("activation_status_no_payments"), reply_markup=get_buy_inline_kb())
        return
    status = payment_summary["last_provision_status"] or payment_summary["status"]
    if payment_summary["status"] in {"needs_repair", "stuck_manual", "failed"}:
        status = payment_summary["status"]
    await cb.message.answer(
        f"{await get_activation_status_text(status, has_config=has_config)}\n\n{await get_support_short_text()}",
        parse_mode="HTML",
        reply_markup=get_support_back_kb() if status in {"needs_repair", "stuck_manual", "failed"} else get_profile_inline_kb(subscription_active=is_active),
    )


@router.callback_query(F.data == CB_OPEN_SUPPORT)
async def open_support_callback(cb: types.CallbackQuery):
    await cb.answer()
    if cb.message:
        await _send_support_center(cb.message)


@router.callback_query(F.data == CB_SUPPORT_PAYMENT)
async def support_payment_callback(cb: types.CallbackQuery):
    await cb.answer()
    if cb.message:
        await cb.message.answer(_payment_support_text(), parse_mode="HTML", reply_markup=get_support_back_kb())


@router.callback_query(F.data == CB_SUPPORT_CONNECTION)
async def support_connection_callback(cb: types.CallbackQuery):
    await cb.answer()
    if cb.message:
        await cb.message.answer(
            f"{await get_instruction_with_policy_text()}\n\n{await get_support_short_text()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=get_support_back_kb(),
        )


@router.callback_query(F.data == CB_SUPPORT_TERMS)
async def support_terms_callback(cb: types.CallbackQuery):
    await cb.answer()
    if cb.message:
        await cb.message.answer(_terms_text(), parse_mode="HTML", reply_markup=get_support_back_kb())


@router.callback_query(F.data == CB_SUPPORT_BACK)
async def support_back_callback(cb: types.CallbackQuery):
    await cb.answer()
    if cb.message:
        sub_until = await get_user_subscription(cb.from_user.id)
        await cb.message.answer(
            "⬅️ Возврат в основное меню помощи.\nОткройте «👤 Профиль» или «🔑 Подключение» для нужного действия.",
            reply_markup=get_profile_inline_kb(subscription_active=subscription_is_active(sub_until)),
        )


@router.message(F.text == BTN_BUY)
async def buy(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        maybe_set_support_username(message.from_user.username)
    await _send_buy_menu(message, message.from_user.id)


@router.message(F.text == BTN_REFERRALS)
async def referrals_screen(message: types.Message, bot):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    me = await bot.get_me()
    bot_username = getattr(me, "username", "") or "bot"
    data = await get_referral_screen_data(message.from_user.id, bot_username)
    await message.answer(await get_text("referral_screen", ref_link=data["link"], invited_count=data["invited_count"], rewarded_count=data["rewarded_count"], bonus_days=data["bonus_days"]), parse_mode="HTML")


@router.callback_query(F.data == CB_SHOW_BUY_MENU)
async def show_buy_menu_callback(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.answer()
    if not cb.message:
        await cb.answer(await get_text("callback_message_unavailable"), show_alert=True)
        return
    await _send_buy_menu(cb.message, cb.from_user.id)


@router.callback_query(F.data == CB_SHOW_INSTRUCTION)
async def show_instruction_callback(cb: types.CallbackQuery):
    await cb.answer()
    if cb.message:
        await cb.message.answer(await get_instruction_with_policy_text(), parse_mode="HTML", disable_web_page_preview=True)


@router.message()
async def fallback_message(message: types.Message):
    if message.text and message.text.startswith("/"):
        await message.answer(await get_text("unknown_slash"))
        return
    await message.answer(
        await get_text("unknown_message"),
        reply_markup=get_main_menu(message.from_user.id, ADMIN_ID),
    )
