from datetime import timedelta

import config

from aiogram import F, Router, types
from aiogram import Bot
from aiogram.filters import BaseFilter, Command, CommandObject

from awg_backend import (
    check_awg_container, count_free_ip_slots, delete_user_everywhere,
    delete_user_device, get_awg_peers, get_orphan_awg_peers, issue_subscription, reissue_user_device, revoke_user_access, run_docker,
    sync_qos_state,
)
from config import (
    ADMIN_COMMAND_COOLDOWN_SECONDS,
    ADMIN_ID,
    AWG_HELPER_POLICY_PATH,
    DOCKER_CONTAINER,
    WG_INTERFACE,
    logger,
    set_stars_price,
)
from database import (
    clear_pending_admin_action, clear_pending_broadcast, create_broadcast_job, create_promo_code, db_health_info, disable_promo_code, fetchall, fetchone, fetchval,
    get_latest_user_payment_summary,
    list_promo_codes,
    get_metric, get_pending_jobs_stats, get_recovery_lag_seconds,
    get_pending_admin_action, get_pending_broadcast, get_recent_audit, get_referral_admin_stats, get_referral_summary, get_user_keys, get_user_meta, normalize_promo_code, pop_pending_admin_action,
    set_app_setting, set_pending_admin_action, set_pending_broadcast, write_audit_log,
)
from helpers import escape_html, format_tg_username, get_status_text, utc_now_naive
from device_activity import render_device_activity_line
from keyboards import (
    get_admin_confirm_kb, get_admin_inline_kb, get_admin_price_confirm_kb, get_admin_prices_kb, get_admin_simple_back_kb, get_broadcast_cancel_kb, get_broadcast_confirm_kb,
)
from ui_constants import (
    BTN_ADMIN, CB_ADMIN_BACK_MAIN, CB_ADMIN_BROADCAST,
    CB_ADMIN_COMMANDS, CB_ADMIN_HEALTH, CB_ADMIN_LIST, CB_ADMIN_PRICE_CANCEL, CB_ADMIN_PRICE_EDIT_30, CB_ADMIN_PRICE_EDIT_7,
    CB_ADMIN_PRICE_EDIT_90, CB_ADMIN_PRICE_SAVE, CB_ADMIN_PRICES, CB_ADMIN_REFERRALS,
    CB_ADMIN_REFRESH_HEALTH, CB_ADMIN_REFRESH_REFERRALS, CB_ADMIN_STATS, CB_ADMIN_SYNC,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM,
    CB_ADMIN_USERS_PAGE_PREFIX, CB_ADMIN_MANAGE_USER_PREFIX, CB_ADMIN_ADD_DAYS_PREFIX,
    CB_ADMIN_RETRY_ACTIVATION_PREFIX,
    CB_ADMIN_DEVICE_DELETE_PREFIX, CB_ADMIN_DEVICE_REISSUE_PREFIX,
    CB_ADMIN_REVOKE_PREFIX, CB_ADMIN_DELETE_PREFIX, CB_CONFIRM_REVOKE, CB_CANCEL_REVOKE, CB_CONFIRM_DELETE_USER,
    CB_CANCEL_DELETE_USER, CB_CONFIRM_DEVICE_DELETE,
    CB_CANCEL_DEVICE_DELETE, CB_CONFIRM_DEVICE_REISSUE, CB_CANCEL_DEVICE_REISSUE,
)
from config_validate import read_helper_policy
from network_policy import denylist_sync, policy_metrics
from content_settings import get_setting
from payments import manual_retry_activation

router = Router()
admin_command_rate_limit: dict[str, object] = {}
ADMIN_USERS_PAGE_SIZE = 10
ADMIN_MANUAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/health", "быстрая проверка selfhost readiness"),
    ("/sync_awg", "сверка AWG и БД"),
    ("/stats", "краткая статистика"),
    ("/users", "короткий список пользователей"),
    ("/audit", "последние события"),
    ("/ref_stats", "сводка по рефералам"),
    ("/send TEXT", "рассылка (осторожно)"),
    ("/finduser QUERY", "поиск пользователя по id или username"),
    ("/payinfo USER_ID", "краткая сводка по последнему платежу"),
    ("/give USER_ID DAYS", "выдать/продлить доступ вручную"),
    ("/promo_create CODE DAYS [MAX]", "создать промокод"),
    ("/promo_list", "краткий список промокодов"),
    ("/promo_disable CODE", "отключить промокод"),
    ("/revoke USER_ID", "отключить доступ вручную (осторожно)"),
    ("/maintenance_status", "статус freeze новых покупок"),
    ("/maintenance_on", "включить freeze новых покупок"),
    ("/maintenance_off", "выключить freeze новых покупок"),
)
BROADCAST_INPUT_ACTION_KEY = "broadcast_input"
PRICE_INPUT_ACTION_KEY = "price_input"
PRICE_CONFIRM_ACTION_KEY = "price_confirm"
PRICE_TARGETS = {
    CB_ADMIN_PRICE_EDIT_7: ("STARS_PRICE_7_DAYS", "7 дней"),
    CB_ADMIN_PRICE_EDIT_30: ("STARS_PRICE_30_DAYS", "30 дней"),
    CB_ADMIN_PRICE_EDIT_90: ("STARS_PRICE_90_DAYS", "90 дней"),
}


def _render_admin_prices_text() -> str:
    return (
        "💸 <b>Цены</b>\n\n"
        f"7 дней — {config.STARS_PRICE_7_DAYS}⭐\n"
        f"30 дней — {config.STARS_PRICE_30_DAYS}⭐\n"
        f"90 дней — {config.STARS_PRICE_90_DAYS}⭐"
    )


def _parse_price_input(raw_text: str) -> int | None:
    value_text = raw_text.strip()
    if not value_text.isdigit():
        return None
    value = int(value_text)
    if value <= 0:
        return None
    return value


def _build_broadcast_preview(raw_text: str) -> str:
    preview = raw_text.strip()
    if len(preview) > 500:
        preview = f"{preview[:500]}…"
    return escape_html(preview)


async def _guard_admin_callback(cb: types.CallbackQuery, *, require_message: bool = False) -> bool:
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return False
    if require_message and not cb.message:
        await cb.answer("Сообщение недоступно", show_alert=True)
        return False
    return True


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


class HasPendingBroadcastInput(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        pending_action = await get_pending_admin_action(ADMIN_ID, BROADCAST_INPUT_ACTION_KEY)
        return bool(pending_action)


class HasPendingPriceInput(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        pending_action = await get_pending_admin_action(ADMIN_ID, PRICE_INPUT_ACTION_KEY)
        return bool(pending_action)


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
    total_users = await fetchval("SELECT COUNT(*) FROM users")
    total_with_sub = await fetchval("SELECT COUNT(*) FROM users WHERE sub_until != '0'")
    active_users = await fetchval("SELECT COUNT(*) FROM users WHERE sub_until > ?", (utc_now_naive().isoformat(),))
    total_keys = await fetchval("SELECT COUNT(*) FROM keys")
    new_24h = await fetchval(
        "SELECT COUNT(*) FROM users WHERE created_at >= ?",
        ((utc_now_naive() - timedelta(days=1)).isoformat(),),
    )
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


async def build_ref_stats_text() -> str:
    stats = await get_referral_admin_stats()
    recent = "\n".join([f"• invitee={r[0]} inviter={r[1]} pay={r[2]}" for r in stats["recent"]]) or "—"
    top = "\n".join([f"• inviter={row[0]} rewards={row[1]}" for row in stats["top"]]) or "—"
    total_bonus_days = int(stats["total_bonus_days"])
    return (
        "🎁 <b>Referral admin summary</b>\n\n"
        f"pending=<b>{stats['pending']}</b>\n"
        f"rewarded=<b>{stats['rewarded']}</b>\n"
        f"total_bonus_days=<b>{total_bonus_days}</b>\n\n"
        f"<b>Последние начисления</b>\n{recent}\n\n"
        f"<b>Top inviters</b>\n{top}"
    )




def _smoke_status_line(name: str, state: str, detail: str) -> str:
    icon = {"ok": "✅", "warning": "⚠️", "failed": "❌"}.get(state, "⚪")
    return f"{icon} {name}: {detail}"


def _hint_for_awg_target_error(error: str) -> str:
    lowered = error.lower()
    if "not configured" in lowered or "missing" in lowered:
        return "проверь .env target и перезапусти сервис"
    return "проверь контейнер/helper и сервис awg-bot"


def _hint_for_helper_policy_error(error: str) -> str:
    lowered = error.lower()
    if "parse failed" in lowered or "json object" in lowered:
        return "исправь формат helper policy (JSON) и перезапусти helper"
    return "проверь путь/доступ к helper policy"


async def run_runtime_smokecheck() -> dict[str, object]:
    checks: list[dict[str, str]] = []

    missing_env = []
    if not DOCKER_CONTAINER:
        missing_env.append("DOCKER_CONTAINER")
    if not WG_INTERFACE:
        missing_env.append("WG_INTERFACE")
    if not AWG_HELPER_POLICY_PATH:
        missing_env.append("AWG_HELPER_POLICY_PATH")
    if missing_env:
        checks.append(
            {
                "name": "Runtime config",
                "state": "failed",
                "detail": f"missing {', '.join(missing_env)}",
                "hint": "дополни .env selfhost и перезапусти сервис",
            }
        )
    else:
        checks.append({"name": "Runtime config", "state": "ok", "detail": "ok", "hint": ""})

    db_info = await db_health_info()
    if db_info.get("is_healthy"):
        checks.append({"name": "DB", "state": "ok", "detail": "ok", "hint": ""})
    else:
        checks.append(
            {
                "name": "DB",
                "state": "failed",
                "detail": "schema/db is not ready",
                "hint": "проверь БД вручную: init/migrations/права",
            }
        )

    try:
        await check_awg_container()
        checks.append({"name": "AWG target", "state": "ok", "detail": "reachable", "hint": ""})
    except Exception as e:
        checks.append(
            {
                "name": "AWG target",
                "state": "failed",
                "detail": f"failed ({str(e)[:120]})",
                "hint": _hint_for_awg_target_error(str(e)),
            }
        )

    if AWG_HELPER_POLICY_PATH and DOCKER_CONTAINER and WG_INTERFACE:
        policy_container, policy_interface, policy_error = read_helper_policy(Path(AWG_HELPER_POLICY_PATH))
        if policy_error:
            detail = policy_error
            if "parse failed:" in policy_error:
                detail = "helper policy parse failed (invalid JSON)"
            checks.append(
                {
                    "name": "Helper policy",
                    "state": "failed",
                    "detail": detail,
                    "hint": _hint_for_helper_policy_error(policy_error),
                }
            )
        elif policy_container != DOCKER_CONTAINER or policy_interface != WG_INTERFACE:
            checks.append(
                {
                    "name": "Helper policy",
                    "state": "warning",
                    "detail": f"mismatch env={DOCKER_CONTAINER}/{WG_INTERFACE} policy={policy_container}/{policy_interface}",
                    "hint": "синхронизируй helper policy с .env",
                }
            )
        else:
            checks.append({"name": "Helper policy", "state": "ok", "detail": "ok", "hint": ""})

    failed = [c for c in checks if c["state"] == "failed"]
    warnings = [c for c in checks if c["state"] == "warning"]
    if failed:
        overall = "failed"
    elif warnings:
        overall = "warning"
    else:
        overall = "ok"

    next_hint = "готово к работе"
    for item in checks:
        if item["state"] != "ok" and item.get("hint"):
            next_hint = item["hint"]
            break

    return {"overall": overall, "checks": checks, "hint": next_hint}


async def build_runtime_smokecheck_text() -> str:
    report = await run_runtime_smokecheck()
    overall = str(report["overall"])
    overall_label = {"ok": "READY", "warning": "DEGRADED", "failed": "FAILED"}.get(overall, "UNKNOWN")
    lines = [
        "🧪 <b>Selfhost smoke-check</b>",
        "",
        f"Overall: <b>{overall_label}</b>",
    ]
    for check in report["checks"]:
        lines.append(_smoke_status_line(str(check["name"]), str(check["state"]), str(check["detail"])))
    lines.append("")
    lines.append(f"➡️ Next step: <b>{report['hint']}</b>")
    return "\n".join(lines)


async def build_health_text() -> str:
    stats = await get_pending_jobs_stats()
    lag = await get_recovery_lag_seconds()
    helper_failures = await get_metric("awg_helper_failures")
    policy_stats = await policy_metrics()
    rate_drop_total = await get_metric("rate_limit_dropped_total")
    rate_drop_message = await get_metric("rate_limit_dropped_message")
    rate_drop_callback = await get_metric("rate_limit_dropped_callback")
    rate_buckets = await get_metric("rate_limit_active_buckets")
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
        f"denylist_entries=<b>{policy_stats['denylist_entries']}</b>\n"
        f"rate_limit_dropped_total=<b>{rate_drop_total}</b>\n"
        f"rate_limit_dropped_message=<b>{rate_drop_message}</b>\n"
        f"rate_limit_dropped_callback=<b>{rate_drop_callback}</b>\n"
        f"rate_limit_active_buckets=<b>{rate_buckets}</b>"
    )


def _users_page_kb(rows: list[tuple[int, str]], page: int, total_pages: int) -> types.InlineKeyboardMarkup:
    keyboard: list[list[types.InlineKeyboardButton]] = []
    for uid, label in rows:
        keyboard.append([
            types.InlineKeyboardButton(text=f"👤 {label}", callback_data=f"{CB_ADMIN_MANAGE_USER_PREFIX}{uid}_{page}"),
        ])

    nav_row: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{page - 1}"))
    nav_row.append(types.InlineKeyboardButton(text=f"📄 {page + 1}/{max(total_pages, 1)}", callback_data="noop"))
    if page + 1 < total_pages:
        nav_row.append(types.InlineKeyboardButton(text="➡️ Далее", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{page + 1}"))
    keyboard.append(nav_row)
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _user_manage_kb(
    uid: int,
    page: int,
    *,
    show_retry_activation: bool = False,
    device_nums: list[int] | None = None,
) -> types.InlineKeyboardMarkup:
    rows: list[list[types.InlineKeyboardButton]] = [
        [
            types.InlineKeyboardButton(text="+1 день", callback_data=f"{CB_ADMIN_ADD_DAYS_PREFIX}{uid}_1_{page}"),
            types.InlineKeyboardButton(text="+7 дней", callback_data=f"{CB_ADMIN_ADD_DAYS_PREFIX}{uid}_7_{page}"),
            types.InlineKeyboardButton(text="+30 дней", callback_data=f"{CB_ADMIN_ADD_DAYS_PREFIX}{uid}_30_{page}"),
        ],
        [
            types.InlineKeyboardButton(text="⛔ Отключить", callback_data=f"{CB_ADMIN_REVOKE_PREFIX}{uid}_{page}"),
            types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{CB_ADMIN_DELETE_PREFIX}{uid}_{page}"),
        ],
    ]
    if show_retry_activation:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🛠 Retry activation now",
                    callback_data=f"{CB_ADMIN_RETRY_ACTIVATION_PREFIX}{uid}_{page}",
                ),
            ]
        )
    if device_nums:
        for device_num in device_nums:
            rows.append(
                [
                    types.InlineKeyboardButton(
                        text=f"🗑 Устр. {device_num}",
                        callback_data=f"{CB_ADMIN_DEVICE_DELETE_PREFIX}{uid}_{device_num}_{page}",
                    ),
                    types.InlineKeyboardButton(
                        text=f"♻️ Перевыпуск {device_num}",
                        callback_data=f"{CB_ADMIN_DEVICE_REISSUE_PREFIX}{uid}_{device_num}_{page}",
                    ),
                ]
            )
    rows.extend([
        [types.InlineKeyboardButton(text="🔄 Обновить карточку", callback_data=f"{CB_ADMIN_MANAGE_USER_PREFIX}{uid}_{page}")],
        [types.InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{page}")],
    ])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _is_retry_activation_relevant(payment_summary: dict | None, has_keys: bool) -> bool:
    if not payment_summary or has_keys:
        return False
    payment_status = str(payment_summary.get("status") or "")
    activation_status = str(payment_summary.get("last_provision_status") or "")
    retryable_payment_statuses = {"received", "provisioning", "needs_repair", "failed", "stuck_manual"}
    retryable_activation_statuses = {"payment_received", "provisioning", "ready_config_pending", "needs_repair", "failed", "stuck_manual"}
    return payment_status in retryable_payment_statuses or activation_status in retryable_activation_statuses


def _operator_next_step(payment_status: str | None, activation_status: str | None, has_keys: bool) -> str:
    if has_keys:
        return "wait/close: доступ уже выдан"
    if payment_status in {"stuck_manual", "failed"} or activation_status in {"stuck_manual", "failed", "needs_repair"}:
        return "investigate: проверить audit + при необходимости выдать вручную"
    if payment_status in {"needs_repair", "provisioning", "received"} or activation_status in {"payment_received", "provisioning", "ready_config_pending"}:
        return "sync/wait: дождаться recovery, затем обновить карточку"
    if payment_status == "applied" and not has_keys:
        return "manual give: подписка активна, но ключа нет"
    return "wait/sync: обновить карточку после /sync_awg"


async def _build_admin_device_activity_lines(uid: int) -> list[str]:
    key_rows = await fetchall(
        """
        SELECT device_num, public_key
        FROM keys
        WHERE user_id = ?
          AND state = 'active'
          AND public_key NOT LIKE 'pending:%'
        ORDER BY device_num
        """,
        (uid,),
    )
    if not key_rows:
        return ["• нет активных устройств"]

    runtime_available = True
    peer_by_public_key: dict[str, dict] = {}
    try:
        runtime_peers = await get_awg_peers()
        peer_by_public_key = {
            str(peer.get("public_key") or "").strip(): peer
            for peer in runtime_peers
            if str(peer.get("public_key") or "").strip()
        }
    except Exception:
        runtime_available = False

    now = utc_now_naive()
    lines: list[str] = []
    for device_num, public_key in key_rows:
        peer = peer_by_public_key.get(str(public_key).strip())
        lines.append(
            render_device_activity_line(
                device_num=int(device_num),
                has_runtime_peer=peer is not None,
                last_handshake_at=peer.get("latest_handshake_at") if peer else None,
                runtime_available=runtime_available,
                now=now,
            )
        )
    return lines


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


def _payment_admin_details(payment_summary: dict | None) -> tuple[str, str, str]:
    if not payment_summary:
        return ("нет платежей", "—", "—")
    payment_line = f"{payment_summary['status']} · {payment_summary['amount']} {payment_summary['currency']}"
    activation_line = str(payment_summary.get("last_provision_status") or "—")
    charge_id = str(payment_summary.get("payment_id") or "—")
    return payment_line, activation_line, charge_id


async def _send_user_manage_card(target_message: types.Message, uid: int, page: int) -> None:
    row = await fetchone("SELECT sub_until FROM users WHERE user_id = ?", (uid,))
    if not row:
        await target_message.answer("Пользователь не найден.")
        return
    sub_until = row[0]
    status_text, until_text = get_status_text(sub_until)
    tg_username, first_name = await get_user_meta(uid)
    keys = await get_user_keys(uid)
    payment_summary = await get_latest_user_payment_summary(uid)
    referral = await get_referral_summary(uid)
    admin_device_rows = await fetchall(
        """
        SELECT device_num
        FROM keys
        WHERE user_id = ?
          AND public_key NOT LIKE 'pending:%'
          AND state = 'active'
        ORDER BY device_num
        """,
        (uid,),
    )
    admin_device_nums = [int(row[0]) for row in admin_device_rows]
    connection_status = "готово" if keys else "нет ключа"
    payment_line, activation_line, charge_id = _payment_admin_details(payment_summary)
    operator_step = _operator_next_step(payment_summary["status"], activation_line, bool(keys)) if payment_summary else "wait"
    show_retry_activation = _is_retry_activation_relevant(payment_summary, bool(keys))
    retry_hint = "\n🧰 Retry: <b>доступен</b> для ручной повторной активации" if show_retry_activation else ""
    activity_lines = await _build_admin_device_activity_lines(uid)
    await target_message.answer(
        (
            "🛠 <b>Управление пользователем</b>\n\n"
            f"🆔 <code>{uid}</code>\n"
            f"👤 Имя: {escape_html(first_name)}\n"
            f"✈️ Telegram: {format_tg_username(tg_username)}\n"
            f"📌 Статус: {status_text}\n"
            f"📅 До: <b>{until_text}</b>\n"
            f"🔑 Подключение: <b>{connection_status}</b> (устройств: {len(keys)})\n"
            f"💸 Последний платёж: <b>{payment_line}</b>\n"
            f"🚦 Активация: <b>{activation_line}</b>\n"
            f"🧾 Charge ID: <code>{escape_html(charge_id)}</code>\n"
            "↩️ Возврат: обрабатывается оператором вручную по user_id и telegram_payment_charge_id.\n"
            "Автоматический refund flow в selfhost MVP пока не реализован.\n"
            f"➡️ Шаг оператора: <b>{operator_step}</b>\n"
            f"🎁 Рефералы: приглашено {referral['invited_count']} · с бонусом {referral['rewarded_count']}\n\n"
            "📶 Активность устройств:\n"
            f"{'\n'.join(activity_lines)}"
            f"{retry_hint}"
        ),
        parse_mode="HTML",
        reply_markup=_user_manage_kb(
            uid,
            page,
            show_retry_activation=show_retry_activation,
            device_nums=admin_device_nums,
        ),
    )


def build_admin_manual_commands_text() -> str:
    lines = ["⌨️ <b>Ручные admin-команды</b>", ""]
    for command, description in ADMIN_MANUAL_COMMANDS:
        lines.append(f"• <code>{command}</code> — {description}")
    return "\n".join(lines)


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
    if not await _guard_admin_callback(cb):
        return
    await cb.message.answer("⚙️ Админ-меню", reply_markup=get_admin_inline_kb())
    await cb.answer()


@router.callback_query(F.data == CB_ADMIN_COMMANDS)
async def admin_manual_commands(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.message.answer(
        build_admin_manual_commands_text(),
        parse_mode="HTML",
        reply_markup=get_admin_simple_back_kb(CB_ADMIN_BACK_MAIN),
    )
    await cb.answer("Готово")


@router.callback_query(F.data == CB_ADMIN_PRICES)
async def admin_prices(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.message.answer(
        _render_admin_prices_text(),
        parse_mode="HTML",
        reply_markup=get_admin_prices_kb(),
    )
    await cb.answer()


@router.callback_query(F.data.in_(set(PRICE_TARGETS.keys())))
async def admin_prices_start_edit(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    target = PRICE_TARGETS.get(cb.data)
    if not target:
        await cb.answer("Некорректный тариф", show_alert=True)
        return
    maintenance_enabled = int(await get_setting("MAINTENANCE_MODE", int) or 0) == 1
    if not maintenance_enabled:
        await cb.answer("Сначала включите /maintenance_on, затем изменяйте цену.", show_alert=True)
        return
    env_key, label = target
    current_value = int(getattr(config, env_key))
    await clear_pending_admin_action(ADMIN_ID, PRICE_CONFIRM_ACTION_KEY)
    await set_pending_admin_action(ADMIN_ID, PRICE_INPUT_ACTION_KEY, {"env_key": env_key, "label": label})
    await cb.message.answer(f"Введите новую цену для «{label}» в ⭐. Текущая: {current_value}⭐")
    await cb.answer()


@router.message(IsAdmin(), F.text, ~F.text.startswith("/"), HasPendingPriceInput())
async def admin_prices_capture_input(message: types.Message):
    action = await get_pending_admin_action(ADMIN_ID, PRICE_INPUT_ACTION_KEY)
    if not action:
        return
    new_value = _parse_price_input(message.text or "")
    if new_value is None:
        await message.answer("Нужно положительное целое число.")
        return
    env_key = str(action.get("env_key", ""))
    label = str(action.get("label", ""))
    old_value = int(getattr(config, env_key, 0))
    await clear_pending_admin_action(ADMIN_ID, PRICE_INPUT_ACTION_KEY)
    await set_pending_admin_action(
        ADMIN_ID,
        PRICE_CONFIRM_ACTION_KEY,
        {"env_key": env_key, "label": label, "old": old_value, "new": new_value},
    )
    await message.answer(
        (
            f"{label}\n"
            f"Было: {old_value}⭐\n"
            f"Станет: {new_value}⭐"
        ),
        reply_markup=get_admin_price_confirm_kb(),
    )


@router.callback_query(F.data == CB_ADMIN_PRICE_SAVE)
async def admin_prices_save(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    action = await pop_pending_admin_action(ADMIN_ID, PRICE_CONFIRM_ACTION_KEY)
    await clear_pending_admin_action(ADMIN_ID, PRICE_INPUT_ACTION_KEY)
    if not action:
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    env_key = str(action.get("env_key", ""))
    label = str(action.get("label", ""))
    new_value = int(action.get("new", 0))
    old_value, saved_value = set_stars_price(env_key, new_value)
    await write_audit_log(ADMIN_ID, "admin_price_updated", f"key={env_key}; old={old_value}; new={saved_value}")
    await cb.message.answer(
        (
            f"✅ Сохранено: {label}\n"
            f"{old_value}⭐ → {saved_value}⭐"
        ),
    )
    await cb.message.answer(
        _render_admin_prices_text(),
        parse_mode="HTML",
        reply_markup=get_admin_prices_kb(),
    )
    await cb.answer("Сохранено")


@router.callback_query(F.data == CB_ADMIN_PRICE_CANCEL)
async def admin_prices_cancel(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(ADMIN_ID, PRICE_INPUT_ACTION_KEY)
    await clear_pending_admin_action(ADMIN_ID, PRICE_CONFIRM_ACTION_KEY)
    await cb.message.answer(
        _render_admin_prices_text(),
        parse_mode="HTML",
        reply_markup=get_admin_prices_kb(),
    )
    await cb.answer("Отменено")


@router.callback_query(F.data == CB_ADMIN_STATS)
async def admin_stats_cb(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.message.answer(await build_stats_text(), parse_mode="HTML")
    await cb.answer("Готово")


@router.callback_query(F.data == CB_ADMIN_SYNC)
async def admin_sync_awg(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        await sync_qos_state()
        await denylist_sync(run_docker)
        await cb.message.answer(await build_awg_sync_text(), parse_mode="HTML")
        await cb.answer("Синхронизация проверена")
    except Exception as e:
        logger.exception("Ошибка admin_sync_awg: %s", e)
        await cb.answer("❌ Ошибка проверки", show_alert=True)


@router.callback_query(F.data == CB_ADMIN_LIST)
async def admin_list_all(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await _render_users_page(cb.message, 0)
    await cb.answer()


@router.callback_query(F.data.startswith(CB_ADMIN_USERS_PAGE_PREFIX))
async def admin_users_page(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        page = int(cb.data.removeprefix(CB_ADMIN_USERS_PAGE_PREFIX))
        await _render_users_page(cb.message, page)
        await cb.answer("Открыто")
    except ValueError:
        await cb.answer("Некорректный номер страницы", show_alert=True)
    except Exception as e:
        logger.exception("Ошибка admin_users_page: %s", e)
        await cb.answer("❌ Не удалось открыть страницу", show_alert=True)


@router.callback_query(F.data.startswith(CB_ADMIN_MANAGE_USER_PREFIX))
async def admin_manage_user(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        _, _, _, uid_raw, page_raw = cb.data.split("_", 4)
        uid = int(uid_raw)
        page = int(page_raw)
        await _send_user_manage_card(cb.message, uid, page)
        await cb.answer("Открыто")
    except ValueError:
        await cb.answer("Некорректный user_id", show_alert=True)
    except Exception as e:
        logger.exception("Ошибка admin_manage_user: %s", e)
        await cb.answer("❌ Не удалось открыть карточку пользователя", show_alert=True)


@router.callback_query(F.data.startswith(CB_ADMIN_ADD_DAYS_PREFIX))
async def admin_add_days_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        _, _, _, uid_raw, days_raw, page_raw = cb.data.split("_", 5)
        uid = int(uid_raw)
        days = int(days_raw)
        page = int(page_raw)
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
            reply_markup=_user_manage_kb(uid, page),
        )
        if not notified:
            await cb.message.answer("⚠️ Доступ выдан, но уведомление пользователю отправить не удалось.")
    except Exception as e:
        logger.exception("Ошибка admin_add_days_btn: %s", e)
        await cb.answer("❌ Не удалось продлить доступ", show_alert=True)


@router.callback_query(F.data.startswith(CB_ADMIN_RETRY_ACTIVATION_PREFIX))
async def admin_retry_activation_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        _, _, _, uid_raw, page_raw = cb.data.split("_", 4)
        uid = int(uid_raw)
        page = int(page_raw)
        if admin_command_limited(f"admin_retry_activation_{uid}", cb.from_user.id):
            await cb.answer("Слишком часто: подождите перед новым retry.", show_alert=True)
            return

        payment_summary = await get_latest_user_payment_summary(uid)
        if not payment_summary:
            await write_audit_log(ADMIN_ID, "manual_retry_noop", f"target={uid}; reason=no_payment")
            await cb.message.answer(
                "ℹ️ Нет платежей для retry. Нечего повторно активировать.",
                reply_markup=_user_manage_kb(uid, page),
            )
            await cb.answer("Nothing to retry")
            return

        payment_id = str(payment_summary["payment_id"])
        await write_audit_log(ADMIN_ID, "manual_retry_requested", f"target={uid}; payment_id={payment_id}")
        result = await manual_retry_activation(payment_id, bot=cb.bot)
        result_code = result.get("result", "unknown")
        result_message = result.get("message", "Без деталей.")
        if result_code == "succeeded":
            await write_audit_log(ADMIN_ID, "manual_retry_succeeded", f"target={uid}; payment_id={payment_id}")
            outcome = "✅ Retry succeeded"
        elif result_code in {"no_payment", "already_applied", "in_progress", "not_retryable", "no_op"}:
            await write_audit_log(ADMIN_ID, "manual_retry_noop", f"target={uid}; payment_id={payment_id}; result={result_code}")
            outcome = "ℹ️ Retry no-op"
        else:
            await write_audit_log(ADMIN_ID, "manual_retry_failed", f"target={uid}; payment_id={payment_id}; result={result_code}")
            outcome = "⚠️ Retry failed"

        await cb.message.answer(
            (
                f"{outcome}\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"💳 payment_id: <code>{payment_id}</code>\n"
                f"🧩 Результат: <b>{escape_html(result_code)}</b>\n"
                f"📝 Детали: {escape_html(result_message)}\n\n"
                "Следующий шаг: обновите карточку; если статус не меняется — проверьте audit и выдайте доступ вручную."
            ),
            parse_mode="HTML",
            reply_markup=_user_manage_kb(uid, page),
        )
        await cb.answer("Retry обработан")
    except ValueError:
        await cb.answer("Некорректные параметры действия", show_alert=True)
    except Exception as e:
        logger.exception("Ошибка admin_retry_activation_btn: %s", e)
        await write_audit_log(ADMIN_ID, "manual_retry_failed", f"error={str(e)[:300]}")
        await cb.answer("❌ Не удалось выполнить retry", show_alert=True)


@router.callback_query(F.data.startswith(CB_ADMIN_DEVICE_DELETE_PREFIX))
async def admin_device_delete_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        _, _, _, uid_raw, device_num_raw, page_raw = cb.data.split("_", 5)
        uid = int(uid_raw)
        device_num = int(device_num_raw)
        page = int(page_raw)
    except ValueError:
        await cb.answer("Некорректные параметры действия", show_alert=True)
        return
    await set_pending_admin_action(
        ADMIN_ID,
        "device_delete",
        {"action": "device_delete", "target": uid, "device_num": device_num, "page": page},
    )
    await cb.message.answer(
        (
            "⚠️ <b>Подтвердите удаление устройства</b>\n\n"
            f"Пользователь: <code>{uid}</code>\n"
            f"Устройство: <b>{device_num}</b>\n\n"
            "Будет удалён только выбранный peer."
        ),
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=CB_CONFIRM_DEVICE_DELETE)],
                [types.InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_DEVICE_DELETE)],
            ]
        ),
    )
    await cb.answer()


@router.callback_query(F.data == CB_CONFIRM_DEVICE_DELETE)
async def confirm_device_delete(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    action = await pop_pending_admin_action(ADMIN_ID, "device_delete")
    if not action or action.get("action") != "device_delete":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    uid = int(action["target"])
    device_num = int(action["device_num"])
    page = int(action.get("page", 0))
    try:
        result = await delete_user_device(uid, device_num)
        await write_audit_log(
            ADMIN_ID,
            "admin_device_delete",
            f"target={uid}; device_num={device_num}; status={result['status']}; removed_runtime={int(result.get('removed_runtime', False))}",
        )
        if result["status"] == "not_found":
            await cb.message.answer(
                (
                    "ℹ️ <b>Устройство уже отсутствует</b>\n\n"
                    f"🆔 <code>{uid}</code>\n"
                    f"📱 Device: <b>{device_num}</b>\n\n"
                    "Обновите карточку пользователя и проверьте activity/runtime."
                ),
                parse_mode="HTML",
                reply_markup=_user_manage_kb(uid, page),
            )
            await cb.answer("Nothing to delete")
            return
        await cb.message.answer(
            (
                "✅ <b>Устройство удалено</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"📱 Device: <b>{device_num}</b>\n\n"
                "Дальше: обновите карточку; если не помогло — проверьте activity/runtime."
            ),
            parse_mode="HTML",
            reply_markup=_user_manage_kb(uid, page),
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_device_delete: %s", e)
        await write_audit_log(ADMIN_ID, "admin_device_delete_failed", f"target={uid}; device_num={device_num}; error={str(e)[:200]}")
        await cb.answer("❌ Не удалось удалить устройство", show_alert=True)


@router.callback_query(F.data == CB_CANCEL_DEVICE_DELETE)
async def cancel_device_delete(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(ADMIN_ID, "device_delete")
    await cb.message.answer("❌ Удаление устройства отменено")
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith(CB_ADMIN_DEVICE_REISSUE_PREFIX))
async def admin_device_reissue_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        _, _, _, uid_raw, device_num_raw, page_raw = cb.data.split("_", 5)
        uid = int(uid_raw)
        device_num = int(device_num_raw)
        page = int(page_raw)
    except ValueError:
        await cb.answer("Некорректные параметры действия", show_alert=True)
        return
    await set_pending_admin_action(
        ADMIN_ID,
        "device_reissue",
        {"action": "device_reissue", "target": uid, "device_num": device_num, "page": page},
    )
    await cb.message.answer(
        (
            "⚠️ <b>Подтвердите перевыпуск конфига устройства</b>\n\n"
            f"Пользователь: <code>{uid}</code>\n"
            f"Устройство: <b>{device_num}</b>\n\n"
            "Будет заменён только один peer в этом slot."
        ),
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=CB_CONFIRM_DEVICE_REISSUE)],
                [types.InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_DEVICE_REISSUE)],
            ]
        ),
    )
    await cb.answer()


@router.callback_query(F.data == CB_CONFIRM_DEVICE_REISSUE)
async def confirm_device_reissue(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    action = await pop_pending_admin_action(ADMIN_ID, "device_reissue")
    if not action or action.get("action") != "device_reissue":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    uid = int(action["target"])
    device_num = int(action["device_num"])
    page = int(action.get("page", 0))
    try:
        result = await reissue_user_device(uid, device_num)
        await write_audit_log(ADMIN_ID, "admin_device_reissue", f"target={uid}; device_num={device_num}; status={result['status']}")
        if result["status"] == "not_found":
            await cb.message.answer(
                (
                    "ℹ️ <b>Перевыпуск не требуется</b>\n\n"
                    f"🆔 <code>{uid}</code>\n"
                    f"📱 Device: <b>{device_num}</b>\n\n"
                    "Устройство уже отсутствует. Обновите карточку пользователя."
                ),
                parse_mode="HTML",
                reply_markup=_user_manage_kb(uid, page),
            )
            await cb.answer("Nothing to reissue")
            return
        await cb.message.answer(
            (
                "♻️ <b>Конфиг устройства перевыпущен</b>\n\n"
                f"🆔 <code>{uid}</code>\n"
                f"📱 Device: <b>{device_num}</b>\n\n"
                "Дальше: отправьте пользователю новый конфиг через existing flow «Подключение». "
                "Если не помогло — проверьте activity/runtime."
            ),
            parse_mode="HTML",
            reply_markup=_user_manage_kb(uid, page),
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_device_reissue: %s", e)
        await write_audit_log(ADMIN_ID, "admin_device_reissue_failed", f"target={uid}; device_num={device_num}; error={str(e)[:200]}")
        await cb.answer("❌ Не удалось перевыпустить устройство", show_alert=True)


@router.callback_query(F.data == CB_CANCEL_DEVICE_REISSUE)
async def cancel_device_reissue(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(ADMIN_ID, "device_reissue")
    await cb.message.answer("❌ Перевыпуск устройства отменён")
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith(CB_ADMIN_REVOKE_PREFIX))
async def admin_revoke_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        _, _, uid_raw, page_raw = cb.data.split("_", 3)
        uid = int(uid_raw)
        page = int(page_raw)
    except ValueError:
        await cb.answer("Некорректные параметры действия", show_alert=True)
        return
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


@router.callback_query(F.data == CB_CONFIRM_REVOKE)
async def confirm_revoke(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    action = await pop_pending_admin_action(ADMIN_ID, "revoke")
    if not action or action.get("action") != "revoke":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    uid = int(action["target"])
    page = int(action.get("page", 0))
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
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{page}")]]
            ),
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_revoke: %s", e)
        await cb.answer("❌ Не удалось отключить пользователя", show_alert=True)


@router.callback_query(F.data == CB_CANCEL_REVOKE)
async def cancel_revoke(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(ADMIN_ID, "revoke")
    await cb.message.answer("❌ Отключение отменено")
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith(CB_ADMIN_DELETE_PREFIX))
async def admin_del_user(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    try:
        _, _, uid_raw, page_raw = cb.data.split("_", 3)
        uid = int(uid_raw)
        page = int(page_raw)
    except ValueError:
        await cb.answer("Некорректные параметры действия", show_alert=True)
        return
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


@router.callback_query(F.data == CB_CONFIRM_DELETE_USER)
async def confirm_delete_user(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    action = await pop_pending_admin_action(ADMIN_ID, "delete_user")
    if not action or action.get("action") != "delete_user":
        await cb.answer("Нет ожидающего действия", show_alert=True)
        return
    uid = int(action["target"])
    page = int(action.get("page", 0))
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
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{CB_ADMIN_USERS_PAGE_PREFIX}{page}")]]
            ),
        )
        await cb.answer("Готово")
    except Exception as e:
        logger.exception("Ошибка confirm_delete_user: %s", e)
        await cb.answer(f"❌ Не удалось удалить пользователя: {str(e)[:120]}", show_alert=True)


@router.callback_query(F.data == CB_CANCEL_DELETE_USER)
async def cancel_delete_user(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(ADMIN_ID, "delete_user")
    await cb.message.answer("❌ Удаление отменено")
    await cb.answer("Отменено")


@router.callback_query(F.data == CB_ADMIN_BROADCAST)
async def admin_broadcast_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.answer()
    await clear_pending_broadcast(ADMIN_ID)
    await set_pending_admin_action(ADMIN_ID, BROADCAST_INPUT_ACTION_KEY, {"action": BROADCAST_INPUT_ACTION_KEY})
    users_total = int(await fetchval("SELECT COUNT(*) FROM users"))
    await cb.message.answer(
        (
            "📢 <b>Рассылка</b>\n\n"
            f"Сейчас в базе: <b>{users_total}</b> пользователей.\n\n"
            "Отправьте текст рассылки одним сообщением.\n"
            "Перед отправкой будет обязательное подтверждение."
        ),
        parse_mode="HTML",
        reply_markup=get_broadcast_cancel_kb(),
    )


@router.callback_query(F.data == CB_BROADCAST_CONFIRM)
async def broadcast_confirm(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
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
            "Отправка идёт в фоне; итог придёт отдельным сообщением.\n"
            "Снимок получателей будет зафиксирован воркером при старте задачи."
        ),
        parse_mode="HTML",
    )
    await cb.answer("Поставлено в очередь")


@router.callback_query(F.data == CB_BROADCAST_CANCEL)
async def broadcast_cancel(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_broadcast(ADMIN_ID)
    await clear_pending_admin_action(ADMIN_ID, BROADCAST_INPUT_ACTION_KEY)
    await write_audit_log(ADMIN_ID, "broadcast_cancel", "")
    await cb.message.answer("❌ Рассылка отменена")
    await cb.answer("Отменено")


@router.message(IsAdmin(), F.text, ~F.text.startswith("/"), HasPendingBroadcastInput())
async def broadcast_capture_text(message: types.Message):
    if message.text.startswith("/"):
        return
    text = message.text.strip()
    if not text:
        await message.answer("Текст пустой. Отправьте сообщение для рассылки или нажмите «Отменить».", reply_markup=get_broadcast_cancel_kb())
        return

    await set_pending_broadcast(ADMIN_ID, text)
    await clear_pending_admin_action(ADMIN_ID, BROADCAST_INPUT_ACTION_KEY)
    users_total = int(await fetchval("SELECT COUNT(*) FROM users"))
    await message.answer(
        (
            "📢 <b>Подтвердите рассылку</b>\n\n"
            f"Получателей (по текущей базе): <b>{users_total}</b>\n\n"
            f"Текст:\n{_build_broadcast_preview(text)}"
        ),
        parse_mode="HTML",
        reply_markup=get_broadcast_confirm_kb(),
    )


@router.callback_query(F.data == CB_ADMIN_REFERRALS)
@router.callback_query(F.data == CB_ADMIN_REFRESH_REFERRALS)
async def admin_referrals_summary(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
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
    if not await _guard_admin_callback(cb):
        return
    await cb.message.answer(await build_runtime_smokecheck_text(), parse_mode="HTML")
    await cb.answer("Готово")


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


@router.message(Command("promo_create"), IsAdmin())
async def promo_create_cmd(message: types.Message, command: CommandObject):
    if not command.args:
        await message.answer("Формат: <code>/promo_create CODE DAYS [MAX]</code>", parse_mode="HTML")
        return
    try:
        parts = command.args.split()
        if len(parts) < 2:
            raise ValueError
        code = normalize_promo_code(parts[0])
        days = int(parts[1])
        max_activations = int(parts[2]) if len(parts) > 2 else None
        if days <= 0 or (max_activations is not None and max_activations <= 0):
            raise ValueError
        created = await create_promo_code(code, days, max_activations, created_by=message.from_user.id)
        if not created:
            await message.answer("⚠️ Такой промокод уже существует.")
            return
        await write_audit_log(message.from_user.id, "promo_created", f"code={code}; days={days}; max={max_activations or 0}")
        max_text = str(max_activations) if max_activations is not None else "∞"
        await message.answer(f"✅ Промокод <code>{code}</code> создан: +{days} дней, лимит: {max_text}.", parse_mode="HTML")
    except ValueError:
        await message.answer("Ошибка формата. Пример: <code>/promo_create SPRING10 10 50</code>", parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /promo_create: %s", e)
        await message.answer("❌ Не удалось создать промокод.")


@router.message(Command("promo_list"), IsAdmin())
async def promo_list_cmd(message: types.Message, command: CommandObject):
    limit = 20
    if command.args:
        try:
            limit = max(1, min(50, int(command.args)))
        except ValueError:
            pass
    rows = await list_promo_codes(limit=limit)
    if not rows:
        await message.answer("Промокодов пока нет.")
        return
    lines = [f"🎟 <b>Промокоды ({len(rows)})</b>\n"]
    for code, days, max_activations, used_count, is_active, _created_at in rows:
        max_text = str(max_activations) if max_activations is not None else "∞"
        status = "on" if int(is_active) == 1 else "off"
        lines.append(f"• <code>{code}</code> | +{int(days)}д | {int(used_count)}/{max_text} | {status}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("promo_disable"), IsAdmin())
async def promo_disable_cmd(message: types.Message, command: CommandObject):
    code = normalize_promo_code(command.args or "")
    if not code:
        await message.answer("Формат: <code>/promo_disable CODE</code>", parse_mode="HTML")
        return
    try:
        disabled = await disable_promo_code(code)
        if not disabled:
            await message.answer("⚠️ Промокод не найден или уже выключен.")
            return
        await write_audit_log(message.from_user.id, "promo_disabled", f"code={code}")
        await message.answer(f"✅ Промокод <code>{code}</code> отключён.", parse_mode="HTML")
    except Exception as e:
        logger.exception("Ошибка /promo_disable: %s", e)
        await message.answer("❌ Не удалось отключить промокод.")


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


@router.message(Command("finduser"), IsAdmin())
async def find_user_cmd(message: types.Message, command: CommandObject):
    query = (command.args or "").strip()
    if not query:
        await message.answer("Формат: <code>/finduser QUERY</code>\nQUERY: user_id или username/@username", parse_mode="HTML")
        return
    if query.isdigit():
        uid = int(query)
        row = await fetchone("SELECT 1 FROM users WHERE user_id = ?", (uid,))
        if not row:
            await message.answer("Пользователь с таким user_id не найден.")
            return
        await _send_user_manage_card(message, uid, 0)
        return

    needle = query.lstrip("@").lower()
    exact_rows = await fetchall(
        """
        SELECT user_id, tg_username
        FROM users
        WHERE LOWER(COALESCE(tg_username, '')) = ?
        ORDER BY created_at DESC
        LIMIT 3
        """,
        (needle,),
    )
    if exact_rows:
        uid = int(exact_rows[0][0])
        await _send_user_manage_card(message, uid, 0)
        return

    rows = await fetchall(
        """
        SELECT user_id, tg_username
        FROM users
        WHERE LOWER(COALESCE(tg_username, '')) LIKE ?
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (f"%{needle}%",),
    )
    if not rows:
        await message.answer("Совпадений не найдено.")
        return
    kb_rows = [
        [
            types.InlineKeyboardButton(
                text=f"👤 {uid} — {format_tg_username(username)}",
                callback_data=f"{CB_ADMIN_MANAGE_USER_PREFIX}{uid}_0",
            )
        ]
        for uid, username in rows
    ]
    await message.answer(
        "Найдено несколько пользователей. Откройте карточку:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.message(Command("payinfo"), IsAdmin())
async def payinfo_cmd(message: types.Message, command: CommandObject):
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: <code>/payinfo USER_ID</code>", parse_mode="HTML")
        return
    uid = int(command.args.strip())
    payment_summary = await get_latest_user_payment_summary(uid)
    if not payment_summary:
        await message.answer("Платежей не найдено.")
        return
    await message.answer(
        (
            "💳 <b>Последний платёж пользователя</b>\n\n"
            f"🆔 user_id: <code>{uid}</code>\n"
            f"📌 status: <b>{escape_html(str(payment_summary.get('status') or '—'))}</b>\n"
            f"💰 amount: <b>{payment_summary.get('amount')} {escape_html(str(payment_summary.get('currency') or '—'))}</b>\n"
            f"🧾 telegram_payment_charge_id: <code>{escape_html(str(payment_summary.get('payment_id') or '—'))}</code>\n"
            "↩️ Подготовка к возврату: используйте user_id и telegram_payment_charge_id.\n"
            "TODO: автоматический refund flow пока не реализован в selfhost MVP."
        ),
        parse_mode="HTML",
    )


@router.message(Command("stats"), IsAdmin())
async def stats_cmd(message: types.Message):
    await message.answer(await build_stats_text(), parse_mode="HTML")


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


@router.message(Command("send"), IsAdmin())
async def broadcast_prepare(message: types.Message, command: CommandObject):
    if admin_command_limited("send", message.from_user.id):
        await message.answer("⏳ Слишком частый вызов /send")
        return
    if not command.args:
        await clear_pending_broadcast(ADMIN_ID)
        await set_pending_admin_action(ADMIN_ID, BROADCAST_INPUT_ACTION_KEY, {"action": BROADCAST_INPUT_ACTION_KEY})
        await message.answer(
            "Отправьте текст рассылки одним сообщением.",
            reply_markup=get_broadcast_cancel_kb(),
        )
        return
    text = command.args.strip()
    if not text:
        await message.answer("Текст пустой. Отправьте сообщение после <code>/send</code>.", parse_mode="HTML")
        return
    await set_pending_broadcast(ADMIN_ID, text)
    await clear_pending_admin_action(ADMIN_ID, BROADCAST_INPUT_ACTION_KEY)
    users_total = int(await fetchval("SELECT COUNT(*) FROM users"))
    await message.answer(
        (
            "📢 <b>Подтвердите рассылку</b>\n\n"
            f"Получателей (по текущей базе): <b>{users_total}</b>\n\n"
            f"Текст:\n{_build_broadcast_preview(text)}"
        ),
        parse_mode="HTML",
        reply_markup=get_broadcast_confirm_kb(),
    )


@router.message(Command("health"), IsAdmin())
async def health_cmd(message: types.Message):
    await message.answer(await build_runtime_smokecheck_text(), parse_mode="HTML")


@router.message(Command("maintenance_status"), IsAdmin())
async def maintenance_status_cmd(message: types.Message):
    enabled = int(await get_setting("MAINTENANCE_MODE", int) or 0) == 1
    status_line = "🟠 maintenance: ON (покупки заморожены)" if enabled else "🟢 maintenance: OFF (покупки доступны)"
    await message.answer(status_line)


@router.message(Command("maintenance_on"), IsAdmin())
async def maintenance_on_cmd(message: types.Message):
    enabled = int(await get_setting("MAINTENANCE_MODE", int) or 0) == 1
    if enabled:
        await message.answer("🟠 Maintenance уже включен: новые покупки заморожены.")
        return
    await set_app_setting("MAINTENANCE_MODE", "1", updated_by=message.from_user.id)
    await write_audit_log(message.from_user.id, "maintenance_enabled", "purchase_flow=frozen")
    await message.answer(
        "🟠 Maintenance включен: новые покупки временно заморожены.\n"
        "💡 При необходимости отправьте ручной broadcast через /send."
    )


@router.message(Command("maintenance_off"), IsAdmin())
async def maintenance_off_cmd(message: types.Message):
    enabled = int(await get_setting("MAINTENANCE_MODE", int) or 0) == 1
    if not enabled:
        await message.answer("🟢 Maintenance уже выключен: покупки доступны.")
        return
    await set_app_setting("MAINTENANCE_MODE", "0", updated_by=message.from_user.id)
    await write_audit_log(message.from_user.id, "maintenance_disabled", "purchase_flow=active")
    await message.answer("🟢 Maintenance выключен: новые покупки снова доступны.")


@router.message(Command("ref_stats"), IsAdmin())
async def ref_stats_cmd(message: types.Message):
    await message.answer(await build_ref_stats_text(), parse_mode="HTML")
