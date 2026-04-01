from content_settings import get_setting, get_text


async def is_purchase_maintenance_enabled() -> bool:
    return int(await get_setting("MAINTENANCE_MODE", int) or 0) == 1


async def get_purchase_maintenance_text() -> str:
    return await get_text("maintenance_purchase_unavailable")
