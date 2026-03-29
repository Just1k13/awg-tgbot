from config import get_download_url, get_support_username


def get_instruction_text() -> str:
    download_url = get_download_url()
    support_username = get_support_username()
    support_line = (
        f"🆘 <b>Поддержка:</b> {support_username}"
        if support_username
        else "🆘 <b>Поддержка:</b> временно не настроена. Напишите администратору сервиса."
    )
    return (
        "📖 <b>Как подключиться</b>\n\n"
        "1. Нажмите <b>💳 Оплатить доступ</b>\n"
        "2. Выберите тариф: <b>7 дней</b> или <b>30 дней</b>\n"
        "3. После оплаты дождитесь статуса <b>«Доступ готов»</b>\n"
        "4. Выберите ваше устройство и скопируйте <code>vpn://</code> ключ\n"
        f"5. Установите <a href='{download_url}'>Amnezia</a> и импортируйте ключ\n"
        "6. <code>.conf</code> файл можно взять отдельно (для продвинутой настройки)\n\n"
        "Если активация задержалась — нажмите <b>«Проверить статус активации»</b>.\n\n"
        "🔑 <b>Где получить ключ?</b>\n"
        "В разделе <b>🔑 Подключение</b>.\n\n"
        + support_line
    )
