from __future__ import annotations

import hashlib
from typing import Any

from awg_backend import issue_subscription
from config import logger
from content_settings import get_setting
from database import (
    count_user_applied_payments,
    create_referral_reward_once,
    create_referral_recurring_reward_once,
    ensure_referral_code,
    get_referral_attribution,
    get_referral_code,
    get_referral_summary,
    get_user_id_by_referral_code,
    has_referral_first_reward,
    set_referral_attribution,
    user_has_paid_subscription,
    get_user_meta,
    write_audit_log,
)


def _build_ref_code(user_id: int) -> str:
    digest = hashlib.sha256(f"awg-ref-{user_id}".encode("utf-8")).hexdigest()[:10]
    return digest.upper()


async def ensure_user_referral_code(user_id: int) -> str:
    code = await get_referral_code(user_id)
    if code:
        return code
    code = _build_ref_code(user_id)
    await ensure_referral_code(user_id, code)
    return code


async def capture_referral_start(invitee_user_id: int, start_arg: str) -> bool:
    if int(await get_setting("REFERRAL_ENABLED", int) or 0) != 1:
        return False
    if not start_arg.startswith("ref_"):
        return False
    if await user_has_paid_subscription(invitee_user_id):
        return False
    if await get_referral_attribution(invitee_user_id):
        return False
    code = start_arg.removeprefix("ref_").strip().upper()
    inviter_user_id = await get_user_id_by_referral_code(code)
    if not inviter_user_id or inviter_user_id == invitee_user_id:
        return False
    saved = await set_referral_attribution(invitee_user_id, inviter_user_id, code)
    if saved:
        await write_audit_log(invitee_user_id, "referral_attribution_set", f"inviter={inviter_user_id}; code={code}")
    return saved


async def apply_referral_rewards_on_first_payment(invitee_user_id: int, payment_id: str) -> bool:
    if int(await get_setting("REFERRAL_ENABLED", int) or 0) != 1:
        return False
    attribution = await get_referral_attribution(invitee_user_id)
    if not attribution:
        return False
    inviter_user_id, code = attribution
    invitee_days = int(await get_setting("REFERRAL_INVITEE_BONUS_DAYS", int) or 5)
    inviter_days = int(await get_setting("REFERRAL_INVITER_BONUS_DAYS", int) or 3)
    created = await create_referral_reward_once(
        invitee_user_id=invitee_user_id,
        inviter_user_id=inviter_user_id,
        payment_id=payment_id,
        invitee_bonus_days=invitee_days,
        inviter_bonus_days=inviter_days,
    )
    if not created:
        return False
    await issue_subscription(invitee_user_id, invitee_days, silent=True, operation_id=f"ref-invitee-{payment_id}")
    await issue_subscription(inviter_user_id, inviter_days, silent=True, operation_id=f"ref-inviter-{payment_id}")
    await write_audit_log(
        invitee_user_id,
        "referral_rewards_applied",
        f"inviter={inviter_user_id}; code={code}; invitee_days={invitee_days}; inviter_days={inviter_days}",
    )
    logger.info("Referral rewards applied for payment=%s invitee=%s inviter=%s", payment_id, invitee_user_id, inviter_user_id)
    return True


def _format_tg_mention(username: str | None, user_id: int) -> str:
    if username:
        return f"@{username}"
    return f"id={user_id}"


async def notify_inviter_about_referral_reward(bot: Any, invitee_user_id: int) -> bool:
    if bot is None:
        return False
    attribution = await get_referral_attribution(invitee_user_id)
    if not attribution:
        return False
    inviter_user_id, _code = attribution
    inviter_days = int(await get_setting("REFERRAL_INVITER_BONUS_DAYS", int) or 3)
    invitee_username, _invitee_first_name = await get_user_meta(invitee_user_id)
    invitee_mention = _format_tg_mention(invitee_username, invitee_user_id)
    text = (
        "🎉 <b>Реферальный бонус начислен</b>\n\n"
        f"По покупке пользователя {invitee_mention} (ID: <code>{invitee_user_id}</code>) "
        f"вам начислено <b>+{inviter_days} дн.</b>"
    )
    try:
        await bot.send_message(inviter_user_id, text, parse_mode="HTML")
        return True
    except Exception as error:
        logger.warning("Не удалось отправить уведомление о реферальном бонусе inviter=%s: %s", inviter_user_id, error)
        return False


async def apply_recurring_inviter_reward(
    invitee_user_id: int,
    payment_id: str,
    purchased_days: int,
) -> bool:
    if int(await get_setting("REFERRAL_ENABLED", int) or 0) != 1:
        return False
    if purchased_days < 30:
        return False
    attribution = await get_referral_attribution(invitee_user_id)
    if not attribution:
        return False
    if not await has_referral_first_reward(invitee_user_id):
        return False
    applied_payments = await count_user_applied_payments(invitee_user_id)
    if applied_payments < 2:
        return False
    inviter_user_id, code = attribution
    inviter_days = 1
    created = await create_referral_recurring_reward_once(
        invitee_user_id=invitee_user_id,
        inviter_user_id=inviter_user_id,
        payment_id=payment_id,
        inviter_bonus_days=inviter_days,
    )
    if not created:
        return False
    await issue_subscription(inviter_user_id, inviter_days, silent=True, operation_id=f"ref-recurring-inviter-{payment_id}")
    await write_audit_log(
        invitee_user_id,
        "referral_recurring_inviter_reward_applied",
        f"inviter={inviter_user_id}; code={code}; inviter_days={inviter_days}; payment_id={payment_id}; purchased_days={purchased_days}",
    )
    logger.info(
        "Recurring referral inviter reward applied for payment=%s invitee=%s inviter=%s",
        payment_id,
        invitee_user_id,
        inviter_user_id,
    )
    return True


async def notify_inviter_about_recurring_referral_reward(bot: Any, invitee_user_id: int) -> bool:
    if bot is None:
        return False
    attribution = await get_referral_attribution(invitee_user_id)
    if not attribution:
        return False
    inviter_user_id, _code = attribution
    invitee_username, _invitee_first_name = await get_user_meta(invitee_user_id)
    invitee_mention = _format_tg_mention(invitee_username, invitee_user_id)
    text = (
        "🎁 <b>Дополнительный реферальный бонус</b>\n\n"
        f"За повторную покупку пользователя {invitee_mention} "
        f"(ID: <code>{invitee_user_id}</code>) вам начислен <b>+1 дн.</b>"
    )
    try:
        await bot.send_message(inviter_user_id, text, parse_mode="HTML")
        return True
    except Exception as error:
        logger.warning("Не удалось отправить уведомление о recurring-реферальном бонусе inviter=%s: %s", inviter_user_id, error)
        return False


async def get_referral_screen_data(user_id: int, bot_username: str) -> dict[str, str | int]:
    code = await ensure_user_referral_code(user_id)
    summary = await get_referral_summary(user_id)
    return {
        "code": code,
        "link": f"https://t.me/{bot_username}?start=ref_{code}",
        "invited_count": summary["invited_count"],
        "rewarded_count": summary["rewarded_count"],
        "bonus_days": summary["inviter_bonus_days"] + summary["invitee_bonus_days"],
    }
