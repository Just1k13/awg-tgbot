from config import get_download_url, get_support_username


def get_instruction_text() -> str:
    download_url = get_download_url()
    support_username = get_support_username()
    return (
        "📖 <b>Инструкция</b>\n\n"
        "1. Нажмите <b>💳 Купить / Продлить</b>\n"
        "2. Выберите тариф: <b>7 дней</b> или <b>30 дней</b>\n"
        "3. Оплатите доступ в Telegram Stars\n"
        "4. После оплаты откройте <b>🔑 Конфиги</b>\n"
        "5. Скопируйте ключ доступа или скачайте <code>.conf</code> файл\n"
        f"6. Установите приложение <a href='{download_url}'>Amnezia</a>\n"
        "7. В приложении можно:\n"
        "   • вставить ключ доступа\n"
        "   • или импортировать <code>.conf</code> файл\n\n"
        "🔑 <b>Где взять ключ?</b>\n"
        "Он находится в разделе <b>🔑 Конфиги</b>.\n\n"
        f"🆘 <b>Поддержка:</b> {support_username}"
    )
