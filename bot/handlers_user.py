import re

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject

from config import (
    ADMIN_ID,
    SERVER_NAME,
    STARS_PRICE_7_DAYS,
    STARS_PRICE_30_DAYS,
    logger,
    get_support_username,
    maybe_set_support_username,
)
from database import ensure_user_exists, get_latest_user_payment_summary, get_user_keys, get_user_subscription
from helpers import escape_html, format_remaining_time, format_tg_username, get_status_text, subscription_is_active
from keyboards import (
    get_buy_inline_kb,
    get_config_result_kb,
    get_configs_devices_kb,
    get_instruction_inline_kb,
    get_main_menu,
    get_profile_inline_kb,
)
from texts import get_instruction_text
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
    CB_SHOW_BUY_MENU,
    CB_SHOW_INSTRUCTION,
)
from content_settings import get_text
from referrals import capture_referral_start, get_referral_screen_data

router = Router()


def _config_filename_prefix() -> str:
    base = re.sub(r"[^\w.-]+", "_", (SERVER_NAME or "configs").strip(), flags=re.UNICODE).strip("._")
    return base or "configs"


async def _send_buy_menu(target, user_id: int):
    sub_until = await get_user_subscription(user_id)
    price_lines = [
        f"• 7 дней — {STARS_PRICE_7_DAYS}⭐",
        f"• 30 дней — {STARS_PRICE_30_DAYS}⭐",
    ]
    if subscription_is_active(sub_until):
        remaining = format_remaining_time(sub_until)
        await target.answer(
            (
                "🔄 <b>У вас уже есть активная подписка</b>\n"
                f"⏳ Осталось: <b>{remaining}</b>\n\n"
                "💡 Вы можете продлить её заранее. Новые дни добавятся к текущему сроку.\n\n"
                "В подписку входит доступ до <b>2 устройств</b> и быстрый импорт в Amnezia.\n\n"
                + "\n".join(price_lines)
            ),
            parse_mode="HTML",
            reply_markup=get_buy_inline_kb(),
        )
        return
    await target.answer(
        "💳 <b>Выберите срок доступа</b>\n\n"
        "В подписку входит доступ до <b>2 устройств</b> и быстрый импорт в Amnezia.\n\n"
        + "\n".join(price_lines),
        parse_mode="HTML",
        reply_markup=get_buy_inline_kb(),
    )


async def _send_configs_menu(target, user: types.User):
    configs = await get_user_keys(user.id)
    if not configs:
        await target.answer(
            (
                "🔑 <b>Подключение</b>\n\n"
                "У вас пока нет активного подключения.\n"
                "Сначала оформите или продлите подписку.\n\n"
                "Если нужна помощь — откройте инструкцию ниже."
            ),
            parse_mode="HTML",
            reply_markup=get_instruction_inline_kb(),
        )
        return

    await target.answer(
        (
            "🔑 <b>Подключение</b>\n\n"
            "Выберите устройство, с которого хотите подключиться.\n"
            "Это может быть любое устройство: телефон, планшет, ноутбук, ПК и т.д.\n\n"
            "Сначала я отправлю:\n"
            "• <code>vpn://</code> — быстрый импорт в Amnezia.\n\n"
            "Файл <code>.conf</code> можно запросить отдельно, если нужен ручной вариант настройки."
        ),
        parse_mode="HTML",
        reply_markup=get_configs_devices_kb(configs),
    )


async def _find_user_config_by_key_id(user_id: int, key_id: int):
    configs = await get_user_keys(user_id)
    return next((item for item in configs if item[0] == key_id), None)


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
    payment_summary = await get_latest_user_payment_summary(message.from_user.id)
    payment_line = "нет данных"
    activation_line = "нет данных"
    if payment_summary:
        created_at = str(payment_summary["created_at"]).replace("T", " ")[:16]
        payment_line = f"{payment_summary['amount']} {payment_summary['currency']} ({created_at})"
        activation_line = payment_summary["last_provision_status"] or payment_summary["status"]
    await message.answer(
        (
            "👤 <b>Профиль</b>\n\n"
            f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
            f"👤 <b>Имя:</b> {first_name}\n"
            f"✈️ <b>Telegram:</b> {escape_html(tg_username)}\n"
            f"📌 <b>Подписка:</b> {status_text}\n"
            f"📅 <b>Действует до:</b> {until_text}\n"
            f"⏳ <b>Осталось:</b> {remaining}\n"
            f"💸 <b>Последний платёж:</b> {payment_line}\n"
            f"🚦 <b>Последняя активация:</b> {activation_line}\n\n"
            "⬇️ Ниже — быстрые действия."
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
        await cb.answer("Некорректный выбор устройства", show_alert=True)
        return

    selected = await _find_user_config_by_key_id(cb.from_user.id, key_id)
    if not selected:
        await cb.message.answer(
            "Не удалось найти ключ для выбранного устройства. Попробуйте открыть раздел «Подключение» ещё раз.",
            reply_markup=get_instruction_inline_kb(),
        )
        return

    _, device_num, _cfg, vpn_key = selected
    if vpn_key and vpn_key.strip():
        await cb.message.answer(
            f"🔐 <b>vpn:// для устройства {device_num}</b>\n\n<code>{escape_html(vpn_key)}</code>\n\n"
            "Подходит для быстрого импорта в Amnezia.",
            parse_mode="HTML",
            reply_markup=get_config_result_kb(key_id),
        )
    else:
        await cb.message.answer(
            "Для выбранного устройства не удалось собрать ключ импорта. Напишите в поддержку или попросите администратора перевыдать доступ.",
            reply_markup=get_instruction_inline_kb(),
        )


@router.callback_query(F.data.startswith(CB_CONFIG_CONF_PREFIX))
async def send_selected_device_conf(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.answer()
    try:
        key_id = int(cb.data.removeprefix(CB_CONFIG_CONF_PREFIX))
    except ValueError:
        await cb.answer("Некорректный запрос .conf", show_alert=True)
        return

    selected = await _find_user_config_by_key_id(cb.from_user.id, key_id)
    if not selected:
        await cb.message.answer(
            "Не удалось найти .conf для выбранного устройства. Откройте раздел «Подключение» ещё раз.",
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
            caption=f"📄 Файл подключения для устройства {device_num}",
            parse_mode="HTML",
        )
        await cb.message.answer(
            "Файл отправлен ✅ Можно вернуться к списку устройств:",
            reply_markup=get_config_result_kb(key_id),
        )
    else:
        await cb.message.answer(
            "Для выбранного устройства не удалось собрать .conf. Напишите в поддержку или попросите администратора перевыдать доступ.",
            reply_markup=get_instruction_inline_kb(),
        )


@router.callback_query(F.data == CB_OPEN_CONFIGS)
async def open_configs_from_profile(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if cb.from_user.id == ADMIN_ID:
        maybe_set_support_username(cb.from_user.username)
    await cb.answer()
    if not cb.message:
        await cb.answer("Сообщение недоступно", show_alert=True)
        return
    await _send_configs_menu(cb.message, cb.from_user)


@router.message(F.text == BTN_GUIDE)
async def guide(message: types.Message):
    await message.answer(get_instruction_text(), parse_mode="HTML", disable_web_page_preview=True)


@router.message(F.text == BTN_SUPPORT)
async def support(message: types.Message):
    support_username = get_support_username()
    if not support_username:
        logger.warning("SUPPORT_USERNAME is not configured; support contact hidden from user flow")
        await message.answer(await get_text("support_unavailable"))
        return
    await message.answer(f"🆘 <b>Поддержка</b>\n\nПо всем вопросам пишите: <b>{escape_html(support_username)}</b>", parse_mode="HTML")


@router.callback_query(F.data == CB_CHECK_ACTIVATION_STATUS)
async def check_activation_status(cb: types.CallbackQuery):
    await cb.answer()
    payment_summary = await get_latest_user_payment_summary(cb.from_user.id)
    if not payment_summary:
        await cb.message.answer("Платежей пока нет. Нажмите «💳 Оплатить доступ», чтобы начать.")
        return
    status = payment_summary["last_provision_status"] or payment_summary["status"]
    if status == "ready":
        await cb.message.answer("✅ Оплата получена → доступ выпускается → доступ готов. Откройте «🔑 Подключение».")
        return
    if status in {"provisioning", "payment_received"}:
        await cb.message.answer("⏳ Оплата получена. Доступ выпускается. Обычно это занимает до минуты.")
        return
    await cb.message.answer("⚠️ Активация задержалась. Проверьте статус позже или напишите в поддержку.")


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
    await message.answer(
        (
            "🎁 <b>Рефералы</b>\n\n"
            f"🔗 Ваша ссылка:\n<code>{data['link']}</code>\n\n"
            f"👥 Приглашено: <b>{data['invited_count']}</b>\n"
            f"🎉 Бонусных дней: <b>{data['bonus_days']}</b>\n\n"
            "Бонус начисляется только после первой успешной оплаты приглашённого пользователя."
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == CB_SHOW_BUY_MENU)
async def show_buy_menu_callback(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.answer()
    if not cb.message:
        await cb.answer("Сообщение недоступно", show_alert=True)
        return
    await _send_buy_menu(cb.message, cb.from_user.id)


@router.callback_query(F.data == CB_SHOW_INSTRUCTION)
async def show_instruction_callback(cb: types.CallbackQuery):
    await cb.answer()
    if cb.message:
        await cb.message.answer(get_instruction_text(), parse_mode="HTML", disable_web_page_preview=True)


@router.message()
async def fallback_message(message: types.Message):
    if message.text and message.text.startswith("/"):
        await message.answer(await get_text("unknown_slash"))
        return
    await message.answer(
        "Не понял сообщение. Используйте кнопки меню ниже.",
        reply_markup=get_main_menu(message.from_user.id, ADMIN_ID),
    )
