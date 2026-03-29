from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from config import STARS_PRICE_7_DAYS, STARS_PRICE_30_DAYS
from ui_constants import (
    BTN_ADMIN, BTN_BUY, BTN_CONFIGS, BTN_GUIDE, BTN_PROFILE, BTN_REFERRALS, BTN_SUPPORT,
    CB_ADMIN_BROADCAST, CB_ADMIN_CLEAN_ORPHANS, CB_ADMIN_LIST, CB_ADMIN_STATS, CB_ADMIN_SYNC,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM, CB_BUY_30, CB_BUY_7,
    CB_CHECK_ACTIVATION_STATUS,
    CB_CONFIG_CONF_PREFIX, CB_CONFIG_DEVICE_PREFIX, CB_OPEN_CONFIGS,
    CB_SHOW_BUY_MENU, CB_SHOW_INSTRUCTION,
)


def get_main_menu(user_id: int, admin_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_BUY)],
        [KeyboardButton(text=BTN_CONFIGS), KeyboardButton(text=BTN_GUIDE)],
        [KeyboardButton(text=BTN_REFERRALS), KeyboardButton(text=BTN_SUPPORT)],
    ]
    if user_id == admin_id:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие...",
    )


def get_buy_inline_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"7 дней — {STARS_PRICE_7_DAYS}⭐", callback_data=CB_BUY_7)],
        [InlineKeyboardButton(text=f"30 дней — {STARS_PRICE_30_DAYS}⭐", callback_data=CB_BUY_30)],
        [InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_profile_inline_kb(subscription_active: bool) -> InlineKeyboardMarkup:
    rows = []
    if subscription_active:
        rows.append([InlineKeyboardButton(text="🔄 Продлить доступ", callback_data=CB_SHOW_BUY_MENU)])
    else:
        rows.append([InlineKeyboardButton(text="💳 Оплатить доступ", callback_data=CB_SHOW_BUY_MENU)])
    rows.append([InlineKeyboardButton(text="🔑 Подключение", callback_data=CB_OPEN_CONFIGS)])
    rows.append([InlineKeyboardButton(text="⏱ Проверить статус активации", callback_data=CB_CHECK_ACTIVATION_STATUS)])
    rows.append([InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_instruction_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)]]
    )


def get_configs_devices_kb(configs: list[tuple[int, int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📱 Устройство {device_num}", callback_data=f"{CB_CONFIG_DEVICE_PREFIX}{key_id}")]
        for key_id, device_num, _, _ in configs
    ]
    rows.append([InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_config_result_kb(key_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Выдать .conf файл (для опытных)", callback_data=f"{CB_CONFIG_CONF_PREFIX}{key_id}")],
            [InlineKeyboardButton(text="⬅️ Назад к устройствам", callback_data=CB_OPEN_CONFIGS)],
            [InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)],
        ]
    )


def get_post_payment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Получить подключение", callback_data=CB_OPEN_CONFIGS)],
            [InlineKeyboardButton(text="⏱ Проверить статус активации", callback_data=CB_CHECK_ACTIVATION_STATUS)],
            [InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)],
        ]
    )


def get_admin_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Пользователи", callback_data=CB_ADMIN_LIST)],
            [InlineKeyboardButton(text="📊 Статистика", callback_data=CB_ADMIN_STATS)],
            [InlineKeyboardButton(text="🔄 Синхронизация", callback_data=CB_ADMIN_SYNC)],
            [InlineKeyboardButton(text="🧹 Очистить потерянные peer", callback_data=CB_ADMIN_CLEAN_ORPHANS)],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data=CB_ADMIN_BROADCAST)],
        ]
    )


def get_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=CB_BROADCAST_CONFIRM)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=CB_BROADCAST_CANCEL)],
        ]
    )


def get_admin_confirm_kb(action_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{action_key}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{action_key}")],
        ]
    )


def get_admin_force_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить FORCE", callback_data="confirm_clean_orphans_force")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_clean_orphans_force")],
        ]
    )
