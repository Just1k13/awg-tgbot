from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

import config
from ui_constants import (
    BTN_ADMIN, BTN_BUY, BTN_CONFIGS, BTN_GUIDE, BTN_PROFILE, BTN_REFERRALS, BTN_SUPPORT,
    CB_ADMIN_BACK_MAIN, CB_ADMIN_BROADCAST, CB_ADMIN_COMMANDS, CB_ADMIN_BACKUP, CB_ADMIN_LIST, CB_ADMIN_PRICE_CANCEL, CB_ADMIN_PRICE_EDIT_30,
    CB_ADMIN_PRICE_EDIT_7, CB_ADMIN_PRICE_EDIT_90, CB_ADMIN_PRICE_SAVE, CB_ADMIN_PRICES, CB_ADMIN_REFERRALS,
    CB_ADMIN_STATS, CB_ADMIN_SYNC,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM, CB_BUY_30, CB_BUY_7, CB_BUY_90,
    CB_CHECK_ACTIVATION_STATUS,
    CB_CONFIG_CONF_PREFIX, CB_CONFIG_DEVICE_PREFIX, CB_OPEN_CONFIGS,
    CB_OPEN_SUPPORT,
    CB_SHOW_BUY_MENU, CB_SHOW_INSTRUCTION, CB_USER_REISSUE_CANCEL, CB_USER_REISSUE_CONFIRM,
    CB_SUPPORT_BACK, CB_SUPPORT_CONNECTION, CB_SUPPORT_PAYMENT, CB_SUPPORT_TERMS, CB_USER_REISSUE_DEVICE_PREFIX,
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
        [InlineKeyboardButton(text=f"7 дней — {config.STARS_PRICE_7_DAYS}⭐", callback_data=CB_BUY_7)],
        [InlineKeyboardButton(text=f"30 дней — {config.STARS_PRICE_30_DAYS}⭐", callback_data=CB_BUY_30)],
        [InlineKeyboardButton(text=f"90 дней — {config.STARS_PRICE_90_DAYS}⭐", callback_data=CB_BUY_90)],
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
    rows.append([InlineKeyboardButton(text="⏱ Статус активации", callback_data=CB_CHECK_ACTIVATION_STATUS)])
    rows.append([InlineKeyboardButton(text="🆘 Помощь и поддержка", callback_data=CB_OPEN_SUPPORT)])
    rows.append([InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_instruction_inline_kb() -> InlineKeyboardMarkup:
    return _single_button_kb(_guide_row())


def _guide_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="📖 Как подключиться", callback_data=CB_SHOW_INSTRUCTION)]


def _single_button_kb(row: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row])


def get_configs_devices_kb(configs: list[tuple[int, int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📱 Устройство {device_num}", callback_data=f"{CB_CONFIG_DEVICE_PREFIX}{key_id}")]
        for key_id, device_num, _, _ in configs
    ]
    rows.append(_guide_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_config_result_kb(key_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Выдать .conf файл (для опытных)", callback_data=f"{CB_CONFIG_CONF_PREFIX}{key_id}")],
            [InlineKeyboardButton(text="♻️ Перевыпустить это устройство", callback_data=f"{CB_USER_REISSUE_DEVICE_PREFIX}{key_id}")],
            [InlineKeyboardButton(text="⬅️ Назад к устройствам", callback_data=CB_OPEN_CONFIGS)],
            [InlineKeyboardButton(text="🆘 Помощь и поддержка", callback_data=CB_OPEN_SUPPORT)],
            _guide_row(),
        ]
    )


def get_post_payment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Получить подключение", callback_data=CB_OPEN_CONFIGS)],
            [InlineKeyboardButton(text="⏱ Проверить статус активации", callback_data=CB_CHECK_ACTIVATION_STATUS)],
            [InlineKeyboardButton(text="🆘 Помощь и поддержка", callback_data=CB_OPEN_SUPPORT)],
            _guide_row(),
        ]
    )


def get_support_center_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Помощь с оплатой", callback_data=CB_SUPPORT_PAYMENT)],
            [InlineKeyboardButton(text="🔌 Помощь с подключением", callback_data=CB_SUPPORT_CONNECTION)],
            [InlineKeyboardButton(text="📄 Краткие условия", callback_data=CB_SUPPORT_TERMS)],
            [InlineKeyboardButton(text="⬅️ К меню", callback_data=CB_SUPPORT_BACK)],
        ]
    )


def get_support_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад в помощь", callback_data=CB_OPEN_SUPPORT)],
        ]
    )


def get_configs_empty_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _guide_row(),
            [InlineKeyboardButton(text="🆘 Помощь и поддержка", callback_data=CB_OPEN_SUPPORT)],
        ]
    )


def get_admin_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Пользователи", callback_data=CB_ADMIN_LIST)],
            [InlineKeyboardButton(text="📊 Статистика", callback_data=CB_ADMIN_STATS)],
            [InlineKeyboardButton(text="🎁 Рефералы", callback_data=CB_ADMIN_REFERRALS)],
            [InlineKeyboardButton(text="🔄 Синхронизация", callback_data=CB_ADMIN_SYNC)],
            [InlineKeyboardButton(text="💸 Цены", callback_data=CB_ADMIN_PRICES)],
            [InlineKeyboardButton(text="💾 Бэкап", callback_data=CB_ADMIN_BACKUP)],
            [InlineKeyboardButton(text="⌨️ Команды", callback_data=CB_ADMIN_COMMANDS)],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data=CB_ADMIN_BROADCAST)],
        ]
    )


def get_admin_prices_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить 7 дней", callback_data=CB_ADMIN_PRICE_EDIT_7)],
            [InlineKeyboardButton(text="Изменить 30 дней", callback_data=CB_ADMIN_PRICE_EDIT_30)],
            [InlineKeyboardButton(text="Изменить 90 дней", callback_data=CB_ADMIN_PRICE_EDIT_90)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_ADMIN_BACK_MAIN)],
        ]
    )


def get_admin_price_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Сохранить", callback_data=CB_ADMIN_PRICE_SAVE)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_ADMIN_PRICE_CANCEL)],
        ]
    )


def get_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=CB_BROADCAST_CONFIRM)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=CB_BROADCAST_CANCEL)],
        ]
    )


def get_broadcast_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data=CB_BROADCAST_CANCEL)],
        ]
    )


def get_user_reissue_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♻️ Да, перевыпустить", callback_data=CB_USER_REISSUE_CONFIRM)],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_USER_REISSUE_CANCEL)],
        ]
    )


def get_admin_confirm_kb(action_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{action_key}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{action_key}")],
        ]
    )


def get_admin_simple_back_kb(back_cb: str, refresh_cb: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if refresh_cb:
        rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data=refresh_cb)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
