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
    BTN_SUPPORT,
    CB_CONFIG_CONF_PREFIX,
    CB_CONFIG_DEVICE_PREFIX,
    CB_OPEN_CONFIGS,
    CB_SHOW_BUY_MENU,
    CB_SHOW_INSTRUCTION,
)

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
                "🔄 <b>У вас уже есть активный доступ</b>\n"
                f"⏳ Осталось: <b>{remaining}</b>\n\n"
                "💡 Продлите заранее — новые дни добавятся к текущему сроку.\n\n"
                + "\n".join(price_lines)
            ),
            parse_mode="HTML",
            reply_markup=get_buy_inline_kb(),
        )
        return
    await target.answer(
        (
            "💳 <b>Выберите срок доступа</b>\n\n"
            "После оплаты я сразу выдам данные для подключения в этом чате.\n\n"
            + "\n".join(price_lines)
        ),
        parse_mode="HTML",
        reply_markup=get_buy_inline_kb(),
    )


async def _send_configs_menu(target, user: types.User):
    configs = await get_user_keys(user.id)
    if not configs:
        await target.answer(
            (
                "📲 <b>Подключить устройство</b>\n\n"
                "У вас пока нет активного доступа.\n"
                "Сначала оформите или продлите доступ.\n\n"
                "Если нужна помощь — откройте инструкцию ниже."
            ),
            parse_mode="HTML",
            reply_markup=get_instruction_inline_kb(),
        )
        return

    await target.answer(
        (
            "📲 <b>Подключить устройство</b>\n\n"
            "Выберите устройство. Сначала я отправлю ключ быстрого подключения.\n\n"
            "Если нужен ручной способ — можно отдельно запросить файл <code>.conf</code>."
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
async def start(message: types.Message):
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if message.from_user.id == ADMIN_ID:
        maybe_set_support_username(message.from_user.username)
    await message.answer(
        (
            "🌐 <b>Свободный интернет</b>\n\n"
            "Подключение занимает 1–2 минуты:\n"
            "1) Оформите доступ\n"
            "2) Получите данные в этом чате\n"
            "3) Подключите устройство\n\n"
            "Нажмите <b>💳 Оформить доступ</b>, чтобы начать."
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
            "Не удалось найти данные для этого устройства. Откройте раздел «📲 Подключить устройство» ещё раз.",
            reply_markup=get_instruction_inline_kb(),
        )
        return

    _, device_num, _cfg, vpn_key = selected
    if vpn_key and vpn_key.strip():
        await cb.message.answer(
            f"🔐 <b>Ключ быстрого подключения для устройства {device_num}</b>\n\n<code>{escape_html(vpn_key)}</code>\n\n"
            "Скопируйте ключ и вставьте его в приложении Amnezia.",
            parse_mode="HTML",
            reply_markup=get_config_result_kb(key_id),
        )
    else:
        await cb.message.answer(
            "Не получилось подготовить ключ подключения для этого устройства. Напишите в поддержку — поможем вручную.",
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
            "Не удалось найти файл для выбранного устройства. Откройте раздел «📲 Подключить устройство» ещё раз.",
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
            caption=f"📄 Файл для ручной настройки устройства {device_num}",
            parse_mode="HTML",
        )
        await cb.message.answer(
            "Файл отправлен ✅ Если нужно, выберите другое устройство:",
            reply_markup=get_config_result_kb(key_id),
        )
    else:
        await cb.message.answer(
            "Не получилось подготовить файл для выбранного устройства. Напишите в поддержку — поможем вручную.",
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
    await message.answer(
        (
            "🆘 <b>Поддержка</b>\n\n"
            f"Напишите: <b>{escape_html(get_support_username())}</b>\n"
            "Обычно отвечаем за 5–15 минут.\n\n"
            "Чтобы помочь быстрее, отправьте:\n"
            "• кратко, что не получается\n"
            "• скрин ошибки (если есть)\n"
            "• время оплаты (если вопрос по оплате)"
        ),
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
                "🔄 <b>У вас уже есть активный доступ</b>\n"
                f"⏳ Осталось: <b>{remaining}</b>\n\n"
                "💡 Продлите заранее — новые дни добавятся к текущему сроку.\n\n"
                + "\n".join(price_lines)
            ),
            parse_mode="HTML",
            reply_markup=get_buy_inline_kb(),
        )
        return
    await message.answer(
        (
            "💳 <b>Выберите срок доступа</b>\n\n"
            "После оплаты я сразу выдам данные для подключения в этом чате.\n\n"
            + "\n".join(price_lines)
        ),
        parse_mode="HTML",
        reply_markup=get_buy_inline_kb(),
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
        return
    await message.answer(
        "Не понял сообщение. Нажмите кнопку в меню ниже. Если уже оплатили — откройте «📲 Подключить устройство».",
        reply_markup=get_main_menu(message.from_user.id, ADMIN_ID),
    )
