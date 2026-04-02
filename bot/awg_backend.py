import asyncio
import base64
import json
import ipaddress
import uuid
import zlib
from datetime import datetime, timedelta
import re
from typing import Any

from config import (
    ADMIN_ID, AWG_H1, AWG_H2, AWG_H3, AWG_H4, AWG_I1, AWG_I2, AWG_I3, AWG_I4, AWG_I5,
    AWG_JC, AWG_JMAX, AWG_JMIN, AWG_PEERS_CACHE_TTL_SECONDS, AWG_PROTOCOL_VERSION, AWG_S1, AWG_S2, AWG_S3,
    AWG_S4, AWG_TRANSPORT_PROTO, CLIENT_ALLOWED_IPS, CLIENT_MTU, CONFIGS_PER_USER, DOCKER_CONTAINER,
    DOCKER_RETRIES, DOCKER_RETRY_BASE_DELAY, DOCKER_TIMEOUT_SECONDS, FIRST_CLIENT_OCTET, IGNORE_PEERS, MAX_CLIENT_OCTET,
    PENDING_KEY_TTL_SECONDS, PERSISTENT_KEEPALIVE, PRIMARY_DNS, SECONDARY_DNS, SERVER_IP, SERVER_NAME, SERVER_PUBLIC_KEY,
    VPN_SUBNET_PREFIX, WG_INTERFACE, AWG_HELPER_PATH, AWG_HELPER_USE_SUDO, logger,
)
from database import (
    add_protected_peer, count_protected_peers, db_health_info, ensure_user_exists, fetchall,
    get_bot_managed_known_public_keys, get_protected_public_keys, get_reserved_ips_from_db, get_reserved_ips_from_db_conn,
    get_valid_db_public_keys, increment_metric, open_db, sync_traffic_counters_from_runtime_peers, write_audit_log,
)
from helpers import is_valid_awg_public_key, parse_server_host_port, utc_now_naive
from network_policy import denylist_sync, qos_clear, qos_rate_for_key, qos_set, qos_sync
from security_utils import decrypt_text, encrypt_text

subscription_lock = asyncio.Lock()
_peers_cache: dict[str, Any] = {"expires_at": None, "data": None}


def _invalidate_peers_cache() -> None:
    _peers_cache["expires_at"] = None
    _peers_cache["data"] = None


async def run_docker_once(args: list[str], input_data: str | None = None, timeout: int = DOCKER_TIMEOUT_SECONDS) -> str:
    cmd = [AWG_HELPER_PATH]
    if AWG_HELPER_USE_SUDO:
        cmd = ["sudo", "-n", AWG_HELPER_PATH]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        *args,
        stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        process.communicate(input=input_data.encode("utf-8") if input_data is not None else None),
        timeout=timeout,
    )
    out = stdout.decode("utf-8", errors="ignore").strip()
    err = stderr.decode("utf-8", errors="ignore").strip()
    if process.returncode != 0:
        raise RuntimeError(err or out or "unknown docker error")
    return out


async def run_docker(args: list[str], input_data: str | None = None, timeout: int = DOCKER_TIMEOUT_SECONDS) -> str:
    last_error: Exception | None = None
    for attempt in range(1, DOCKER_RETRIES + 1):
        try:
            return await run_docker_once(args, input_data=input_data, timeout=timeout)
        except asyncio.TimeoutError:
            last_error = RuntimeError(f"helper timeout: {' '.join(args)}")
            logger.warning("helper timeout attempt=%s/%s cmd=%s", attempt, DOCKER_RETRIES, args)
            await increment_metric("awg_helper_failures")
        except Exception as e:
            last_error = RuntimeError(f"helper exec failed: {e}")
            logger.warning("helper error attempt=%s/%s cmd=%s error=%s", attempt, DOCKER_RETRIES, args, e)
            await increment_metric("awg_helper_failures")
        if attempt < DOCKER_RETRIES:
            await asyncio.sleep(DOCKER_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
    raise last_error if last_error else RuntimeError("helper exec failed")



def _parse_transfer_bytes(raw_value: str) -> int | None:
    value = raw_value.strip()
    if not value:
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?$", value)
    if not match:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "B").strip().upper()
    multipliers = {
        "B": 1,
        "BYTE": 1,
        "BYTES": 1,
        "KB": 1024,
        "KIB": 1024,
        "MB": 1024 ** 2,
        "MIB": 1024 ** 2,
        "GB": 1024 ** 3,
        "GIB": 1024 ** 3,
        "TB": 1024 ** 4,
        "TIB": 1024 ** 4,
    }
    multiplier = multipliers.get(unit)
    if multiplier is None:
        return None
    return max(0, int(number * multiplier))


def _parse_transfer_line(raw_value: str) -> tuple[int | None, int | None]:
    value = raw_value.strip()
    if not value:
        return (None, None)
    rx_match = re.search(r"([\d.]+\s*[A-Za-z]+|\d+)\s+received", value, re.IGNORECASE)
    tx_match = re.search(r"([\d.]+\s*[A-Za-z]+|\d+)\s+sent", value, re.IGNORECASE)
    rx_bytes = _parse_transfer_bytes(rx_match.group(1)) if rx_match else None
    tx_bytes = _parse_transfer_bytes(tx_match.group(1)) if tx_match else None
    return (rx_bytes, tx_bytes)


def parse_awg_show_output(show_output: str) -> list[dict[str, str | datetime | int | None]]:
    def flush_peer() -> dict[str, str | datetime | int | None]:
        return {
            "public_key": current_pub,
            "ip": current_ip,
            "latest_handshake_at": current_handshake_at,
            "rx_bytes": current_rx_bytes,
            "tx_bytes": current_tx_bytes,
        }

    def parse_latest_handshake(raw_value: str) -> datetime | None:
        value = raw_value.strip().lower()
        if not value or value in {"(none)", "none", "never", "n/a"}:
            return None
        if value.endswith("ago"):
            value = value[:-3].strip()
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            return None

        delta_seconds = 0
        for part in parts:
            match = re.match(r"^(\d+)\s+([a-z]+)", part)
            if not match:
                return None
            amount = int(match.group(1))
            unit = match.group(2)
            if unit.startswith("second"):
                delta_seconds += amount
            elif unit.startswith("minute"):
                delta_seconds += amount * 60
            elif unit.startswith("hour"):
                delta_seconds += amount * 3600
            elif unit.startswith("day"):
                delta_seconds += amount * 86400
            elif unit.startswith("week"):
                delta_seconds += amount * 7 * 86400
            elif unit.startswith("month"):
                delta_seconds += amount * 30 * 86400
            elif unit.startswith("year"):
                delta_seconds += amount * 365 * 86400
            else:
                return None
        return utc_now_naive() - timedelta(seconds=delta_seconds)

    lines = show_output.splitlines()
    peers: list[dict[str, str | datetime | int | None]] = []
    current_pub: str | None = None
    current_ip: str | None = None
    current_handshake_at: datetime | None = None
    current_rx_bytes: int | None = None
    current_tx_bytes: int | None = None
    for raw_line in lines:
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("peer:"):
            if current_pub:
                peers.append(flush_peer())
            current_pub = line.split(":", 1)[1].strip()
            current_ip = None
            current_handshake_at = None
            current_rx_bytes = None
            current_tx_bytes = None
            continue
        if lowered.startswith("allowed ips:"):
            allowed = line.split(":", 1)[1].strip()
            candidates = [item.strip() for item in allowed.split(",") if item.strip()]
            current_ip = None
            for item in candidates:
                try:
                    network = ipaddress.ip_network(item, strict=False)
                except ValueError:
                    continue
                if network.version != 4 or network.prefixlen != 32:
                    continue
                host_ip = str(network.network_address)
                if not host_ip.startswith(VPN_SUBNET_PREFIX):
                    continue
                current_ip = host_ip
                break
        if lowered.startswith("latest handshake:"):
            current_handshake_at = parse_latest_handshake(line.split(":", 1)[1])
        if lowered.startswith("transfer:"):
            current_rx_bytes, current_tx_bytes = _parse_transfer_line(line.split(":", 1)[1])
    if current_pub:
        peers.append(flush_peer())
    return peers


async def check_awg_container() -> None:
    result = await run_docker(["check-awg"])
    if "interface:" not in result:
        raise RuntimeError(f"Не удалось проверить интерфейс {WG_INTERFACE}")


async def generate_keypair() -> tuple[str, str]:
    private_key = (await run_docker(["genkey"])).strip()
    if not private_key:
        raise RuntimeError("awg genkey вернул пустой private key")
    public_key = (await run_docker(["pubkey"], input_data=private_key)).strip()
    if not public_key or not is_valid_awg_public_key(public_key):
        raise RuntimeError("awg pubkey вернул некорректный public key")
    return private_key, public_key


async def generate_psk() -> str:
    psk = (await run_docker(["genpsk"])).strip()
    if not psk:
        raise RuntimeError("wg genpsk вернул пустой PSK")
    return psk


async def add_peer_to_awg(public_key: str, ip: str, psk_key: str) -> None:
    if not is_valid_awg_public_key(public_key):
        raise RuntimeError("Некорректный public key перед awg set")
    await run_docker([
        "add-peer",
        "--public-key", public_key,
        "--ip", ip,
    ], input_data=psk_key)
    _invalidate_peers_cache()


async def sync_qos_state() -> None:
    db = await open_db()
    try:
        async with db.execute(
            """
            SELECT ip, rate_limit_mbit
            FROM keys
            WHERE state='active' AND ip IS NOT NULL AND TRIM(ip) != ''
            """
        ) as cursor:
            rows = await cursor.fetchall()
    finally:
        await db.close()
    active = []
    for ip, override in rows:
        active.append((ip, await qos_rate_for_key(override)))
    await qos_sync(run_docker, active)


async def remove_peer_from_awg(public_key: str) -> None:
    if not is_valid_awg_public_key(public_key):
        raise RuntimeError("Некорректный public key для удаления peer")
    await run_docker([
        "remove-peer",
        "--public-key", public_key,
    ])
    _invalidate_peers_cache()


async def get_awg_peers() -> list[dict[str, str | datetime | int | None]]:
    now_ts = utc_now_naive().timestamp()
    expires_at = _peers_cache.get("expires_at")
    cached = _peers_cache.get("data")
    if cached is not None and isinstance(expires_at, (int, float)) and now_ts < expires_at:
        return list(cached)

    output = await run_docker(["show"])
    peers = parse_awg_show_output(output)
    _peers_cache["data"] = list(peers)
    _peers_cache["expires_at"] = now_ts + AWG_PEERS_CACHE_TTL_SECONDS
    return peers


async def sync_traffic_counters() -> int:
    peers = await get_awg_peers()
    return await sync_traffic_counters_from_runtime_peers(peers)


async def get_used_ips_from_awg() -> set[int]:
    peers = await get_awg_peers()
    used = set()
    for peer in peers:
        ip = peer.get("ip")
        if not ip:
            continue
        try:
            octet = int(ip.split(".")[-1])
            if FIRST_CLIENT_OCTET <= octet <= MAX_CLIENT_OCTET:
                used.add(octet)
        except ValueError:
            continue
    return used


def pick_free_ips(used: set[int], amount: int) -> list[str]:
    reserved = set(used)
    free_ips: list[str] = []
    for octet in range(FIRST_CLIENT_OCTET, MAX_CLIENT_OCTET + 1):
        if octet not in reserved:
            free_ips.append(f"{VPN_SUBNET_PREFIX}{octet}")
            reserved.add(octet)
            if len(free_ips) == amount:
                return free_ips
    raise RuntimeError("Свободные IP закончились")


async def count_free_ip_slots() -> int:
    used = await get_used_ips_from_awg()
    reserved = await get_reserved_ips_from_db()
    total_slots = MAX_CLIENT_OCTET - FIRST_CLIENT_OCTET + 1
    return total_slots - len(used | reserved)


def _is_managed_client_ip(ip: str | None) -> bool:
    if not ip:
        return False
    if not ip.startswith(VPN_SUBNET_PREFIX):
        return False
    try:
        octet = int(ip.split(".")[-1])
    except ValueError:
        return False
    return FIRST_CLIENT_OCTET <= octet <= MAX_CLIENT_OCTET


def _awg_settings() -> dict[str, str]:
    settings = {
        "Jc": AWG_JC,
        "Jmin": AWG_JMIN,
        "Jmax": AWG_JMAX,
        "S1": AWG_S1,
        "S2": AWG_S2,
        "S3": AWG_S3,
        "S4": AWG_S4,
        "H1": AWG_H1,
        "H2": AWG_H2,
        "H3": AWG_H3,
        "H4": AWG_H4,
        "I1": AWG_I1,
        "I2": AWG_I2,
        "I3": AWG_I3,
        "I4": AWG_I4,
        "I5": AWG_I5,
    }
    return {k: str(v).strip() for k, v in settings.items() if str(v).strip()}


def build_client_config(private_key: str, ip: str, psk_key: str) -> str:
    settings = "".join(f"{k} = {v}\n" for k, v in _awg_settings().items())
    return (
        f"[Interface]\n"
        f"Address = {ip}/32\n"
        f"DNS = {PRIMARY_DNS}, {SECONDARY_DNS}\n"
        f"PrivateKey = {private_key}\n"
        f"{settings}\n"
        f"[Peer]\n"
        f"PublicKey = {SERVER_PUBLIC_KEY}\n"
        f"PresharedKey = {psk_key}\n"
        f"AllowedIPs = {CLIENT_ALLOWED_IPS}\n"
        f"Endpoint = {SERVER_IP}\n"
        f"PersistentKeepalive = {PERSISTENT_KEEPALIVE}\n"
    )


def build_vpn_payload(
    client_private_key: str,
    client_public_key: str,
    client_ip: str,
    psk_key: str,
    *,
    device_num: int | None = None,
) -> dict[str, Any]:
    host, port = parse_server_host_port(SERVER_IP)
    subnet_address = ".".join(client_ip.split(".")[:3]) + ".0"
    settings = _awg_settings()
    config_text = build_client_config(client_private_key, client_ip, psk_key).replace(
        f"DNS = {PRIMARY_DNS}, {SECONDARY_DNS}", "DNS = $PRIMARY_DNS, $SECONDARY_DNS"
    )
    last_config_obj = {
        **settings,
        "allowed_ips": [item.strip() for item in CLIENT_ALLOWED_IPS.split(",")],
        "clientId": client_public_key,
        "client_ip": client_ip,
        "client_priv_key": client_private_key,
        "client_pub_key": client_public_key,
        "config": config_text,
        "hostName": host,
        "mtu": CLIENT_MTU,
        "persistent_keep_alive": PERSISTENT_KEEPALIVE,
        "port": port,
        "psk_key": psk_key,
        "server_pub_key": SERVER_PUBLIC_KEY,
    }
    profile_name = SERVER_NAME
    try:
        device_int = int(device_num) if device_num is not None else None
    except (TypeError, ValueError):
        device_int = None
    if device_int is not None and device_int > 0 and not re.search(r" #\d+$", profile_name):
        profile_name = f"{profile_name} #{device_int}"

    return {
        "containers": [
            {
                "awg": {
                    **settings,
                    "last_config": json.dumps(last_config_obj, ensure_ascii=False, indent=4),
                    "port": str(port),
                    "protocol_version": AWG_PROTOCOL_VERSION,
                    "subnet_address": subnet_address,
                    "transport_proto": AWG_TRANSPORT_PROTO,
                },
                "container": DOCKER_CONTAINER,
            }
        ],
        "defaultContainer": DOCKER_CONTAINER,
        "description": profile_name,
        "dns1": PRIMARY_DNS,
        "dns2": SECONDARY_DNS,
        "hostName": host,
        "nameOverriddenByUser": True,
    }


def encode_vpn_key(payload: dict[str, Any]) -> str:
    json_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(json_bytes)
    blob = len(json_bytes).to_bytes(4, "big") + compressed
    encoded = base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")
    return f"vpn://{encoded}"


async def _cleanup_legacy_bootstrap_protected_peers() -> tuple[int, int]:
    rows = await fetchall(
        "SELECT public_key, reason FROM protected_peers WHERE reason = 'bootstrap-existing-peer'"
    )
    if not rows:
        return 0, 0

    peers = await get_awg_peers()
    ip_by_key = {
        (peer.get("public_key") or "").strip(): (peer.get("ip") or "").strip() or None
        for peer in peers
        if (peer.get("public_key") or "").strip()
    }

    removed = 0
    normalized = 0
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        for public_key, _reason in rows:
            ip = ip_by_key.get(public_key)
            if _is_managed_client_ip(ip):
                await db.execute(
                    "DELETE FROM protected_peers WHERE public_key = ?",
                    (public_key,),
                )
                removed += 1
                logger.info(
                    "Снята legacy-защита с managed peer: %s ip=%s",
                    public_key,
                    ip or '-',
                )
            else:
                await db.execute(
                    "UPDATE protected_peers SET reason = 'bootstrap-system-peer' WHERE public_key = ?",
                    (public_key,),
                )
                normalized += 1
        await db.commit()
    finally:
        await db.close()
    return removed, normalized


async def bootstrap_protected_peers() -> int:
    removed_legacy, normalized_legacy = await _cleanup_legacy_bootstrap_protected_peers()
    if removed_legacy or normalized_legacy:
        logger.info(
            'Legacy protected peer sync: removed=%s normalized=%s',
            removed_legacy,
            normalized_legacy,
        )

    health = await db_health_info()
    if await count_protected_peers() > 0:
        return removed_legacy
    if health.get('valid_keys_count', 0) > 0:
        return removed_legacy

    peers = await get_awg_peers()
    added = 0
    protected = set(IGNORE_PEERS)
    for peer in peers:
        public_key = (peer.get('public_key') or '').strip()
        peer_ip = (peer.get('ip') or '').strip() or None
        if not public_key:
            continue
        if public_key in protected:
            await add_protected_peer(public_key, 'env-ignore-peer')
            added += 1
            continue
        if _is_managed_client_ip(peer_ip):
            logger.info(
                'Bootstrap: peer оставлен незащищённым для orphan-проверки: %s ip=%s',
                public_key,
                peer_ip or '-',
            )
            continue
        await add_protected_peer(public_key, 'bootstrap-system-peer')
        added += 1
    if added:
        logger.info('Добавлено protected peer при первом запуске: %s', added)
    return removed_legacy + added


async def _get_quarantined_public_keys() -> set[str]:
    rows = await fetchall(
        "SELECT public_key FROM protected_peers WHERE reason = 'orphan-quarantine' AND public_key IS NOT NULL AND TRIM(public_key) != ''"
    )
    return {row[0].strip() for row in rows if row and row[0]}


async def get_orphan_awg_peers() -> list[dict[str, str | None]]:
    awg_peers = await get_awg_peers()
    if not awg_peers:
        return []
    db_keys = await get_valid_db_public_keys()
    bot_managed_keys = await get_bot_managed_known_public_keys()
    protected = await get_protected_public_keys()
    protected.update(IGNORE_PEERS)
    return [
        peer
        for peer in awg_peers
        if peer["public_key"] not in db_keys
        and peer["public_key"] in bot_managed_keys
        and peer["public_key"] not in protected
    ]


async def _get_subscription_operation(db, operation_id: str) -> tuple[str, str, str] | None:
    async with db.execute(
        "SELECT previous_sub_until, new_until, status FROM subscription_operations WHERE operation_id = ?",
        (operation_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return str(row[0]), str(row[1]), str(row[2])


async def _upsert_subscription_operation_pending(
    db,
    operation_id: str,
    user_id: int,
    days: int,
    previous_sub_until: str,
    new_until: str,
    now_iso: str,
) -> None:
    await db.execute(
        """
        INSERT INTO subscription_operations (operation_id, user_id, days, previous_sub_until, new_until, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
        ON CONFLICT(operation_id) DO UPDATE SET
            user_id = excluded.user_id,
            days = excluded.days,
            previous_sub_until = excluded.previous_sub_until,
            new_until = excluded.new_until,
            updated_at = excluded.updated_at
        """,
        (operation_id, user_id, days, previous_sub_until, new_until, now_iso, now_iso),
    )


async def _mark_subscription_operation_applied(operation_id: str) -> None:
    db = await open_db()
    try:
        await db.execute(
            "UPDATE subscription_operations SET status = 'applied', updated_at = ? WHERE operation_id = ?",
            (utc_now_naive().isoformat(), operation_id),
        )
        await db.commit()
    finally:
        await db.close()


async def issue_subscription(user_id: int, days: int, silent: bool = False, operation_id: str | None = None) -> datetime:
    async with subscription_lock:
        now = utc_now_naive()
        created_peers: list[str] = []
        placeholders: list[str] = []
        await ensure_user_exists(user_id)

        db = await open_db()
        previous_sub_until = "0"
        new_until: datetime
        reused_operation = False
        placeholder_prefix = f"pending:{operation_id}:" if operation_id else None
        try:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("SELECT sub_until FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()

            current_until = None
            if row and row[0] != "0":
                previous_sub_until = row[0]
                try:
                    current_until = datetime.fromisoformat(row[0])
                except ValueError:
                    current_until = None

            if operation_id:
                op_row = await _get_subscription_operation(db, operation_id)
                if op_row:
                    stored_previous_sub_until, stored_new_until, op_status = op_row
                    if op_status == "applied":
                        await db.rollback()
                        return datetime.fromisoformat(stored_new_until)
                    reused_operation = True
                    previous_sub_until = stored_previous_sub_until
                    new_until = datetime.fromisoformat(stored_new_until)
                    await db.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (new_until.isoformat(), user_id))
                else:
                    new_until = current_until + timedelta(days=days) if current_until and current_until > now else now + timedelta(days=days)
                    await db.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (new_until.isoformat(), user_id))
                    await _upsert_subscription_operation_pending(
                        db,
                        operation_id,
                        user_id,
                        days,
                        previous_sub_until,
                        new_until.isoformat(),
                        now.isoformat(),
                    )
            else:
                new_until = current_until + timedelta(days=days) if current_until and current_until > now else now + timedelta(days=days)
                await db.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (new_until.isoformat(), user_id))

            async with db.execute(
                """
                SELECT COUNT(*)
                FROM keys
                WHERE user_id = ?
                  AND state = 'active'
                  AND public_key NOT LIKE 'pending:%'
                  AND public_key IS NOT NULL
                  AND TRIM(public_key) != ''
                  AND ip IS NOT NULL
                  AND TRIM(ip) != ''
                """,
                (user_id,),
            ) as cursor:
                valid_keys_count = (await cursor.fetchone())[0]

            if operation_id:
                async with db.execute(
                    """
                    SELECT public_key
                    FROM keys
                    WHERE user_id = ?
                      AND state = 'pending'
                      AND public_key LIKE ?
                    ORDER BY device_num
                    """,
                    (user_id, f"{placeholder_prefix}%"),
                ) as cursor:
                    placeholders = [r[0] for r in await cursor.fetchall()]
            else:
                placeholders = []

            missing_count = max(0, CONFIGS_PER_USER - valid_keys_count - len(placeholders))
            if missing_count > 0:
                used_ips_awg = await get_used_ips_from_awg()
                reserved_ips_db = await get_reserved_ips_from_db_conn(db)
                free_ips = pick_free_ips(used_ips_awg | reserved_ips_db, missing_count)

                async with db.execute(
                    """
                    SELECT device_num
                    FROM keys
                    WHERE user_id = ?
                      AND state != 'deleted'
                    ORDER BY device_num
                    """,
                    (user_id,),
                ) as cursor:
                    existing_nums = {r[0] for r in await cursor.fetchall()}

                next_device_nums: list[int] = []
                for n in range(1, CONFIGS_PER_USER + 1):
                    if n not in existing_nums:
                        next_device_nums.append(n)
                    if len(next_device_nums) == missing_count:
                        break

                if len(next_device_nums) != missing_count:
                    raise RuntimeError(
                        "Не хватает свободных device slots из-за незавершённых записей keys; требуется repair/cleanup состояния пользователя."
                    )

                reserved_rows: list[tuple[int, int, str, str, str, str]] = []
                reuse_deleted_rows: list[tuple[str, str, str, str, int, int]] = []
                for device_num, ip in zip(next_device_nums, free_ips, strict=False):
                    suffix = operation_id or str(uuid.uuid4())
                    placeholder_key = f"pending:{suffix}:{uuid.uuid4()}"
                    placeholders.append(placeholder_key)
                    reserved_rows.append((user_id, device_num, placeholder_key, "", ip, now.isoformat()))
                    reuse_deleted_rows.append((placeholder_key, "", ip, now.isoformat(), user_id, device_num))

                if reuse_deleted_rows:
                    await db.executemany(
                        """
                        UPDATE keys
                        SET public_key = ?,
                            config = ?,
                            ip = ?,
                            created_at = ?,
                            client_private_key = NULL,
                            psk_key = NULL,
                            vpn_key = NULL,
                            delete_reason = NULL,
                            bot_managed = 1,
                            state = 'pending',
                            state_updated_at = ?
                        WHERE user_id = ?
                          AND device_num = ?
                          AND state = 'deleted'
                        """,
                        [
                            (placeholder_key, config, ip, created_at, now.isoformat(), uid, device_num)
                            for placeholder_key, config, ip, created_at, uid, device_num in reuse_deleted_rows
                        ],
                    )

                insert_rows: list[tuple[int, int, str, str, str, str]] = []
                for row in reserved_rows:
                    uid, device_num, *_ = row
                    async with db.execute(
                        "SELECT 1 FROM keys WHERE user_id = ? AND device_num = ?",
                        (uid, device_num),
                    ) as cursor:
                        exists_row = await cursor.fetchone()
                    if not exists_row:
                        insert_rows.append(row)

                if insert_rows:
                    await db.executemany(
                        """
                        INSERT INTO keys (user_id, device_num, public_key, config, ip, created_at, bot_managed)
                        VALUES (?, ?, ?, ?, ?, ?, 1)
                        """,
                        insert_rows,
                    )
                await db.executemany(
                    "UPDATE keys SET state='pending', state_updated_at=? WHERE public_key = ?",
                    [(now.isoformat(), placeholder_key) for placeholder_key in placeholders],
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()

        generated_rows: list[tuple[str, str, str, str, str]] = []
        try:
            for placeholder_key in placeholders:
                db = await open_db()
                try:
                    async with db.execute(
                        "SELECT device_num, ip, state, rate_limit_mbit FROM keys WHERE public_key = ?",
                        (placeholder_key,),
                    ) as cursor:
                        row = await cursor.fetchone()
                finally:
                    await db.close()
                if not row:
                    raise RuntimeError("Не найдена зарезервированная запись ключа")
                _, ip, state, rate_limit_override = row
                if state != 'pending':
                    continue
                private_key, public_key = await generate_keypair()
                psk_key = await generate_psk()
                await add_peer_to_awg(public_key, ip, psk_key)
                await qos_set(run_docker, ip, await qos_rate_for_key(rate_limit_override), user_id)
                created_peers.append(public_key)
                generated_rows.append((placeholder_key, public_key, encrypt_text(private_key), encrypt_text(psk_key), ip))

            if generated_rows:
                db = await open_db()
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    for placeholder_key, public_key, private_key_enc, psk_key_enc, ip in generated_rows:
                        await db.execute(
                            """
                            UPDATE keys
                            SET public_key = ?,
                                config = '',
                                vpn_key = '',
                                client_private_key = ?,
                                psk_key = ?,
                                state = 'active',
                                state_updated_at = ?
                            WHERE public_key = ? AND ip = ?
                            """,
                            (public_key, private_key_enc, psk_key_enc, utc_now_naive().isoformat(), placeholder_key, ip),
                        )
                    await db.commit()
                finally:
                    await db.close()
                if user_id == ADMIN_ID:
                    for _, public_key, _, _, _ in generated_rows:
                        await add_protected_peer(public_key, 'admin-issued')

            if operation_id:
                await _mark_subscription_operation_applied(operation_id)
        except Exception:
            db = await open_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                if placeholder_prefix:
                    await db.execute(
                        "DELETE FROM keys WHERE user_id = ? AND (public_key LIKE ? OR state='pending' AND public_key LIKE ?)",
                        (user_id, f"{placeholder_prefix}%", f"{placeholder_prefix}%"),
                    )
                else:
                    await db.execute("DELETE FROM keys WHERE user_id = ? AND (public_key LIKE 'pending:%' OR state='pending')", (user_id,))
                await db.execute("UPDATE users SET sub_until = ? WHERE user_id = ?", (previous_sub_until, user_id))
                if operation_id:
                    await _upsert_subscription_operation_pending(
                        db,
                        operation_id,
                        user_id,
                        days,
                        previous_sub_until,
                        new_until.isoformat(),
                        utc_now_naive().isoformat(),
                    )
                await db.commit()
            finally:
                await db.close()
            for public_key in created_peers:
                try:
                    await remove_peer_from_awg(public_key)
                except Exception as remove_error:
                    logger.error("Rollback: не удалось удалить peer %s: %s", public_key, remove_error)
            raise

        await write_audit_log(user_id, "issue_subscription", f"days={days}; until={new_until.isoformat()}; silent={silent}; reused={int(reused_operation)}")
        return new_until


async def delete_user_device(user_id: int, device_num: int) -> dict[str, Any]:
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT id, public_key, ip
            FROM keys
            WHERE user_id = ?
              AND device_num = ?
              AND public_key NOT LIKE 'pending:%'
              AND state != 'deleted'
            """,
            (user_id, device_num),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.commit()
            await write_audit_log(user_id, "delete_user_device_noop", f"device_num={device_num}; reason=not_found")
            return {"status": "not_found", "removed_runtime": False}
        key_id, public_key, ip = int(row[0]), str(row[1]), str(row[2] or "")
        await db.execute(
            "UPDATE keys SET state='delete_pending', state_updated_at=?, delete_reason='admin_device_delete' WHERE id = ?",
            (utc_now_naive().isoformat(), key_id),
        )
        await db.commit()
    finally:
        await db.close()

    removed_runtime = False
    try:
        await remove_peer_from_awg(public_key)
        if ip:
            await qos_clear(run_docker, ip, user_id)
        removed_runtime = True
    except Exception:
        current_keys = {(peer.get("public_key") or "").strip() for peer in await get_awg_peers()}
        if public_key and public_key not in current_keys:
            removed_runtime = True
        else:
            db = await open_db()
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    "UPDATE keys SET state='active', state_updated_at=? WHERE id = ?",
                    (utc_now_naive().isoformat(), key_id),
                )
                await db.commit()
            finally:
                await db.close()
            raise

    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "UPDATE keys SET state='deleted', state_updated_at=? WHERE id = ?",
            (utc_now_naive().isoformat(), key_id),
        )
        await db.commit()
    finally:
        await db.close()
    await write_audit_log(user_id, "delete_user_device", f"device_num={device_num}; removed_runtime={int(removed_runtime)}")
    return {"status": "deleted", "removed_runtime": removed_runtime}


async def reissue_user_device(user_id: int, device_num: int) -> dict[str, Any]:
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT id, public_key, ip, psk_key
            FROM keys
            WHERE user_id = ?
              AND device_num = ?
              AND public_key NOT LIKE 'pending:%'
              AND state = 'active'
            """,
            (user_id, device_num),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.commit()
            await write_audit_log(user_id, "reissue_user_device_noop", f"device_num={device_num}; reason=not_found")
            return {"status": "not_found"}
        key_id, old_public_key, ip, old_psk_enc = int(row[0]), str(row[1]), str(row[2]), str(row[3] or "")
        await db.execute(
            "UPDATE keys SET state='reissue_pending', state_updated_at=? WHERE id = ?",
            (utc_now_naive().isoformat(), key_id),
        )
        await db.commit()
    finally:
        await db.close()

    old_psk = decrypt_text(old_psk_enc) if old_psk_enc else ""
    old_removed = False
    try:
        try:
            await remove_peer_from_awg(old_public_key)
            old_removed = True
        except Exception:
            current_keys = {(peer.get("public_key") or "").strip() for peer in await get_awg_peers()}
            if old_public_key and old_public_key not in current_keys:
                old_removed = True
            else:
                raise

        client_private_key, new_public_key = await generate_keypair()
        psk_key = await generate_psk()
        await add_peer_to_awg(new_public_key, ip, psk_key)
        config = build_client_config(client_private_key, ip, psk_key)
        vpn_key = encode_vpn_key(
            build_vpn_payload(client_private_key, new_public_key, ip, psk_key, device_num=device_num)
        )

        db = await open_db()
        try:
            now_iso = utc_now_naive().isoformat()
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """
                UPDATE keys
                SET public_key = ?,
                    client_private_key = ?,
                    psk_key = ?,
                    config = ?,
                    vpn_key = ?,
                    state = 'active',
                    state_updated_at = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    new_public_key,
                    encrypt_text(client_private_key),
                    encrypt_text(psk_key),
                    config,
                    vpn_key,
                    now_iso,
                    now_iso,
                    key_id,
                ),
            )
            await db.commit()
        finally:
            await db.close()
        await add_protected_peer(new_public_key, "admin-issued")
        await write_audit_log(user_id, "reissue_user_device", f"device_num={device_num}")
        return {"status": "reissued", "new_public_key": new_public_key, "old_public_key": old_public_key}
    except Exception:
        if "new_public_key" in locals():
            try:
                await remove_peer_from_awg(new_public_key)
            except Exception as rollback_error:
                logger.error("Rollback: не удалось удалить новый peer %s: %s", new_public_key, rollback_error)
        if old_removed and old_psk:
            try:
                await add_peer_to_awg(old_public_key, ip, old_psk)
            except Exception as restore_error:
                logger.error("Rollback: не удалось восстановить старый peer %s: %s", old_public_key, restore_error)
        db = await open_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "UPDATE keys SET state='active', state_updated_at=? WHERE id = ?",
                (utc_now_naive().isoformat(), key_id),
            )
            await db.commit()
        finally:
            await db.close()
        raise


async def revoke_user_access(user_id: int, only_if_expired: bool = False) -> int:
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        if only_if_expired:
            async with db.execute("SELECT sub_until FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
            if not row or row[0] == "0":
                await db.commit()
                return 0
            try:
                if datetime.fromisoformat(row[0]) > utc_now_naive():
                    await db.commit()
                    return 0
            except ValueError:
                pass

        await db.execute(
            """
            UPDATE keys
            SET state='revoke_pending',
                state_updated_at=?,
                delete_reason='revoke_expired_or_admin'
            WHERE user_id = ?
              AND public_key NOT LIKE 'pending:%'
              AND state != 'deleted'
            """,
            (utc_now_naive().isoformat(), user_id),
        )
        async with db.execute(
            "SELECT public_key, ip FROM keys WHERE user_id = ? AND public_key NOT LIKE 'pending:%' AND state IN ('active','delete_pending','revoke_pending')",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        await db.commit()
    finally:
        await db.close()

    public_keys = [(row[0], row[1]) for row in rows]
    removed_keys: list[str] = []
    failed_remove: list[str] = []
    for public_key, ip in public_keys:
        try:
            await remove_peer_from_awg(public_key)
            if ip:
                await qos_clear(run_docker, ip, user_id)
            removed_keys.append(public_key)
        except Exception as e:
            current_keys = {(peer.get("public_key") or "").strip() for peer in await get_awg_peers()}
            if public_key and public_key not in current_keys:
                removed_keys.append(public_key)
                logger.info("Peer %s уже отсутствует в AWG при revoke, считаем удаленным", public_key)
            else:
                logger.error("Ошибка удаления peer %s для user %s: %s", public_key, user_id, e)
                failed_remove.append(public_key)

    if failed_remove:
        await write_audit_log(
            user_id,
            "revoke_user_access_pending",
            f"failed_peers={len(failed_remove)}; total_found={len(public_keys)}",
        )
        raise RuntimeError(f"Не удалось удалить {len(failed_remove)} peer в AWG. Пользователь оставлен в состоянии revoke_pending.")

    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        if removed_keys:
            await db.executemany(
                "UPDATE keys SET state='deleted', state_updated_at=? WHERE public_key = ?",
                [(utc_now_naive().isoformat(), key) for key in removed_keys],
            )
        async with db.execute(
            "SELECT COUNT(*) FROM keys WHERE user_id = ? AND public_key NOT LIKE 'pending:%' AND state='revoke_pending'",
            (user_id,),
        ) as cursor:
            still_pending = (await cursor.fetchone())[0]
        if still_pending > 0:
            await db.commit()
            await write_audit_log(user_id, "revoke_user_access_retry_needed", f"pending_keys={still_pending}")
            raise RuntimeError("Не все peer удалены при revoke, требуется повторный запуск.")
        await db.execute("DELETE FROM keys WHERE user_id = ?", (user_id,))
        await db.execute("UPDATE users SET sub_until = '0' WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()

    await write_audit_log(user_id, "revoke_user_access", f"removed_peers={len(removed_keys)}; total_found={len(public_keys)}")
    return len(removed_keys)


async def delete_user_everywhere(user_id: int) -> tuple[int, int]:
    db = await open_db()
    try:
        async with db.execute(
            "SELECT public_key, ip FROM keys WHERE user_id = ? AND public_key NOT LIKE 'pending:%' AND state != 'deleted'",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    finally:
        await db.close()

    public_keys = [(row[0], row[1]) for row in rows]
    removed_keys: list[str] = []
    failed_remove: list[str] = []
    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "UPDATE keys SET state='delete_pending', state_updated_at=?, delete_reason='user_delete' WHERE user_id = ? AND state != 'deleted'",
            (utc_now_naive().isoformat(), user_id),
        )
        await db.commit()
    finally:
        await db.close()

    for public_key, ip in public_keys:
        try:
            await remove_peer_from_awg(public_key)
            if ip:
                await qos_clear(run_docker, ip, user_id)
            removed_keys.append(public_key)
        except Exception as e:
            current_keys = {(peer.get("public_key") or "").strip() for peer in await get_awg_peers()}
            if public_key and public_key not in current_keys:
                removed_keys.append(public_key)
                logger.info("Peer %s уже отсутствует в AWG, считаем удаленным", public_key)
            else:
                logger.error("Не удалось удалить peer %s: %s", public_key, e)
                failed_remove.append(public_key)

    if failed_remove:
        await write_audit_log(
            user_id,
            "delete_user_everywhere_pending",
            f"failed_peers={len(failed_remove)}",
        )
        raise RuntimeError(f"Не удалось удалить {len(failed_remove)} peer в AWG. Пользователь оставлен в состоянии delete_pending.")

    db = await open_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        if removed_keys:
            await db.executemany(
                "UPDATE keys SET state='deleted', state_updated_at=? WHERE public_key = ?",
                [(utc_now_naive().isoformat(), key) for key in removed_keys],
            )
        async with db.execute(
            "SELECT COUNT(*) FROM keys WHERE user_id = ? AND public_key NOT LIKE 'pending:%' AND state='delete_pending'",
            (user_id,),
        ) as cursor:
            still_pending = (await cursor.fetchone())[0]
        if still_pending > 0:
            await db.commit()
            await write_audit_log(user_id, "delete_user_everywhere_retry_needed", f"pending_keys={still_pending}")
            raise RuntimeError("Не все peer удалены, требуется повторный запуск delete.")
        cur_keys = await db.execute("DELETE FROM keys WHERE user_id = ?", (user_id,))
        cur_users = await db.execute(
            "DELETE FROM users WHERE user_id = ? AND NOT EXISTS (SELECT 1 FROM keys WHERE keys.user_id = users.user_id)",
            (user_id,),
        )
        await db.commit()
        affected = (cur_keys.rowcount or 0) + (cur_users.rowcount or 0) + len(removed_keys)
    finally:
        await db.close()

    await write_audit_log(user_id, "delete_user_everywhere", f"removed_peers={len(removed_keys)}")
    return len(removed_keys), affected


async def cleanup_expired_subscriptions() -> int:
    db = await open_db()
    try:
        async with db.execute("SELECT user_id, sub_until FROM users WHERE sub_until != '0'") as cursor:
            rows = await cursor.fetchall()
    finally:
        await db.close()

    now = utc_now_naive()
    expired_users: list[int] = []
    for user_id, sub_until in rows:
        try:
            if datetime.fromisoformat(sub_until) <= now:
                expired_users.append(user_id)
        except ValueError:
            expired_users.append(user_id)

    cleaned = 0
    for user_id in expired_users:
        try:
            removed = await revoke_user_access(user_id, only_if_expired=True)
            cleaned += int(removed > 0)
        except Exception as e:
            logger.exception("Ошибка cleanup user_id=%s: %s", user_id, e)
    return cleaned


async def expired_subscriptions_worker(cleanup_interval_seconds: int) -> None:
    while True:
        try:
            cleaned = await cleanup_expired_subscriptions()
            if cleaned:
                logger.info("Фоновая очистка завершена. Удалено просроченных: %s", cleaned)
        except Exception as e:
            logger.exception("Ошибка фоновой очистки: %s", e)
        await asyncio.sleep(cleanup_interval_seconds)


async def reconcile_pending_awg_state() -> dict[str, int]:
    peers = await get_awg_peers()
    awg_keys = {(peer.get("public_key") or "").strip() for peer in peers if (peer.get("public_key") or "").strip()}
    now_iso = utc_now_naive().isoformat()
    db = await open_db()
    stats = {"activated": 0, "deleted": 0, "marked_manual": 0, "awg_removed": 0}
    users_for_finalize: set[int] = set()
    finalize_intent: dict[int, set[str]] = {}
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT id, user_id, public_key, state, created_at, COALESCE(delete_reason, '')
            FROM keys
            WHERE state IN ('pending', 'revoke_pending', 'delete_pending')
            """
        ) as cursor:
            rows = await cursor.fetchall()

        for key_id, user_id, public_key, state, created_at, delete_reason in rows:
            key = (public_key or "").strip()
            if state == "pending":
                if key.startswith("pending:"):
                    stale = False
                    try:
                        stale = (utc_now_naive() - datetime.fromisoformat(str(created_at))).total_seconds() > PENDING_KEY_TTL_SECONDS
                    except Exception:
                        stale = True
                    if stale:
                        await db.execute(
                            "UPDATE keys SET state='needs_manual_repair', delete_reason='pending_stuck', state_updated_at=? WHERE id=?",
                            (now_iso, key_id),
                        )
                        stats["marked_manual"] += 1
                    continue
                if key in awg_keys:
                    await db.execute("UPDATE keys SET state='active', state_updated_at=? WHERE id=?", (now_iso, key_id))
                    stats["activated"] += 1
                else:
                    await db.execute(
                        "UPDATE keys SET state='needs_manual_repair', delete_reason='pending_missing_in_awg', state_updated_at=? WHERE id=?",
                        (now_iso, key_id),
                    )
                    stats["marked_manual"] += 1
                continue

            if key and key in awg_keys:
                try:
                    await remove_peer_from_awg(key)
                    stats["awg_removed"] += 1
                except Exception:
                    await db.execute(
                        "UPDATE keys SET state='needs_manual_repair', delete_reason='pending_remove_failed', state_updated_at=? WHERE id=?",
                        (now_iso, key_id),
                    )
                    stats["marked_manual"] += 1
                    continue
            await db.execute("UPDATE keys SET state='deleted', state_updated_at=? WHERE id=?", (now_iso, key_id))
            stats["deleted"] += 1
            user_id_int = int(user_id)
            users_for_finalize.add(user_id_int)
            reason = str(delete_reason or "").strip() or "unknown"
            finalize_intent.setdefault(user_id_int, set()).add(reason)

        for user_id in users_for_finalize:
            async with db.execute(
                "SELECT state FROM keys WHERE user_id=?",
                (user_id,),
            ) as cursor:
                state_rows = [str(row[0]) for row in await cursor.fetchall()]
            if not state_rows:
                continue
            state_set = set(state_rows)
            has_live_keys = any(
                state in {"active", "pending", "revoke_pending", "delete_pending"}
                for state in state_set
            )
            if has_live_keys:
                continue
            if "needs_manual_repair" in state_set:
                continue
            if any(state != "deleted" for state in state_set):
                await db.execute(
                    "INSERT INTO audit_log (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, "reconcile_user_finalize_skipped", f"unexpected_states={','.join(sorted(state_set))}", now_iso),
                )
                continue

            intents = finalize_intent.get(user_id, set())
            if "user_delete" in intents:
                await db.execute("DELETE FROM keys WHERE user_id=?", (user_id,))
                await db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
                continue
            if "revoke_expired_or_admin" in intents:
                await db.execute("DELETE FROM keys WHERE user_id=?", (user_id,))
                await db.execute("UPDATE users SET sub_until='0' WHERE user_id=?", (user_id,))
                continue

            await db.execute(
                "INSERT INTO audit_log (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                (user_id, "reconcile_user_finalize_skipped", f"ambiguous_intent={','.join(sorted(intents)) or 'none'}", now_iso),
            )
        await db.commit()
    finally:
        await db.close()
    await sync_qos_state()
    await denylist_sync(run_docker)
    return stats


async def reconcile_active_awg_state() -> dict[str, int]:
    db = await open_db()
    try:
        async with db.execute(
            """
            SELECT public_key, ip, psk_key, user_id, rate_limit_mbit
            FROM keys
            WHERE state='active'
              AND public_key NOT LIKE 'pending:%'
              AND public_key IS NOT NULL
              AND TRIM(public_key) != ''
              AND ip IS NOT NULL
              AND TRIM(ip) != ''
            """
        ) as cursor:
            rows = await cursor.fetchall()
    finally:
        await db.close()

    peers = await get_awg_peers()
    awg_keys = {(peer.get("public_key") or "").strip() for peer in peers if (peer.get("public_key") or "").strip()}
    stats = {"restored": 0, "skipped_invalid_secret": 0, "already_present": 0, "failed": 0}

    for public_key, ip, psk_key_enc, user_id, rate_limit_override in rows:
        public_key = str(public_key).strip()
        ip = str(ip).strip()
        if not public_key or not ip:
            continue
        if public_key in awg_keys:
            stats["already_present"] += 1
            continue
        try:
            psk_key = decrypt_text(psk_key_enc)
            if not psk_key:
                stats["skipped_invalid_secret"] += 1
                logger.error("Active reconcile skipped: empty psk for key=%s", public_key)
                continue
            await add_peer_to_awg(public_key, ip, psk_key)
            await qos_set(run_docker, ip, await qos_rate_for_key(rate_limit_override), int(user_id))
            stats["restored"] += 1
        except Exception as error:
            stats["failed"] += 1
            logger.error("Active reconcile failed for key=%s ip=%s: %s", public_key, ip, error)
    return stats
