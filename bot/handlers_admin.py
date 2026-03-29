from datetime import timedelta
from cryptography.fernet import Fernet


from aiogram import F, Router, types
from aiogram import Bot
from aiogram.filters import BaseFilter, Command, CommandObject

from awg_backend import (
    clean_orphan_awg_peers, count_free_ip_slots, delete_user_everywhere,
    get_orphan_awg_peers, issue_subscription, list_orphan_delete_candidates_force, revoke_user_access,
)
from config import ADMIN_COMMAND_COOLDOWN_SECONDS, ADMIN_ID, BACKUP_ENCRYPTION_KEY, BACKUP_SECURE_MODE, logger
from database import (
    clear_pending_admin_action, clear_pending_broadcast, create_broadcast_job, db_health_info, fetchall, fetchone,
    get_metric, get_pending_jobs_stats, get_recovery_lag_seconds,
    get_pending_broadcast, get_recent_audit, get_user_meta, pop_pending_admin_action,
    set_pending_admin_action, set_pending_broadcast, write_audit_log,
)
from helpers import escape_html, format_tg_username, get_status_text, utc_now_naive
from keyboards import get_admin_confirm_kb, get_admin_force_confirm_kb, get_admin_inline_kb, get_broadcast_confirm_kb
from ui_constants import (
    BTN_ADMIN, CB_ADMIN_BROADCAST, CB_ADMIN_CLEAN_ORPHANS, CB_ADMIN_LIST, CB_ADMIN_STATS, CB_ADMIN_SYNC,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM,
)

router = Router()
admin_command_rate_limit: dict[str, object] = {}
ADMIN_USERS_PAGE_SIZE = 10


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
    stats = await get_pending_jobs_stats()
    lag = await get_recovery_lag_seconds()
    helper_failures = await get_metric("awg_helper_failures")
    await message.answer(
        (
            "🩺 <b>Отчёт о состоянии</b>\n\n"
            f"jobs.received=<b>{stats['received']}</b>\n"
            f"jobs.provisioning=<b>{stats['provisioning']}</b>\n"
            f"jobs.needs_repair=<b>{stats['needs_repair']}</b>\n"
            f"jobs.stuck_manual=<b>{stats['stuck_manual']}</b>\n"
            f"recovery_lag_sec=<b>{lag}</b>\n"
            f"awg_helper_failures=<b>{helper_failures}</b>"
        ),
        parse_mode="HTML",
    )
