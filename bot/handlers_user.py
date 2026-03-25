from aiogram import F, Router, types
from aiogram.filters import Command

from config import ADMIN_ID, STARS_PRICE_7_DAYS, STARS_PRICE_30_DAYS, get_support_username, maybe_set_support_username
from database import ensure_user_exists, get_user_keys, get_user_subscription
from helpers import escape_html, format_remaining_time, format_tg_username, get_status_text, subscription_is_active
from keyboards import get_buy_inline_kb, get_instruction_inline_kb, get_main_menu, get_profile_inline_kb
from texts import get_instruction_text
from ui_constants import BTN_BUY, BTN_CONFIGS, BTN_GUIDE, BTN_PROFILE, BTN_SUPPORT, CB_SHOW_BUY_MENU, CB_SHOW_INSTRUCTION

router = Router()


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
            "🌐 <b>Свободный Интернет</b>\n\n"
            "Здесь можно:\n"
            "• оформить или продлить подписку\n"
            "• получить ключ доступа\n"
            "• скачать <b>.conf</b>\n"
            "• посмотреть срок действия\n"
            "• открыть инструкцию\n"
            "• написать в поддержку\n\n"
            "Выберите действие в меню ниже."
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
            f"📌 <b>Статус:</b> {status_text}\n"
            f"📅 <b>Действует до:</b> {until_text}\n"
            f"⏳ <b>Осталось:</b> {remaining}"
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
                "🔑 <b>Конфиги</b>\n\n"
                "У вас пока нет активных конфигураций.\n"
                "Сначала оформите доступ.\n\n"
                "Если нужна помощь — откройте инструкцию ниже."
            ),
            parse_mode="HTML",
            reply_markup=get_instruction_inline_kb(),
        )
        return
    await message.answer(
        "🔑 <b>Ваши конфиги</b>\n\nСначала идёт <b>ключ доступа</b>, ниже — <b>.conf</b> файл.",
        parse_mode="HTML",
    )
    sent_any = False
    for key_id, device_num, cfg, vpn_key in configs:
        if vpn_key and vpn_key.strip():
            await message.answer(
                f"🔐 <b>Ключ доступа для устройства {device_num}</b>\n\n<code>{escape_html(vpn_key)}</code>",
                parse_mode="HTML",
            )
        if cfg and cfg.strip():
            await message.answer_document(
                types.BufferedInputFile(cfg.encode("utf-8"), filename=f"Poland_just1k_{key_id}.conf"),
                caption=f"📄 Конфиг для устройства {device_num}",
                parse_mode="HTML",
            )
            sent_any = True
    if not sent_any:
        await message.answer(
            "Найдены только повреждённые записи ключей. Напишите в поддержку или попросите администратора перевыдать доступ.",
            reply_markup=get_instruction_inline_kb(),
        )
        return
    await message.answer("Если не знаете, что делать дальше, откройте инструкцию:", reply_markup=get_instruction_inline_kb())


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
