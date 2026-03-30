from config import get_download_url, get_support_username
from content_settings import get_setting, get_text
from helpers import escape_html


async def get_instruction_text() -> str:
    download_url = get_download_url()
    body = await get_text("instruction_body", download_url=download_url)
    return f"{body}\n\n{await get_support_short_text()}"


async def get_support_short_text() -> str:
    support_username = get_support_username()
    if not support_username:
        return await get_text("support_unavailable")
    return await get_text("support_short", support_username=escape_html(support_username))


async def get_support_full_text() -> str:
    support_username = get_support_username()
    if not support_username:
        return await get_text("support_unavailable")
    return await get_text("support_contact", support_username=escape_html(support_username))


async def get_instruction_with_policy_text() -> str:
    text = await get_instruction_text()
    if int(await get_setting("TORRENT_POLICY_TEXT_ENABLED", int) or 0) != 1:
        return text
    return f"{text}\n\n{await get_text('policy_torrent')}\n{await get_text('policy_sensitive')}"


async def get_activation_status_text(status: str | None) -> str:
    if status == "ready":
        return await get_text("activation_status_ready")
    if status in {"provisioning", "payment_received"}:
        return await get_text("activation_status_pending")
    return await get_text("activation_status_delayed")


async def get_payment_result_text(status: str) -> str:
    if status == "ready":
        return f"{await get_text('payment_success')}\n{await get_text('payment_next_step')}"
    return f"{await get_text('payment_pending')}\n\n{await get_text('payment_pending_followup')}"
