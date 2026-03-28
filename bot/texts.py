from config import get_download_url, get_support_username


def get_instruction_text() -> str:
    download_url = get_download_url()
    support_username = get_support_username()
    return (
        "📖 <b>Как подключиться</b>\n\n"
        "1. Нажмите <b>💳 Оформить доступ</b>\n"
        "2. Выберите срок: <b>7 дней</b> или <b>30 дней</b>\n"
        "3. Оплатите через Telegram Stars\n"
        "4. Нажмите <b>📲 Подключить устройство</b>\n\n"
        "✅ <b>Рекомендуемый способ</b>\n"
        "• выберите устройство\n"
        "• скопируйте ключ быстрого подключения\n"
        "• откройте Amnezia и вставьте ключ\n\n"
        "🛠 <b>Если нужен ручной способ</b>\n"
        "• откройте устройство\n"
        "• запросите файл <code>.conf</code>\n"
        "• импортируйте файл в приложении\n\n"
        f"📥 Скачать приложение: <a href='{download_url}'>Amnezia</a>\n\n"
        f"🆘 <b>Поддержка:</b> {support_username}\n"
        "Обычно отвечаем за 5–15 минут."
    )
