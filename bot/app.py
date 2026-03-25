import asyncio

from aiogram import Bot, Dispatcher, Router, types
from aiogram.exceptions import TelegramUnauthorizedError

from awg_backend import (
    bootstrap_protected_peers,
    check_awg_container,
    cleanup_expired_subscriptions,
    expired_subscriptions_worker,
    get_orphan_awg_peers,
)
from config import (
    ADMIN_ID,
    API_TOKEN,
    CLEANUP_INTERVAL_SECONDS,
    DB_PATH,
    DOCKER_CONTAINER,
    WG_INTERFACE,
    logger,
    maybe_set_support_username,
)
from database import close_shared_db, db_health_info, ensure_db_ready
from handlers_admin import router as admin_router
from handlers_user import router as user_router
from payments import router as payments_router

dp = Dispatcher()
bg_worker_task: asyncio.Task | None = None

fallback_router = Router()


@fallback_router.callback_query()
async def fallback_callback(cb: types.CallbackQuery):
    await cb.answer("Действие не найдено")


dp.include_router(payments_router)
dp.include_router(admin_router)
dp.include_router(user_router)
dp.include_router(fallback_router)


async def main():
    global bg_worker_task

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

    bg_worker_task = asyncio.create_task(expired_subscriptions_worker(CLEANUP_INTERVAL_SECONDS))
    try:
        await dp.start_polling(bot)
    finally:
        if bg_worker_task:
            bg_worker_task.cancel()
        await close_shared_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
