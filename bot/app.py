from __future__ import annotations

import asyncio
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher, Router, types
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.utils.keyboard import InlineKeyboardBuilder

from awg_backend import (
    bootstrap_protected_peers,
    check_awg_container,
    cleanup_expired_subscriptions,
    expired_subscriptions_worker,
    get_orphan_awg_peers,
    reconcile_active_awg_state,
    reconcile_pending_awg_state,
    run_docker,
)
from config import (
    ADMIN_ID,
    API_TOKEN,
    BROADCAST_BATCH_DELAY_SECONDS,
    BROADCAST_BATCH_SIZE,
    CLEANUP_INTERVAL_SECONDS,
    DB_PATH,
    DOCKER_CONTAINER,
    PENDING_KEY_TTL_SECONDS,
    RECONCILIATION_INTERVAL_SECONDS,
    WG_INTERFACE,
    logger,
    maybe_set_support_username,
)
from content_settings import get_text
from database import (
    claim_next_broadcast_job,
    cleanup_stale_pending_keys,
    close_shared_db,
    complete_broadcast_job,
    db_health_info,
    ensure_db_ready,
    get_broadcast_recipients,
    get_shared_db,
    get_subscriptions_expiring_within,
    has_subscription_notification,
    mark_subscription_notification_sent,
    update_broadcast_job_progress,
    write_audit_log,
)
from handlers_admin import router as admin_router
from handlers_user import router as user_router
from middlewares import DuplicateCallbackGuardMiddleware, DuplicateMessageGuardMiddleware, RateLimitMiddleware
from network_policy import denylist_should_refresh, denylist_sync
from payments import payment_recovery_worker
from payments import router as payments_router
from ui_constants import is_admin_callback_data
from ui_constants import CB_SHOW_BUY_MENU
from workers import WorkerPool, WorkerSpec


@dataclass(frozen=True)
class RuntimeSettings:
    cleanup_interval_seconds: int
    reconciliation_interval_seconds: int
    broadcast_batch_delay_seconds: float
    broadcast_batch_size: int


@dataclass(frozen=True)
class RuntimeDeps:
    bot: Bot
    settings: RuntimeSettings


dp = Dispatcher()
fallback_router = Router()


dp.message.middleware(RateLimitMiddleware(ttl_seconds=2.0, max_hits=6))
dp.callback_query.middleware(RateLimitMiddleware(ttl_seconds=2.0, max_hits=8))
dp.message.middleware(DuplicateMessageGuardMiddleware())
dp.callback_query.middleware(DuplicateCallbackGuardMiddleware())


@fallback_router.callback_query()
async def fallback_callback(cb: types.CallbackQuery) -> None:
    if is_admin_callback_data(cb.data):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer(await get_text("unknown_callback_action"))


dp.include_router(payments_router)
dp.include_router(admin_router)
dp.include_router(user_router)
dp.include_router(fallback_router)


async def process_one_broadcast_job(deps: RuntimeDeps) -> bool:
    claimed = await claim_next_broadcast_job()
    if not claimed:
        return False

    job_id, admin_id, text, total = claimed
    cursor = 0
    while True:
        recipients = await get_broadcast_recipients(job_id, cursor, deps.settings.broadcast_batch_size)
        if not recipients:
            break

        batch_delivered = 0
        batch_failed = 0
        for uid in recipients:
            try:
                await deps.bot.send_message(uid, text, disable_web_page_preview=True)
                batch_delivered += 1
            except Exception as send_error:
                batch_failed += 1
                logger.warning("Broadcast job=%s user_id=%s error=%s", job_id, uid, send_error)

        cursor += len(recipients)
        await update_broadcast_job_progress(job_id, batch_delivered, batch_failed, cursor)
        await asyncio.sleep(deps.settings.broadcast_batch_delay_seconds)

    _, done_delivered, done_failed = await complete_broadcast_job(job_id, "finished")
    await write_audit_log(
        admin_id,
        "broadcast",
        f"job_id={job_id}; total={total}; delivered={done_delivered}; failed={done_failed}",
    )
    await deps.bot.send_message(
        admin_id,
        (
            "📢 <b>Рассылка завершена</b>\n\n"
            f"job_id=<code>{job_id}</code>\n"
            f"✅ Доставлено: <b>{done_delivered}</b>\n"
            f"❌ Ошибок: <b>{done_failed}</b>"
        ),
        parse_mode="HTML",
    )
    return True


async def _payments_worker(deps: RuntimeDeps) -> None:
    try:
        while True:
            try:
                repaired = await payment_recovery_worker(deps.bot)
                if repaired:
                    logger.info("Payment recovery: успешно обработано %s зависших платежей", repaired)
            except Exception as error:
                logger.exception("Payment recovery worker error: %s", error)
            await asyncio.sleep(15)
    except asyncio.CancelledError:
        logger.info("Payment recovery worker cancelled")
        raise


async def _reconciliation_worker(deps: RuntimeDeps) -> None:
    try:
        while True:
            try:
                stats = await reconcile_pending_awg_state()
                if any(stats.values()):
                    logger.info("Reconciliation stats: %s", stats)
            except Exception as error:
                logger.exception("Reconciliation worker error: %s", error)
            await asyncio.sleep(deps.settings.reconciliation_interval_seconds)
    except asyncio.CancelledError:
        logger.info("Reconciliation worker cancelled")
        raise


async def _broadcast_worker(deps: RuntimeDeps) -> None:
    try:
        while True:
            try:
                processed = await process_one_broadcast_job(deps)
                if not processed:
                    await asyncio.sleep(1)
                    continue
            except Exception as error:
                logger.exception("Broadcast worker error: %s", error)
                await asyncio.sleep(2)
    except asyncio.CancelledError:
        logger.info("Broadcast worker cancelled")
        raise


async def _denylist_refresh_worker() -> None:
    try:
        while True:
            try:
                if await denylist_should_refresh():
                    await denylist_sync(run_docker)
            except Exception as error:
                logger.exception("Denylist refresh worker error: %s", error)
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("Denylist refresh worker cancelled")
        raise


async def _startup_checks(bot: Bot) -> None:
    logger.info("Запуск бота")
    logger.info("DB_PATH=%s", DB_PATH)
    logger.info("DOCKER_CONTAINER=%s WG_INTERFACE=%s", DOCKER_CONTAINER, WG_INTERFACE)

    try:
        await bot.get_me()
    except TelegramUnauthorizedError as error:
        logger.error("Telegram API вернул Unauthorized. Проверь API_TOKEN в .env и перевыпусти токен в BotFather при необходимости.")
        raise RuntimeError("Неверный API_TOKEN") from error

    await ensure_db_ready()
    await get_shared_db()

    try:
        marked_pending = await cleanup_stale_pending_keys(PENDING_KEY_TTL_SECONDS)
        if marked_pending:
            logger.warning("Помечено stale pending-ключей для repair при старте: %s", marked_pending)
    except Exception as error:
        logger.exception("Ошибка маркировки stale pending-ключей: %s", error)

    try:
        await check_awg_container()
        logger.info("Контейнер и интерфейс AWG доступны")
    except Exception as error:
        logger.exception("AWG недоступен: %s", error)
        raise RuntimeError("AWG недоступен") from error

    try:
        admin_chat = await bot.get_chat(ADMIN_ID)
        maybe_set_support_username(getattr(admin_chat, "username", None))
    except Exception as error:
        logger.info("Не удалось автоопределить username администратора: %s", error)

    try:
        await bootstrap_protected_peers()
    except Exception as error:
        logger.exception("Ошибка bootstrap protected peers: %s", error)

    try:
        active_sync = await reconcile_active_awg_state()
        if active_sync["restored"] or active_sync["failed"]:
            logger.info("Active reconcile stats: %s", active_sync)
    except Exception as error:
        logger.exception("Ошибка восстановления active peers: %s", error)

    try:
        db_info = await db_health_info()
        orphan_count = len(await get_orphan_awg_peers())
        logger.info(
            "Проверка состояния: db_exists=%s, keys_table=%s, required_cols=%s, valid_keys=%s, orphan_peers=%s",
            db_info["exists"],
            db_info["keys_table_exists"],
            db_info["has_required_columns"],
            db_info["valid_keys_count"],
            orphan_count,
        )
    except Exception as error:
        logger.exception("Ошибка стартовой диагностики: %s", error)

    try:
        cleaned = await cleanup_expired_subscriptions()
        logger.info("Стартовая очистка завершена. Очищено просроченных: %s", cleaned)
    except Exception as error:
        logger.exception("Ошибка стартовой очистки: %s", error)


async def _notify_expiring_subscriptions(bot: Bot) -> None:
    rows = await get_subscriptions_expiring_within(24)
    for user_id, sub_until in rows:
        if await has_subscription_notification(user_id, sub_until, "24h_before"):
            continue
        kb = InlineKeyboardBuilder()
        kb.button(text="Продлить подписку", callback_data=CB_SHOW_BUY_MENU)
        try:
            await bot.send_message(
                user_id,
                f"⏰ Подписка истекает менее чем через 24 часа.\nОкончание: {sub_until[:16].replace('T', ' ')}",
                reply_markup=kb.as_markup(),
            )
            await mark_subscription_notification_sent(user_id, sub_until, "24h_before")
        except Exception as error:
            logger.warning("Не удалось отправить напоминание user_id=%s: %s", user_id, error)


async def main() -> None:
    bot = Bot(token=API_TOKEN)
    scheduler = AsyncIOScheduler(timezone="UTC")
    deps = RuntimeDeps(
        bot=bot,
        settings=RuntimeSettings(
            cleanup_interval_seconds=CLEANUP_INTERVAL_SECONDS,
            reconciliation_interval_seconds=RECONCILIATION_INTERVAL_SECONDS,
            broadcast_batch_delay_seconds=BROADCAST_BATCH_DELAY_SECONDS,
            broadcast_batch_size=BROADCAST_BATCH_SIZE,
        ),
    )
    worker_pool = WorkerPool()

    try:
        await _startup_checks(bot)
        scheduler.add_job(_notify_expiring_subscriptions, "interval", minutes=30, kwargs={"bot": bot}, id="expiring-24h", replace_existing=True)
        scheduler.start()
        worker_pool.start(
            [
                WorkerSpec(
                    "expired_subscriptions",
                    lambda: expired_subscriptions_worker(deps.settings.cleanup_interval_seconds),
                ),
                WorkerSpec("payment_recovery", lambda: _payments_worker(deps)),
                WorkerSpec("reconciliation", lambda: _reconciliation_worker(deps)),
                WorkerSpec("broadcast", lambda: _broadcast_worker(deps)),
                WorkerSpec("denylist_refresh", _denylist_refresh_worker),
            ]
        )
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await worker_pool.stop()
        await close_shared_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
