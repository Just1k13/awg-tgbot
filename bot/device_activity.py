from __future__ import annotations

from datetime import datetime, timedelta

RECENT_HANDSHAKE_WINDOW = timedelta(hours=24)


def classify_handshake_recency(last_handshake_at: datetime | None, now: datetime) -> str:
    if last_handshake_at is None:
        return "no_data"
    if now - last_handshake_at <= RECENT_HANDSHAKE_WINDOW:
        return "recent"
    return "stale"


def format_handshake_timestamp(last_handshake_at: datetime | None) -> str:
    if last_handshake_at is None:
        return "—"
    return last_handshake_at.strftime("%d.%m %H:%M")


def render_device_activity_line(
    *,
    device_num: int,
    has_runtime_peer: bool,
    last_handshake_at: datetime | None,
    runtime_available: bool,
    now: datetime,
) -> str:
    prefix = f"• Устройство {device_num}:"
    if not runtime_available:
        return f"{prefix} активность не определена"
    if not has_runtime_peer:
        return f"{prefix} нет данных"
    if last_handshake_at is None:
        return f"{prefix} ещё не подключалось"

    recency = classify_handshake_recency(last_handshake_at, now)
    stamp = format_handshake_timestamp(last_handshake_at)
    if recency == "recent":
        return f"{prefix} активно недавно ({stamp})"
    return f"{prefix} давно не подключалось ({stamp})"
