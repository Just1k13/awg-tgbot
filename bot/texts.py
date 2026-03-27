from config import get_download_url, get_support_username


def get_instruction_text() -> str:
    download_url = get_download_url()
    support_username = get_support_username()
    return (
        "📖 <b>Как подключиться</b>\n\n"
        "1) Откройте <b>💳 Купить / Продлить</b> и оплатите доступ.\n"
        "2) Перейдите в <b>📱 Мои устройства</b>.\n"
        "3) Выберите устройство и получите:\n"
        "   • <code>vpn://</code> — быстрый импорт в Amnezia;\n"
        "   • <code>.conf</code> — ручной импорт в WireGuard-клиенты.\n"
        f"4) Если приложения нет — установите <a href='{download_url}'>Amnezia</a>.\n\n"
        "💡 <b>Что выбрать?</b>\n"
        "• Amnezia → используйте <code>vpn://</code>\n"
        "• Другие клиенты → используйте <code>.conf</code>\n\n"
        f"🆘 <b>Поддержка:</b> {support_username}"
    )
