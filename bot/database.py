
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from config import DB_PATH, logger
from helpers import utc_now_naive
from security_utils import decrypt_text

_shared_db: aiosqlite.Connection | None = None


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
        await ensure_column(db, "keys", "state", "TEXT NOT NULL DEFAULT 'active'")
        await ensure_column(db, "keys", "state_updated_at", "TEXT")
        await ensure_column(db, "keys", "delete_reason", "TEXT")

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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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

        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_sub_until ON users(sub_until)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_keys_user_id ON keys(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_keys_ip ON keys(ip)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_created_at ON payments(user_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_retry ON provisioning_jobs(status, next_retry_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_keys_state ON keys(state)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_guards_expires ON callback_guards(expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)")

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
    db = await get_shared_db()
    await db.execute(
        """
        INSERT OR IGNORE INTO users (user_id, sub_until, created_at, tg_username, first_name)
        VALUES (?, '0', ?, ?, ?)
        """,
        (user_id, utc_now_naive().isoformat(), tg_username, first_name),
    )
    await db.execute(
        """
        UPDATE users
        SET tg_username = COALESCE(?, tg_username),
            first_name = COALESCE(?, first_name)
        WHERE user_id = ?
        """,
        (tg_username, first_name, user_id),
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
          AND state != 'delete_pending'
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
          AND state != 'delete_pending'
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
    await execute(
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
            raw_payload_json,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    await execute(
        """
        INSERT INTO provisioning_jobs (payment_id, user_id, payload, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(payment_id) DO NOTHING
        """,
        (telegram_payment_charge_id, user_id, payload, status, now_iso, now_iso),
    )


async def claim_payment_for_provisioning(telegram_payment_charge_id: str) -> bool:
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            UPDATE payments
            SET status = 'provisioning',
                updated_at = ?,
                attempt_count = attempt_count + 1,
                last_attempt_at = ?
            WHERE telegram_payment_charge_id = ?
              AND status IN ('received', 'needs_repair', 'failed')
            """,
            (utc_now_naive().isoformat(), utc_now_naive().isoformat(), telegram_payment_charge_id),
        )
        await db.commit()
        return (cursor.rowcount or 0) == 1
    finally:
        await db.close()


async def update_payment_status(
    telegram_payment_charge_id: str,
    status: str,
    provisioned_until: str | None = None,
    error_message: str | None = None,
) -> None:
    now_iso = utc_now_naive().isoformat()
    await execute(
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
    await execute(
        """
        UPDATE provisioning_jobs
        SET status = ?,
            last_error = ?,
            updated_at = ?,
            next_retry_at = CASE WHEN ? IN ('failed', 'needs_repair') THEN ? ELSE NULL END
        WHERE payment_id = ?
        """,
        (status, error_message, now_iso, status, now_iso, telegram_payment_charge_id),
    )


async def lock_provisioning_job(payment_id: str, lock_token: str) -> bool:
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            UPDATE provisioning_jobs
            SET lock_token = ?, status = 'provisioning', attempt_count = attempt_count + 1, updated_at = ?
            WHERE payment_id = ?
              AND status IN ('received', 'failed', 'needs_repair')
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            """,
            (lock_token, utc_now_naive().isoformat(), payment_id, utc_now_naive().isoformat()),
        )
        await db.commit()
        return (cursor.rowcount or 0) == 1
    finally:
        await db.close()


async def release_provisioning_job(payment_id: str, lock_token: str, status: str, error_message: str | None = None) -> None:
    await execute(
        """
        UPDATE provisioning_jobs
        SET lock_token = NULL,
            status = ?,
            last_error = ?,
            updated_at = ?,
            next_retry_at = CASE WHEN ? IN ('failed', 'needs_repair') THEN ? ELSE NULL END
        WHERE payment_id = ? AND lock_token = ?
        """,
        (status, error_message, utc_now_naive().isoformat(), status, utc_now_naive().isoformat(), payment_id, lock_token),
    )


async def get_repairable_payments(limit: int = 20) -> list[tuple[str, int, str]]:
    return await fetchall(
        """
        SELECT payment_id, user_id, payload
        FROM provisioning_jobs
        WHERE status IN ('failed', 'needs_repair', 'received')
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (utc_now_naive().isoformat(), limit),
    )


async def cleanup_stale_pending_keys(max_age_seconds: int) -> int:
    cutoff = utc_now_naive().timestamp() - max_age_seconds
    rows = await fetchall(
        "SELECT id, created_at FROM keys WHERE public_key LIKE 'pending:%' OR state='pending'",
    )
    stale_ids: list[int] = []
    for key_id, created_at in rows:
        try:
            if datetime.fromisoformat(created_at).timestamp() <= cutoff:  # type: ignore[name-defined]
                stale_ids.append(int(key_id))
        except Exception:
            stale_ids.append(int(key_id))
    if not stale_ids:
        return 0
    await execute(
        f"DELETE FROM keys WHERE id IN ({','.join(['?'] * len(stale_ids))})",
        tuple(stale_ids),
    )
    return len(stale_ids)


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
            (key, scope, now.isoformat(), datetime.fromtimestamp(expires).isoformat()),  # type: ignore[name-defined]
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
