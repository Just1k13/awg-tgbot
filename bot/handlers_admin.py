from datetime import datetime, timedelta
from cryptography.fernet import Fernet


from aiogram import F, Router, types
from aiogram import Bot
from aiogram.filters import BaseFilter, Command, CommandObject

from awg_backend import (
    clean_orphan_awg_peers, count_free_ip_slots, delete_user_everywhere,
    get_orphan_awg_peers, issue_subscription, list_orphan_delete_candidates_force, revoke_user_access, run_docker, sync_qos_state,
)
from config import (
    ADMIN_COMMAND_COOLDOWN_SECONDS,
    ADMIN_ID,
    BACKUP_ALLOW_INSECURE_SEND,
    BACKUP_ENCRYPTION_KEY,
    BACKUP_SECURE_MODE,
    logger,
)
from database import (
    clear_pending_admin_action, clear_pending_broadcast, create_broadcast_job, db_health_info, fetchall, fetchone,
    get_app_setting,
    get_metric, get_pending_jobs_stats, get_recovery_lag_seconds,
    get_pending_broadcast, get_recent_audit, get_referral_admin_stats, get_text_override, get_user_meta, list_app_settings,
    list_text_overrides, pop_pending_admin_action, reset_text_override,
    reset_app_setting, set_app_setting, set_text_override,
    set_pending_admin_action, set_pending_broadcast, write_audit_log,
)
from helpers import escape_html, format_tg_username, get_status_text, utc_now_naive
from keyboards import (
    get_admin_confirm_kb, get_admin_edit_mode_kb, get_admin_force_confirm_kb, get_admin_inline_kb,
    get_admin_setting_detail_kb, get_admin_settings_list_kb, get_admin_simple_back_kb, get_admin_text_detail_kb,
    get_admin_texts_list_kb, get_broadcast_confirm_kb,
)
from ui_constants import (
    BTN_ADMIN, CB_ADMIN_BACK_MAIN, CB_ADMIN_BACK_SETTINGS, CB_ADMIN_BACK_TEXTS, CB_ADMIN_BROADCAST,
    CB_ADMIN_CANCEL_EDIT, CB_ADMIN_CLEAN_ORPHANS, CB_ADMIN_HEALTH, CB_ADMIN_LIST, CB_ADMIN_REFERRALS,
    CB_ADMIN_REFRESH_HEALTH, CB_ADMIN_REFRESH_REFERRALS, CB_ADMIN_REFRESH_SETTINGS, CB_ADMIN_REFRESH_TEXTS,
    CB_ADMIN_SETTING_EDIT_PREFIX, CB_ADMIN_SETTING_KEY_PREFIX, CB_ADMIN_SETTING_RESET_PREFIX, CB_ADMIN_SETTINGS,
    CB_ADMIN_SETTINGS_PAGE_PREFIX, CB_ADMIN_STATS, CB_ADMIN_SYNC, CB_ADMIN_TEXT_EDIT_PREFIX, CB_ADMIN_TEXT_KEY_PREFIX,
    CB_ADMIN_TEXT_RESET_PREFIX, CB_ADMIN_TEXTS, CB_ADMIN_TEXTS_PAGE_PREFIX,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM,
)
from content_settings import SETTING_DEFAULTS, TEXT_DEFAULTS, validate_text_template
from network_policy import denylist_sync, policy_metrics
from content_settings import get_setting

router = Router()
admin_command_rate_limit: dict[str, object] = {}
ADMIN_USERS_PAGE_SIZE = 10
ADMIN_CONTENT_PAGE_SIZE = 8
ADMIN_EDIT_TIMEOUT_SECONDS = 600


def _cleanup_admin_rate_limit(now) -> None:
    stale = [key for key, dt in admin_command_rate_limit.items() if (now - dt).total_seconds() > 3600]
    for key in stale:
        admin_command_rate_limit.pop(key, None)


def admin_command_limited(action: str, actor_id: int = ADMIN_ID) -> bool:
    now = utc_now_naive()
    _cleanup_admin_rate_limit(now)
    key = f"{actor_id}:{action}"
    last = admin_command_rate_limit.get(key)
    admin_command_rate_limit[key] = now
    return bool(last and (now - last).total_seconds() < ADMIN_COMMAND_COOLDOWN_SECONDS)


class IsAdmin(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return bool(message.from_user and message.from_user.id == ADMIN_ID)


class HasPendingAdminEdit(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        if not message.from_user:
            return False
        row = await fetchone(
            "SELECT 1 FROM pending_actions WHERE admin_id = ? AND action_key IN (?, ?) LIMIT 1",
            (message.from_user.id, "edit_text", "edit_setting"),
        )
        return bool(row)


async def notify_user_subscription_granted(bot: Bot, user_id: int, days: int, new_until) -> bool:
    try:
        await bot.send_message(
            user_id,
            (
                "🎁 <b>Вам выдан доступ</b>\n\n"
                f"⏳ <b>Срок:</b> +{days} дн.\n"
                f"📅 <b>Действует до:</b> {new_until.strftime('%d.%m.%Y %H:%M')}\n\n"
                "🔑 Подключение доступно в разделе <b>Подключение</b>."
            ),
            parse_mode="HTML",
        )
        return True
    except Exception as notify_error:
        logger.warning("Не удалось уведомить пользователя %s о выдаче доступа: %s", user_id, notify_error)
        return False


async def build_awg_sync_text() -> str:
    db_info = await db_health_info()
    orphans = await get_orphan_awg_peers()
    details = []
    for peer in orphans[:20]:
        details.append(f"• <code>{peer['public_key']}</code> — {peer.get('ip') or 'IP не указан'}")
    extra = "\n".join(details) if details else "Потерянных peer не найдено."
    return (
        "🔄 <b>Проверка синхронизации AWG ↔ БД</b>\n\n"
        f"🗄 БД существует: <b>{'да' if db_info['exists'] else 'нет'}</b>\n"
        f"📋 Таблица keys: <b>{'да' if db_info['keys_table_exists'] else 'нет'}</b>\n"
        f"🧱 Нужные колонки: <b>{'да' if db_info['has_required_columns'] else 'нет'}</b>\n"
        f"✅ Валидных ключей в БД: <b>{db_info['valid_keys_count']}</b>\n"
        f"👻 Потерянных peer в AWG: <b>{len(orphans)}</b>\n\n"
        f"{extra}"
    )


async def build_stats_text() -> str:
    total_users = (await fetchone("SELECT COUNT(*) FROM users"))[0]
    total_with_sub = (await fetchone("SELECT COUNT(*) FROM users WHERE sub_until != '0'"))[0]
    active_users = (await fetchone("SELECT COUNT(*) FROM users WHERE sub_until > ?", (utc_now_naive().isoformat(),)))[0]
    total_keys = (await fetchone("SELECT COUNT(*) FROM keys"))[0]
    new_24h = (
        await fetchone(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?",
            ((utc_now_naive() - timedelta(days=1)).isoformat(),),
        )
    )[0]
    free_slots = await count_free_ip_slots()
    orphans = await get_orphan_awg_peers()
    return (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"🟢 Активных подписок: <b>{active_users}</b>\n"
        f"🗃 Записей с sub_until != 0: <b>{total_with_sub}</b>\n"
        f"🔑 Всего ключей в БД: <b>{total_keys}</b>\n"
        f"🆕 Новых за 24ч: <b>{new_24h}</b>\n"
        f"🧩 Свободных IP: <b>{free_slots}</b>\n"
        f"👻 Потерянных peer: <b>{len(orphans)}</b>"
    )


def _truncate_preview(value: str, limit: int = 700) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…<i>обрезано</i>"


def _chunk_keys(keys: list[str], page: int, page_size: int = ADMIN_CONTENT_PAGE_SIZE) -> tuple[list[str], int, int]:
    total_pages = max(1, (len(keys) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    end = start + page_size
    return keys[start:end], page, total_pages


def _is_stale_edit(payload: dict) -> bool:
    started_at = payload.get("started_at")
    if not started_at:
        return False
    try:
        ts = datetime.fromisoformat(started_at)
    except Exception:
        return False
    return (utc_now_naive() - ts).total_seconds() > ADMIN_EDIT_TIMEOUT_SECONDS


def _all_text_keys() -> list[str]:
    return sorted(TEXT_DEFAULTS.keys())


def _all_setting_keys() -> list[str]:
    return sorted(SETTING_DEFAULTS.keys())


def _value_type_hint(default_value) -> str:
    if isinstance(default_value, int):
        return "int"
    if isinstance(default_value, float):
        return "float"
    return "str"


async def _render_texts_list(target_message: types.Message, page: int = 0) -> None:
    keys = _all_text_keys()
    chunk, page, total_pages = _chunk_keys(keys, page)
    await target_message.answer(
        "📝 <b>Тексты</b>\nВыберите ключ для просмотра/редактирования.",
        parse_mode="HTML",
        reply_markup=get_admin_texts_list_kb(chunk, page, total_pages),
    )


async def _render_settings_list(target_message: types.Message, page: int = 0) -> None:
    keys = _all_setting_keys()
    chunk, page, total_pages = _chunk_keys(keys, page)
    await target_message.answer(
        "⚙️ <b>Настройки</b>\nВыберите ключ для просмотра/редактирования.",
        parse_mode="HTML",
        reply_markup=get_admin_settings_list_kb(chunk, page, total_pages),
    )


async def _render_text_detail(target_message: types.Message, key: str, index: int, page: int) -> None:
    current_value = await get_text_override(key) or TEXT_DEFAULTS.get(key, "")
    default_value = TEXT_DEFAULTS.get(key, "")
    await target_message.answer(
        (
            "📝 <b>Карточка текста</b>\n\n"
            f"key=<code>{key}</code>\n"
            f"current:\n<blockquote>{escape_html(_truncate_preview(str(current_value)))}</blockquote>\n"
            f"default:\n<blockquote>{escape_html(_truncate_preview(str(default_value), 280))}</blockquote>"
        ),
        parse_mode="HTML",
        reply_markup=get_admin_text_detail_kb(index, page),
    )


async def _render_setting_detail(target_message: types.Message, key: str, index: int, page: int) -> None:
    raw_current = await get_app_setting(key)
    default_value = SETTING_DEFAULTS.get(key)
    current_value = raw_current if raw_current is not None else default_value
    await target_message.answer(
        (
            "⚙️ <b>Карточка настройки</b>\n\n"
            f"key=<code>{key}</code>\n"
            f"type=<b>{_value_type_hint(default_value)}</b>\n"
            f"current=<code>{escape_html(str(current_value))}</code>\n"
            f"default=<code>{escape_html(str(default_value))}</code>"
        ),
        parse_mode="HTML",
        reply_markup=get_admin_setting_detail_kb(index, page),
    )


async def build_ref_stats_text() -> str:
    stats = await get_referral_admin_stats()
    recent = "\n".join([f"• invitee={r[0]} inviter={r[1]} pay={r[2]}" for r in stats["recent"]]) or "—"
    top = "\n".join([f"• inviter={row[0]} rewards={row[1]}" for row in stats["top"]]) or "—"
    total_bonus_row = await fetchone(
        "SELECT COALESCE(SUM(invitee_bonus_days + inviter_bonus_days), 0) FROM referral_rewards"
    )
    total_bonus_days = int(total_bonus_row[0]) if total_bonus_row else 0
    return (
        "🎁 <b>Referral admin summary</b>\n\n"
        f"pending=<b>{stats['pending']}</b>\n"
        f"rewarded=<b>{stats['rewarded']}</b>\n"
        f"total_bonus_days=<b>{total_bonus_days}</b>\n\n"
        f"<b>Последние начисления</b>\n{recent}\n\n"
        f"<b>Top inviters</b>\n{top}"
    )


async def build_health_text() -> str:
    stats = await get_pending_jobs_stats()
    lag = await get_recovery_lag_seconds()
    helper_failures = await get_metric("awg_helper_failures")
    policy_stats = await policy_metrics()
    denylist_enabled = int(await get_setting("EGRESS_DENYLIST_ENABLED", int) or 0)
    denylist_mode = await get_setting("EGRESS_DENYLIST_MODE", str) or "soft"
    qos_enabled = int(await get_setting("QOS_ENABLED", int) or 0)
    qos_strict = int(await get_setting("QOS_STRICT", int) or 0)
    return (
        "🩺 <b>Отчёт о состоянии</b>\n\n"
        f"jobs.received=<b>{stats['received']}</b>\n"
        f"jobs.provisioning=<b>{stats['provisioning']}</b>\n"
        f"jobs.needs_repair=<b>{stats['needs_repair']}</b>\n"
        f"jobs.stuck_manual=<b>{stats['stuck_manual']}</b>\n"
        f"recovery_lag_sec=<b>{lag}</b>\n"
        f"awg_helper_failures=<b>{helper_failures}</b>\n"
        f"qos_enabled=<b>{qos_enabled}</b> strict=<b>{qos_strict}</b>\n"
        f"qos_errors=<b>{policy_stats['qos_errors']}</b>\n"
        f"qos_last_sync_ok=<b>{policy_stats['qos_last_sync_ok']}</b>\n"
        f"denylist_enabled=<b>{denylist_enabled}</b> mode=<b>{denylist_mode}</b>\n"
        f"denylist_errors=<b>{policy_stats['denylist_errors']}</b>\n"
        f"denylist_last_sync_ok=<b>{policy_stats['denylist_last_sync_ok']}</b>\n"
        f"denylist_last_sync_ts=<b>{policy_stats['denylist_last_sync_ts']}</b>\n"
        f"denylist_entries=<b>{policy_stats['denylist_entries']}</b>"
    )


def _users_page_kb(rows: list[tuple[int, str]], page: int, total_pages: int) -> types.InlineKeyboardMarkup:
    keyboard: list[list[types.InlineKeyboardButton]] = []
    for uid, label in rows:
        keyboard.append([
            types.InlineKeyboardButton(text=f"👤 {label}", callback_data=f"admin_manage_user_{uid}_{page}"),
        ])

    nav_row: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_users_page_{page - 1}"))
    nav_row.append(types.InlineKeyboardButton(text=f"📄 {page + 1}/{max(total_pages, 1)}", callback_data="noop"))
    if page + 1 < total_pages:
        nav_row.append(types.InlineKeyboardButton(text="➡️ Далее", callback_data=f"admin_users_page_{page + 1}"))
    keyboard.append(nav_row)
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _user_manage_kb(uid: int, page: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="+1 день", callback_data=f"admin_add_days_{uid}_1_{page}"),
                types.InlineKeyboardButton(text="+7 дней", callback_data=f"admin_add_days_{uid}_7_{page}"),
                types.InlineKeyboardButton(text="+30 дней", callback_data=f"admin_add_days_{uid}_30_{page}"),
            ],
            [
                types.InlineKeyboardButton(text="⛔ Отключить", callback_data=f"admin_revoke_{uid}_{page}"),
                types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_delete_{uid}_{page}"),
            ],
            [types.InlineKeyboardButton(text="⬅️ К списку", callback_data=f"admin_users_page_{page}")],
        ]
    )


async def _render_users_page(target_message: types.Message, page: int) -> None:
    total_users = (await fetchone("SELECT COUNT(*) FROM users"))[0]
    if total_users == 0:
        await target_message.answer("Список пользователей пуст.")
        return
    total_pages = max(1, (total_users + ADMIN_USERS_PAGE_SIZE - 1) // ADMIN_USERS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    offset = page * ADMIN_USERS_PAGE_SIZE
    rows = await fetchall(
        """
        SELECT user_id, sub_until
        FROM users
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (ADMIN_USERS_PAGE_SIZE, offset),
    )
    labels: list[tuple[int, str]] = []
    lines = [f"👥 <b>Пользователи</b> (страница {page + 1}/{total_pages})\n"]
    for uid, sub_until in rows:
        status_text, until_text = get_status_text(sub_until)
        tg_username, _ = await get_user_meta(uid)
        short_name = format_tg_username(tg_username)
        labels.append((uid, f"{uid} — {short_name}"))
        lines.append(f"• <code>{uid}</code> — {short_name} — {status_text} — {until_text}")
    await target_message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=_users_page_kb(labels, page, total_pages),
    )


@router.message(F.text == BTN_ADMIN, IsAdmin())
async def admin_panel(message: types.Message):
    stats_text = await build_stats_text()
    db_info = await db_health_info()
    db_status = "🟢 Нормально" if db_info["is_healthy"] else "🟡 Нужна проверка"
    await message.answer(
        stats_text + f"\n🗄 Статус БД: <b>{db_status}</b>",
        parse_mode="HTML",
        reply_markup=get_admin_inline_kb(),
    )


@router.callback_query(F.data == CB_ADMIN_BACK_MAIN)
async def admin_back_main(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.answer("⚙️ Админ-меню", reply_markup=get_admin_inline_kb())
    await cb.answer()


@router.callback_query(F.data == CB_ADMIN_TEXTS)
async def admin_texts_menu(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await _render_texts_list(cb.message, 0)
    await cb.answer("Открыто")


@router.callback_query(F.data == CB_ADMIN_SETTINGS)
async def admin_settings_menu(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await _render_settings_list(cb.message, 0)
    await cb.answer("Открыто")


@router.callback_query(F.data == CB_ADMIN_BACK_TEXTS)
@router.callback_query(F.data == CB_ADMIN_REFRESH_TEXTS)
async def admin_texts_back_refresh(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await _render_texts_list(cb.message, 0)
    await cb.answer("Готово")


@router.callback_query(F.data == CB_ADMIN_BACK_SETTINGS)
@router.callback_query(F.data == CB_ADMIN_REFRESH_SETTINGS)
async def admin_settings_back_refresh(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await _render_settings_list(cb.message, 0)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith(CB_ADMIN_TEXTS_PAGE_PREFIX))
async def admin_texts_page(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    page = int(cb.data.removeprefix(CB_ADMIN_TEXTS_PAGE_PREFIX))
    await _render_texts_list(cb.message, page)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith(CB_ADMIN_SETTINGS_PAGE_PREFIX))
async def admin_settings_page(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    page = int(cb.data.removeprefix(CB_ADMIN_SETTINGS_PAGE_PREFIX))
    await _render_settings_list(cb.message, page)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith(CB_ADMIN_TEXT_KEY_PREFIX))
async def admin_text_key_detail(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    raw = cb.data.removeprefix(CB_ADMIN_TEXT_KEY_PREFIX)
    index_raw, page_raw = raw.split("_", 1)
    idx = int(index_raw)
    page = int(page_raw)
    chunk, _, _ = _chunk_keys(_all_text_keys(), page)
    if idx < 0 or idx >= len(chunk):
        await cb.answer("Ключ не найден", show_alert=True)
        return
    key = chunk[idx]
    await _render_text_detail(cb.message, key, idx, page)
    await cb.answer("Открыто")


@router.callback_query(F.data.startswith(CB_ADMIN_SETTING_KEY_PREFIX))
async def admin_setting_key_detail(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    raw = cb.data.removeprefix(CB_ADMIN_SETTING_KEY_PREFIX)
    index_raw, page_raw = raw.split("_", 1)
    idx = int(index_raw)
    page = int(page_raw)
    chunk, _, _ = _chunk_keys(_all_setting_keys(), page)
    if idx < 0 or idx >= len(chunk):
        await cb.answer("Ключ не найден", show_alert=True)
        return
    key = chunk[idx]
    await _render_setting_detail(cb.message, key, idx, page)
    await cb.answer("Открыто")


@router.callback_query(F.data.startswith(CB_ADMIN_TEXT_EDIT_PREFIX))
async def admin_text_edit_start(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    raw = cb.data.removeprefix(CB_ADMIN_TEXT_EDIT_PREFIX)
    index_raw, page_raw = raw.split("_", 1)
    idx = int(index_raw)
    page = int(page_raw)
    chunk, _, _ = _chunk_keys(_all_text_keys(), page)
    if idx < 0 or idx >= len(chunk):
        await cb.answer("Ключ не найден", show_alert=True)
        return
    key = chunk[idx]
    await set_pending_admin_action(
        cb.from_user.id,
        "edit_text",
        {"key": key, "page": page, "index": idx, "started_at": utc_now_naive().isoformat()},
    )
    await cb.message.answer(
        f"✏️ Отправьте новое значение для <code>{key}</code>.\nДля отмены нажмите кнопку ниже.",
        parse_mode="HTML",
        reply_markup=get_admin_edit_mode_kb(),
    )
    await cb.answer("Режим редактирования")


@router.callback_query(F.data.startswith(CB_ADMIN_SETTING_EDIT_PREFIX))
async def admin_setting_edit_start(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    raw = cb.data.removeprefix(CB_ADMIN_SETTING_EDIT_PREFIX)
    index_raw, page_raw = raw.split("_", 1)
    idx = int(index_raw)
    page = int(page_raw)
    chunk, _, _ = _chunk_keys(_all_setting_keys(), page)
    if idx < 0 or idx >= len(chunk):
        await cb.answer("Ключ не найден", show_alert=True)
        return
    key = chunk[idx]
    await set_pending_admin_action(
        cb.from_user.id,
        "edit_setting",
        {"key": key, "page": page, "index": idx, "started_at": utc_now_naive().isoformat()},
    )
    await cb.message.answer(
        f"✏️ Отправьте новое значение для <code>{key}</code>.\nДля отмены нажмите кнопку ниже.",
        parse_mode="HTML",
        reply_markup=get_admin_edit_mode_kb(),
    )
    await cb.answer("Режим редактирования")


@router.callback_query(F.data == CB_ADMIN_CANCEL_EDIT)
async def admin_cancel_edit(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await clear_pending_admin_action(cb.from_user.id, "edit_text")
    await clear_pending_admin_action(cb.from_user.id, "edit_setting")
    await cb.message.answer("❌ Редактирование отменено.")
    await cb.answer("Отменено")

@router.callback_query(F.data == CB_ADMIN_STATS)
async def admin_stats_cb(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.answer(await build_stats_text(), parse_mode="HTML")
    await cb.answer("Готово")


@router.callback_query(F.data == CB_ADMIN_SYNC)
async def admin_sync_awg(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        await sync_qos_state()
        await denylist_sync(run_docker)
        await cb.message.answer(await build_awg_sync_text(), parse_mode="HTML")
        await cb.answer("Синхронизация проверена")
    except Exception as e:
        logger.exception("Ошибка admin_sync_awg: %s", e)
        await cb.answer("❌ Ошибка проверки", show_alert=True)


@router.callback_query(F.data == CB_ADMIN_CLEAN_ORPHANS)
async def admin_clean_orphans(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    orphans = await get_orphan_awg_peers()
    await set_pending_admin_action(ADMIN_ID, "clean_orphans", {"action": "clean_orphans", "orphans": len(orphans)})
    await cb.message.answer(
        (
            "⚠️ <b>Проверка потерянных peer</b>\n\n"
            f"Найдено потерянных peer: <b>{len(orphans)}</b>\n"
            "Первый этап: peer будут помещены в карантин (без удаления).\n"
            "Force-удаление выполняйте только после повторной проверки."
        ),
        parse_mode="HTML",
        reply_markup=get_admin_confirm_kb("clean_orphans"),
    )
    await cb.answer()


@router.callback_query(F.data == "confirm_clean_orphans")
async def confirm_clean_orphans(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    action = await pop_pending_admin_action(ADMIN_ID, "clean_orphans")
    if not action or action.get("action") != "clean_orphans":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    try:
        removed = await clean_orphan_awg_peers(force=False)
        await write_audit_log(ADMIN_ID, "clean_orphans_quarantine", f"removed={removed}")
        await cb.message.answer(
            "🧹 <b>Проверка потерянных peer завершена</b>\n\n"
            "Peer помечены как защищённые (карантин).\n"
            f"Физически удалено (force only): <b>{removed}</b>",
            parse_mode="HTML",
        )
        await cb.answer("Очистка завершена")
    except Exception as e:
        logger.exception("Ошибка confirm_clean_orphans: %s", e)
        await cb.answer(str(e), show_alert=True)


@router.callback_query(F.data == "cancel_clean_orphans")
async def cancel_clean_orphans(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans")
    await cb.message.answer("❌ Очистка потерянных peer отменена")
    await cb.answer("Отменено")


@router.callback_query(F.data == CB_ADMIN_LIST)
async def admin_list_all(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await _render_users_page(cb.message, 0)
    await cb.answer()


@router.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        page = int(cb.data.removeprefix("admin_users_page_"))
        await _render_users_page(cb.message, page)
        await cb.answer("Открыто")
    except Exception as e:
        logger.exception("Ошибка admin_users_page: %s", e)
        await cb.answer("❌ Не удалось открыть страницу", show_alert=True)


@router.callback_query(F.data.startswith("admin_manage_user_"))
async def admin_manage_user(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        _, _, _, uid_raw, page_raw = cb.data.split("_", 4)
        uid = int(uid_raw)
        page = int(page_raw)
        row = await fetchone("SELECT sub_until FROM users WHERE user_id = ?", (uid,))
        if not row:
            await cb.answer("Пользователь не найден", show_alert=True)
            return
        sub_until = row[0]
        status_text, until_text = get_status_text(sub_until)
        tg_username, first_name = await get_user_meta(uid)
        await cb.message.answer(
            (
                "🛠 <b>Управление пользователем</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"👤 Имя: {escape_html(first_name)}\n"
                f"✈️ Telegram: {format_tg_username(tg_username)}\n"
                f"📌 Статус: {status_text}\n"
                f"📅 До: <b>{until_text}</b>"
            ),
            parse_mode="HTML",
            reply_markup=_user_manage_kb(uid, page),
        )
        await cb.answer("Открыто")
    except Exception as e:
        logger.exception("Ошибка admin_manage_user: %s", e)
        await cb.answer("❌ Не удалось открыть карточку пользователя", show_alert=True)


@router.callback_query(F.data.startswith("admin_add_days_"))
async def admin_add_days_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        _, _, _, uid_raw, days_raw, _page_raw = cb.data.split("_", 5)
        uid = int(uid_raw)
        days = int(days_raw)
        if admin_command_limited(f"admin_add_{days}", cb.from_user.id):
            await cb.answer("Слишком часто", show_alert=True)
            return
        new_until = await issue_subscription(uid, days)
        notified = await notify_user_subscription_granted(cb.bot, uid, days, new_until)
        await write_audit_log(ADMIN_ID, f"admin_add_{days}", f"target={uid}; until={new_until.isoformat()}; notified={int(notified)}")
        await cb.answer(f"✅ +{days} дней пользователю {uid}")
        await cb.message.answer(
            (
                f"✅ <b>Пользователю выдано +{days} дней</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"📅 До: <b>{new_until.strftime('%d.%m.%Y %H:%M')}</b>"
            ),
            parse_mode="HTML",
        )
        if not notified:
            await cb.message.answer("⚠️ Доступ выдан, но уведомление пользователю отправить не удалось.")
    except Exception as e:
        logger.exception("Ошибка admin_add_days_btn: %s", e)
        await cb.answer("❌ Не удалось продлить доступ", show_alert=True)


@router.callback_query(F.data.startswith("admin_revoke_"))
async def admin_revoke_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    _, _, uid_raw, page_raw = cb.data.split("_", 3)
    uid = int(uid_raw)
    page = int(page_raw)
    await set_pending_admin_action(ADMIN_ID, "revoke", {"action": "revoke", "target": uid, "page": page})
    await cb.message.answer(
        (
            "⚠️ <b>Подтвердите отключение доступа</b>\n\n"
            f"Пользователь: <code>{uid}</code>"
        ),
        parse_mode="HTML",
        reply_markup=get_admin_confirm_kb("revoke"),
    )
    await cb.answer()


@router.callback_query(F.data == "confirm_revoke")
async def confirm_revoke(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    action = await pop_pending_admin_action(ADMIN_ID, "revoke")
    if not action or action.get("action") != "revoke":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    uid = int(action["target"])
    try:
        removed = await revoke_user_access(uid)
        await write_audit_log(ADMIN_ID, "admin_revoke", f"target={uid}; removed={removed}")
        await cb.message.answer(
            (
                f"⛔ <b>Доступ отключён</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"🔌 Удалено peer: <b>{removed}</b>"
            ),
            parse_mode="HTML",
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_revoke: %s", e)
        await cb.answer("❌ Не удалось отключить пользователя", show_alert=True)


@router.callback_query(F.data == "cancel_revoke")
async def cancel_revoke(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "revoke")
    await cb.message.answer("❌ Отключение отменено")
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith("admin_delete_"))
async def admin_del_user(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    _, _, uid_raw, page_raw = cb.data.split("_", 3)
    uid = int(uid_raw)
    page = int(page_raw)
    await set_pending_admin_action(ADMIN_ID, "delete_user", {"action": "delete_user", "target": uid, "page": page})
    await cb.message.answer(
        (
            "⚠️ <b>Подтвердите полное удаление пользователя</b>\n\n"
            f"Пользователь: <code>{uid}</code>"
        ),
        parse_mode="HTML",
        reply_markup=get_admin_confirm_kb("delete_user"),
    )
    await cb.answer()


@router.callback_query(F.data == "confirm_delete_user")
async def confirm_delete_user(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    action = await pop_pending_admin_action(ADMIN_ID, "delete_user")
    if not action or action.get("action") != "delete_user":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    uid = int(action["target"])
    try:
        peers_count, _ = await delete_user_everywhere(uid)
        await write_audit_log(ADMIN_ID, "admin_delete_user", f"target={uid}; removed={peers_count}")
        await cb.message.answer(
            (
                f"🗑 <b>Пользователь удалён</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"🔌 Удалено peer: <b>{peers_count}</b>"
            ),
            parse_mode="HTML",
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_delete_user: %s", e)
        await cb.answer(f"❌ Не удалось удалить пользователя: {str(e)[:120]}", show_alert=True)


@router.callback_query(F.data == "cancel_delete_user")
async def cancel_delete_user(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "delete_user")
    await cb.message.answer("❌ Удаление отменено")
    await cb.answer("Отменено")


@router.callback_query(F.data == CB_ADMIN_BROADCAST)
async def admin_broadcast_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer(
        (
            "📢 <b>Рассылка</b>\n\n"
            "Используйте команду:\n"
            "<code>/send Ваш текст</code>\n\n"
            "Перед отправкой будет подтверждение."
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == CB_BROADCAST_CONFIRM)
async def broadcast_confirm(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    text = await get_pending_broadcast(ADMIN_ID)
    if not text:
        await cb.answer("Нет ожидающей рассылки", show_alert=True)
        return
    job_id = await create_broadcast_job(ADMIN_ID, text)
    await clear_pending_broadcast(ADMIN_ID)
    await write_audit_log(ADMIN_ID, "broadcast_queued", f"job_id={job_id}")
    await cb.message.answer(
        (
            "📢 <b>Рассылка поставлена в очередь</b>\n\n"
            f"job_id: <code>{job_id}</code>\n"
            "Отправка идёт в фоне; итог придёт отдельным сообщением."
        ),
        parse_mode="HTML",
    )
    await cb.answer("Поставлено в очередь")


@router.callback_query(F.data == CB_BROADCAST_CANCEL)
async def broadcast_cancel(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await clear_pending_broadcast(ADMIN_ID)
    await write_audit_log(ADMIN_ID, "broadcast_cancel", "")
    await cb.message.answer("❌ Рассылка отменена")
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith(CB_ADMIN_TEXT_RESET_PREFIX))
async def admin_text_reset_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    raw = cb.data.removeprefix(CB_ADMIN_TEXT_RESET_PREFIX)
    index_raw, page_raw = raw.split("_", 1)
    idx = int(index_raw)
    page = int(page_raw)
    chunk, _, _ = _chunk_keys(_all_text_keys(), page)
    if idx < 0 or idx >= len(chunk):
        await cb.answer("Ключ не найден", show_alert=True)
        return
    key = chunk[idx]
    await reset_text_override(key)
    await write_audit_log(cb.from_user.id, "text_reset", f"key={key}")
    await cb.message.answer(f"♻️ Сброшен override для <code>{key}</code>.", parse_mode="HTML")
    await _render_text_detail(cb.message, key, idx, page)
    await cb.answer("Сброшено")


@router.callback_query(F.data.startswith(CB_ADMIN_SETTING_RESET_PREFIX))
async def admin_setting_reset_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    raw = cb.data.removeprefix(CB_ADMIN_SETTING_RESET_PREFIX)
    index_raw, page_raw = raw.split("_", 1)
    idx = int(index_raw)
    page = int(page_raw)
    chunk, _, _ = _chunk_keys(_all_setting_keys(), page)
    if idx < 0 or idx >= len(chunk):
        await cb.answer("Ключ не найден", show_alert=True)
        return
    key = chunk[idx]
    await reset_app_setting(key)
    await write_audit_log(cb.from_user.id, "setting_reset", f"key={key}")
    await cb.message.answer(f"♻️ Сброшена настройка <code>{key}</code> к default.", parse_mode="HTML")
    await _render_setting_detail(cb.message, key, idx, page)
    await cb.answer("Сброшено")


@router.callback_query(F.data == CB_ADMIN_REFERRALS)
@router.callback_query(F.data == CB_ADMIN_REFRESH_REFERRALS)
async def admin_referrals_summary(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.answer(
        await build_ref_stats_text(),
        parse_mode="HTML",
        reply_markup=get_admin_simple_back_kb(CB_ADMIN_BACK_MAIN, CB_ADMIN_REFRESH_REFERRALS),
    )
    await cb.answer("Готово")


@router.callback_query(F.data == CB_ADMIN_HEALTH)
@router.callback_query(F.data == CB_ADMIN_REFRESH_HEALTH)
async def admin_health_summary(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.answer(
        await build_health_text(),
        parse_mode="HTML",
        reply_markup=get_admin_simple_back_kb(CB_ADMIN_BACK_MAIN, CB_ADMIN_REFRESH_HEALTH),
    )
    await cb.answer("Готово")


@router.message(Command("cancel_edit"), IsAdmin())
async def cancel_edit_cmd(message: types.Message):
    await clear_pending_admin_action(message.from_user.id, "edit_text")
    await clear_pending_admin_action(message.from_user.id, "edit_setting")
    await message.answer("❌ Редактирование отменено.")


@router.message(IsAdmin(), HasPendingAdminEdit(), F.text, ~F.text.startswith("/"))
async def admin_pending_edit_consumer(message: types.Message):
    edit_text_state = await pop_pending_admin_action(message.from_user.id, "edit_text")
    if edit_text_state:
        if _is_stale_edit(edit_text_state):
            await message.answer("⌛ Сессия редактирования текста устарела. Запустите заново.")
            return
        key = str(edit_text_state.get("key") or "")
        valid, err = await validate_text_template(key, message.text)
        if not valid:
            await message.answer(f"❌ {err}\nОтправьте значение снова или нажмите ❌ Отмена.", reply_markup=get_admin_edit_mode_kb())
            await set_pending_admin_action(message.from_user.id, "edit_text", edit_text_state)
            return
        await set_text_override(key, message.text, updated_by=message.from_user.id)
        await write_audit_log(message.from_user.id, "text_set", f"key={key}; via=ui")
        await message.answer("✅ Текст сохранён.")
        await _render_text_detail(
            message,
            key,
            int(edit_text_state.get("index", 0)),
            int(edit_text_state.get("page", 0)),
        )
        return

    edit_setting_state = await pop_pending_admin_action(message.from_user.id, "edit_setting")
    if not edit_setting_state:
        return
    if _is_stale_edit(edit_setting_state):
        await message.answer("⌛ Сессия редактирования настройки устарела. Запустите заново.")
        return
    key = str(edit_setting_state.get("key") or "")
    default_value = SETTING_DEFAULTS.get(key)
    cast_type = type(default_value) if default_value is not None else str
    try:
        cast_type(message.text)
    except Exception:
        await message.answer(
            f"❌ Некорректный тип: ожидается {_value_type_hint(default_value)}.\nОтправьте значение снова или нажмите ❌ Отмена.",
            reply_markup=get_admin_edit_mode_kb(),
        )
        await set_pending_admin_action(message.from_user.id, "edit_setting", edit_setting_state)
        return
    await set_app_setting(key, message.text, updated_by=message.from_user.id)
    await write_audit_log(message.from_user.id, "setting_set", f"key={key}; via=ui")
    await message.answer("✅ Настройка сохранена.")
    await _render_setting_detail(
        message,
        key,
        int(edit_setting_state.get("index", 0)),
        int(edit_setting_state.get("page", 0)),
    )


@router.message(Command("give"), IsAdmin())
async def give_manual(message: types.Message, command: CommandObject):
    if admin_command_limited("give", message.from_user.id):
        await message.answer("⏳ Слишком частый вызов /give")
        return
    if not command.args:
        await message.answer("Формат: <code>/give ID [ДНИ]</code>\nПо умолчанию: 30 дней", parse_mode="HTML")
        return
    try:
        parts = command.args.split()
        uid = int(parts[0])
        days = int(parts[1]) if len(parts) > 1 else 30
        if days <= 0:
            await message.answer("Количество дней должно быть больше 0.")
            return
        new_until = await issue_subscription(uid, days)
        notified = await notify_user_subscription_granted(message.bot, uid, days, new_until)
        await write_audit_log(ADMIN_ID, "give", f"target={uid}; days={days}; until={new_until.isoformat()}; notified={int(notified)}")
        await message.answer(
            (
                f"✅ Доступ продлён на {days} дней пользователю <code>{uid}</code>\n"
                f"📅 Действует до: <b>{new_until.strftime('%d.%m.%Y %H:%M')}</b>"
            ),
            parse_mode="HTML",
        )
        if not notified:
            await message.answer("⚠️ Доступ выдан, но уведомление пользователю отправить не удалось.")
    except ValueError:
        await message.answer("Ошибка формата. Пример: <code>/give 123456789 30</code> или <code>/give 123456789</code>", parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /give: %s", e)
        await message.answer("❌ Не удалось выдать доступ.")


@router.message(Command("revoke"), IsAdmin())
async def revoke_user_cmd(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Формат: <code>/revoke ID</code>", parse_mode="HTML")
        return
    try:
        uid = int(command.args)
        await set_pending_admin_action(ADMIN_ID, "revoke", {"action": "revoke", "target": uid})
        await message.answer(
            f"⚠️ Подтвердите отключение пользователя <code>{uid}</code>",
            parse_mode="HTML",
            reply_markup=get_admin_confirm_kb("revoke"),
        )
    except Exception as e:
        logger.exception("Ошибка /revoke: %s", e)
        await message.answer("❌ Не удалось подготовить отключение пользователя")


@router.message(Command("users"), IsAdmin())
async def list_users_cmd(message: types.Message):
    rows = await fetchall("SELECT user_id, sub_until FROM users ORDER BY created_at DESC LIMIT 50")
    if not rows:
        await message.answer("Пользователей пока нет.")
        return
    lines = ["👥 <b>Последние пользователи</b>\n"]
    for uid, sub_until in rows:
        status_text, until_text = get_status_text(sub_until)
        tg_username, _ = await get_user_meta(uid)
        lines.append(f"• <code>{uid}</code> — {format_tg_username(tg_username)} — {status_text} — {until_text}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"), IsAdmin())
async def stats_cmd(message: types.Message):
    await message.answer(await build_stats_text(), parse_mode="HTML")


@router.message(Command("orphans"), IsAdmin())
async def orphans_cmd(message: types.Message):
    try:
        orphans = await get_orphan_awg_peers()
        if not orphans:
            await message.answer("✅ Потерянные peer не найдены.")
            return
        lines = [f"👻 <b>Потерянные peer ({len(orphans)})</b>\n"]
        for peer in orphans[:50]:
            lines.append(f"• <code>{peer['public_key']}</code> — {peer.get('ip') or 'IP не указан'}")
        if len(orphans) > 50:
            lines.append(f"\n... и ещё {len(orphans) - 50}")
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /orphans: %s", e)
        await message.answer("❌ Не удалось получить список потерянных peer.")


@router.message(Command("audit"), IsAdmin())
async def audit_cmd(message: types.Message, command: CommandObject):
    limit = 20
    if command.args:
        try:
            limit = max(1, min(100, int(command.args)))
        except ValueError:
            pass
    try:
        rows = await get_recent_audit(limit=limit)
        if not rows:
            await message.answer("Журнал действий пуст.")
            return
        lines = [f"📜 <b>Последние события ({len(rows)})</b>\n"]
        for row_id, user_id, action, details, created_at in rows:
            lines.append(
                f"#{row_id} | <code>{user_id}</code> | <b>{action}</b>\n"
                f"{created_at}\n"
                f"{details or '-'}\n"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /audit: %s", e)
        await message.answer("❌ Не удалось получить audit log.")


@router.message(Command("sync_awg"), IsAdmin())
async def sync_awg_cmd(message: types.Message):
    try:
        await sync_qos_state()
        await denylist_sync(run_docker)
        await message.answer(await build_awg_sync_text(), parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /sync_awg: %s", e)
        await message.answer("❌ Ошибка проверки синхронизации.")


@router.message(Command("clean_orphans"), IsAdmin())
async def clean_orphans_cmd(message: types.Message):
    try:
        orphans = await get_orphan_awg_peers()
        await set_pending_admin_action(
            ADMIN_ID,
            "clean_orphans",
            {"action": "clean_orphans", "orphans": len(orphans)},
        )
        await message.answer(
            (
                "⚠️ <b>Подтвердите очистку потерянных peer (карантин)</b>\n\n"
                f"Найдено потерянных peer: <b>{len(orphans)}</b>\n"
                "На этом шаге peer только помечаются как карантин и не удаляются физически.\n"
                "Для физического удаления используйте отдельную команду <code>/clean_orphans_force</code> после проверки."
            ),
            parse_mode="HTML",
            reply_markup=get_admin_confirm_kb("clean_orphans"),
        )
    except Exception as e:
        logger.exception("Ошибка /clean_orphans: %s", e)
        await message.answer("❌ Не удалось подготовить очистку потерянных peer.")


@router.message(Command("clean_orphans_force"), IsAdmin())
async def clean_orphans_force_cmd(message: types.Message):
    try:
        candidates = await list_orphan_delete_candidates_force()
        preview_keys = [item.get("public_key") for item in candidates[:10] if item.get("public_key")]
        await set_pending_admin_action(
            ADMIN_ID,
            "clean_orphans_force",
            {
                "action": "clean_orphans_force",
                "candidate_count": len(candidates),
                "preview": preview_keys,
                "confirmed": False,
            },
        )
        preview_text = "\n".join(f"• <code>{key}</code>" for key in preview_keys) or "—"
        await message.answer(
            (
                "🧨 <b>Force-cleanup (предпросмотр)</b>\n\n"
                f"Кандидатов на удаление: <b>{len(candidates)}</b>\n"
                "Будут удалены только quarantined+managed peer, которые всё ещё отсутствуют в БД.\n\n"
                f"Первые ключи:\n{preview_text}\n\n"
                "Нажмите подтверждение ниже, затем введите: <code>/force_delete FORCE</code> или <code>/force_delete DELETE</code>."
            ),
            parse_mode="HTML",
            reply_markup=get_admin_force_confirm_kb(),
        )
    except Exception as e:
        logger.exception("Ошибка /clean_orphans_force: %s", e)
        await message.answer("❌ Не удалось подготовить принудительную очистку потерянных peer.")


@router.callback_query(F.data == "confirm_clean_orphans_force")
async def confirm_clean_orphans_force(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    action = await pop_pending_admin_action(ADMIN_ID, "clean_orphans_force")
    if not action or action.get("action") != "clean_orphans_force":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    action["confirmed"] = True
    await set_pending_admin_action(ADMIN_ID, "clean_orphans_force_word", action)
    await cb.message.answer(
        "🛡 Дополнительное подтверждение: введите <code>/force_delete FORCE</code> или <code>/force_delete DELETE</code>.",
        parse_mode="HTML",
    )
    await cb.answer("Ожидаю кодовое слово")


@router.callback_query(F.data == "cancel_clean_orphans_force")
async def cancel_clean_orphans_force(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans_force")
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans_force_word")
    await cb.message.answer("❌ Force-очистка потерянных peer отменена")
    await cb.answer()


@router.message(Command("force_delete"), IsAdmin())
async def force_delete_cmd(message: types.Message, command: CommandObject):
    action = await pop_pending_admin_action(ADMIN_ID, "clean_orphans_force_word")
    if not action or action.get("action") != "clean_orphans_force" or not action.get("confirmed"):
        await message.answer("Нет подтверждённого force-действия. Сначала выполните /clean_orphans_force.")
        return
    word = (command.args or "").strip().upper()
    if word not in {"FORCE", "DELETE"}:
        await set_pending_admin_action(ADMIN_ID, "clean_orphans_force_word", action)
        await message.answer("❌ Неверное кодовое слово. Введите /force_delete FORCE или /force_delete DELETE.")
        return
    try:
        removed = await clean_orphan_awg_peers(force=True)
        await write_audit_log(ADMIN_ID, "clean_orphans_force", f"removed={removed} confirm_word={word}")
        await message.answer(
            (
                "🧨 <b>Force-cleanup завершён</b>\n\n"
                f"Физически удалено потерянных peer: <b>{removed}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Ошибка /force_delete: %s", e)
        await message.answer("❌ Не удалось выполнить принудительную очистку потерянных peer.")


@router.message(Command("backup"), IsAdmin())
async def backup_db(message: types.Message):
    from pathlib import Path
    from tempfile import NamedTemporaryFile
    import sqlite3
    from config import DB_PATH
    try:
        db_file = Path(DB_PATH)
        if not db_file.exists():
            await message.answer("❌ Файл базы данных не найден.")
            return
        with NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        src = sqlite3.connect(str(db_file))
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
            dst.execute("UPDATE keys SET config = '', vpn_key = '', psk_key = '', client_private_key = ''")
            dst.commit()
        finally:
            dst.close()
            src.close()
        redacted = Path(tmp_path)
        payload = redacted.read_bytes()
        filename = f"redacted_{db_file.name}"
        caption = (f"📦 Резервная копия базы данных без секретов\n"
                   f"📅 {utc_now_naive().strftime('%d.%m.%Y %H:%M')}")
        if BACKUP_SECURE_MODE:
            if not BACKUP_ENCRYPTION_KEY:
                await message.answer("❌ BACKUP_SECURE_MODE=1, но BACKUP_ENCRYPTION_KEY не задан.")
                redacted.unlink(missing_ok=True)
                return
            payload = Fernet(BACKUP_ENCRYPTION_KEY.encode("utf-8")).encrypt(payload)
            filename = f"{filename}.enc"
            caption += "\n🔐 Secure mode: backup зашифрован (Fernet)."
        elif not BACKUP_ALLOW_INSECURE_SEND:
            redacted.unlink(missing_ok=True)
            await message.answer("❌ Небезопасная отправка backup отключена. Включите BACKUP_SECURE_MODE=1 или явно задайте BACKUP_ALLOW_INSECURE_SEND=1.")
            return
        else:
            caption += "\n⚠️ Insecure mode: файл отправлен без шифрования (явный opt-in)."
        await message.answer_document(
            types.BufferedInputFile(payload, filename=filename),
            caption=caption,
        )
        redacted.unlink(missing_ok=True)
    except Exception as e:
        logger.exception("Ошибка /backup: %s", e)
        await message.answer("❌ Не удалось отправить backup базы.")


@router.message(Command("send"), IsAdmin())
async def broadcast_prepare(message: types.Message, command: CommandObject):
    if admin_command_limited("send", message.from_user.id):
        await message.answer("⏳ Слишком частый вызов /send")
        return
    if not command.args:
        await message.answer("Напишите текст после <code>/send</code>", parse_mode="HTML")
        return
    await set_pending_broadcast(ADMIN_ID, command.args)
    await message.answer(
        (
            "📢 <b>Подтвердите рассылку</b>\n\n"
            f"{escape_html(command.args)}"
        ),
        parse_mode="HTML",
        reply_markup=get_broadcast_confirm_kb(),
    )


@router.message(Command("health"), IsAdmin())
async def health_cmd(message: types.Message):
    await message.answer(await build_health_text(), parse_mode="HTML")


@router.message(Command("text_list"), IsAdmin())
async def text_list_cmd(message: types.Message):
    rows = await list_text_overrides()
    defaults = ", ".join(sorted(TEXT_DEFAULTS.keys()))
    custom = ", ".join([row[0] for row in rows]) or "—"
    await message.answer(f"TEXT_DEFAULTS: {defaults}\nCUSTOM: {custom}")


@router.message(Command("text_get"), IsAdmin())
async def text_get_cmd(message: types.Message, command: CommandObject):
    key = (command.args or "").strip()
    if not key:
        await message.answer("Использование: /text_get <key>")
        return
    value = await get_text_override(key) or TEXT_DEFAULTS.get(key)
    await message.answer(f"{key}:\n\n{escape_html(str(value or ''))}", parse_mode="HTML")


@router.message(Command("text_set"), IsAdmin())
async def text_set_cmd(message: types.Message, command: CommandObject):
    raw = (command.args or "").strip()
    if " " not in raw:
        await message.answer("Использование: /text_set <key> <value>")
        return
    key, value = raw.split(" ", 1)
    valid, err = await validate_text_template(key, value)
    if not valid:
        await message.answer(f"❌ {err}")
        return
    await set_text_override(key, value, updated_by=message.from_user.id)
    await write_audit_log(message.from_user.id, "text_set", f"key={key}")
    await message.answer("✅ Текст обновлён.")


@router.message(Command("text_reset"), IsAdmin())
async def text_reset_cmd(message: types.Message, command: CommandObject):
    key = (command.args or "").strip()
    if not key:
        await message.answer("Использование: /text_reset <key>")
        return
    await reset_text_override(key)
    await write_audit_log(message.from_user.id, "text_reset", f"key={key}")
    await message.answer("✅ Override удалён.")


@router.message(Command("setting_list"), IsAdmin())
async def setting_list_cmd(message: types.Message):
    rows = await list_app_settings()
    defaults = ", ".join(sorted(SETTING_DEFAULTS.keys()))
    custom = ", ".join([row[0] for row in rows]) or "—"
    await message.answer(f"SETTING_DEFAULTS: {defaults}\nCUSTOM: {custom}")


@router.message(Command("setting_get"), IsAdmin())
async def setting_get_cmd(message: types.Message, command: CommandObject):
    key = (command.args or "").strip()
    if not key:
        await message.answer("Использование: /setting_get <key>")
        return
    value = await get_app_setting(key)
    if value is None:
        value = SETTING_DEFAULTS.get(key)
    await message.answer(f"{key}={value}")


@router.message(Command("setting_set"), IsAdmin())
async def setting_set_cmd(message: types.Message, command: CommandObject):
    raw = (command.args or "").strip()
    if " " not in raw:
        await message.answer("Использование: /setting_set <key> <value>")
        return
    key, value = raw.split(" ", 1)
    await set_app_setting(key, value, updated_by=message.from_user.id)
    await write_audit_log(message.from_user.id, "setting_set", f"key={key}; value={value[:120]}")
    await message.answer("✅ Настройка сохранена.")


@router.message(Command("ref_stats"), IsAdmin())
async def ref_stats_cmd(message: types.Message):
    await message.answer(await build_ref_stats_text(), parse_mode="HTML")
