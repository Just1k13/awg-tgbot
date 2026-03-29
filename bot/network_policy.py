from __future__ import annotations

import ipaddress
import socket

from config import QOS_ENABLED, QOS_STRICT, logger
from content_settings import get_setting
from database import get_metric, increment_metric, write_audit_log


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def parse_cidrs(raw: str) -> list[str]:
    cidrs: list[str] = []
    for item in _parse_csv(raw):
        network = ipaddress.ip_network(item, strict=False)
        cidrs.append(str(network))
    return cidrs


def resolve_domains(domains_raw: str) -> list[str]:
    resolved: set[str] = set()
    for domain in _parse_csv(domains_raw):
        for family, _, _, _, sockaddr in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM):
            if family == socket.AF_INET:
                resolved.add(f"{sockaddr[0]}/32")
    return sorted(resolved)


async def qos_rate_for_key(rate_limit_mbit: int | None) -> int:
    if rate_limit_mbit:
        return int(rate_limit_mbit)
    return int(await get_setting("DEFAULT_KEY_RATE_MBIT", int) or 100)


async def qos_set(run_docker, ip: str, rate_mbit: int, user_id: int) -> None:
    if not QOS_ENABLED:
        return
    try:
        await run_docker(["qos-set", "--ip", ip, "--rate-mbit", str(rate_mbit)])
    except Exception as e:
        await increment_metric("qos_errors")
        await write_audit_log(user_id, "qos_set_failed", f"ip={ip}; rate={rate_mbit}; error={str(e)[:200]}")
        if QOS_STRICT:
            raise
        logger.warning("qos_set failed for ip=%s: %s", ip, e)


async def qos_clear(run_docker, ip: str, user_id: int) -> None:
    if not QOS_ENABLED:
        return
    try:
        await run_docker(["qos-clear", "--ip", ip])
    except Exception as e:
        await increment_metric("qos_errors")
        await write_audit_log(user_id, "qos_clear_failed", f"ip={ip}; error={str(e)[:200]}")
        if QOS_STRICT:
            raise
        logger.warning("qos_clear failed for ip=%s: %s", ip, e)


async def qos_sync(run_docker, active_ips_with_rate: list[tuple[str, int]]) -> None:
    if not QOS_ENABLED:
        return
    payload = "\n".join(f"{ip},{rate}" for ip, rate in active_ips_with_rate)
    try:
        await run_docker(["qos-sync"], input_data=payload)
    except Exception as e:
        await increment_metric("qos_errors")
        if QOS_STRICT:
            raise
        logger.warning("qos_sync failed: %s", e)


async def denylist_sync(run_docker) -> None:
    enabled = int(await get_setting("EGRESS_DENYLIST_ENABLED", int) or 0) == 1
    if not enabled:
        return
    mode = str(await get_setting("EGRESS_DENYLIST_MODE", str) or "soft").strip().lower()
    domains = str(await get_setting("EGRESS_DENYLIST_DOMAINS", str) or "")
    cidrs = str(await get_setting("EGRESS_DENYLIST_CIDRS", str) or "")
    resolved = resolve_domains(domains)
    cidr_values = parse_cidrs(cidrs)
    payload = "\n".join(sorted(set(resolved + cidr_values)))
    try:
        await run_docker(["denylist-sync"], input_data=payload)
    except Exception as e:
        await increment_metric("denylist_errors")
        if mode == "strict":
            raise
        logger.warning("denylist_sync failed in soft mode: %s", e)


async def policy_metrics() -> dict[str, int]:
    return {
        "qos_errors": await get_metric("qos_errors"),
        "denylist_errors": await get_metric("denylist_errors"),
    }
