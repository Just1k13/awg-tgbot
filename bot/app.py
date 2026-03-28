import asyncio
from time import monotonic

from aiogram import BaseMiddleware, Bot, Dispatcher, Router, types
from aiogram.exceptions import TelegramUnauthorizedError

from awg_backend import (
    bootstrap_protected_peers,
    check_awg_container,
    cleanup_expired_subscriptions,
    expired_subscriptions_worker,
    get_orphan_awg_peers,
    reconcile_pending_awg_state,
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
from payments import router as payments_router
from payments import payment_recovery_worker

dp = Dispatcher()
bg_worker_task: asyncio.Task | None = None
payment_recovery_task: asyncio.Task | None = None
broadcast_task: asyncio.Task | None = None
reconciliation_task: asyncio.Task | None = None

fallback_router = Router()


class DuplicateMessageGuardMiddleware(BaseMiddleware):
    def __init__(self, ttl: float = 1.5):
        self.ttl = ttl
        self._seen: dict[tuple[int, int, str], float] = {}

    async def __call__(self, handler, event, data):
        if isinstance(event, types.Message):
            user_id = event.from_user.id if event.from_user else 0
            chat_id = event.chat.id if event.chat else 0
            payload = (event.text or event.caption or "").strip()
            if payload:
                now = monotonic()
                stale = [key for key, ts in self._seen.items() if now - ts > self.ttl]
                for key in stale:
                    self._seen.pop(key, None)

                key = (chat_id, user_id, payload)
                last = self._seen.get(key)
                self._seen[key] = now
                if last is not None and (now - last) < self.ttl:
                    logger.info(
                        "Подавлен дубль message: chat=%s user=%s payload=%r",
                        chat_id,
                        user_id,
                        payload,
                    )
                    return
        return await handler(event, data)


class DuplicateCallbackGuardMiddleware(BaseMiddleware):
    def __init__(self, ttl: float = 1.5):
        self.ttl = ttl
        self._seen: dict[tuple[int, int, str], float] = {}

    async def __call__(self, handler, event, data):
        if isinstance(event, types.CallbackQuery):
            user_id = event.from_user.id if event.from_user else 0
            chat_id = event.message.chat.id if event.message and event.message.chat else 0
            payload = (event.data or "").strip()
            if payload:
                now = monotonic()
                stale = [key for key, ts in self._seen.items() if now - ts > self.ttl]
                for key in stale:
                    self._seen.pop(key, None)

                key = (chat_id, user_id, payload)
                last = self._seen.get(key)
                self._seen[key] = now
                if last is not None and (now - last) < self.ttl:
                    logger.info(
                        "Подавлен дубль callback: chat=%s user=%s payload=%r",
                        chat_id,
                        user_id,
                        payload,
                    )
                    await event.answer()
                    return
        return await handler(event, data)


dp.message.middleware(DuplicateMessageGuardMiddleware())
dp.callback_query.middleware(DuplicateCallbackGuardMiddleware())


@fallback_router.callback_query()
async def fallback_callback(cb: types.CallbackQuery):
    await cb.answer("Действие не найдено")


dp.include_router(payments_router)
dp.include_router(admin_router)
dp.include_router(user_router)
dp.include_router(fallback_router)


async def main():
    global bg_worker_task, payment_recovery_task, broadcast_task, reconciliation_task

    bot = Bot(token=API_TOKEN)
    logger.info("Запуск бота")
    logger.info("DB_PATH=%s", DB_PATH)
    logger.info("DOCKER_CONTAINER=%s WG_INTERFACE=%s", DOCKER_CONTAINER, WG_INTERFACE)

    try:
        await bot.get_me()
    except TelegramUnauthorizedError as e:
        logger.error("Telegram API вернул Unauthorized. Проверь API_TOKEN в .env и перевыпусти токен в BotFather при необходимости.")
        await bot.session.close()
        raise RuntimeError("Неверный API_TOKEN") from e

    await ensure_db_ready()

    try:
        marked_pending = await cleanup_stale_pending_keys(PENDING_KEY_TTL_SECONDS)
        if marked_pending:
            logger.warning("Помечено stale pending-ключей для repair при старте: %s", marked_pending)
    except Exception as e:
        logger.exception("Ошибка маркировки stale pending-ключей: %s", e)

    try:
        await check_awg_container()
        logger.info("Контейнер и интерфейс AWG доступны")
    except Exception as e:
        logger.exception("AWG недоступен: %s", e)
        await bot.session.close()
        raise RuntimeError("AWG недоступен") from e

    try:
        admin_chat = await bot.get_chat(ADMIN_ID)
        maybe_set_support_username(getattr(admin_chat, 'username', None))
    except Exception as e:
        logger.info('Не удалось автоопределить username администратора: %s', e)

    try:
        await bootstrap_protected_peers()
    except Exception as e:
        logger.exception('Ошибка bootstrap protected peers: %s', e)

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
    except Exception as e:
        logger.exception("Ошибка стартовой диагностики: %s", e)

    try:
        cleaned = await cleanup_expired_subscriptions()
        logger.info("Стартовая очистка завершена. Очищено просроченных: %s", cleaned)
    except Exception as e:
        logger.exception("Ошибка стартовой очистки: %s", e)

    async def _payments_worker() -> None:
        while True:
            try:
                repaired = await payment_recovery_worker(bot)
                if repaired:
                    logger.info("Payment recovery: успешно обработано %s зависших платежей", repaired)
            except Exception as e:
                logger.exception("Payment recovery worker error: %s", e)
            await asyncio.sleep(15)

    async def _reconciliation_worker() -> None:
        while True:
            try:
                stats = await reconcile_pending_awg_state()
                if any(stats.values()):
                    logger.info("Reconciliation stats: %s", stats)
            except Exception as e:
                logger.exception("Reconciliation worker error: %s", e)
            await asyncio.sleep(RECONCILIATION_INTERVAL_SECONDS)

    async def process_one_broadcast_job(bot: Bot) -> bool:
        claimed = await claim_next_broadcast_job()
        if not claimed:
            return False
        job_id, admin_id, text, total = claimed
        cursor = 0
        while True:
            recipients = await get_broadcast_recipients(cursor, BROADCAST_BATCH_SIZE)
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
        await write_audit_log(admin_id, "broadcast", f"job_id={job_id}; total={total}; delivered={done_delivered}; failed={done_failed}")
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

    async def _broadcast_worker() -> None:
        while True:
            try:
                processed = await process_one_broadcast_job(bot)
                if not processed:
                    await asyncio.sleep(1)
                    continue
            except Exception as e:
                logger.exception("Broadcast worker error: %s", e)
                await asyncio.sleep(2)

    bg_worker_task = asyncio.create_task(expired_subscriptions_worker(CLEANUP_INTERVAL_SECONDS))
    payment_recovery_task = asyncio.create_task(_payments_worker())
    reconciliation_task = asyncio.create_task(_reconciliation_worker())
    broadcast_task = asyncio.create_task(_broadcast_worker())
    try:
        await dp.start_polling(bot)
    finally:
        if bg_worker_task:
            bg_worker_task.cancel()
        if payment_recovery_task:
            payment_recovery_task.cancel()
        if reconciliation_task:
            reconciliation_task.cancel()
        if broadcast_task:
            broadcast_task.cancel()
        await close_shared_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
