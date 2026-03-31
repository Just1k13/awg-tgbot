from __future__ import annotations

import string
from typing import Any, Callable

from config import (
    DEFAULT_KEY_RATE_MBIT,
    EGRESS_DENYLIST_CIDRS,
    EGRESS_DENYLIST_DOMAINS,
    EGRESS_DENYLIST_ENABLED,
    EGRESS_DENYLIST_MODE,
    EGRESS_DENYLIST_REFRESH_MINUTES,
    REFERRAL_ENABLED,
    REFERRAL_INVITEE_BONUS_DAYS,
    REFERRAL_INVITER_BONUS_DAYS,
    TORRENT_POLICY_TEXT_ENABLED,
    VPN_SUBNET_PREFIX,
    logger,
)
from database import get_app_setting, get_text_override

TEXT_DEFAULTS: dict[str, str] = {
    "start": (
        "🌐 <b>Свободный интернет</b>\n\n"
        "1) Нажмите <b>💳 Купить / Продлить</b>\n"
        "2) После оплаты откройте <b>🔑 Подключение</b>\n"
        "3) Импортируйте <code>vpn://</code> в Amnezia\n\n"
        "Проверить срок доступа можно в <b>👤 Профиль</b>."
    ),
    "support_unavailable": "🆘 Поддержка временно не настроена. Попробуйте позже или напишите администратору сервиса.",
    "support_short": "🆘 <b>Поддержка:</b> {support_username}",
    "unknown_slash": "Неизвестная команда. Используйте кнопки меню или /start.",
    "buy_menu": "💳 <b>Выберите срок доступа</b>\n\nВ подписку входит доступ до <b>2 устройств</b>.\n\n{price_lines}",
    "renew_menu": "🔄 <b>У вас уже есть активная подписка</b>\n⏳ Осталось: <b>{remaining}</b>\n\n💡 Можно продлить заранее.\n\n{price_lines}",
    "guide_hint": "Если активация задержалась — нажмите «Проверить статус активации».",
    "instruction_body": (
        "📖 <b>Как подключиться</b>\n\n"
        "1. Нажмите <b>💳 Оплатить доступ</b>\n"
        "2. Выберите тариф: <b>7 дней</b> или <b>30 дней</b>\n"
        "3. После оплаты дождитесь статуса <b>«Доступ готов»</b>\n"
        "4. Выберите устройство и скопируйте <code>vpn://</code> ключ\n"
        "5. Установите <a href='{download_url}'>Amnezia</a> и импортируйте ключ\n"
        "6. <code>.conf</code> можно запросить отдельно для ручной настройки\n\n"
        "Если активация задержалась — нажмите <b>«Проверить статус активации»</b>.\n\n"
        "🔑 <b>Где получить ключ?</b>\n"
        "В разделе <b>🔑 Подключение</b>."
    ),
    "support_contact": "🆘 <b>Поддержка</b>\n\nПо всем вопросам пишите: <b>{support_username}</b>",
    "profile_screen": (
        "👤 <b>Профиль</b>\n\n"
        "🆔 <b>ID:</b> <code>{user_id}</code>\n"
        "👤 <b>Имя:</b> {first_name}\n"
        "✈️ <b>Telegram:</b> {tg_username}\n"
        "📌 <b>Подписка:</b> {status_text}\n"
        "📅 <b>Действует до:</b> {until_text}\n"
        "⏳ <b>Осталось:</b> {remaining}\n"
        "🔑 <b>Подключение:</b> {connection_status}\n"
        "💸 <b>Последний платёж:</b> {payment_line}\n"
        "🚦 <b>Последняя активация:</b> {activation_line}\n\n"
        "➡️ <b>Дальше:</b> {next_step}\n"
        "{support_line}"
    ),
    "configs_empty": (
        "🔑 <b>Подключение</b>\n\n"
        "У вас пока нет активного подключения.\n"
        "Сначала оформите или продлите подписку.\n\n"
        "Если нужна помощь — откройте инструкцию ниже."
    ),
    "configs_menu": (
        "🔑 <b>Подключение</b>\n\n"
        "Выберите устройство для подключения.\n\n"
        "Сначала я отправлю:\n"
        "• <code>vpn://</code> — быстрый импорт в Amnezia.\n\n"
        "Файл <code>.conf</code> можно запросить отдельно, если нужен ручной вариант настройки."
    ),
    "config_not_found": "Не удалось найти ключ для выбранного устройства. Попробуйте открыть раздел «Подключение» ещё раз.",
    "config_vpn_ready": "🔐 <b>vpn:// для устройства {device_num}</b>\n\n<code>{vpn_key}</code>\n\nПодходит для быстрого импорта в Amnezia.",
    "config_vpn_missing": "Для выбранного устройства не удалось собрать ключ импорта. Напишите в поддержку или попросите администратора перевыдать доступ.",
    "config_conf_not_found": "Не удалось найти .conf для выбранного устройства. Откройте раздел «Подключение» ещё раз.",
    "config_conf_caption": "📄 Файл подключения для устройства {device_num}",
    "config_conf_sent": "Файл отправлен ✅ Можно вернуться к списку устройств:",
    "config_conf_missing": "Для выбранного устройства не удалось собрать .conf. Напишите в поддержку или попросите администратора перевыдать доступ.",
    "config_invalid_device": "Некорректный выбор устройства.",
    "config_invalid_conf_request": "Некорректный запрос .conf.",
    "callback_message_unavailable": "Сообщение недоступно.",
    "activation_status_no_payments": "Платежей пока нет. Нажмите «💳 Оплатить доступ», чтобы начать.",
    "payment_success": "🎉 <b>Доступ готов</b>\n\nМожно сразу открыть <b>🔑 Подключение</b> и импортировать ключ.",
    "payment_pending": "⏳ Платёж принят. Доступ выпускается в фоне (обычно до минуты).",
    "payment_error": "❌ Платёж получен, но активация задержалась. Попробуйте позже или обратитесь в поддержку.",
    "payment_received": "✅ Оплата получена.",
    "payment_provisioning_started": "⏳ Доступ выпускается. Обычно это занимает до минуты.",
    "payment_payload_error": "Ошибка оплаты: неизвестный payload.",
    "payment_currency_error": "Ошибка оплаты: неверная валюта.",
    "payment_amount_error": "Ошибка оплаты: неверная сумма.",
    "payment_already_processed": "✅ Этот платёж уже был обработан.",
    "payment_already_provisioning": "⏳ Платёж уже обрабатывается. Подождите немного и проверьте профиль или конфиги.",
    "payment_next_step": "Следующий шаг: нажмите «🔑 Получить подключение» и импортируйте vpn:// в Amnezia.",
    "payment_pending_followup": "Если статус долго не меняется, нажмите «⏱ Проверить статус активации» или напишите в поддержку.",
    "payment_recovery_ready": "✅ Доступ готов. Платёж успешно применён в фоне. Откройте «🔑 Подключение».",
    "precheckout_unavailable": "Сервис временно недоступен для активации. Попробуйте чуть позже.",
    "activation_status_ready": "✅ Доступ готов. Можно открывать «🔑 Подключение».",
    "activation_status_pending": "⏳ Оплата получена, доступ выпускается. Обычно до минуты.",
    "activation_status_delayed": "⚠️ Активация задержалась. Проверьте ещё раз через минуту или напишите в поддержку.",
    "referral_screen": (
        "🎁 <b>Рефералы</b>\n\n"
        "🔗 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        "👥 Приглашено: <b>{invited_count}</b>\n"
        "✅ С бонусом после оплаты: <b>{rewarded_count}</b>\n"
        "🎉 Бонусных дней: <b>{bonus_days}</b>\n\n"
        "Как это работает: друг открывает бота по ссылке и оплачивает первый доступ — после этого начисляется бонус."
    ),
    "policy_torrent": "⚠️ Не рекомендуется использовать торренты/P2P через сервис: это повышает риск abuse-жалоб.",
    "policy_sensitive": "ℹ️ Часть чувствительных сайтов/сервисов может быть недоступна через VPN по policy сервиса.",
    "unknown_message": "Не понял сообщение. Используйте кнопки меню ниже.",
    "unknown_callback_action": "Действие не найдено",
}

TEXT_REQUIRED_PLACEHOLDERS: dict[str, set[str]] = {
    "support_contact": {"support_username"},
}

SETTING_DEFAULTS: dict[str, Any] = {
    "DEFAULT_KEY_RATE_MBIT": DEFAULT_KEY_RATE_MBIT,
    "REFERRAL_ENABLED": int(REFERRAL_ENABLED),
    "REFERRAL_INVITEE_BONUS_DAYS": REFERRAL_INVITEE_BONUS_DAYS,
    "REFERRAL_INVITER_BONUS_DAYS": REFERRAL_INVITER_BONUS_DAYS,
    "EGRESS_DENYLIST_ENABLED": int(EGRESS_DENYLIST_ENABLED),
    "EGRESS_DENYLIST_DOMAINS": EGRESS_DENYLIST_DOMAINS,
    "EGRESS_DENYLIST_CIDRS": EGRESS_DENYLIST_CIDRS,
    "EGRESS_DENYLIST_REFRESH_MINUTES": EGRESS_DENYLIST_REFRESH_MINUTES,
    "EGRESS_DENYLIST_MODE": EGRESS_DENYLIST_MODE,
    "TORRENT_POLICY_TEXT_ENABLED": int(TORRENT_POLICY_TEXT_ENABLED),
    "VPN_SUBNET_PREFIX": VPN_SUBNET_PREFIX,
}


async def get_text(key: str, **kwargs: Any) -> str:
    template = await get_text_override(key) or TEXT_DEFAULTS.get(key, "")
    try:
        return template.format(**kwargs) if kwargs else template
    except Exception as e:
        logger.warning("text format fallback key=%s error=%s", key, e)
        default_template = TEXT_DEFAULTS.get(key, "")
        return default_template.format(**kwargs) if kwargs else default_template


async def validate_text_template(key: str, value: str) -> tuple[bool, str]:
    try:
        placeholders = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(value)
            if field_name
        }
    except Exception as e:
        return False, f"invalid template format: {e}"
    required = TEXT_REQUIRED_PLACEHOLDERS.get(key, set())
    missing = sorted(required - placeholders)
    if missing:
        return False, f"missing placeholders: {', '.join(missing)}"
    return True, ""


async def get_setting(key: str, cast: Callable[[str], Any] | None = None) -> Any:
    raw = await get_app_setting(key)
    if raw is None:
        return SETTING_DEFAULTS.get(key)
    if cast is None:
        return raw
    try:
        return cast(raw)
    except Exception as e:
        logger.warning("setting cast fallback key=%s raw=%r error=%s", key, raw, e)
        return SETTING_DEFAULTS.get(key)
