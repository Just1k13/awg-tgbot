from datetime import datetime, timedelta
import asyncio
from pathlib import Path
from cryptography.fernet import Fernet


from aiogram import F, Router, types
from aiogram import Bot
from aiogram.filters import BaseFilter, Command, CommandObject

from awg_backend import (
    check_awg_container, clean_orphan_awg_peers, count_free_ip_slots, delete_user_everywhere,
    delete_user_device, get_awg_peers, get_orphan_awg_peers, issue_subscription, reissue_user_device, revoke_user_access, run_docker,
    sync_qos_state,
)
from config import (
    ADMIN_COMMAND_COOLDOWN_SECONDS,
    ADMIN_ID,
    AWG_HELPER_POLICY_PATH,
    BACKUP_ALLOW_INSECURE_SEND,
    BACKUP_ENCRYPTION_KEY,
    BACKUP_SECURE_MODE,
    DOCKER_CONTAINER,
    WG_INTERFACE,
    logger,
)
from database import (
    clear_pending_admin_action, clear_pending_broadcast, create_broadcast_job, create_promo_code, db_health_info, disable_promo_code, fetchall, fetchone, fetchval,
    get_app_setting,
    get_latest_user_payment_summary,
    list_promo_codes,
    get_metric, get_pending_jobs_stats, get_recovery_lag_seconds,
    get_pending_broadcast, get_recent_audit, get_referral_admin_stats, get_referral_summary, get_text_override, get_user_keys, get_user_meta, normalize_promo_code, pop_pending_admin_action, reset_text_override,
    reset_app_setting, set_app_setting, set_text_override,
    set_pending_admin_action, set_pending_broadcast, write_audit_log,
)
from helpers import escape_html, format_tg_username, get_status_text, utc_now_naive
from device_activity import render_device_activity_line
from keyboards import (
    get_admin_confirm_kb, get_admin_edit_mode_kb, get_admin_inline_kb,
    get_admin_setting_detail_kb, get_admin_settings_list_kb, get_admin_simple_back_kb, get_admin_text_detail_kb,
    get_admin_texts_list_kb, get_broadcast_confirm_kb,
)
from ui_constants import (
    BTN_ADMIN, CB_ADMIN_BACK_MAIN, CB_ADMIN_BACK_SETTINGS, CB_ADMIN_BACK_TEXTS, CB_ADMIN_BROADCAST, CB_ADMIN_BACKUP,
    CB_ADMIN_CANCEL_EDIT, CB_ADMIN_CLEAN_ORPHANS, CB_ADMIN_COMMANDS, CB_ADMIN_HEALTH, CB_ADMIN_LIST, CB_ADMIN_REFERRALS,
    CB_ADMIN_REFRESH_HEALTH, CB_ADMIN_REFRESH_REFERRALS, CB_ADMIN_REFRESH_SETTINGS, CB_ADMIN_REFRESH_TEXTS,
    CB_ADMIN_SETTING_EDIT_PREFIX, CB_ADMIN_SETTING_KEY_PREFIX, CB_ADMIN_SETTING_RESET_PREFIX, CB_ADMIN_SETTINGS,
    CB_ADMIN_SETTINGS_PAGE_PREFIX, CB_ADMIN_STATS, CB_ADMIN_SYNC, CB_ADMIN_TEXT_EDIT_PREFIX, CB_ADMIN_TEXT_KEY_PREFIX,
    CB_ADMIN_TEXT_RESET_PREFIX, CB_ADMIN_TEXTS, CB_ADMIN_TEXTS_PAGE_PREFIX,
    CB_BROADCAST_CANCEL, CB_BROADCAST_CONFIRM,
    CB_ADMIN_USERS_PAGE_PREFIX, CB_ADMIN_MANAGE_USER_PREFIX, CB_ADMIN_ADD_DAYS_PREFIX,
    CB_ADMIN_SET_RATE_PREFIX,
    CB_ADMIN_RETRY_ACTIVATION_PREFIX,
    CB_ADMIN_DEVICE_DELETE_PREFIX, CB_ADMIN_DEVICE_REISSUE_PREFIX,
    CB_ADMIN_REVOKE_PREFIX, CB_ADMIN_DELETE_PREFIX, CB_CONFIRM_CLEAN_ORPHANS,
    CB_CANCEL_CLEAN_ORPHANS, CB_CONFIRM_REVOKE, CB_CANCEL_REVOKE, CB_CONFIRM_DELETE_USER,
    CB_CANCEL_DELETE_USER, CB_CONFIRM_CLEAN_ORPHANS_FORCE, CB_CANCEL_CLEAN_ORPHANS_FORCE, CB_CONFIRM_DEVICE_DELETE,
    CB_CANCEL_DEVICE_DELETE, CB_CONFIRM_DEVICE_REISSUE, CB_CANCEL_DEVICE_REISSUE,
)
from content_settings import SETTING_DEFAULTS, TEXT_DEFAULTS, validate_text_template
from config_validate import read_helper_policy
from network_policy import denylist_sync, policy_metrics
from content_settings import get_setting
from payments import manual_retry_activation

router = Router()
admin_command_rate_limit: dict[str, object] = {}
ADMIN_USERS_PAGE_SIZE = 10
ADMIN_CONTENT_PAGE_SIZE = 8
ADMIN_EDIT_TIMEOUT_SECONDS = 600
ADMIN_MANUAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/health", "быстрая проверка selfhost readiness"),
    ("/sync_awg", "сверка AWG и БД"),
    ("/stats", "краткая статистика"),
    ("/users", "короткий список пользователей"),
    ("/audit", "последние события"),
    ("/ref_stats", "сводка по рефералам"),
    ("/send TEXT", "рассылка (осторожно)"),
    ("/backup", "redacted backup в Telegram и на диск"),
    ("/give USER_ID DAYS", "выдать/продлить доступ вручную"),
    ("/promo_create CODE DAYS [MAX]", "создать промокод"),
    ("/promo_list", "краткий список промокодов"),
    ("/promo_disable CODE", "отключить промокод"),
    ("/revoke USER_ID", "отключить доступ вручную (осторожно)"),
)


def _build_redacted_backup_payload(db_path: str) -> tuple[bytes, str]:
    from pathlib import Path
    import sqlite3
    import tempfile

    db_file = Path(db_path)
    if not db_file.exists():
        raise FileNotFoundError("db_not_found")

    tmp_file = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp_file.close()
    redacted = Path(tmp_file.name)
    try:
        src = sqlite3.connect(str(db_file))
        dst = sqlite3.connect(str(redacted))
        try:
            src.backup(dst)
            dst.execute("UPDATE keys SET config = '', vpn_key = '', psk_key = '', client_private_key = ''")
            dst.commit()
        finally:
            dst.close()
            src.close()
        payload = redacted.read_bytes()
    finally:
        redacted.unlink(missing_ok=True)
    return payload, db_file.name


def _build_backup_file_path(db_path: str, *, secure_mode: bool, now: datetime | None = None) -> Path:
    ts = (now or utc_now_naive()).strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(db_path).resolve().parent / "backups"
    filename = f"redacted_vpn_bot_{ts}.sqlite"
    if secure_mode:
        filename += ".enc"
    return backup_dir / filename


def _build_backup_result_message(path: Path, *, secure_mode: bool) -> str:
    mode = "secure" if secure_mode else "insecure"
    return (
        "✅ Backup created\n"
        f"💾 Saved: <code>{escape_html(str(path))}</code>\n"
        f"🔐 Mode: <b>{mode}</b>"
    )


def _persist_backup_payload(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


async def _run_backup_flow(message: types.Message) -> None:
    from config import DB_PATH

    try:
        try:
            payload, db_name = await asyncio.to_thread(_build_redacted_backup_payload, DB_PATH)
        except FileNotFoundError:
            await message.answer("❌ Файл базы данных не найден.")
            return
        filename = f"redacted_{db_name}"
        caption = (
            "📦 Резервная копия базы данных без секретов\n"
            f"📅 {utc_now_naive().strftime('%d.%m.%Y %H:%M')}"
        )
        if BACKUP_SECURE_MODE:
            if not BACKUP_ENCRYPTION_KEY:
                await message.answer("❌ BACKUP_SECURE_MODE=1, но BACKUP_ENCRYPTION_KEY не задан.")
                return
            payload = Fernet(BACKUP_ENCRYPTION_KEY.encode("utf-8")).encrypt(payload)
            filename = f"{filename}.enc"
            caption += "\n🔐 Secure mode: backup зашифрован (Fernet)."
        elif not BACKUP_ALLOW_INSECURE_SEND:
            await message.answer("❌ Небезопасная отправка backup отключена. Включите BACKUP_SECURE_MODE=1 или явно задайте BACKUP_ALLOW_INSECURE_SEND=1.")
            return
        else:
            caption += "\n⚠️ Insecure mode: файл отправлен без шифрования (явный opt-in)."

        backup_path = _build_backup_file_path(DB_PATH, secure_mode=BACKUP_SECURE_MODE)
        await asyncio.to_thread(_persist_backup_payload, backup_path, payload)
        await write_audit_log(ADMIN_ID, "backup_created", f"path={backup_path}; mode={'secure' if BACKUP_SECURE_MODE else 'insecure'}")
        await message.answer(_build_backup_result_message(backup_path, secure_mode=BACKUP_SECURE_MODE), parse_mode="HTML")
        await message.answer_document(
            types.BufferedInputFile(payload, filename=filename),
            caption=caption,
        )
    except Exception as e:
        logger.exception("Ошибка /backup: %s", e)
        await message.answer("❌ Не удалось отправить backup базы.")


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


SETTING_LABELS: dict[str, tuple[str, str]] = {
    "REFERRAL_ENABLED": ("Рефералка включена", "1 — включена, 0 — выключена."),
    "REFERRAL_INVITEE_BONUS_DAYS": ("Бонус приглашённому (дни)", "Сколько дней получает новый пользователь после первой оплаты."),
    "REFERRAL_INVITER_BONUS_DAYS": ("Бонус пригласившему (дни)", "Сколько дней получает пригласивший после первой оплаты приглашённого."),
    "DEFAULT_KEY_RATE_MBIT": ("Скорость по умолчанию (Мбит/с)", "Лимит скорости для новых ключей."),
    "QOS_ENABLED": ("Ограничение скорости включено", "1 — лимиты скорости активны."),
    "QOS_STRICT": ("Строгий режим QoS", "1 — ошибка QoS останавливает операцию; 0 — только warning."),
    "EGRESS_DENYLIST_ENABLED": ("Блок-лист сайтов включен", "1 — включен denylist исходящего трафика."),
    "EGRESS_DENYLIST_MODE": ("Режим блок-листа", "strict — ошибки sync критичны; soft — только логируются."),
    "EGRESS_DENYLIST_DOMAINS": ("Домены в блок-листе", "Список доменов через запятую."),
    "EGRESS_DENYLIST_CIDRS": ("IP/CIDR в блок-листе", "Список сетей через запятую."),
    "EGRESS_DENYLIST_REFRESH_MINUTES": ("Интервал обновления block-листа (мин)", "Как часто обновлять denylist в фоне."),
    "TORRENT_POLICY_TEXT_ENABLED": ("Показывать предупреждение про P2P", "1 — в инструкции отображается блок про policy."),
    "VPN_SUBNET_PREFIX": ("Префикс VPN подсети", "Обычно 10.8.1."),
}

TEXT_LABELS: dict[str, tuple[str, str]] = {
    "start": ("Стартовое сообщение", "Текст после /start."),
    "buy_menu": ("Экран покупки", "Показывается перед выбором тарифа."),
    "renew_menu": ("Экран продления", "Показывается при активной подписке."),
    "profile_screen": ("Экран профиля", "Карточка пользователя и статус подписки."),
    "configs_menu": ("Экран подключения", "Объяснение, что отправляется vpn:// и .conf."),
    "configs_empty": ("Нет подключений", "Сообщение, когда у пользователя нет ключей."),
    "payment_success": ("Оплата: доступ готов", "Статус успешной активации."),
    "payment_pending": ("Оплата: в обработке", "Статус, когда выдача ещё в процессе."),
    "payment_error": ("Оплата: ошибка", "Сообщение при проблеме активации."),
    "referral_screen": ("Экран рефералов", "Ссылка, статистика и правила начисления бонуса."),
    "support_contact": ("Текст поддержки", "Полный текст раздела поддержки."),
    "instruction_body": ("Инструкция подключения", "Пошаговый гайд для пользователя."),
}


def _humanize_setting_key(key: str) -> tuple[str, str]:
    if key in SETTING_LABELS:
        return SETTING_LABELS[key]
    return key.replace("_", " ").capitalize(), "Технический параметр."


def _humanize_text_key(key: str) -> tuple[str, str]:
    if key in TEXT_LABELS:
        return TEXT_LABELS[key]
    return key.replace("_", " ").capitalize(), "Технический текстовый шаблон."


def _compact_setting_title(key: str) -> str:
    title, _ = _humanize_setting_key(key)
    return f"{title} · {key}"


def _compact_text_title(key: str) -> str:
    title, _ = _humanize_text_key(key)
    return f"{title} · {key}"


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
        "📝 <b>Тексты</b>\nВыберите понятное название. Технический ключ показан после точки.",
        parse_mode="HTML",
        reply_markup=get_admin_texts_list_kb(chunk, page, total_pages, _compact_text_title),
    )


async def _render_settings_list(target_message: types.Message, page: int = 0) -> None:
    keys = _all_setting_keys()
    chunk, page, total_pages = _chunk_keys(keys, page)
    await target_message.answer(
        "⚙️ <b>Настройки</b>\nВыберите понятное название. Технический ключ показан после точки.",
        parse_mode="HTML",
        reply_markup=get_admin_settings_list_kb(chunk, page, total_pages, _compact_setting_title),
    )


async def _render_text_detail(target_message: types.Message, key: str, index: int, page: int) -> None:
    current_value = await get_text_override(key) or TEXT_DEFAULTS.get(key, "")
    default_value = TEXT_DEFAULTS.get(key, "")
    title, description = _humanize_text_key(key)
    await target_message.answer(
        (
            "📝 <b>Карточка текста</b>\n\n"
            f"Название: <b>{escape_html(title)}</b>\n"
            f"Описание: {escape_html(description)}\n"
            f"Ключ: <code>{key}</code>\n"
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
    title, description = _humanize_setting_key(key)
    await target_message.answer(
        (
            "⚙️ <b>Карточка настройки</b>\n\n"
            f"Название: <b>{escape_html(title)}</b>\n"
            f"Описание: {escape_html(description)}\n"
            f"Ключ: <code>{key}</code>\n"
            f"Тип: <b>{_value_type_hint(default_value)}</b>\n"
            f"Текущее: <code>{escape_html(str(current_value))}</code>\n"
            f"По умолчанию: <code>{escape_html(str(default_value))}</code>"
        ),
        parse_mode="HTML",
        reply_markup=get_admin_setting_detail_kb(index, page),
    )


async def build_ref_stats_text() -> str:
    stats = await get_referral_admin_stats()
    recent = "\n".join([f"• invitee={r[0]} inviter={r[1]} pay={r[2]}" for r in stats["recent"]]) or "—"
    top = "\n".join([f"• inviter={row[0]} rewards={row[1]}" for row in stats["top"]]) or "—"
    total_bonus_days = int(await fetchval(
        "SELECT COALESCE(SUM(invitee_bonus_days + inviter_bonus_days), 0) FROM referral_rewards"
    ))
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


@router.callback_query(F.data == CB_ADMIN_BACKUP)
async def admin_backup_cb(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb, require_message=True):
        return
    await cb.answer()
    await _run_backup_flow(cb.message)


@router.callback_query(F.data == CB_ADMIN_TEXTS)
async def admin_texts_menu(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.answer("Отключено в personal MVP", show_alert=True)


@router.callback_query(F.data == CB_ADMIN_SETTINGS)
async def admin_settings_menu(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.answer("Отключено в personal MVP", show_alert=True)


@router.callback_query(F.data == CB_ADMIN_BACK_TEXTS)
@router.callback_query(F.data == CB_ADMIN_REFRESH_TEXTS)
async def admin_texts_back_refresh(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await _render_texts_list(cb.message, 0)
    await cb.answer("Готово")


@router.callback_query(F.data == CB_ADMIN_BACK_SETTINGS)
@router.callback_query(F.data == CB_ADMIN_REFRESH_SETTINGS)
async def admin_settings_back_refresh(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await _render_settings_list(cb.message, 0)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith(CB_ADMIN_TEXTS_PAGE_PREFIX))
async def admin_texts_page(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    page = int(cb.data.removeprefix(CB_ADMIN_TEXTS_PAGE_PREFIX))
    await _render_texts_list(cb.message, page)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith(CB_ADMIN_SETTINGS_PAGE_PREFIX))
async def admin_settings_page(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    page = int(cb.data.removeprefix(CB_ADMIN_SETTINGS_PAGE_PREFIX))
    await _render_settings_list(cb.message, page)
    await cb.answer("Готово")


@router.callback_query(F.data.startswith(CB_ADMIN_TEXT_KEY_PREFIX))
async def admin_text_key_detail(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
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
    if not await _guard_admin_callback(cb):
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
    if not await _guard_admin_callback(cb):
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
        f"✏️ Отправьте новое значение для <code>{key}</code> ({_humanize_text_key(key)[0]}).\nДля отмены нажмите кнопку ниже.",
        parse_mode="HTML",
        reply_markup=get_admin_edit_mode_kb(),
    )
    await cb.answer("Режим редактирования")


@router.callback_query(F.data.startswith(CB_ADMIN_SETTING_EDIT_PREFIX))
async def admin_setting_edit_start(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
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
        f"✏️ Отправьте новое значение для <code>{key}</code> ({_humanize_setting_key(key)[0]}).\nДля отмены нажмите кнопку ниже.",
        parse_mode="HTML",
        reply_markup=get_admin_edit_mode_kb(),
    )
    await cb.answer("Режим редактирования")


@router.callback_query(F.data == CB_ADMIN_CANCEL_EDIT)
async def admin_cancel_edit(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(cb.from_user.id, "edit_text")
    await clear_pending_admin_action(cb.from_user.id, "edit_setting")
    await cb.message.answer("❌ Редактирование отменено.")
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


@router.callback_query(F.data == CB_ADMIN_CLEAN_ORPHANS)
async def admin_clean_orphans(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
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


@router.callback_query(F.data == CB_CONFIRM_CLEAN_ORPHANS)
async def confirm_clean_orphans(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
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


@router.callback_query(F.data == CB_CANCEL_CLEAN_ORPHANS)
async def cancel_clean_orphans(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans")
    await cb.message.answer("❌ Очистка потерянных peer отменена")
    await cb.answer("Отменено")


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
        row = await fetchone("SELECT sub_until FROM users WHERE user_id = ?", (uid,))
        if not row:
            await cb.answer("Пользователь не найден", show_alert=True)
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
        payment_line = "нет платежей"
        activation_line = "нет данных"
        operator_step = "wait"
        show_retry_activation = False
        if payment_summary:
            payment_line = (
                f"{payment_summary['status']} · {payment_summary['amount']} {payment_summary['currency']}"
            )
            activation_line = payment_summary["last_provision_status"] or "—"
            operator_step = _operator_next_step(payment_summary["status"], activation_line, bool(keys))
            show_retry_activation = _is_retry_activation_relevant(payment_summary, bool(keys))
        retry_hint = "\n🧰 Retry: <b>доступен</b> для ручной повторной активации" if show_retry_activation else ""
        activity_lines = await _build_admin_device_activity_lines(uid)
        await cb.message.answer(
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


@router.callback_query(F.data.startswith(CB_ADMIN_SET_RATE_PREFIX))
async def admin_set_rate_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.answer("Отключено в personal MVP", show_alert=True)


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
    users_total = int(await fetchval("SELECT COUNT(*) FROM users"))
    await cb.message.answer(
        (
            "📢 <b>Рассылка</b>\n\n"
            "Используйте команду:\n"
            "<code>/send Ваш текст</code>\n\n"
            f"Сейчас в базе: <b>{users_total}</b> пользователей.\n"
            "Перед отправкой будет обязательное подтверждение."
        ),
        parse_mode="HTML",
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
    await write_audit_log(ADMIN_ID, "broadcast_cancel", "")
    await cb.message.answer("❌ Рассылка отменена")
    await cb.answer("Отменено")


@router.callback_query(F.data.startswith(CB_ADMIN_TEXT_RESET_PREFIX))
async def admin_text_reset_btn(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
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
    if not await _guard_admin_callback(cb):
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


@router.message(Command("set_rate"), IsAdmin())
async def set_user_rate_limit_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ /set_rate отключена в personal MVP.")


@router.message(Command("rate"), IsAdmin())
async def get_user_rate_limit_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ /rate отключена в personal MVP.")


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
    await message.answer("⚠️ /clean_orphans_force отключена в personal MVP.")


@router.callback_query(F.data == CB_CONFIRM_CLEAN_ORPHANS_FORCE)
async def confirm_clean_orphans_force(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await cb.answer("Отключено в personal MVP", show_alert=True)


@router.callback_query(F.data == CB_CANCEL_CLEAN_ORPHANS_FORCE)
async def cancel_clean_orphans_force(cb: types.CallbackQuery):
    if not await _guard_admin_callback(cb):
        return
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans_force")
    await clear_pending_admin_action(ADMIN_ID, "clean_orphans_force_word")
    await cb.message.answer("❌ Force-очистка потерянных peer отменена")
    await cb.answer()


@router.message(Command("force_delete"), IsAdmin())
async def force_delete_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ /force_delete отключена в personal MVP.")


@router.message(Command("backup"), IsAdmin())
async def backup_db(message: types.Message):
    await _run_backup_flow(message)


@router.message(Command("send"), IsAdmin())
async def broadcast_prepare(message: types.Message, command: CommandObject):
    if admin_command_limited("send", message.from_user.id):
        await message.answer("⏳ Слишком частый вызов /send")
        return
    if not command.args:
        await message.answer("Напишите текст после <code>/send</code>", parse_mode="HTML")
        return
    await set_pending_broadcast(ADMIN_ID, command.args)
    users_total = int(await fetchval("SELECT COUNT(*) FROM users"))
    preview = command.args.strip()
    if len(preview) > 500:
        preview = f"{preview[:500]}…"
    await message.answer(
        (
            "📢 <b>Подтвердите рассылку</b>\n\n"
            f"Получателей (по текущей базе): <b>{users_total}</b>\n\n"
            f"Текст:\n{escape_html(preview)}"
        ),
        parse_mode="HTML",
        reply_markup=get_broadcast_confirm_kb(),
    )


@router.message(Command("health"), IsAdmin())
async def health_cmd(message: types.Message):
    await message.answer(await build_runtime_smokecheck_text(), parse_mode="HTML")


@router.message(Command("text_list"), IsAdmin())
async def text_list_cmd(message: types.Message):
    await message.answer("⚠️ Text editor отключён в personal MVP.")


@router.message(Command("text_get"), IsAdmin())
async def text_get_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ Text editor отключён в personal MVP.")


@router.message(Command("text_set"), IsAdmin())
async def text_set_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ Text editor отключён в personal MVP.")


@router.message(Command("text_reset"), IsAdmin())
async def text_reset_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ Text editor отключён в personal MVP.")


@router.message(Command("setting_list"), IsAdmin())
async def setting_list_cmd(message: types.Message):
    await message.answer("⚠️ Settings editor отключён в personal MVP.")


@router.message(Command("setting_get"), IsAdmin())
async def setting_get_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ Settings editor отключён в personal MVP.")


@router.message(Command("setting_set"), IsAdmin())
async def setting_set_cmd(message: types.Message, command: CommandObject):
    await message.answer("⚠️ Settings editor отключён в personal MVP.")


@router.message(Command("ref_stats"), IsAdmin())
async def ref_stats_cmd(message: types.Message):
    await message.answer(await build_ref_stats_text(), parse_mode="HTML")
