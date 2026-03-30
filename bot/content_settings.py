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
        "1) Оплатите доступ\n2) Дождитесь статуса «Доступ готов»\n3) Получите ключ и импортируйте в Amnezia.\n\n"
        "⚠️ Не рекомендуется использовать торренты и P2P через сервис.\n"
        "Некоторые чувствительные сайты/сети могут быть недоступны по policy сервиса."
    ),
    "support_unavailable": "🆘 Поддержка временно не настроена. Попробуйте позже или напишите администратору сервиса.",
    "unknown_slash": "Неизвестная команда. Используйте кнопки меню или /start.",
    "buy_menu": "💳 <b>Выберите срок доступа</b>\n\nВ подписку входит доступ до <b>2 устройств</b>.\n\n{price_lines}",
    "renew_menu": "🔄 <b>У вас уже есть активная подписка</b>\n⏳ Осталось: <b>{remaining}</b>\n\n💡 Можно продлить заранее.\n\n{price_lines}",
    "guide_hint": "Если активация задержалась — нажмите «Проверить статус активации».",
    "payment_success": "🎉 <b>Доступ готов</b>\n\nСтатусы: оплата получена → доступ выпускается → доступ готов ✅",
    "payment_pending": "⏳ Платёж принят. Доступ выпускается в фоне. Обычно до минуты.",
    "payment_error": "❌ Платёж получен, но активация задержалась. Попробуйте позже или обратитесь в поддержку.",
    "activation_status_ready": "✅ Оплата получена → доступ выпускается → доступ готов.",
    "activation_status_pending": "⏳ Оплата получена. Доступ выпускается. Обычно до минуты.",
    "activation_status_delayed": "⚠️ Активация задержалась. Попробуйте позже или обратитесь в поддержку.",
    "referral_screen": (
        "🎁 <b>Рефералы</b>\n\n"
        "🔗 Ваша ссылка:\n<code>{ref_link}</code>\n\n"
        "👥 Приглашено: <b>{invited_count}</b>\n"
        "✅ Rewarded: <b>{rewarded_count}</b>\n"
        "🎉 Бонусных дней: <b>{bonus_days}</b>\n\n"
        "Бонус начисляется только после первой успешной оплаты приглашённого пользователя."
    ),
    "policy_torrent": "⚠️ Не рекомендуется использовать торренты/P2P через сервис: это повышает риск abuse-жалоб.",
    "policy_sensitive": "ℹ️ Часть чувствительных сайтов/сервисов может быть недоступна через VPN по policy сервиса.",
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
