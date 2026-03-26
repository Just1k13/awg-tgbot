from datetime import timedelta
import asyncio
import math

from aiogram import Bot, F, Router, types
from aiogram.filters import BaseFilter, Command, CommandObject

from awg_backend import (
    clean_orphan_awg_peers,
    count_free_ip_slots,
    delete_user_everywhere,
    get_orphan_awg_peers,
    issue_subscription,
    revoke_user_access,
)
from config import ADMIN_COMMAND_COOLDOWN_SECONDS, ADMIN_ID, logger, get_support_username
from database import (
    clear_pending_admin_action,
    clear_pending_broadcast,
    db_health_info,
    execute,
    fetchall,
    fetchone,
    get_pending_broadcast,
    get_recent_audit,
    get_user_meta,
    pop_pending_admin_action,
    set_pending_admin_action,
    set_pending_broadcast,
    write_audit_log,
)
from helpers import escape_html, format_tg_username, get_status_text, utc_now_naive
from keyboards import (
    get_admin_confirm_kb,
    get_admin_inline_kb,
    get_admin_user_access_kb,
    get_admin_user_actions_kb,
    get_admin_user_sections_kb,
    get_admin_user_subs_kb,
    get_admin_users_hub_kb,
    get_admin_users_page_kb,
    get_back_to_admin_kb,
    get_back_to_users_page_kb,
    get_broadcast_confirm_kb,
)
from ui_constants import (
    BTN_ADMIN,
    BTN_BUY,
    BTN_CONFIGS,
    BTN_GUIDE,
    BTN_PROFILE,
    BTN_SUPPORT,
    CB_ADMIN_ADD_CUSTOM_PREFIX,
    CB_ADMIN_BROADCAST,
    CB_ADMIN_CLEAN_ORPHANS,
    CB_ADMIN_LIST,
    CB_ADMIN_STATS,
    CB_ADMIN_SYNC,
    CB_ADMIN_USER_ACCESS_PREFIX,
    CB_ADMIN_USER_ACTIONS_PREFIX,
    CB_ADMIN_USER_PREFIX,
    CB_ADMIN_USER_SUBS_PREFIX,
    CB_ADMIN_USERS_ACTIVE,
    CB_ADMIN_USERS_HUB,
    CB_ADMIN_USERS_INACTIVE,
    CB_ADMIN_USERS_NEW24,
    CB_ADMIN_USERS_PAGE_PREFIX,
    CB_ADMIN_USERS_SEARCH,
    CB_BACK_TO_ADMIN,
    CB_BACK_TO_PROFILE,
    CB_BROADCAST_CANCEL,
    CB_BROADCAST_CONFIRM,
)

router = Router()
admin_command_rate_limit: dict[str, object] = {}
ADMIN_USERS_PER_PAGE = 8
LIST_ALL = "all"
LIST_ACTIVE = "active"
LIST_INACTIVE = "inactive"
LIST_NEW24 = "new24"
LIST_SEARCH = "search"


class IsAdmin(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return bool(message.from_user and message.from_user.id == ADMIN_ID)


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


async def _edit_or_answer(message: types.Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)


def _is_active_sub(sub_until: str | None) -> bool:
    if not sub_until or sub_until == "0":
        return False
    return sub_until > utc_now_naive().isoformat()


def _user_button_label(uid: int, sub_until: str | None, tg_username: str | None, first_name: str | None) -> str:
    icon = "🟢" if _is_active_sub(sub_until) else "⚪️"
    label = f"@{tg_username}" if tg_username else (first_name or str(uid))
    label = label.replace("\n", " ").strip()
    if len(label) > 18:
        label = label[:18] + "…"
    return f"{icon} {label} · {uid}"


def _ctx(uid: int, page: int, list_key: str) -> str:
    return f"{uid}:{page}:{list_key}"


def _parse_ctx(payload: str) -> tuple[int, int, str]:
    uid_raw, page_raw, list_key = payload.split(":", 2)
    return int(uid_raw), int(page_raw), list_key


async def _reset_admin_pending_inputs() -> None:
    await clear_pending_admin_action(ADMIN_ID, "search_user")
    await clear_pending_admin_action(ADMIN_ID, "manual_days")


async def notify_user_subscription_granted(bot: Bot, user_id: int, days: int, new_until) -> bool:
    try:
        await bot.send_message(
            user_id,
            (
                "🎁 <b>Вам выдан доступ</b>\n\n"
                f"⏳ <b>Срок:</b> +{days} дн.\n"
                f"📅 <b>Действует до:</b> {new_until.strftime('%d.%m.%Y %H:%M')}\n\n"
                "🔑 Конфиги доступны в разделе <b>Конфиги</b>."
            ),
            parse_mode="HTML",
        )
        return True
    except Exception as notify_error:
        logger.warning("Не удалось уведомить пользователя %s о выдаче доступа: %s", user_id, notify_error)
        return False


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
        f"👻 Orphan peer: <b>{len(orphans)}</b>"
    )


async def _send_admin_panel(target, *, edit: bool = False) -> None:
    await _reset_admin_pending_inputs()
    stats_text = await build_stats_text()
    db_info = await db_health_info()
    db_status = "🟢 Нормально" if db_info["is_healthy"] else "🟡 Нужна проверка"
    text = (
        "⚙️ <b>Админка</b>\n\n"
        f"{stats_text}\n"
        f"🗄 Статус БД: <b>{db_status}</b>\n\n"
        "Выберите раздел:"
    )
    if edit:
        await _edit_or_answer(target, text, get_admin_inline_kb())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=get_admin_inline_kb())


async def _show_users_hub(message: types.Message) -> None:
    await _reset_admin_pending_inputs()
    text = (
        "👥 <b>Пользователи</b>\n\n"
        "Здесь можно искать пользователя, открывать список и управлять доступом.\n\n"
        "Выберите режим:"
    )
    await _edit_or_answer(message, text, get_admin_users_hub_kb())


def _users_filter_sql(list_key: str) -> tuple[str, tuple[object, ...]]:
    now_iso = utc_now_naive().isoformat()
    if list_key == LIST_ACTIVE:
        return "WHERE sub_until > ?", (now_iso,)
    if list_key == LIST_INACTIVE:
        return "WHERE sub_until = '0' OR sub_until <= ?", (now_iso,)
    if list_key == LIST_NEW24:
        return "WHERE created_at >= ?", ((utc_now_naive() - timedelta(days=1)).isoformat(),)
    return "", ()


async def _get_users_page(page: int, list_key: str) -> tuple[list[tuple[int, str, str, str]], int, int]:
    where, params = _users_filter_sql(list_key)
    total_users = (await fetchone(f"SELECT COUNT(*) FROM users {where}", params))[0]
    total_pages = max(1, math.ceil(total_users / ADMIN_USERS_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    rows = await fetchall(
        f"SELECT user_id, sub_until FROM users {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + (ADMIN_USERS_PER_PAGE, page * ADMIN_USERS_PER_PAGE),
    )
    users: list[tuple[int, str, str, str]] = []
    for uid, sub_until in rows:
        tg_username, first_name = await get_user_meta(uid)
        users.append((uid, sub_until, tg_username or "", first_name or ""))
    return users, page, total_pages


async def _show_users_page(message: types.Message, page: int, list_key: str = LIST_ALL) -> None:
    await _reset_admin_pending_inputs()
    users, page, total_pages = await _get_users_page(page, list_key)
    if not users:
        await _edit_or_answer(message, "👥 <b>Пользователей по этому фильтру нет.</b>", get_admin_users_hub_kb())
        return
    title_map = {
        LIST_ALL: "Список пользователей",
        LIST_ACTIVE: "Активные пользователи",
        LIST_INACTIVE: "Без подписки",
        LIST_NEW24: "Новые за 24 часа",
    }
    keyboard_users = [
        (uid, _user_button_label(uid, sub_until, tg_username, first_name))
        for uid, sub_until, tg_username, first_name in users
    ]
    text = (
        f"👥 <b>{title_map.get(list_key, 'Пользователи')}</b>\n\n"
        f"Страница: <b>{page + 1}/{total_pages}</b>\n"
        f"Пользователей на странице: <b>{len(users)}</b>\n\n"
        "Выберите пользователя:"
    )
    await _edit_or_answer(message, text, get_admin_users_page_kb(keyboard_users, page, total_pages, list_key))


async def _show_search_prompt(message: types.Message) -> None:
    await clear_pending_admin_action(ADMIN_ID, "manual_days")
    await set_pending_admin_action(ADMIN_ID, "search_user", {"action": "search_user"})
    text = (
        "🔎 <b>Поиск пользователя</b>\n\n"
        "Отправьте:\n"
        "• user_id\n"
        "• @username\n"
        "• имя или часть имени\n\n"
        "Пример: <code>872658825</code> или <code>@just1k13</code>"
    )
    await _edit_or_answer(message, text, get_admin_users_hub_kb())


async def _show_user_card(message: types.Message, uid: int, page: int, list_key: str) -> None:
    await _reset_admin_pending_inputs()
    row = await fetchone("SELECT sub_until FROM users WHERE user_id = ?", (uid,))
    if not row:
        await _edit_or_answer(message, "Пользователь не найден.", get_back_to_users_page_kb(page, list_key))
        return
    sub_until = row[0]
    status_text, until_text = get_status_text(sub_until)
    tg_username, first_name = await get_user_meta(uid)
    text = (
        "👤 <b>Пользователь</b>\n\n"
        f"🆔 <code>{uid}</code>\n"
        f"👤 Имя: {escape_html(first_name or '—')}\n"
        f"✈️ Telegram: {format_tg_username(tg_username)}\n"
        f"📌 {status_text}\n"
        f"📅 До: <b>{until_text}</b>\n\n"
        "Выберите раздел управления:"
    )
    await _edit_or_answer(message, text, get_admin_user_sections_kb(uid, page, list_key))


async def _show_user_subs(message: types.Message, uid: int, page: int, list_key: str) -> None:
    await clear_pending_admin_action(ADMIN_ID, "search_user")
    row = await fetchone("SELECT sub_until FROM users WHERE user_id = ?", (uid,))
    sub_until = row[0] if row else "0"
    status_text, until_text = get_status_text(sub_until)
    text = (
        "⏳ <b>Подписка пользователя</b>\n\n"
        f"🆔 <code>{uid}</code>\n"
        f"📌 {status_text}\n"
        f"📅 До: <b>{until_text}</b>\n\n"
        "Быстрые действия:"
    )
    await _edit_or_answer(message, text, get_admin_user_subs_kb(uid, page, list_key))


async def _show_user_access(message: types.Message, uid: int, page: int, list_key: str) -> None:
    await _reset_admin_pending_inputs()
    row = await fetchone("SELECT COUNT(*) FROM keys WHERE user_id = ?", (uid,))
    total_devices = int(row[0]) if row else 0
    text = (
        "🔑 <b>Доступ / ключи</b>\n\n"
        f"🆔 <code>{uid}</code>\n"
        f"📱 Устройств в БД: <b>{total_devices}</b>\n\n"
        "Здесь можно отключить доступ пользователя."
    )
    await _edit_or_answer(message, text, get_admin_user_access_kb(uid, page, list_key))


async def _show_user_actions(message: types.Message, uid: int, page: int, list_key: str) -> None:
    await _reset_admin_pending_inputs()
    text = (
        "🛠 <b>Админ-действия</b>\n\n"
        f"🆔 <code>{uid}</code>\n\n"
        "Опасные действия вынесены отдельно."
    )
    await _edit_or_answer(message, text, get_admin_user_actions_kb(uid, page, list_key))


async def _search_users(query: str) -> list[tuple[int, str]]:
    query = query.strip()
    if not query:
        return []
    normalized = query[1:] if query.startswith("@") else query
    rows = []
    if normalized.isdigit():
        rows = await fetchall(
            "SELECT user_id, sub_until FROM users WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
            (int(normalized),),
        )
    if not rows:
        like = f"%{normalized.lower()}%"
        rows = await fetchall(
            """
            SELECT user_id, sub_until
            FROM users
            WHERE LOWER(COALESCE(tg_username, '')) LIKE ?
               OR LOWER(COALESCE(first_name, '')) LIKE ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (like, like),
        )
    result: list[tuple[int, str]] = []
    for uid, sub_until in rows:
        tg_username, first_name = await get_user_meta(uid)
        result.append((uid, _user_button_label(uid, sub_until, tg_username, first_name)))
    return result


@router.message(F.text == BTN_ADMIN, IsAdmin())
async def admin_panel(message: types.Message):
    await _send_admin_panel(message)


@router.callback_query(F.data == "noop")
async def noop_admin(cb: types.CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == CB_BACK_TO_ADMIN)
async def back_to_admin(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _send_admin_panel(cb.message, edit=True)


@router.callback_query(F.data == CB_ADMIN_USERS_HUB)
async def admin_users_hub(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _show_users_hub(cb.message)


@router.callback_query(F.data == CB_ADMIN_USERS_SEARCH)
async def admin_users_search(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _show_search_prompt(cb.message)


@router.callback_query(F.data == CB_ADMIN_LIST)
async def admin_list_all(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _show_users_page(cb.message, 0, LIST_ALL)


@router.callback_query(F.data == CB_ADMIN_USERS_ACTIVE)
async def admin_list_active(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _show_users_page(cb.message, 0, LIST_ACTIVE)


@router.callback_query(F.data == CB_ADMIN_USERS_INACTIVE)
async def admin_list_inactive(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _show_users_page(cb.message, 0, LIST_INACTIVE)


@router.callback_query(F.data == CB_ADMIN_USERS_NEW24)
async def admin_list_new24(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _show_users_page(cb.message, 0, LIST_NEW24)


@router.callback_query(F.data.startswith(CB_ADMIN_USERS_PAGE_PREFIX))
async def admin_users_page(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        payload = cb.data.removeprefix(CB_ADMIN_USERS_PAGE_PREFIX)
        list_key, page_raw = payload.split(":", 1)
        page = int(page_raw)
    except Exception:
        await cb.answer("Некорректная страница", show_alert=True)
        return
    await cb.answer()
    await _show_users_page(cb.message, page, list_key)


@router.callback_query(F.data.startswith(CB_ADMIN_USER_PREFIX))
async def admin_user_card(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        uid, page, list_key = _parse_ctx(cb.data.removeprefix(CB_ADMIN_USER_PREFIX))
    except Exception:
        await cb.answer("Некорректный пользователь", show_alert=True)
        return
    await cb.answer()
    await _show_user_card(cb.message, uid, page, list_key)


@router.callback_query(F.data.startswith(CB_ADMIN_USER_SUBS_PREFIX))
async def admin_user_subs(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    uid, page, list_key = _parse_ctx(cb.data.removeprefix(CB_ADMIN_USER_SUBS_PREFIX))
    await cb.answer()
    await _show_user_subs(cb.message, uid, page, list_key)


@router.callback_query(F.data.startswith(CB_ADMIN_USER_ACCESS_PREFIX))
async def admin_user_access(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    uid, page, list_key = _parse_ctx(cb.data.removeprefix(CB_ADMIN_USER_ACCESS_PREFIX))
    await cb.answer()
    await _show_user_access(cb.message, uid, page, list_key)


@router.callback_query(F.data.startswith(CB_ADMIN_USER_ACTIONS_PREFIX))
async def admin_user_actions(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    uid, page, list_key = _parse_ctx(cb.data.removeprefix(CB_ADMIN_USER_ACTIONS_PREFIX))
    await cb.answer()
    await _show_user_actions(cb.message, uid, page, list_key)


@router.callback_query(F.data.startswith(CB_ADMIN_ADD_CUSTOM_PREFIX))
async def admin_add_custom_prompt(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    uid, page, list_key = _parse_ctx(cb.data.removeprefix(CB_ADMIN_ADD_CUSTOM_PREFIX))
    await clear_pending_admin_action(ADMIN_ID, "search_user")
    await set_pending_admin_action(
        ADMIN_ID,
        "manual_days",
        {"action": "manual_days", "target": uid, "page": page, "list_key": list_key},
    )
    await cb.answer()
    await _edit_or_answer(
        cb.message,
        (
            "✍️ <b>Введите количество дней</b>\n\n"
            f"Пользователь: <code>{uid}</code>\n\n"
            "Например: <code>1</code>, <code>7</code>, <code>45</code>"
        ),
        get_admin_user_subs_kb(uid, page, list_key),
    )


@router.callback_query(F.data == CB_ADMIN_STATS)
async def admin_stats_cb(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer("Готово")
    await _edit_or_answer(cb.message, await build_stats_text(), get_back_to_admin_kb())


@router.callback_query(F.data == CB_ADMIN_SYNC)
async def admin_sync_awg(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        db_info = await db_health_info()
        orphans = await get_orphan_awg_peers()
        details = []
        for peer in orphans[:15]:
            details.append(f"• <code>{peer['public_key']}</code> — {peer.get('ip') or 'no ip'}")
        extra = "\n".join(details) if details else "Нет orphan peer."
        text = (
            "🔄 <b>Проверка синхронизации AWG ↔ БД</b>\n\n"
            f"🗄 БД существует: <b>{'да' if db_info['exists'] else 'нет'}</b>\n"
            f"📋 Таблица keys: <b>{'да' if db_info['keys_table_exists'] else 'нет'}</b>\n"
            f"🧱 Нужные колонки: <b>{'да' if db_info['has_required_columns'] else 'нет'}</b>\n"
            f"✅ Валидных ключей в БД: <b>{db_info['valid_keys_count']}</b>\n"
            f"👻 Orphan peer в AWG: <b>{len(orphans)}</b>\n\n"
            f"{extra}"
        )
        await cb.answer("Синхронизация проверена")
        await _edit_or_answer(cb.message, text, get_back_to_admin_kb())
    except Exception as e:
        logger.exception("Ошибка admin_sync_awg: %s", e)
        await cb.answer("❌ Ошибка проверки", show_alert=True)


@router.callback_query(F.data == CB_ADMIN_CLEAN_ORPHANS)
async def admin_clean_orphans(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    orphans = await get_orphan_awg_peers()
    await clear_pending_admin_action(ADMIN_ID, "search_user")
    await clear_pending_admin_action(ADMIN_ID, "manual_days")
    await set_pending_admin_action(ADMIN_ID, "clean_orphans", {"action": "clean_orphans"})
    await cb.answer()
    await _edit_or_answer(
        cb.message,
        (
            "⚠️ <b>Подтвердите очистку orphan peer</b>\n\n"
            f"Будет удалено peer: <b>{len(orphans)}</b>"
        ),
        get_admin_confirm_kb("clean_orphans"),
    )


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
        await write_audit_log(ADMIN_ID, "clean_orphans", f"removed={removed}")
        await cb.answer("Очистка завершена")
        await _edit_or_answer(
            cb.message,
            f"🧹 <b>Очистка orphan peer завершена</b>\n\nУдалено peer: <b>{removed}</b>",
            get_back_to_admin_kb(),
        )
    except Exception as e:
        logger.exception("Ошибка confirm_clean_orphans: %s", e)
        await cb.answer(str(e), show_alert=True)


@router.callback_query(F.data == "cancel_clean_orphans")
async def cancel_clean_orphans(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans")
    await cb.answer("Отменено")
    await _send_admin_panel(cb.message, edit=True)


@router.callback_query(F.data.startswith("add_"))
async def admin_add_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        _prefix, days_raw, uid_raw, page_raw, list_key = cb.data.split("_", 4)
        days = int(days_raw)
        uid = int(uid_raw)
        page = int(page_raw)
    except Exception:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    try:
        if days <= 0:
            await cb.answer("Некорректное количество дней", show_alert=True)
            return
        if admin_command_limited(f"admin_add_{days}", cb.from_user.id):
            await cb.answer("Слишком часто", show_alert=True)
            return
        new_until = await issue_subscription(uid, days)
        notified = await notify_user_subscription_granted(cb.bot, uid, days, new_until)
        await write_audit_log(
            ADMIN_ID,
            f"admin_add_{days}",
            f"target={uid}; until={new_until.isoformat()}; notified={int(notified)}",
        )
        await cb.answer(f"✅ +{days} дней")
        await _edit_or_answer(
            cb.message,
            (
                f"✅ <b>Пользователю выдано +{days} дней</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"📅 До: <b>{new_until.strftime('%d.%m.%Y %H:%M')}</b>"
            ),
            get_back_to_users_page_kb(page, list_key),
        )
        if not notified:
            await cb.message.answer("⚠️ Доступ выдан, но уведомление пользователю отправить не удалось.")
    except Exception as e:
        logger.exception("Ошибка add_days: %s", e)
        await cb.answer("❌ Не удалось продлить доступ", show_alert=True)


@router.callback_query(F.data.startswith("revoke_"))
async def admin_revoke_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        _prefix, uid_raw, page_raw, list_key = cb.data.split("_", 3)
        uid = int(uid_raw)
        page = int(page_raw)
    except Exception:
        await cb.answer("Некорректный пользователь", show_alert=True)
        return
    await clear_pending_admin_action(ADMIN_ID, "search_user")
    await clear_pending_admin_action(ADMIN_ID, "manual_days")
    await set_pending_admin_action(
        ADMIN_ID,
        "revoke",
        {"action": "revoke", "target": uid, "page": page, "list_key": list_key},
    )
    await cb.answer()
    await _edit_or_answer(
        cb.message,
        (
            "⚠️ <b>Подтвердите отключение доступа</b>\n\n"
            f"Пользователь: <code>{uid}</code>"
        ),
        get_admin_confirm_kb("revoke"),
    )


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
    page = int(action.get("page", 0))
    list_key = str(action.get("list_key", LIST_ALL))
    try:
        removed = await revoke_user_access(uid)
        await execute("UPDATE users SET sub_until = '0' WHERE user_id = ?", (uid,))
        await write_audit_log(ADMIN_ID, "admin_revoke", f"target={uid}; removed={removed}")
        await cb.answer("Готово")
        await _edit_or_answer(
            cb.message,
            (
                f"⛔ <b>Доступ отключён</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"🔌 Удалено peer: <b>{removed}</b>"
            ),
            get_back_to_users_page_kb(page, list_key),
        )
    except Exception as e:
        logger.exception("Ошибка confirm_revoke: %s", e)
        await cb.answer("❌ Не удалось отключить пользователя", show_alert=True)


@router.callback_query(F.data == "cancel_revoke")
async def cancel_revoke(cb: types.CallbackQuery):
    action = await pop_pending_admin_action(ADMIN_ID, "revoke")
    await cb.answer("Отменено")
    if action and action.get("target") is not None:
        await _show_user_access(
            cb.message,
            int(action["target"]),
            int(action.get("page", 0)),
            str(action.get("list_key", LIST_ALL)),
        )
        return
    await _send_admin_panel(cb.message, edit=True)


@router.callback_query(F.data.startswith("del_"))
async def admin_del_user(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        _prefix, uid_raw, page_raw, list_key = cb.data.split("_", 3)
        uid = int(uid_raw)
        page = int(page_raw)
    except Exception:
        await cb.answer("Некорректный пользователь", show_alert=True)
        return
    await clear_pending_admin_action(ADMIN_ID, "search_user")
    await clear_pending_admin_action(ADMIN_ID, "manual_days")
    await set_pending_admin_action(
        ADMIN_ID,
        "delete_user",
        {"action": "delete_user", "target": uid, "page": page, "list_key": list_key},
    )
    await cb.answer()
    await _edit_or_answer(
        cb.message,
        (
            "⚠️ <b>Подтвердите полное удаление пользователя</b>\n\n"
            f"Пользователь: <code>{uid}</code>"
        ),
        get_admin_confirm_kb("delete_user"),
    )


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
    page = int(action.get("page", 0))
    list_key = str(action.get("list_key", LIST_ALL))
    try:
        peers_count, _ = await delete_user_everywhere(uid)
        await write_audit_log(ADMIN_ID, "admin_delete_user", f"target={uid}; removed={peers_count}")
        await cb.answer("Готово")
        await _edit_or_answer(
            cb.message,
            (
                f"🗑 <b>Пользователь удалён</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"🔌 Удалено peer: <b>{peers_count}</b>"
            ),
            get_back_to_users_page_kb(page, list_key),
        )
    except Exception as e:
        logger.exception("Ошибка confirm_delete_user: %s", e)
        await cb.answer("❌ Не удалось удалить пользователя", show_alert=True)


@router.callback_query(F.data == "cancel_delete_user")
async def cancel_delete_user(cb: types.CallbackQuery):
    action = await pop_pending_admin_action(ADMIN_ID, "delete_user")
    await cb.answer("Отменено")
    if action and action.get("target") is not None:
        await _show_user_actions(
            cb.message,
            int(action["target"]),
            int(action.get("page", 0)),
            str(action.get("list_key", LIST_ALL)),
        )
        return
    await _send_admin_panel(cb.message, edit=True)


@router.callback_query(F.data == CB_ADMIN_BROADCAST)
async def admin_broadcast_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await _reset_admin_pending_inputs()
    await cb.answer()
    await _edit_or_answer(
        cb.message,
        (
            "📢 <b>Рассылка</b>\n\n"
            "Используйте команду:\n"
            "<code>/send Ваш текст</code>\n\n"
            "Перед отправкой будет подтверждение."
        ),
        get_back_to_admin_kb(),
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
    users = await fetchall("SELECT user_id FROM users")
    delivered = 0
    failed = 0
    for (uid,) in users:
        try:
            await cb.bot.send_message(uid, text, disable_web_page_preview=True)
            delivered += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            logger.warning("Не удалось отправить сообщение user_id=%s: %s", uid, e)
    await clear_pending_broadcast(ADMIN_ID)
    await write_audit_log(ADMIN_ID, "broadcast", f"delivered={delivered}; failed={failed}")
    await cb.answer("Отправлено")
    await _edit_or_answer(
        cb.message,
        (
            "📢 <b>Рассылка завершена</b>\n\n"
            f"✅ Доставлено: <b>{delivered}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>"
        ),
        get_back_to_admin_kb(),
    )


@router.callback_query(F.data == CB_BROADCAST_CANCEL)
async def broadcast_cancel(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await clear_pending_broadcast(ADMIN_ID)
    await write_audit_log(ADMIN_ID, "broadcast_cancel", "")
    await cb.answer("Отменено")
    await _send_admin_panel(cb.message, edit=True)


@router.message(Command("give"), IsAdmin())
async def give_manual(message: types.Message, command: CommandObject):
    if admin_command_limited("give", message.from_user.id):
        await message.answer("⏳ Слишком частый вызов /give")
        return
    if not command.args:
        await message.answer(
            "Формат: <code>/give ID [ДНИ]</code>\nНапример: <code>/give 123456789 45</code>",
            parse_mode="HTML",
        )
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
        await message.answer("Ошибка формата. Пример: <code>/give 123456789 45</code>", parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /give: %s", e)
        await message.answer("❌ Не удалось выдать доступ.")


@router.message(Command("users"), IsAdmin())
async def list_users_cmd(message: types.Message):
    await _show_users_page(message, 0, LIST_ALL)


@router.message(Command("stats"), IsAdmin())
async def stats_cmd(message: types.Message):
    await message.answer(await build_stats_text(), parse_mode="HTML")


@router.message(Command("orphans"), IsAdmin())
async def orphans_cmd(message: types.Message):
    try:
        orphans = await get_orphan_awg_peers()
        if not orphans:
            await message.answer("✅ Orphan peer не найдено.")
            return
        lines = [f"👻 <b>Orphan peer ({len(orphans)})</b>\n"]
        for peer in orphans[:50]:
            lines.append(f"• <code>{peer['public_key']}</code> — {peer.get('ip') or 'no ip'}")
        if len(orphans) > 50:
            lines.append(f"\n... и ещё {len(orphans) - 50}")
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /orphans: %s", e)
        await message.answer("❌ Не удалось получить orphan peer.")


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
        db_info = await db_health_info()
        orphans = await get_orphan_awg_peers()
        await message.answer(
            (
                "🔄 <b>Проверка синхронизации AWG ↔ БД</b>\n\n"
                f"🗄 БД существует: <b>{'да' if db_info['exists'] else 'нет'}</b>\n"
                f"📋 Таблица keys: <b>{'да' if db_info['keys_table_exists'] else 'нет'}</b>\n"
                f"🧱 Нужные колонки: <b>{'да' if db_info['has_required_columns'] else 'нет'}</b>\n"
                f"✅ Валидных ключей в БД: <b>{db_info['valid_keys_count']}</b>\n"
                f"👻 Orphan peer в AWG: <b>{len(orphans)}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception("Ошибка /sync_awg: %s", e)
        await message.answer("❌ Ошибка проверки синхронизации.")


@router.message(Command("clean_orphans"), IsAdmin())
async def clean_orphans_cmd(message: types.Message):
    try:
        orphans = await get_orphan_awg_peers()
        await set_pending_admin_action(ADMIN_ID, "clean_orphans", {"action": "clean_orphans"})
        await message.answer(
            f"⚠️ Подтвердите очистку orphan peer.\nБудет удалено: <b>{len(orphans)}</b>",
            parse_mode="HTML",
            reply_markup=get_admin_confirm_kb("clean_orphans"),
        )
    except Exception as e:
        logger.exception("Ошибка /clean_orphans: %s", e)
        await message.answer("❌ Не удалось подготовить очистку orphan peer.")


@router.message(Command("clean_orphans_force"), IsAdmin())
async def clean_orphans_force_cmd(message: types.Message):
    try:
        removed = await clean_orphan_awg_peers(force=True)
        await write_audit_log(ADMIN_ID, "clean_orphans_force", f"removed={removed}")
        await message.answer(f"🧨 Принудительно удалено orphan peer: <b>{removed}</b>", parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /clean_orphans_force: %s", e)
        await message.answer("❌ Не удалось выполнить принудительную очистку orphan peer.")


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
        await message.answer_document(
            types.BufferedInputFile(redacted.read_bytes(), filename=f"redacted_{db_file.name}"),
            caption=(f"📦 Резервная копия базы данных без секретов\n"
                     f"📅 {utc_now_naive().strftime('%d.%m.%Y %H:%M')}") ,
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


@router.message(IsAdmin())
async def handle_admin_pending_input(message: types.Message):
    if message.text and message.text.startswith("/"):
        return

    search_payload = await pop_pending_admin_action(ADMIN_ID, "search_user")
    if search_payload:
        results = await _search_users(message.text or "")
        if not results:
            await set_pending_admin_action(ADMIN_ID, "search_user", search_payload)
            await message.answer("Ничего не найдено. Попробуйте другой запрос.", reply_markup=get_admin_users_hub_kb())
            return
        if len(results) == 1:
            uid = results[0][0]
            await _show_user_card(message, uid, 0, LIST_ALL)
            return
        await message.answer(
            "Результаты поиска:",
            parse_mode="HTML",
            reply_markup=get_admin_users_page_kb(results, 0, 1, LIST_ALL),
        )
        return

    manual_days = await pop_pending_admin_action(ADMIN_ID, "manual_days")
    if manual_days:
        try:
            days = int((message.text or "").strip())
            if days <= 0:
                raise ValueError
        except ValueError:
            await set_pending_admin_action(ADMIN_ID, "manual_days", manual_days)
            await message.answer("Введите целое число больше 0.")
            return
        uid = int(manual_days["target"])
        page = int(manual_days.get("page", 0))
        list_key = str(manual_days.get("list_key", LIST_ALL))
        try:
            new_until = await issue_subscription(uid, days)
            notified = await notify_user_subscription_granted(message.bot, uid, days, new_until)
            await write_audit_log(
                ADMIN_ID,
                f"admin_add_{days}",
                f"target={uid}; until={new_until.isoformat()}; notified={int(notified)}",
            )
            await message.answer(
                (
                    f"✅ Пользователю <code>{uid}</code> выдано <b>+{days} дней</b>\n"
                    f"📅 До: <b>{new_until.strftime('%d.%m.%Y %H:%M')}</b>"
                ),
                parse_mode="HTML",
                reply_markup=get_back_to_users_page_kb(page, list_key),
            )
        except Exception as e:
            logger.exception("Ошибка manual_days: %s", e)
            await message.answer("❌ Не удалось выдать доступ вручную.")
        return

    if message.text == BTN_PROFILE:
        from handlers_user import _send_profile
        await _send_profile(message, message.from_user)
        return
    if message.text == BTN_CONFIGS:
        from handlers_user import _send_configs_menu
        await _send_configs_menu(message, message.from_user)
        return
    if message.text == BTN_BUY:
        from handlers_user import _send_buy_menu
        await _send_buy_menu(message, message.from_user.id)
        return
    if message.text == BTN_GUIDE:
        from handlers_user import _send_instruction
        await _send_instruction(message, CB_BACK_TO_PROFILE)
        return
    if message.text == BTN_SUPPORT:
        await message.answer(
            f"🆘 <b>Поддержка</b>\n\nПо всем вопросам пишите: <b>{escape_html(get_support_username())}</b>",
            parse_mode="HTML",
        )
        return
