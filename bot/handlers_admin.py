from datetime import timedelta


from aiogram import F, Router, types
from aiogram import Bot
from aiogram.filters import BaseFilter, Command, CommandObject

from awg_backend import (
    clean_orphan_awg_peers, count_free_ip_slots, delete_user_everywhere,
    get_orphan_awg_peers, issue_subscription, revoke_user_access,
)
from config import ADMIN_COMMAND_COOLDOWN_SECONDS, ADMIN_ID, logger
from database import (
    clear_pending_admin_action, clear_pending_broadcast, db_health_info, fetchall, fetchone,
    get_pending_broadcast, get_recent_audit, get_user_meta, pop_pending_admin_action,
    set_pending_admin_action, set_pending_broadcast, write_audit_log,
)
from helpers import escape_html, format_tg_username, get_status_text, utc_now_naive
import asyncio
from keyboards import get_admin_confirm_kb, get_admin_inline_kb, get_back_to_admin_kb, get_broadcast_confirm_kb
from ui_constants import (
    BTN_ADMIN, CB_ADMIN_BROADCAST, CB_ADMIN_CLEAN_ORPHANS, CB_ADMIN_LIST, CB_ADMIN_STATS, CB_ADMIN_SYNC,
    CB_BACK_TO_ADMIN, CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM,
)

router = Router()
admin_command_rate_limit: dict[str, object] = {}


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


async def _send_admin_panel(target) -> None:
    stats_text = await build_stats_text()
    db_info = await db_health_info()
    db_status = "🟢 Нормально" if db_info["is_healthy"] else "🟡 Нужна проверка"
    await target.answer(
        stats_text + f"\n🗄 Статус БД: <b>{db_status}</b>",
        parse_mode="HTML",
        reply_markup=get_admin_inline_kb(),
    )


@router.message(F.text == BTN_ADMIN, IsAdmin())
async def admin_panel(message: types.Message):
    await _send_admin_panel(message)


@router.callback_query(F.data == CB_BACK_TO_ADMIN)
async def back_to_admin(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await _send_admin_panel(cb.message)


@router.callback_query(F.data == CB_ADMIN_STATS)
async def admin_stats_cb(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.message.answer(await build_stats_text(), parse_mode="HTML", reply_markup=get_back_to_admin_kb())
    await cb.answer("Готово")


@router.callback_query(F.data == CB_ADMIN_SYNC)
async def admin_sync_awg(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        db_info = await db_health_info()
        orphans = await get_orphan_awg_peers()
        details = []
        for peer in orphans[:20]:
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
        await cb.message.answer(text, parse_mode="HTML", reply_markup=get_back_to_admin_kb())
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
    await set_pending_admin_action(ADMIN_ID, "clean_orphans", {"action": "clean_orphans"})
    await cb.message.answer(
        (
            "⚠️ <b>Подтвердите очистку orphan peer</b>\n\n"
            f"Будет удалено peer: <b>{len(orphans)}</b>"
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
        await write_audit_log(ADMIN_ID, "clean_orphans", f"removed={removed}")
        await cb.message.answer(
            f"🧹 <b>Очистка orphan peer завершена</b>\n\nУдалено peer: <b>{removed}</b>",
            parse_mode="HTML",
            reply_markup=get_back_to_admin_kb(),
        )
        await cb.answer("Очистка завершена")
    except Exception as e:
        logger.exception("Ошибка confirm_clean_orphans: %s", e)
        await cb.answer(str(e), show_alert=True)


@router.callback_query(F.data == "cancel_clean_orphans")
async def cancel_clean_orphans(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans")
    await cb.message.answer("❌ Очистка orphan peer отменена", reply_markup=get_back_to_admin_kb())
    await cb.answer("Отменено")


@router.callback_query(F.data == CB_ADMIN_LIST)
async def admin_list_all(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    users = await fetchall("SELECT user_id, sub_until FROM users ORDER BY created_at DESC LIMIT 30")
    if not users:
        await cb.message.answer("Список пользователей пуст.", reply_markup=get_back_to_admin_kb())
        await cb.answer()
        return
    for uid, sub_until in users:
        status_text, until_text = get_status_text(sub_until)
        tg_username, first_name = await get_user_meta(uid)
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[
                types.InlineKeyboardButton(text="+30 дней", callback_data=f"add_30_{uid}"),
                types.InlineKeyboardButton(text="Отключить", callback_data=f"revoke_{uid}"),
                types.InlineKeyboardButton(text="Удалить", callback_data=f"del_{uid}"),
            ]]
        )
        await cb.message.answer(
            (
                f"👤 <b>Пользователь</b>\n"
                f"🆔 <code>{uid}</code>\n"
                f"👤 Имя: {escape_html(first_name)}\n"
                f"✈️ Telegram: {format_tg_username(tg_username)}\n"
                f"📌 {status_text}\n"
                f"📅 До: <b>{until_text}</b>"
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
    await cb.message.answer("Вернуться в админ-панель:", reply_markup=get_back_to_admin_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("add_30_"))
async def admin_add_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        uid = int(cb.data.split("_")[2])
        if admin_command_limited("admin_add_30", cb.from_user.id):
            await cb.answer("Слишком часто", show_alert=True)
            return
        new_until = await issue_subscription(uid, 30)
        notified = await notify_user_subscription_granted(cb.bot, uid, 30, new_until)
        await write_audit_log(ADMIN_ID, "admin_add_30", f"target={uid}; until={new_until.isoformat()}; notified={int(notified)}")
        await cb.answer(f"✅ +30 дней пользователю {uid}")
        await cb.message.answer(
            (
                f"✅ <b>Пользователю выдано +30 дней</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"📅 До: <b>{new_until.strftime('%d.%m.%Y %H:%M')}</b>"
            ),
            parse_mode="HTML",
            reply_markup=get_back_to_admin_kb(),
        )
        if not notified:
            await cb.message.answer("⚠️ Доступ выдан, но уведомление пользователю отправить не удалось.")
    except Exception as e:
        logger.exception("Ошибка add_30: %s", e)
        await cb.answer("❌ Не удалось продлить доступ", show_alert=True)


@router.callback_query(F.data.startswith("revoke_"))
async def admin_revoke_btn(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    uid = int(cb.data.split("_")[1])
    await set_pending_admin_action(ADMIN_ID, "revoke", {"action": "revoke", "target": uid})
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
            reply_markup=get_back_to_admin_kb(),
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_revoke: %s", e)
        await cb.answer("❌ Не удалось отключить пользователя", show_alert=True)


@router.callback_query(F.data == "cancel_revoke")
async def cancel_revoke(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "revoke")
    await cb.message.answer("❌ Отключение отменено", reply_markup=get_back_to_admin_kb())
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith("del_"))
async def admin_del_user(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    uid = int(cb.data.split("_")[1])
    await set_pending_admin_action(ADMIN_ID, "delete_user", {"action": "delete_user", "target": uid})
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
            reply_markup=get_back_to_admin_kb(),
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_delete_user: %s", e)
        await cb.answer("❌ Не удалось удалить пользователя", show_alert=True)


@router.callback_query(F.data == "cancel_delete_user")
async def cancel_delete_user(cb: types.CallbackQuery):
    await clear_pending_admin_action(ADMIN_ID, "delete_user")
    await cb.message.answer("❌ Удаление отменено", reply_markup=get_back_to_admin_kb())
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
        reply_markup=get_back_to_admin_kb(),
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
    await cb.message.answer(
        (
            "📢 <b>Рассылка завершена</b>\n\n"
            f"✅ Доставлено: <b>{delivered}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>"
        ),
        parse_mode="HTML",
        reply_markup=get_back_to_admin_kb(),
    )
    await cb.answer("Отправлено")


@router.callback_query(F.data == CB_BROADCAST_CANCEL)
async def broadcast_cancel(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await clear_pending_broadcast(ADMIN_ID)
    await write_audit_log(ADMIN_ID, "broadcast_cancel", "")
    await cb.message.answer("❌ Рассылка отменена", reply_markup=get_back_to_admin_kb())
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
