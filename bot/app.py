from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher, Router, types
from aiogram.exceptions import TelegramUnauthorizedError

from awg_backend import (
    bootstrap_protected_peers,
    check_awg_container,
    cleanup_expired_subscriptions,
    expired_subscriptions_worker,
    get_orphan_awg_peers,
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
    update_broadcast_job_progress,
    write_audit_log,
)
from handlers_admin import router as admin_router
from handlers_user import router as user_router
from middlewares import DuplicateCallbackGuardMiddleware, DuplicateMessageGuardMiddleware
from network_policy import denylist_should_refresh, denylist_sync
from payments import payment_recovery_worker
from payments import router as payments_router
from ui_constants import is_admin_callback_data
from workers import WorkerPool, WorkerSpec

dp = Dispatcher()
fallback_router = Router()


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


async def process_one_broadcast_job(bot: Bot) -> bool:
    claimed = await claim_next_broadcast_job()
    if not claimed:
        return False

    job_id, admin_id, text, total = claimed
    cursor = 0
    while True:
        recipients = await get_broadcast_recipients(job_id, cursor, BROADCAST_BATCH_SIZE)
        if not recipients:
            break

        batch_delivered = 0
        batch_failed = 0
        for uid in recipients:
            try:
                await bot.send_message(uid, text, disable_web_page_preview=True)
                batch_delivered += 1
            except Exception as send_error:
                batch_failed += 1
                logger.warning("Broadcast job=%s user_id=%s error=%s", job_id, uid, send_error)

        cursor += len(recipients)
        await update_broadcast_job_progress(job_id, batch_delivered, batch_failed, cursor)
        await asyncio.sleep(BROADCAST_BATCH_DELAY_SECONDS)

    _, done_delivered, done_failed = await complete_broadcast_job(job_id, "finished")
    await write_audit_log(
        admin_id,
        "broadcast",
        f"job_id={job_id}; total={total}; delivered={done_delivered}; failed={done_failed}",
    )
    await bot.send_message(
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


async def _payments_worker(bot: Bot) -> None:
    while True:
        try:
            repaired = await payment_recovery_worker(bot)
            if repaired:
                logger.info("Payment recovery: успешно обработано %s зависших платежей", repaired)
        except Exception as error:
            logger.exception("Payment recovery worker error: %s", error)
        await asyncio.sleep(15)


async def _reconciliation_worker() -> None:
    while True:
        try:
            stats = await reconcile_pending_awg_state()
            if any(stats.values()):
                logger.info("Reconciliation stats: %s", stats)
        except Exception as error:
            logger.exception("Reconciliation worker error: %s", error)
        await asyncio.sleep(RECONCILIATION_INTERVAL_SECONDS)


async def _broadcast_worker(bot: Bot) -> None:
    while True:
        try:
            processed = await process_one_broadcast_job(bot)
            if not processed:
                await asyncio.sleep(1)
                continue
        except Exception as error:
            logger.exception("Broadcast worker error: %s", error)
            await asyncio.sleep(2)


async def _denylist_refresh_worker() -> None:
    while True:
        try:
            if await denylist_should_refresh():
                await denylist_sync(run_docker)
        except Exception as error:
            logger.exception("Denylist refresh worker error: %s", error)
        await asyncio.sleep(60)


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


async def main() -> None:
    bot = Bot(token=API_TOKEN)
    worker_pool = WorkerPool()

    try:
        await _startup_checks(bot)
        worker_pool.start(
            [
                WorkerSpec("expired_subscriptions", lambda: expired_subscriptions_worker(CLEANUP_INTERVAL_SECONDS)),
                WorkerSpec("payment_recovery", lambda: _payments_worker(bot)),
                WorkerSpec("reconciliation", _reconciliation_worker),
                WorkerSpec("broadcast", lambda: _broadcast_worker(bot)),
                WorkerSpec("denylist_refresh", _denylist_refresh_worker),
            ]
        )
        await dp.start_polling(bot)
    finally:
        await worker_pool.stop()
        await close_shared_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
