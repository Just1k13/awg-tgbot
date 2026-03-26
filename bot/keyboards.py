from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from config import STARS_PRICE_7_DAYS, STARS_PRICE_30_DAYS
from ui_constants import (
    BTN_ADMIN, BTN_BUY, BTN_CONFIGS, BTN_GUIDE, BTN_PROFILE, BTN_SUPPORT,
    CB_ADMIN_BROADCAST, CB_ADMIN_CLEAN_ORPHANS, CB_ADMIN_LIST, CB_ADMIN_STATS, CB_ADMIN_SYNC,
    CB_BACK_TO_ADMIN, CB_BACK_TO_CONFIGS, CB_BACK_TO_PROFILE,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM, CB_BUY_30, CB_BUY_7, CB_CONFIG_DEVICE_PREFIX,
    CB_SHOW_BUY_MENU, CB_SHOW_INSTRUCTION, CB_SHOW_INSTRUCTION_BUY, CB_SHOW_INSTRUCTION_CONFIGS,
    CB_SHOW_INSTRUCTION_PROFILE,
)


def get_main_menu(user_id: int, admin_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_CONFIGS)],
        [KeyboardButton(text=BTN_BUY), KeyboardButton(text=BTN_GUIDE)],
        [KeyboardButton(text=BTN_SUPPORT)],
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
        [InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_BUY)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_PROFILE)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_profile_inline_kb(subscription_active: bool) -> InlineKeyboardMarkup:
    rows = []
    if subscription_active:
        rows.append([InlineKeyboardButton(text="🔄 Продлить подписку", callback_data=CB_SHOW_BUY_MENU)])
    else:
        rows.append([InlineKeyboardButton(text="💳 Купить подписку", callback_data=CB_SHOW_BUY_MENU)])
    rows.append([InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_PROFILE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_instruction_inline_kb(back_callback: str = CB_BACK_TO_PROFILE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Обновить инструкцию", callback_data=CB_SHOW_INSTRUCTION)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)],
        ]
    )


def get_configs_devices_kb(configs: list[tuple[int, int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📱 Устройство {device_num}", callback_data=f"{CB_CONFIG_DEVICE_PREFIX}{key_id}")]
        for key_id, device_num, _, _ in configs
    ]
    rows.append([InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_CONFIGS)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_PROFILE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_config_result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_CONFIGS)],
            [InlineKeyboardButton(text="⬅️ Назад к устройствам", callback_data=CB_BACK_TO_CONFIGS)],
        ]
    )


def get_admin_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Пользователи", callback_data=CB_ADMIN_LIST)],
            [InlineKeyboardButton(text="📊 Статистика", callback_data=CB_ADMIN_STATS)],
            [InlineKeyboardButton(text="🔄 Синхронизация", callback_data=CB_ADMIN_SYNC)],
            [InlineKeyboardButton(text="🧹 Очистить orphan peer", callback_data=CB_ADMIN_CLEAN_ORPHANS)],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data=CB_ADMIN_BROADCAST)],
        ]
    )


def get_back_to_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_ADMIN)]]
    )


def get_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=CB_BROADCAST_CONFIRM)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=CB_BROADCAST_CANCEL)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_ADMIN)],
        ]
    )


def get_admin_confirm_kb(action_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{action_key}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{action_key}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_ADMIN)],
        ]
    )
