import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from config import DB_PATH, logger
from helpers import utc_now_naive
from security_utils import decrypt_text

_shared_db: aiosqlite.Connection | None = None
SAFE_TG_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")


async def _apply_pragmas(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA synchronous=NORMAL;")
    await db.execute("PRAGMA foreign_keys=ON;")
    await db.execute("PRAGMA busy_timeout=5000;")


async def get_shared_db() -> aiosqlite.Connection:
    global _shared_db
    if _shared_db is None:
        _shared_db = await aiosqlite.connect(DB_PATH)
        await _apply_pragmas(_shared_db)
    return _shared_db


async def close_shared_db() -> None:
    global _shared_db
    if _shared_db is not None:
        await _shared_db.close()
        _shared_db = None


async def open_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    await _apply_pragmas(db)
    return db


async def fetchone(sql: str, params: tuple[Any, ...] = ()) -> Any:
    db = await get_shared_db()
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    db = await get_shared_db()
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchall()


async def fetchval(sql: str, params: tuple[Any, ...] = (), default: Any = 0) -> Any:
    row = await fetchone(sql, params)
    if not row:
        return default
    return row[0]


async def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    db = await get_shared_db()
    await db.execute(sql, params)
    await db.commit()


async def ensure_column(db: aiosqlite.Connection, table_name: str, column_name: str, column_def: str) -> None:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    if column_name not in existing:
        await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


async def init_db() -> None:
    db = await open_db()
    try:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                sub_until TEXT NOT NULL DEFAULT '0',
                created_at TEXT NOT NULL
            )
            """
        )
        await ensure_column(db, "users", "tg_username", "TEXT")
        await ensure_column(db, "users", "first_name", "TEXT")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device_num INTEGER NOT NULL,
                public_key TEXT NOT NULL UNIQUE,
                config TEXT NOT NULL,
                ip TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                UNIQUE(user_id, device_num)
            )
            """
        )
        await ensure_column(db, "keys", "psk_key", "TEXT")
        await ensure_column(db, "keys", "vpn_key", "TEXT")
        await ensure_column(db, "keys", "client_private_key", "TEXT")
        await ensure_column(db, "keys", "bot_managed", "INTEGER NOT NULL DEFAULT 1")
        await ensure_column(db, "keys", "state", "TEXT NOT NULL DEFAULT 'active'")
        await ensure_column(db, "keys", "state_updated_at", "TEXT")
        await ensure_column(db, "keys", "delete_reason", "TEXT")
        await ensure_column(db, "keys", "rate_limit_mbit", "INTEGER")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                telegram_payment_charge_id TEXT PRIMARY KEY,
                provider_payment_charge_id TEXT,
                user_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await ensure_column(db, "payments", "currency", "TEXT")
        await ensure_column(db, "payments", "payment_method", "TEXT")
        await ensure_column(db, "payments", "status", "TEXT NOT NULL DEFAULT 'received'")
        await ensure_column(db, "payments", "provisioned_until", "TEXT")
        await ensure_column(db, "payments", "error_message", "TEXT")
        await ensure_column(db, "payments", "raw_payload_json", "TEXT")
        await ensure_column(db, "payments", "updated_at", "TEXT")
        await ensure_column(db, "payments", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
        await ensure_column(db, "payments", "last_attempt_at", "TEXT")
        await ensure_column(db, "payments", "last_provision_status", "TEXT NOT NULL DEFAULT 'payment_received'")
        await ensure_column(db, "payments", "user_notified_ready_at", "TEXT")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_actions (
                admin_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (admin_id, action_key)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_broadcasts (
                admin_id INTEGER PRIMARY KEY,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                total_count INTEGER NOT NULL DEFAULT 0,
                delivered_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                offset_cursor INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_job_targets (
                job_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (job_id, user_id),
                FOREIGN KEY (job_id) REFERENCES broadcast_jobs(id) ON DELETE CASCADE
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS protected_peers (
                public_key TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS provisioning_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'received',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                lock_token TEXT,
                last_error TEXT,
                next_retry_at TEXT,
                lease_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await ensure_column(db, "provisioning_jobs", "lease_expires_at", "TEXT")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_prechecks (
                precheckout_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_notifications (
                user_id INTEGER NOT NULL,
                sub_until TEXT NOT NULL,
                kind TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (user_id, sub_until, kind)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS callback_guards (
                guard_key TEXT PRIMARY KEY,
                action_scope TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_operations (
                operation_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                days INTEGER NOT NULL,
                previous_sub_until TEXT NOT NULL,
                new_until TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_metrics (
                metric_key TEXT PRIMARY KEY,
                metric_value INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by INTEGER
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS text_overrides (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by INTEGER
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_codes (
                user_id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_attributions (
                invitee_user_id INTEGER PRIMARY KEY,
                inviter_user_id INTEGER NOT NULL,
                referral_code TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_rewards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invitee_user_id INTEGER NOT NULL,
                inviter_user_id INTEGER NOT NULL,
                payment_id TEXT NOT NULL UNIQUE,
                invitee_bonus_days INTEGER NOT NULL,
                inviter_bonus_days INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'applied',
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                bonus_days INTEGER NOT NULL,
                max_activations INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by INTEGER
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_activations (
                code TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                PRIMARY KEY (code, user_id),
                FOREIGN KEY (code) REFERENCES promo_codes(code) ON DELETE CASCADE
            )
            """
        )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_sub_until ON users(sub_until)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_keys_user_id ON keys(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_keys_ip ON keys(ip)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_created_at ON payments(user_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_retry ON provisioning_jobs(status, next_retry_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_lease_expires ON provisioning_jobs(lease_expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prechecks_user_created ON payment_prechecks(user_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_keys_state ON keys(state)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_guards_expires ON callback_guards(expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subscription_operations_status ON subscription_operations(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_inviter ON referral_rewards(inviter_user_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_invitee ON referral_rewards(invitee_user_id, created_at)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_reward_once_per_invitee ON referral_rewards(invitee_user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_promo_codes_created_at ON promo_codes(created_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_promo_activations_user ON promo_activations(user_id, activated_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_jobs_status ON broadcast_jobs(status, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_targets_job ON broadcast_job_targets(job_id, user_id)")

        await db.commit()
    finally:
        await db.close()


async def ensure_db_ready() -> None:
    await init_db()


async def set_pending_admin_action(admin_id: int, action_key: str, payload: dict[str, Any]) -> None:
    await execute(
        """
        INSERT INTO pending_actions (admin_id, action_key, payload, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(admin_id, action_key)
        DO UPDATE SET payload = excluded.payload, created_at = excluded.created_at
        """,
        (admin_id, action_key, json.dumps(payload, ensure_ascii=False), utc_now_naive().isoformat()),
    )


def _safe_load_json(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        logger.error("Некорректный JSON в pending_actions: %s", raw)
        return None


async def pop_pending_admin_action(admin_id: int, action_key: str) -> dict[str, Any] | None:
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT payload FROM pending_actions WHERE admin_id = ? AND action_key = ?",
            (admin_id, action_key),
        ) as cursor:
            row = await cursor.fetchone()
        await db.execute(
            "DELETE FROM pending_actions WHERE admin_id = ? AND action_key = ?",
            (admin_id, action_key),
        )
        await db.commit()
        if not row:
            return None
        return _safe_load_json(row[0])
    finally:
        await db.close()


async def clear_pending_admin_action(admin_id: int, action_key: str) -> None:
    await execute(
        "DELETE FROM pending_actions WHERE admin_id = ? AND action_key = ?",
        (admin_id, action_key),
    )


async def set_pending_broadcast(admin_id: int, text: str) -> None:
    await execute(
        """
        INSERT INTO pending_broadcasts (admin_id, text, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(admin_id)
        DO UPDATE SET text = excluded.text, created_at = excluded.created_at
        """,
        (admin_id, text, utc_now_naive().isoformat()),
    )


async def get_pending_broadcast(admin_id: int) -> str | None:
    row = await fetchone(
        "SELECT text FROM pending_broadcasts WHERE admin_id = ?",
        (admin_id,),
    )
    return row[0] if row else None


async def clear_pending_broadcast(admin_id: int) -> None:
    await execute("DELETE FROM pending_broadcasts WHERE admin_id = ?", (admin_id,))


async def ensure_user_exists(user_id: int, tg_username: str | None = None, first_name: str | None = None) -> None:
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("invalid telegram user_id")
    safe_username = tg_username
    if tg_username:
        candidate = tg_username.lstrip("@").strip()
        safe_username = candidate if SAFE_TG_USERNAME_RE.fullmatch(candidate) else None
    safe_first_name = (first_name or "").strip()[:128] or None
    db = await get_shared_db()
    await db.execute(
        """
        INSERT OR IGNORE INTO users (user_id, sub_until, created_at, tg_username, first_name)
        VALUES (?, '0', ?, ?, ?)
        """,
        (user_id, utc_now_naive().isoformat(), safe_username, safe_first_name),
    )
    await db.execute(
        """
        UPDATE users
        SET tg_username = COALESCE(?, tg_username),
            first_name = COALESCE(?, first_name)
        WHERE user_id = ?
        """,
        (safe_username, safe_first_name, user_id),
    )
    await db.commit()


async def get_user_subscription(user_id: int) -> str | None:
    row = await fetchone("SELECT sub_until FROM users WHERE user_id = ?", (user_id,))
    return row[0] if row else None


async def get_user_meta(user_id: int) -> tuple[str | None, str | None]:
    row = await fetchone(
        "SELECT tg_username, first_name FROM users WHERE user_id = ?",
        (user_id,),
    )
    return (row[0], row[1]) if row else (None, None)


async def get_reserved_ips_from_db() -> set[int]:
    rows = await fetchall(
        """
        SELECT ip
        FROM keys
        WHERE ip IS NOT NULL
          AND TRIM(ip) != ''
          AND state != 'deleted'
        """
    )
    used: set[int] = set()
    for (ip,) in rows:
        try:
            octet = int(str(ip).split(".")[-1])
            used.add(octet)
        except Exception:
            continue
    return used


async def get_reserved_ips_from_db_conn(db: aiosqlite.Connection) -> set[int]:
    async with db.execute(
        """
        SELECT ip
        FROM keys
        WHERE ip IS NOT NULL
          AND TRIM(ip) != ''
          AND state != 'deleted'
        """
    ) as cursor:
        rows = await cursor.fetchall()
    used: set[int] = set()
    for (ip,) in rows:
        try:
            octet = int(str(ip).split(".")[-1])
            used.add(octet)
        except Exception:
            continue
    return used


async def get_user_keys(user_id: int) -> list[tuple[int, int, str, str]]:
    now_iso = utc_now_naive().isoformat()
    rows = await fetchall(
        """
        SELECT k.id, k.device_num, k.ip, k.client_private_key, k.public_key, k.psk_key
        FROM keys k
        JOIN users u ON u.user_id = k.user_id
        WHERE k.user_id = ?
          AND k.public_key NOT LIKE 'pending:%'
          AND k.state = 'active'
          AND k.ip IS NOT NULL
          AND TRIM(k.ip) != ''
          AND u.sub_until != '0'
          AND u.sub_until > ?
        ORDER BY k.device_num
        """,
        (user_id, now_iso),
    )
    from awg_backend import build_client_config, build_vpn_payload, encode_vpn_key

    result: list[tuple[int, int, str, str]] = []
    for key_id, device_num, ip, client_private_key, public_key, psk_key in rows:
        try:
            private_key = decrypt_text(client_private_key)
            psk = decrypt_text(psk_key)
        except Exception as e:
            logger.error("Пропуск key_id=%s из-за ошибки расшифровки: %s", key_id, e)
            continue
        if not private_key or not public_key or not psk or not ip:
            continue
        config = build_client_config(private_key, ip, psk)
        vpn_key = encode_vpn_key(build_vpn_payload(private_key, public_key, ip, psk))
        result.append((key_id, device_num, config, vpn_key))
    return result


async def get_payment_status(payment_id: str) -> str | None:
    row = await fetchone(
        "SELECT status FROM payments WHERE telegram_payment_charge_id = ?",
        (payment_id,),
    )
    return row[0] if row else None


async def get_payment_activation_snapshot(payment_id: str) -> tuple[str, str | None] | None:
    row = await fetchone(
        "SELECT last_provision_status, provisioned_until FROM payments WHERE telegram_payment_charge_id = ?",
        (payment_id,),
    )
    return (row[0], row[1]) if row else None


async def get_latest_user_payment_summary(user_id: int) -> dict[str, Any] | None:
    row = await fetchone(
        """
        SELECT telegram_payment_charge_id, payload, status, amount, currency, created_at, updated_at, provisioned_until, last_provision_status
        FROM payments
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id,),
    )
    if not row:
        return None
    return {
        "payment_id": row[0],
        "payload": row[1],
        "status": row[2],
        "amount": row[3],
        "currency": row[4],
        "created_at": row[5],
        "updated_at": row[6],
        "provisioned_until": row[7],
        "last_provision_status": row[8],
    }


async def update_last_provision_status(payment_id: str, status: str) -> None:
    await execute(
        """
        UPDATE payments
        SET last_provision_status = ?, updated_at = ?
        WHERE telegram_payment_charge_id = ?
        """,
        (status, utc_now_naive().isoformat(), payment_id),
    )


async def mark_ready_notification_sent(payment_id: str) -> bool:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        cur = await db.execute(
            """
            UPDATE payments
            SET user_notified_ready_at = ?, updated_at = ?
            WHERE telegram_payment_charge_id = ?
              AND user_notified_ready_at IS NULL
            """,
            (now_iso, now_iso, payment_id),
        )
        await db.commit()
        return (cur.rowcount or 0) == 1
    finally:
        await db.close()


async def payment_already_processed(payment_id: str) -> bool:
    status = await get_payment_status(payment_id)
    return status == "applied"


async def save_payment(
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str | None,
    user_id: int,
    payload: str,
    amount: int,
    currency: str,
    payment_method: str,
    status: str = "received",
    raw_payload_json: str | None = None,
) -> None:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """
            INSERT INTO payments (
                telegram_payment_charge_id,
                provider_payment_charge_id,
                user_id,
                payload,
                amount,
                currency,
                payment_method,
                status,
                last_provision_status,
                raw_payload_json,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'payment_received', ?, ?, ?)
            ON CONFLICT(telegram_payment_charge_id) DO UPDATE SET
                provider_payment_charge_id = COALESCE(excluded.provider_payment_charge_id, provider_payment_charge_id),
                raw_payload_json = COALESCE(excluded.raw_payload_json, raw_payload_json),
                updated_at = excluded.updated_at
            """,
            (
                telegram_payment_charge_id,
                provider_payment_charge_id,
                user_id,
                payload,
                amount,
                currency,
                payment_method,
                status,
                raw_payload_json,
                now_iso,
                now_iso,
            ),
        )
        await db.execute(
            """
            INSERT INTO provisioning_jobs (payment_id, user_id, payload, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(payment_id) DO UPDATE SET
                user_id = excluded.user_id,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (telegram_payment_charge_id, user_id, payload, status, now_iso, now_iso),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def upsert_payment_precheck(
    precheckout_id: str,
    user_id: int,
    payload: str,
    status: str = "created",
    error_message: str | None = None,
) -> None:
    now_iso = utc_now_naive().isoformat()
    await execute(
        """
        INSERT INTO payment_prechecks (precheckout_id, user_id, payload, status, error_message, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(precheckout_id) DO UPDATE SET
            status = excluded.status,
            error_message = excluded.error_message,
            updated_at = excluded.updated_at
        """,
        (precheckout_id, user_id, payload, status, error_message, now_iso, now_iso),
    )


async def mark_payment_precheck_status(precheckout_id: str, status: str, error_message: str | None = None) -> None:
    await execute(
        """
        UPDATE payment_prechecks
        SET status = ?, error_message = ?, updated_at = ?
        WHERE precheckout_id = ?
        """,
        (status, error_message, utc_now_naive().isoformat(), precheckout_id),
    )


async def has_subscription_notification(user_id: int, sub_until: str, kind: str) -> bool:
    row = await fetchone(
        """
        SELECT 1
        FROM subscription_notifications
        WHERE user_id = ? AND sub_until = ? AND kind = ?
        """,
        (user_id, sub_until, kind),
    )
    return bool(row)


async def mark_subscription_notification_sent(user_id: int, sub_until: str, kind: str) -> None:
    await execute(
        """
        INSERT OR IGNORE INTO subscription_notifications (user_id, sub_until, kind, sent_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, sub_until, kind, utc_now_naive().isoformat()),
    )


async def get_subscriptions_expiring_within(hours: int = 24) -> list[tuple[int, str]]:
    now_iso = utc_now_naive().isoformat()
    deadline = (utc_now_naive() + timedelta(hours=hours)).isoformat()
    rows = await fetchall(
        """
        SELECT user_id, sub_until
        FROM users
        WHERE sub_until != '0'
          AND sub_until > ?
          AND sub_until <= ?
        """,
        (now_iso, deadline),
    )
    return [(int(uid), str(sub_until)) for uid, sub_until in rows]


async def claim_payment_and_job_for_provisioning(
    payment_id: str,
    lock_token: str,
    lease_expires_at: str,
) -> bool:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT p.status, j.status, j.lease_expires_at
            FROM payments p
            JOIN provisioning_jobs j ON j.payment_id = p.telegram_payment_charge_id
            WHERE p.telegram_payment_charge_id = ?
            """,
            (payment_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await db.rollback()
            return False

        payment_status, job_status, current_lease_expires_at = row

        claimable = False
        if payment_status == "applied" or job_status == "applied":
            await db.rollback()
            return False

        if payment_status in {"received", "needs_repair", "failed"} and job_status in {"received", "needs_repair", "failed"}:
            claimable = True
        elif job_status == "provisioning":
            lease_expired = bool(current_lease_expires_at and str(current_lease_expires_at) <= now_iso)
            if lease_expired and payment_status in {"provisioning", "received", "needs_repair", "failed"}:
                claimable = True

        if not claimable:
            await db.rollback()
            return False

        payment_cur = await db.execute(
            """
            UPDATE payments
            SET status = 'provisioning',
                error_message = NULL,
                updated_at = ?,
                attempt_count = attempt_count + 1,
                last_attempt_at = ?
            WHERE telegram_payment_charge_id = ?
            """,
            (now_iso, now_iso, payment_id),
        )
        job_cur = await db.execute(
            """
            UPDATE provisioning_jobs
            SET status = 'provisioning',
                lock_token = ?,
                last_error = NULL,
                next_retry_at = NULL,
                lease_expires_at = ?,
                updated_at = ?,
                attempt_count = attempt_count + 1
            WHERE payment_id = ?
            """,
            (lock_token, lease_expires_at, now_iso, payment_id),
        )
        await db.commit()
        return (payment_cur.rowcount or 0) == 1 and (job_cur.rowcount or 0) == 1
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def finalize_payment_and_job(
    payment_id: str,
    lock_token: str,
    status: str,
    provisioned_until: str | None = None,
    error_message: str | None = None,
    next_retry_at: str | None = None,
) -> bool:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT lock_token FROM provisioning_jobs WHERE payment_id = ?",
            (payment_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] != lock_token:
            await db.rollback()
            return False

        payment_cur = await db.execute(
            """
            UPDATE payments
            SET status = ?,
                provisioned_until = COALESCE(?, provisioned_until),
                error_message = ?,
                updated_at = ?
            WHERE telegram_payment_charge_id = ?
            """,
            (status, provisioned_until, error_message, now_iso, payment_id),
        )
        job_cur = await db.execute(
            """
            UPDATE provisioning_jobs
            SET lock_token = NULL,
                status = ?,
                last_error = ?,
                updated_at = ?,
                next_retry_at = ?,
                lease_expires_at = NULL
            WHERE payment_id = ?
            """,
            (status, error_message, now_iso, next_retry_at, payment_id),
        )
        await db.commit()
        return (payment_cur.rowcount or 0) == 1 and (job_cur.rowcount or 0) == 1
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def update_payment_status(
    telegram_payment_charge_id: str,
    status: str,
    provisioned_until: str | None = None,
    error_message: str | None = None,
    next_retry_at: str | None = None,
) -> None:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """
            UPDATE payments
            SET status = ?,
                provisioned_until = COALESCE(?, provisioned_until),
                error_message = ?,
                provider_payment_charge_id = provider_payment_charge_id,
                updated_at = ?
            WHERE telegram_payment_charge_id = ?
            """,
            (status, provisioned_until, error_message, now_iso, telegram_payment_charge_id),
        )
        await db.execute(
            """
            UPDATE provisioning_jobs
            SET status = ?,
                last_error = ?,
                updated_at = ?,
                next_retry_at = ?,
                lease_expires_at = CASE WHEN ? = 'provisioning' THEN lease_expires_at ELSE NULL END
            WHERE payment_id = ?
            """,
            (status, error_message, now_iso, next_retry_at, status, telegram_payment_charge_id),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_repairable_payments(limit: int = 20) -> list[tuple[str, int, str]]:
    return await fetchall(
        """
        SELECT payment_id, user_id, payload
        FROM provisioning_jobs
        WHERE (
                status IN ('failed', 'needs_repair', 'received')
                AND (next_retry_at IS NULL OR next_retry_at <= ?)
              )
           OR (
                status = 'provisioning'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= ?
              )
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (utc_now_naive().isoformat(), utc_now_naive().isoformat(), limit),
    )


async def get_provisioning_attempt_count(payment_id: str) -> int:
    row = await fetchone("SELECT attempt_count FROM provisioning_jobs WHERE payment_id = ?", (payment_id,))
    return int(row[0]) if row else 0


async def mark_payment_stuck_manual(payment_id: str, reason: str) -> None:
    await update_payment_status(
        payment_id,
        status="stuck_manual",
        error_message=reason[:500],
        next_retry_at=None,
    )


async def cleanup_stale_pending_keys(max_age_seconds: int) -> int:
    cutoff = utc_now_naive().timestamp() - max_age_seconds
    rows = await fetchall(
        "SELECT id, created_at FROM keys WHERE public_key LIKE 'pending:%' OR state='pending'",
    )
    stale_ids: list[int] = []
    for key_id, created_at in rows:
        try:
            if datetime.fromisoformat(created_at).timestamp() <= cutoff:
                stale_ids.append(int(key_id))
        except Exception:
            stale_ids.append(int(key_id))
    if not stale_ids:
        return 0
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.executemany(
            """
            UPDATE keys
            SET state='needs_manual_repair',
                delete_reason='stale_pending_placeholder',
                state_updated_at=?
            WHERE id = ?
            """,
            [(now_iso, key_id) for key_id in stale_ids],
        )
        await db.commit()
    finally:
        await db.close()
    return len(stale_ids)


async def increment_metric(metric_key: str, delta: int = 1) -> None:
    now_iso = utc_now_naive().isoformat()
    await execute(
        """
        INSERT INTO runtime_metrics (metric_key, metric_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(metric_key) DO UPDATE SET
            metric_value = metric_value + excluded.metric_value,
            updated_at = excluded.updated_at
        """,
        (metric_key, delta, now_iso),
    )


async def get_metric(metric_key: str) -> int:
    row = await fetchone("SELECT metric_value FROM runtime_metrics WHERE metric_key = ?", (metric_key,))
    return int(row[0]) if row else 0


async def set_metric(metric_key: str, value: int) -> None:
    await execute(
        """
        INSERT INTO runtime_metrics (metric_key, metric_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(metric_key) DO UPDATE SET
            metric_value = excluded.metric_value,
            updated_at = excluded.updated_at
        """,
        (metric_key, int(value), utc_now_naive().isoformat()),
    )


async def set_app_setting(key: str, value: str, updated_by: int | None = None) -> None:
    await execute(
        """
        INSERT INTO app_settings (key, value, updated_at, updated_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at,
            updated_by = excluded.updated_by
        """,
        (key, value, utc_now_naive().isoformat(), updated_by),
    )


async def get_app_setting(key: str) -> str | None:
    row = await fetchone("SELECT value FROM app_settings WHERE key = ?", (key,))
    return row[0] if row else None


async def list_app_settings() -> list[tuple[str, str, str, int | None]]:
    return await fetchall("SELECT key, value, updated_at, updated_by FROM app_settings ORDER BY key")


async def reset_app_setting(key: str) -> None:
    await execute("DELETE FROM app_settings WHERE key = ?", (key,))


async def set_text_override(key: str, value: str, updated_by: int | None = None) -> None:
    await execute(
        """
        INSERT INTO text_overrides (key, value, updated_at, updated_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at,
            updated_by = excluded.updated_by
        """,
        (key, value, utc_now_naive().isoformat(), updated_by),
    )


async def get_text_override(key: str) -> str | None:
    row = await fetchone("SELECT value FROM text_overrides WHERE key = ?", (key,))
    return row[0] if row else None


async def list_text_overrides() -> list[tuple[str, str, str, int | None]]:
    return await fetchall("SELECT key, value, updated_at, updated_by FROM text_overrides ORDER BY key")


async def reset_text_override(key: str) -> None:
    await execute("DELETE FROM text_overrides WHERE key = ?", (key,))


async def ensure_referral_code(user_id: int, code: str) -> None:
    await execute(
        """
        INSERT OR IGNORE INTO referral_codes (user_id, code, created_at)
        VALUES (?, ?, ?)
        """,
        (user_id, code, utc_now_naive().isoformat()),
    )


async def get_referral_code(user_id: int) -> str | None:
    row = await fetchone("SELECT code FROM referral_codes WHERE user_id = ?", (user_id,))
    return row[0] if row else None


async def get_user_id_by_referral_code(code: str) -> int | None:
    row = await fetchone("SELECT user_id FROM referral_codes WHERE code = ?", (code,))
    return int(row[0]) if row else None


async def set_referral_attribution(invitee_user_id: int, inviter_user_id: int, referral_code: str) -> bool:
    db = await open_db()
    try:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO referral_attributions (invitee_user_id, inviter_user_id, referral_code, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (invitee_user_id, inviter_user_id, referral_code, utc_now_naive().isoformat()),
        )
        await db.commit()
        return (cur.rowcount or 0) == 1
    finally:
        await db.close()


async def get_referral_attribution(invitee_user_id: int) -> tuple[int, str] | None:
    row = await fetchone(
        "SELECT inviter_user_id, referral_code FROM referral_attributions WHERE invitee_user_id = ?",
        (invitee_user_id,),
    )
    return (int(row[0]), str(row[1])) if row else None


async def user_has_paid_subscription(user_id: int) -> bool:
    row = await fetchone(
        "SELECT 1 FROM payments WHERE user_id = ? AND status = 'applied' LIMIT 1",
        (user_id,),
    )
    return bool(row)


async def create_referral_reward_once(
    invitee_user_id: int,
    inviter_user_id: int,
    payment_id: str,
    invitee_bonus_days: int,
    inviter_bonus_days: int,
) -> bool:
    db = await open_db()
    try:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO referral_rewards (
                invitee_user_id, inviter_user_id, payment_id, invitee_bonus_days, inviter_bonus_days, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'applied', ?)
            """,
            (invitee_user_id, inviter_user_id, payment_id, invitee_bonus_days, inviter_bonus_days, utc_now_naive().isoformat()),
        )
        await db.commit()
        return (cur.rowcount or 0) == 1
    finally:
        await db.close()


async def get_referral_summary(user_id: int) -> dict[str, int]:
    invited = await fetchone("SELECT COUNT(*) FROM referral_attributions WHERE inviter_user_id = ?", (user_id,))
    rewarded = await fetchone("SELECT COUNT(*) FROM referral_rewards WHERE inviter_user_id = ?", (user_id,))
    row = await fetchone(
        """
        SELECT COALESCE(SUM(inviter_bonus_days), 0), COALESCE(SUM(invitee_bonus_days), 0)
        FROM referral_rewards
        WHERE inviter_user_id = ? OR invitee_user_id = ?
        """,
        (user_id, user_id),
    )
    inviter_bonus = int(row[0]) if row else 0
    invitee_bonus = int(row[1]) if row else 0
    return {
        "invited_count": int(invited[0]) if invited else 0,
        "rewarded_count": int(rewarded[0]) if rewarded else 0,
        "inviter_bonus_days": inviter_bonus,
        "invitee_bonus_days": invitee_bonus,
    }


async def get_referral_admin_stats(limit: int = 5) -> dict[str, Any]:
    pending = await fetchone(
        """
        SELECT COUNT(*)
        FROM referral_attributions a
        LEFT JOIN referral_rewards r ON r.invitee_user_id = a.invitee_user_id
        WHERE r.invitee_user_id IS NULL
        """
    )
    rewarded = await fetchone("SELECT COUNT(*) FROM referral_rewards")
    recent = await fetchall(
        """
        SELECT invitee_user_id, inviter_user_id, payment_id, invitee_bonus_days, inviter_bonus_days, created_at
        FROM referral_rewards
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    top = await fetchall(
        """
        SELECT inviter_user_id, COUNT(*) AS cnt
        FROM referral_rewards
        GROUP BY inviter_user_id
        ORDER BY cnt DESC
        LIMIT ?
        """,
        (limit,),
    )
    return {
        "pending": int(pending[0]) if pending else 0,
        "rewarded": int(rewarded[0]) if rewarded else 0,
        "recent": recent,
        "top": top,
    }


def normalize_promo_code(raw_code: str) -> str:
    return (raw_code or "").strip().upper()


async def create_promo_code(code: str, bonus_days: int, max_activations: int | None, created_by: int | None = None) -> bool:
    safe_code = normalize_promo_code(code)
    if not safe_code:
        raise ValueError("empty promo code")
    if bonus_days <= 0:
        raise ValueError("bonus_days must be > 0")
    if max_activations is not None and max_activations <= 0:
        raise ValueError("max_activations must be > 0")
    db = await open_db()
    try:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO promo_codes (code, bonus_days, max_activations, used_count, is_active, created_at, created_by)
            VALUES (?, ?, ?, 0, 1, ?, ?)
            """,
            (safe_code, bonus_days, max_activations, utc_now_naive().isoformat(), created_by),
        )
        await db.commit()
        return (cur.rowcount or 0) == 1
    finally:
        await db.close()


async def list_promo_codes(limit: int = 20) -> list[tuple[str, int, int | None, int, int, str]]:
    return await fetchall(
        """
        SELECT code, bonus_days, max_activations, used_count, is_active, created_at
        FROM promo_codes
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )


async def disable_promo_code(code: str) -> bool:
    safe_code = normalize_promo_code(code)
    if not safe_code:
        return False
    db = await open_db()
    try:
        cur = await db.execute("UPDATE promo_codes SET is_active = 0 WHERE code = ? AND is_active = 1", (safe_code,))
        await db.commit()
        return (cur.rowcount or 0) == 1
    finally:
        await db.close()


async def activate_promo_code(user_id: int, code: str) -> dict[str, Any]:
    safe_code = normalize_promo_code(code)
    if not safe_code:
        return {"status": "not_found"}
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT bonus_days, max_activations, used_count, is_active FROM promo_codes WHERE code = ?",
            (safe_code,),
        ) as cursor:
            promo = await cursor.fetchone()
        if not promo:
            await db.rollback()
            return {"status": "not_found"}
        bonus_days, max_activations, used_count, is_active = int(promo[0]), promo[1], int(promo[2]), int(promo[3])
        if not is_active:
            await db.rollback()
            return {"status": "inactive"}
        async with db.execute(
            "SELECT 1 FROM promo_activations WHERE code = ? AND user_id = ?",
            (safe_code, user_id),
        ) as cursor:
            already_used = await cursor.fetchone()
        if already_used:
            await db.rollback()
            return {"status": "already_used"}
        if max_activations is not None and used_count >= int(max_activations):
            await db.rollback()
            return {"status": "exhausted"}
        await db.execute(
            "INSERT INTO promo_activations (code, user_id, activated_at) VALUES (?, ?, ?)",
            (safe_code, user_id, now_iso),
        )
        await db.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?", (safe_code,))
        await db.commit()
        return {"status": "reserved", "bonus_days": bonus_days}
    finally:
        await db.close()


async def rollback_promo_activation_reservation(user_id: int, code: str) -> None:
    safe_code = normalize_promo_code(code)
    if not safe_code:
        return
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("DELETE FROM promo_activations WHERE code = ? AND user_id = ?", (safe_code, user_id))
        if (cur.rowcount or 0) == 1:
            await db.execute(
                "UPDATE promo_codes SET used_count = CASE WHEN used_count > 0 THEN used_count - 1 ELSE 0 END WHERE code = ?",
                (safe_code,),
            )
        await db.commit()
    finally:
        await db.close()


async def create_broadcast_job(admin_id: int, text: str) -> int:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            INSERT INTO broadcast_jobs (admin_id, text, status, created_at, updated_at)
            VALUES (?, ?, 'queued', ?, ?)
            """,
            (admin_id, text, now_iso, now_iso),
        )
        await db.execute("UPDATE pending_broadcasts SET text = ? WHERE admin_id = ?", (text, admin_id))
        await db.commit()
        return int(cur.lastrowid)
    finally:
        await db.close()


async def claim_next_broadcast_job() -> tuple[int, int, str, int] | None:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT id, admin_id, text
            FROM broadcast_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.rollback()
            return None
        job_id, admin_id, text = int(row[0]), int(row[1]), str(row[2])
        await db.execute("DELETE FROM broadcast_job_targets WHERE job_id = ?", (job_id,))
        await db.execute(
            """
            INSERT INTO broadcast_job_targets (job_id, user_id, created_at)
            SELECT ?, user_id, ? FROM users
            """,
            (job_id, now_iso),
        )
        async with db.execute("SELECT COUNT(*) FROM broadcast_job_targets WHERE job_id = ?", (job_id,)) as cursor:
            total = int((await cursor.fetchone())[0])
        await db.execute(
            """
            UPDATE broadcast_jobs
            SET status='running',
                started_at=?,
                total_count=?,
                updated_at=?
            WHERE id = ?
            """,
            (now_iso, total, now_iso, job_id),
        )
        await db.commit()
        return job_id, admin_id, text, total
    finally:
        await db.close()


async def get_broadcast_recipients(job_id: int, offset: int, limit: int) -> list[int]:
    rows = await fetchall(
        "SELECT user_id FROM broadcast_job_targets WHERE job_id = ? ORDER BY user_id ASC LIMIT ? OFFSET ?",
        (job_id, limit, offset),
    )
    return [int(row[0]) for row in rows]


async def update_broadcast_job_progress(job_id: int, delivered_delta: int, failed_delta: int, offset_cursor: int) -> None:
    await execute(
        """
        UPDATE broadcast_jobs
        SET delivered_count = delivered_count + ?,
            failed_count = failed_count + ?,
            offset_cursor = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (delivered_delta, failed_delta, offset_cursor, utc_now_naive().isoformat(), job_id),
    )


async def complete_broadcast_job(job_id: int, status: str, last_error: str | None = None) -> tuple[int, int, int]:
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """
            UPDATE broadcast_jobs
            SET status = ?, last_error = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, last_error, now_iso, now_iso, job_id),
        )
        async with db.execute(
            "SELECT admin_id, delivered_count, failed_count FROM broadcast_jobs WHERE id = ?",
            (job_id,),
        ) as cursor:
            row = await cursor.fetchone()
        await db.execute("DELETE FROM broadcast_job_targets WHERE job_id = ?", (job_id,))
        await db.commit()
        if not row:
            return 0, 0, 0
        return int(row[0]), int(row[1]), int(row[2])
    finally:
        await db.close()


async def get_pending_jobs_stats() -> dict[str, int]:
    rows = await fetchall(
        """
        SELECT status, COUNT(*)
        FROM provisioning_jobs
        GROUP BY status
        """
    )
    data = {str(status): int(count) for status, count in rows}
    return {
        "received": data.get("received", 0),
        "provisioning": data.get("provisioning", 0),
        "needs_repair": data.get("needs_repair", 0),
        "stuck_manual": data.get("stuck_manual", 0),
        "failed": data.get("failed", 0),
        "applied": data.get("applied", 0),
    }


async def get_recovery_lag_seconds() -> int:
    row = await fetchone(
        """
        SELECT MIN(next_retry_at)
        FROM provisioning_jobs
        WHERE status = 'needs_repair'
          AND next_retry_at IS NOT NULL
        """
    )
    if not row or not row[0]:
        return 0
    try:
        lag = (utc_now_naive() - datetime.fromisoformat(str(row[0]))).total_seconds()
        return int(max(0, lag))
    except Exception:
        return 0


def _guard_digest(scope: str, actor_id: int, payload: str) -> str:
    raw = f"{scope}:{actor_id}:{payload}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def persistent_guard_hit(scope: str, actor_id: int, payload: str, ttl_seconds: int) -> bool:
    key = _guard_digest(scope, actor_id, payload)
    now = utc_now_naive()
    expires = now.timestamp() + ttl_seconds
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("DELETE FROM callback_guards WHERE expires_at <= ?", (now.isoformat(),))
        async with db.execute("SELECT guard_key FROM callback_guards WHERE guard_key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
        if row:
            await db.commit()
            return True
        await db.execute(
            "INSERT INTO callback_guards (guard_key, action_scope, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (key, scope, now.isoformat(), datetime.fromtimestamp(expires).isoformat()),
        )
        await db.commit()
        return False
    finally:
        await db.close()


async def write_audit_log(user_id: int, action: str, details: str = "") -> None:
    try:
        await execute(
            "INSERT INTO audit_log (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, details, utc_now_naive().isoformat()),
        )
    except Exception as e:
        logger.error("Не удалось записать audit_log: %s", e)


async def get_recent_audit(limit: int = 20) -> list[tuple[int, int, str, str, str]]:
    return await fetchall(
        """
        SELECT id, user_id, action, details, created_at
        FROM audit_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )


async def db_health_info() -> dict[str, Any]:
    info = {
        "exists": False,
        "keys_table_exists": False,
        "has_required_columns": False,
        "total_keys_count": 0,
        "valid_keys_count": 0,
        "is_healthy": False,
    }
    db_file = Path(DB_PATH)
    if not db_file.exists():
        return info
    info["exists"] = True
    db = await open_db()
    try:
        async with db.execute("PRAGMA table_info(keys)") as cursor:
            cols = await cursor.fetchall()
        if not cols:
            return info
        info["keys_table_exists"] = True
        col_names = {c[1] for c in cols}
        required = {"user_id", "public_key", "ip"}
        info["has_required_columns"] = required.issubset(col_names)
        if info["has_required_columns"]:
            async with db.execute("SELECT COUNT(*) FROM keys") as cursor:
                info["total_keys_count"] = (await cursor.fetchone())[0]
            async with db.execute(
                """
                SELECT COUNT(*)
                FROM keys
                WHERE public_key IS NOT NULL
                  AND TRIM(public_key) != ''
                  AND public_key NOT LIKE 'pending:%'
                  AND ip IS NOT NULL
                  AND TRIM(ip) != ''
                """
            ) as cursor:
                info["valid_keys_count"] = (await cursor.fetchone())[0]
        info["is_healthy"] = bool(info["keys_table_exists"] and info["has_required_columns"])
        return info
    finally:
        await db.close()


async def add_protected_peer(public_key: str, reason: str) -> None:
    public_key = (public_key or '').strip()
    if not public_key:
        return
    await execute(
        """
        INSERT INTO protected_peers (public_key, reason, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(public_key) DO UPDATE SET reason = excluded.reason
        """,
        (public_key, reason, utc_now_naive().isoformat()),
    )


async def get_protected_public_keys() -> set[str]:
    rows = await fetchall(
        "SELECT public_key FROM protected_peers WHERE public_key IS NOT NULL AND TRIM(public_key) != ''"
    )
    return {row[0].strip() for row in rows if row and row[0]}


async def count_protected_peers() -> int:
    row = await fetchone("SELECT COUNT(*) FROM protected_peers")
    return int(row[0]) if row else 0


async def get_valid_db_public_keys() -> set[str]:
    rows = await fetchall(
        """
        SELECT public_key
        FROM keys
        WHERE public_key IS NOT NULL
          AND TRIM(public_key) != ''
          AND public_key NOT LIKE 'pending:%'
          AND ip IS NOT NULL
          AND TRIM(ip) != ''
        """
    )
    return {row[0].strip() for row in rows if row[0]}


async def get_bot_managed_known_public_keys() -> set[str]:
    rows = await fetchall(
        """
        SELECT public_key
        FROM keys
        WHERE public_key IS NOT NULL
          AND TRIM(public_key) != ''
          AND public_key NOT LIKE 'pending:%'
          AND bot_managed = 1
        """
    )
    return {row[0].strip() for row in rows if row[0]}
