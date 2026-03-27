import re

from aiogram import F, Router, types
from aiogram.filters import Command

from config import (
    ADMIN_ID,
    SERVER_NAME,
    STARS_PRICE_7_DAYS,
    STARS_PRICE_30_DAYS,
    get_support_username,
    maybe_set_support_username,
)
from database import ensure_user_exists, get_user_keys, get_user_subscription
from helpers import escape_html, format_remaining_time, format_tg_username, get_status_text, subscription_is_active
from keyboards import (
    get_buy_inline_kb,
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
    BTN_SUPPORT,
    CB_CONFIG_DEVICE_PREFIX,
    CB_SHOW_BUY_MENU,
    CB_SHOW_INSTRUCTION,
)

router = Router()


def _config_filename_prefix() -> str:
    base = re.sub(r"[^\w.-]+", "_", (SERVER_NAME or "vpn").strip(), flags=re.UNICODE).strip("._")
    return base or "vpn"


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
                + "\n".join(price_lines)
            ),
            parse_mode="HTML",
            reply_markup=get_buy_inline_kb(),
        )
        return
    await target.answer(
        "💳 <b>Выберите срок доступа</b>\n\n" + "\n".join(price_lines),
        parse_mode="HTML",
        reply_markup=get_buy_inline_kb(),
    )


@router.callback_query(F.data == "noop")
async def noop_callback(cb: types.CallbackQuery):
    await cb.answer()


@router.message(Command("start"))
async def start(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        maybe_set_support_username(message.from_user.username)
    await message.answer(
        (
            "🌐 <b>Добро пожаловать в VPN-бот</b>\n\n"
            "Здесь всё по шагам:\n"
            "1) оформите подписку,\n"
            "2) откройте <b>Мои устройства</b>,\n"
            "3) получите <code>vpn://</code> или <code>.conf</code>.\n\n"
            "Ниже — основное меню."
        ),
        parse_mode="HTML",
        reply_markup=get_main_menu(message.from_user.id, ADMIN_ID),
    )


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
    await message.answer(
        (
            "👤 <b>Профиль</b>\n\n"
            f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
            f"👤 <b>Имя:</b> {first_name}\n"
            f"✈️ <b>Telegram:</b> {escape_html(tg_username)}\n"
            f"📌 <b>Подписка:</b> {status_text}\n"
            f"📅 <b>Действует до:</b> {until_text}\n"
            f"⏳ <b>Осталось:</b> {remaining}\n\n"
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
    configs = await get_user_keys(message.from_user.id)
    if not configs:
        await message.answer(
            (
                "📱 <b>Мои устройства</b>\n\n"
                "У вас пока нет активных конфигураций.\n"
                "Сначала оформите или продлите подписку.\n\n"
                "Если нужна помощь — откройте инструкцию ниже."
            ),
            parse_mode="HTML",
            reply_markup=get_instruction_inline_kb(),
        )
        return
    await message.answer(
        (
            "📱 <b>Мои устройства</b>\n\n"
            "Выберите устройство. Я отправлю:\n"
            "• <code>vpn://</code> — быстрый импорт в Amnezia,\n"
            "• <code>.conf</code> — универсальный файл для ручного импорта."
        ),
        parse_mode="HTML",
        reply_markup=get_configs_devices_kb(configs),
    )


@router.callback_query(F.data.startswith(CB_CONFIG_DEVICE_PREFIX))
async def show_selected_device_config(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.answer()
    try:
        key_id = int(cb.data.removeprefix(CB_CONFIG_DEVICE_PREFIX))
    except ValueError:
        await cb.answer("Некорректный выбор устройства", show_alert=True)
        return

    configs = await get_user_keys(cb.from_user.id)
    selected = next((item for item in configs if item[0] == key_id), None)
    if not selected:
        await cb.message.answer(
            "Не удалось найти конфиг для выбранного устройства. Попробуйте открыть раздел «Конфиги» ещё раз.",
            reply_markup=get_instruction_inline_kb(),
        )
        return

    _, device_num, cfg, vpn_key = selected
    if vpn_key and vpn_key.strip():
        await cb.message.answer(
            f"🔐 <b>vpn:// для устройства {device_num}</b>\n\n<code>{escape_html(vpn_key)}</code>\n\n"
            "Подходит для быстрого импорта в Amnezia.",
            parse_mode="HTML",
        )
    if cfg and cfg.strip():
        await cb.message.answer_document(
            types.BufferedInputFile(
                cfg.encode("utf-8"),
                filename=f"{_config_filename_prefix()}_device_{device_num}.conf",
            ),
            caption=f"📄 Конфиг для устройства {device_num}",
            parse_mode="HTML",
        )
    else:
        await cb.message.answer(
            "Для выбранного устройства не удалось собрать .conf. Напишите в поддержку или попросите администратора перевыдать доступ.",
            reply_markup=get_instruction_inline_kb(),
        )
        return
    await cb.message.answer(
        "Готово ✅ Если нужна помощь с импортом — откройте инструкцию:",
        reply_markup=get_instruction_inline_kb(),
    )


@router.callback_query(F.data == "open_configs")
async def open_configs_from_profile(cb: types.CallbackQuery):
    await cb.answer()
    await my_keys(cb.message)


@router.message(F.text == BTN_GUIDE)
async def guide(message: types.Message):
    await message.answer(get_instruction_text(), parse_mode="HTML", disable_web_page_preview=True)


@router.message(F.text == BTN_SUPPORT)
async def support(message: types.Message):
    await message.answer(
        f"🆘 <b>Поддержка</b>\n\nПо всем вопросам пишите: <b>{escape_html(get_support_username())}</b>",
        parse_mode="HTML",
    )


@router.message(F.text == BTN_BUY)
async def buy(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        maybe_set_support_username(message.from_user.username)
    sub_until = await get_user_subscription(message.from_user.id)
    price_lines = [
        f"• 7 дней — {STARS_PRICE_7_DAYS}⭐",
        f"• 30 дней — {STARS_PRICE_30_DAYS}⭐",
    ]
    if subscription_is_active(sub_until):
        remaining = format_remaining_time(sub_until)
        await message.answer(
            (
                "🔄 <b>У вас уже есть активная подписка</b>\n"
                f"⏳ Осталось: <b>{remaining}</b>\n\n"
                "💡 Вы можете продлить её заранее. Новые дни добавятся к текущему сроку.\n\n"
                + "\n".join(price_lines)
            ),
            parse_mode="HTML",
            reply_markup=get_buy_inline_kb(),
        )
        return
    await message.answer(
        "💳 <b>Выберите срок доступа</b>\n\n" + "\n".join(price_lines),
        parse_mode="HTML",
        reply_markup=get_buy_inline_kb(),
    )


@router.callback_query(F.data == CB_SHOW_BUY_MENU)
async def show_buy_menu_callback(cb: types.CallbackQuery):
    await ensure_user_exists(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.answer()
    await _send_buy_menu(cb.message, cb.from_user.id)


@router.callback_query(F.data == CB_SHOW_INSTRUCTION)
async def show_instruction_callback(cb: types.CallbackQuery):
    await cb.answer()
    await cb.message.answer(get_instruction_text(), parse_mode="HTML", disable_web_page_preview=True)


@router.message()
async def fallback_message(message: types.Message):
    if message.text and message.text.startswith("/"):
        return
    await message.answer(
        "Не понял сообщение. Используйте кнопки меню ниже.",
        reply_markup=get_main_menu(message.from_user.id, ADMIN_ID),
    )
