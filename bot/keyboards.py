from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from config import STARS_PRICE_7_DAYS, STARS_PRICE_30_DAYS
from ui_constants import (
    BTN_ADMIN, BTN_BUY, BTN_CONFIGS, BTN_GUIDE, BTN_PROFILE, BTN_SUPPORT,
    CB_ADMIN_BROADCAST, CB_ADMIN_CLEAN_ORPHANS, CB_ADMIN_LIST, CB_ADMIN_STATS, CB_ADMIN_SYNC,
    CB_ADMIN_ADD_CUSTOM_PREFIX, CB_ADMIN_USER_ACCESS_PREFIX, CB_ADMIN_USER_ACTIONS_PREFIX,
    CB_ADMIN_USER_PREFIX, CB_ADMIN_USER_SUBS_PREFIX, CB_ADMIN_USERS_ACTIVE,
    CB_ADMIN_USERS_HUB, CB_ADMIN_USERS_INACTIVE, CB_ADMIN_USERS_NEW24, CB_ADMIN_USERS_PAGE_PREFIX,
    CB_ADMIN_USERS_SEARCH,
    CB_BACK_TO_ADMIN, CB_BACK_TO_CONFIGS, CB_BACK_TO_PROFILE,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM, CB_BUY_30, CB_BUY_7, CB_CONFIG_DEVICE_PREFIX,
    CB_SHOW_BUY_MENU, CB_SHOW_INSTRUCTION_BUY, CB_SHOW_INSTRUCTION_CONFIGS,
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
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Выберите действие...")


def get_buy_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"7 дней — {STARS_PRICE_7_DAYS}⭐", callback_data=CB_BUY_7)],
        [InlineKeyboardButton(text=f"30 дней — {STARS_PRICE_30_DAYS}⭐", callback_data=CB_BUY_30)],
        [InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_BUY)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_PROFILE)],
    ])


def get_profile_inline_kb(subscription_active: bool) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(text=("🔄 Продлить подписку" if subscription_active else "💳 Купить подписку"), callback_data=CB_SHOW_BUY_MENU)])
    rows.append([InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_PROFILE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_instruction_inline_kb(back_callback: str = CB_BACK_TO_PROFILE) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)],
    ])


def get_configs_devices_kb(configs: list[tuple[int, int, str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📱 Устройство {device_num}", callback_data=f"{CB_CONFIG_DEVICE_PREFIX}{key_id}")] for key_id, device_num, _, _ in configs]
    rows.append([InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_CONFIGS)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_PROFILE)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_config_result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Инструкция", callback_data=CB_SHOW_INSTRUCTION_CONFIGS)],
        [InlineKeyboardButton(text="⬅️ Назад к устройствам", callback_data=CB_BACK_TO_CONFIGS)],
    ])


def get_admin_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Пользователи", callback_data=CB_ADMIN_USERS_HUB), InlineKeyboardButton(text="📊 Статистика", callback_data=CB_ADMIN_STATS)],
            [InlineKeyboardButton(text="🔄 Синхронизация", callback_data=CB_ADMIN_SYNC), InlineKeyboardButton(text="🧹 Очистить orphan", callback_data=CB_ADMIN_CLEAN_ORPHANS)],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data=CB_ADMIN_BROADCAST)],
        ]
    )


def get_admin_users_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Поиск", callback_data=CB_ADMIN_USERS_SEARCH), InlineKeyboardButton(text="📋 Список", callback_data=CB_ADMIN_LIST)],
        [InlineKeyboardButton(text="🟢 Активные", callback_data=CB_ADMIN_USERS_ACTIVE), InlineKeyboardButton(text="⚪️ Без подписки", callback_data=CB_ADMIN_USERS_INACTIVE)],
        [InlineKeyboardButton(text="🆕 Новые 24ч", callback_data=CB_ADMIN_USERS_NEW24)],
        [InlineKeyboardButton(text="⬅️ В админку", callback_data=CB_BACK_TO_ADMIN)],
    ])


def get_admin_users_page_kb(users: list[tuple[int, str]], page: int, total_pages: int, list_key: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user_id, label in users:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{CB_ADMIN_USER_PREFIX}{user_id}:{page}:{list_key}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{list_key}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{list_key}:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔎 Поиск", callback_data=CB_ADMIN_USERS_SEARCH), InlineKeyboardButton(text="⬅️ Раздел", callback_data=CB_ADMIN_USERS_HUB)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_admin_user_sections_kb(user_id: int, page: int, list_key: str) -> InlineKeyboardMarkup:
    suffix = f"{user_id}:{page}:{list_key}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Подписка", callback_data=f"{CB_ADMIN_USER_SUBS_PREFIX}{suffix}"), InlineKeyboardButton(text="🔑 Доступ / ключи", callback_data=f"{CB_ADMIN_USER_ACCESS_PREFIX}{suffix}")],
        [InlineKeyboardButton(text="🛠 Админ-действия", callback_data=f"{CB_ADMIN_USER_ACTIONS_PREFIX}{suffix}")],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{list_key}:{page}"), InlineKeyboardButton(text="⬅️ В админку", callback_data=CB_BACK_TO_ADMIN)],
    ])


def get_admin_user_subs_kb(user_id: int, page: int, list_key: str) -> InlineKeyboardMarkup:
    suffix = f"{user_id}:{page}:{list_key}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="+1 день", callback_data=f"add_1_{user_id}_{page}_{list_key}"), InlineKeyboardButton(text="+7 дней", callback_data=f"add_7_{user_id}_{page}_{list_key}"), InlineKeyboardButton(text="+30 дней", callback_data=f"add_30_{user_id}_{page}_{list_key}")],
        [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data=f"{CB_ADMIN_ADD_CUSTOM_PREFIX}{suffix}")],
        [InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"{CB_ADMIN_USER_PREFIX}{suffix}")],
    ])


def get_admin_user_access_kb(user_id: int, page: int, list_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛔ Отключить доступ", callback_data=f"revoke_{user_id}_{page}_{list_key}")],
        [InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"{CB_ADMIN_USER_PREFIX}{user_id}:{page}:{list_key}")],
    ])


def get_admin_user_actions_kb(user_id: int, page: int, list_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить пользователя", callback_data=f"del_{user_id}_{page}_{list_key}")],
        [InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"{CB_ADMIN_USER_PREFIX}{user_id}:{page}:{list_key}")],
    ])


def get_back_to_users_page_kb(page: int, list_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{list_key}:{page}")],
        [InlineKeyboardButton(text="⬅️ В раздел", callback_data=CB_ADMIN_USERS_HUB)],
    ])


def get_back_to_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=CB_BACK_TO_ADMIN)]])


def get_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=CB_BROADCAST_CONFIRM)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=CB_BROADCAST_CANCEL)],
        ]
    )


def get_admin_confirm_kb(action_key: str, back_callback: str = CB_BACK_TO_ADMIN) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{action_key}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{action_key}")],
        ]
    )
