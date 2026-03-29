from __future__ import annotations

import hashlib

from awg_backend import issue_subscription
from config import logger
from content_settings import get_setting
from database import (
    create_referral_reward_once,
    ensure_referral_code,
    get_referral_attribution,
    get_referral_code,
    get_referral_summary,
    get_user_id_by_referral_code,
    set_referral_attribution,
    user_has_paid_subscription,
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
