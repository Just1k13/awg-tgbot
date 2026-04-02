BTN_PROFILE = "👤 Профиль"
BTN_CONFIGS = "🔑 Подключение"
BTN_BUY = "💳 Оплатить доступ"
BTN_GUIDE = "📖 Как подключиться"
BTN_SUPPORT = "🆘 Поддержка"
BTN_REFERRALS = "🎁 Рефералы"
BTN_ADMIN = "⚙️ Админка"

ADMIN_CALLBACK_PREFIX = "a:"
USER_CONFIG_CALLBACK_PREFIX = "config_"

CB_BUY_7 = "buy_7"
CB_BUY_30 = "buy_30"
CB_SHOW_INSTRUCTION = "show_instruction"
CB_SHOW_BUY_MENU = "show_buy_menu"
CB_CHECK_ACTIVATION_STATUS = "check_activation_status"
CB_CONFIG_DEVICE_PREFIX = "config_device_"
CB_CONFIG_CONF_PREFIX = "config_conf_"
CB_OPEN_CONFIGS = "open_configs"

CB_ADMIN_LIST = "admin_list"
CB_ADMIN_STATS = "admin_stats"
CB_ADMIN_SYNC = "admin_sync_awg"
CB_ADMIN_CLEAN_ORPHANS = "admin_clean_orphans"
CB_ADMIN_BROADCAST = "admin_broadcast"
CB_ADMIN_BACKUP = "admin_backup"
CB_ADMIN_COMMANDS = "admin_manual_commands"
CB_ADMIN_REFERRALS = "a:rf"
CB_ADMIN_HEALTH = "a:hl"

CB_ADMIN_BACK_MAIN = "a:bk:m"
CB_ADMIN_REFRESH_REFERRALS = "a:rf:r"
CB_ADMIN_REFRESH_HEALTH = "a:rf:h"

CB_BROADCAST_CONFIRM = "broadcast_confirm"
CB_BROADCAST_CANCEL = "broadcast_cancel"

CB_ADMIN_USERS_PAGE_PREFIX = "admin_users_page_"
CB_ADMIN_MANAGE_USER_PREFIX = "admin_manage_user_"
CB_ADMIN_ADD_DAYS_PREFIX = "admin_add_days_"
CB_ADMIN_REVOKE_PREFIX = "admin_revoke_"
CB_ADMIN_DELETE_PREFIX = "admin_delete_"
CB_ADMIN_RETRY_ACTIVATION_PREFIX = "admin_retry_activation_"
CB_ADMIN_DEVICE_DELETE_PREFIX = "admin_device_delete_"
CB_ADMIN_DEVICE_REISSUE_PREFIX = "admin_device_reissue_"

CB_CONFIRM_CLEAN_ORPHANS = "confirm_clean_orphans"
CB_CANCEL_CLEAN_ORPHANS = "cancel_clean_orphans"
CB_CONFIRM_REVOKE = "confirm_revoke"
CB_CANCEL_REVOKE = "cancel_revoke"
CB_CONFIRM_DELETE_USER = "confirm_delete_user"
CB_CANCEL_DELETE_USER = "cancel_delete_user"
CB_CONFIRM_DEVICE_DELETE = "confirm_device_delete"
CB_CANCEL_DEVICE_DELETE = "cancel_device_delete"
CB_CONFIRM_DEVICE_REISSUE = "confirm_device_reissue"
CB_CANCEL_DEVICE_REISSUE = "cancel_device_reissue"
CB_CONFIRM_CLEAN_ORPHANS_FORCE = "confirm_clean_orphans_force"
CB_CANCEL_CLEAN_ORPHANS_FORCE = "cancel_clean_orphans_force"


def is_admin_callback_data(data: str | None) -> bool:
    return bool(data and data.startswith(ADMIN_CALLBACK_PREFIX))


def is_user_config_callback_data(data: str | None) -> bool:
    return bool(data and data.startswith(USER_CONFIG_CALLBACK_PREFIX))
