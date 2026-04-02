from __future__ import annotations


def format_bytes_compact(value: int | None) -> str:
    if value is None:
        return "0 B"
    size = max(0, int(value))
    units = ("B", "KB", "MB", "GB", "TB")
    if size < 1024:
        return f"{size} B"
    amount = float(size)
    unit_index = 0
    while amount >= 1024 and unit_index < len(units) - 1:
        amount /= 1024.0
        unit_index += 1
    if amount >= 100 or unit_index == 0:
        return f"{amount:.0f} {units[unit_index]}"
    return f"{amount:.1f} {units[unit_index]}"


def render_device_traffic_line(device_num: int, rx_bytes_total: int, tx_bytes_total: int) -> str:
    total_bytes = max(0, int(rx_bytes_total)) + max(0, int(tx_bytes_total))
    return (
        f"• Устройство {device_num} — "
        f"↓ {format_bytes_compact(rx_bytes_total)} / "
        f"↑ {format_bytes_compact(tx_bytes_total)} / "
        f"Σ {format_bytes_compact(total_bytes)}"
    )
