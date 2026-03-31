from typing import Callable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from config import STARS_PRICE_7_DAYS, STARS_PRICE_30_DAYS
from ui_constants import (
    BTN_ADMIN, BTN_BUY, BTN_CONFIGS, BTN_GUIDE, BTN_PROFILE, BTN_REFERRALS, BTN_SUPPORT,
    CB_ADMIN_BACK_MAIN, CB_ADMIN_BACK_SETTINGS, CB_ADMIN_BACK_TEXTS, CB_ADMIN_BROADCAST, CB_ADMIN_CANCEL_EDIT,
    CB_ADMIN_LIST, CB_ADMIN_REFERRALS, CB_ADMIN_REFRESH_SETTINGS, CB_ADMIN_REFRESH_TEXTS, CB_ADMIN_SETTING_EDIT_PREFIX,
    CB_ADMIN_SETTING_KEY_PREFIX, CB_ADMIN_SETTING_RESET_PREFIX, CB_ADMIN_SETTINGS_PAGE_PREFIX,
    CB_ADMIN_STATS, CB_ADMIN_SYNC, CB_ADMIN_TEXT_EDIT_PREFIX, CB_ADMIN_TEXT_KEY_PREFIX, CB_ADMIN_TEXT_RESET_PREFIX,
    CB_ADMIN_TEXTS_PAGE_PREFIX,
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
            [InlineKeyboardButton(text="⬅️ Назад к устройствам", callback_data=CB_OPEN_CONFIGS)],
            _guide_row(),
        ]
    )


def get_post_payment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Получить подключение", callback_data=CB_OPEN_CONFIGS)],
            [InlineKeyboardButton(text="⏱ Проверить статус активации", callback_data=CB_CHECK_ACTIVATION_STATUS)],
            _guide_row(),
        ]
    )


def get_admin_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Пользователи", callback_data=CB_ADMIN_LIST)],
            [InlineKeyboardButton(text="📊 Статистика", callback_data=CB_ADMIN_STATS)],
            [InlineKeyboardButton(text="🎁 Рефералы", callback_data=CB_ADMIN_REFERRALS)],
            [InlineKeyboardButton(text="🔄 Синхронизация", callback_data=CB_ADMIN_SYNC)],
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


def get_admin_texts_list_kb(
    keys: list[str],
    page: int,
    total_pages: int,
    title_builder: Callable[[str], str] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, key in enumerate(keys):
        title = title_builder(key) if title_builder else key
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{CB_ADMIN_TEXT_KEY_PREFIX}{index}_{page}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_ADMIN_TEXTS_PAGE_PREFIX}{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page + 1}/{max(total_pages, 1)}", callback_data="noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_ADMIN_TEXTS_PAGE_PREFIX}{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data=CB_ADMIN_REFRESH_TEXTS)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_ADMIN_BACK_MAIN)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_settings_list_kb(
    keys: list[str],
    page: int,
    total_pages: int,
    title_builder: Callable[[str], str] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, key in enumerate(keys):
        title = title_builder(key) if title_builder else key
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{CB_ADMIN_SETTING_KEY_PREFIX}{index}_{page}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_ADMIN_SETTINGS_PAGE_PREFIX}{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page + 1}/{max(total_pages, 1)}", callback_data="noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_ADMIN_SETTINGS_PAGE_PREFIX}{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data=CB_ADMIN_REFRESH_SETTINGS)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_ADMIN_BACK_MAIN)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_text_detail_kb(index: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"{CB_ADMIN_TEXT_EDIT_PREFIX}{index}_{page}")],
            [InlineKeyboardButton(text="♻️ Сбросить", callback_data=f"{CB_ADMIN_TEXT_RESET_PREFIX}{index}_{page}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_ADMIN_BACK_TEXTS)],
        ]
    )


def get_admin_setting_detail_kb(index: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"{CB_ADMIN_SETTING_EDIT_PREFIX}{index}_{page}")],
            [InlineKeyboardButton(text="♻️ Сбросить", callback_data=f"{CB_ADMIN_SETTING_RESET_PREFIX}{index}_{page}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_ADMIN_BACK_SETTINGS)],
        ]
    )


def get_admin_simple_back_kb(back_cb: str, refresh_cb: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if refresh_cb:
        rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data=refresh_cb)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_edit_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=CB_ADMIN_CANCEL_EDIT)]]
    )
