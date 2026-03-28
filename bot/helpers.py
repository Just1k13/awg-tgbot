
import base64
import html
from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)



def subscription_is_active(dt_str: str | None) -> bool:
    if not dt_str or dt_str == "0":
        return False
    try:
        return datetime.fromisoformat(dt_str) > utc_now_naive()
    except ValueError:
        return False


def get_status_text(dt_str: str | None) -> tuple[str, str]:
    if not dt_str or dt_str == "0":
        return "🔴 Не активен", "Доступ отсутствует"
    try:
        sub_dt = datetime.fromisoformat(dt_str)
        if sub_dt > utc_now_naive():
            return "🟢 Активен", sub_dt.strftime("%d.%m.%Y %H:%M")
        return "🔴 Истек", sub_dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return "⚠️ Ошибка", "Некорректная дата"


def format_remaining_time(dt_str: str | None) -> str:
    if not dt_str or dt_str == "0":
        return "0 дн."
    try:
        delta = datetime.fromisoformat(dt_str) - utc_now_naive()
        if delta.total_seconds() <= 0:
            return "0 дн."
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        if days > 0:
            return f"{days} дн. {hours} ч."
        if hours > 0:
            return f"{hours} ч. {minutes} мин."
        return f"{minutes} мин."
    except ValueError:
        return "неизвестно"


def parse_server_host_port(server_ip: str) -> tuple[str, int]:
    host, port_str = server_ip.rsplit(":", 1)
    return host, int(port_str)


def is_valid_awg_public_key(value: str) -> bool:
    if not value:
        return False
    try:
        raw = base64.b64decode(value, validate=True)
        return len(raw) == 32
    except Exception:
        return False


def format_tg_username(username: str | None) -> str:
    return f"@{username}" if username else "не указан"


def escape_html(value: str | None) -> str:
    return html.escape(value or "не указано")
