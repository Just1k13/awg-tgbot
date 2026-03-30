from config import get_download_url, get_support_username
from content_settings import get_text


async def get_instruction_text() -> str:
    download_url = get_download_url()
    support_username = get_support_username()
    if support_username:
        support_line = f"🆘 <b>Поддержка:</b> {support_username}"
    else:
        support_line = await get_text("support_unavailable")
    body = await get_text("instruction_body", download_url=download_url)
    return f"{body}\n\n{support_line}"
