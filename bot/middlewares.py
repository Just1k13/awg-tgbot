from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiogram import BaseMiddleware, types

from config import logger
from database import increment_metric, set_metric

Handler = Callable[[Any, dict[str, Any]], Awaitable[Any]]


class _TTLIdentityCache:
    """Small bounded TTL cache for duplicate-suppression identities."""

    def __init__(self, ttl_seconds: float, max_entries: int = 4096) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store: OrderedDict[tuple[int, int, str], float] = OrderedDict()

    def is_duplicate(self, key: tuple[int, int, str], now: float) -> bool:
        self._evict_expired(now)
        last_seen = self._store.get(key)
        self._store[key] = now
        self._store.move_to_end(key)
        if len(self._store) > self.max_entries:
            self._store.popitem(last=False)

        return last_seen is not None and (now - last_seen) < self.ttl_seconds

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        while self._store:
            oldest_key = next(iter(self._store))
            if self._store[oldest_key] >= cutoff:
                break
            self._store.popitem(last=False)


class _BaseDuplicateGuardMiddleware(BaseMiddleware):
    """Drops repeated updates with same payload in a bounded time window."""

    event_name: str

    def __init__(self, ttl_seconds: float = 1.5, max_entries: int = 4096) -> None:
        self._cache = _TTLIdentityCache(ttl_seconds=ttl_seconds, max_entries=max_entries)

    def _extract_identity(self, event: Any) -> tuple[int, int, str] | None:
        raise NotImplementedError

    async def _on_duplicate(self, event: Any) -> None:
        return None

    async def __call__(self, handler: Handler, event: Any, data: dict[str, Any]) -> Any:
        identity = self._extract_identity(event)
        if identity is None:
            return await handler(event, data)

        if self._cache.is_duplicate(identity, monotonic()):
            chat_id, user_id, payload = identity
            logger.info(
                "Подавлен дубль %s: chat=%s user=%s payload=%r",
                self.event_name,
                chat_id,
                user_id,
                payload,
            )
            await self._on_duplicate(event)
            return None

        return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    """Simple per-user sliding-window limiter for flood protection."""

    def __init__(self, ttl_seconds: float, max_hits: int, max_entries: int = 8192) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_hits = max_hits
        self.max_entries = max_entries
        self._hits: OrderedDict[tuple[int, int, str], deque[float]] = OrderedDict()

    def _is_limited(self, key: tuple[int, int, str], now: float) -> bool:
        bucket = self._hits.get(key)
        if bucket is None:
            bucket = deque()
            self._hits[key] = bucket
        self._hits.move_to_end(key)
        cutoff = now - self.ttl_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_hits:
            return True
        bucket.append(now)
        if len(self._hits) > self.max_entries:
            self._hits.popitem(last=False)
        return False

    async def _record_rate_limit_drop(self, scope: str) -> None:
        try:
            await increment_metric("rate_limit_dropped_total")
            if scope == "message":
                await increment_metric("rate_limit_dropped_message")
            elif scope == "callback":
                await increment_metric("rate_limit_dropped_callback")
            await set_metric("rate_limit_active_buckets", len(self._hits))
        except Exception as error:
            logger.debug("rate-limit metrics update failed: %s", error)

    async def __call__(self, handler: Handler, event: Any, data: dict[str, Any]) -> Any:
        user_id = 0
        chat_id = 0
        scope = type(event).__name__

        if isinstance(event, types.Message):
            user_id = event.from_user.id if event.from_user else 0
            chat_id = event.chat.id if event.chat else 0
            scope = "message"
        elif isinstance(event, types.CallbackQuery):
            user_id = event.from_user.id if event.from_user else 0
            chat_id = event.message.chat.id if event.message and event.message.chat else 0
            scope = "callback"
        elif hasattr(event, "from_user") and getattr(event, "from_user"):
            user_id = int(getattr(event.from_user, "id", 0) or 0)

        if user_id <= 0:
            return await handler(event, data)

        key = (chat_id, user_id, scope)
        if self._is_limited(key, monotonic()):
            logger.warning("Rate limit: dropped %s from user=%s chat=%s", scope, user_id, chat_id)
            await self._record_rate_limit_drop(scope)
            if isinstance(event, types.CallbackQuery):
                await event.answer("Слишком часто. Подождите секунду.")
            return None

        return await handler(event, data)


class DuplicateMessageGuardMiddleware(_BaseDuplicateGuardMiddleware):
    event_name = "message"

    def _extract_identity(self, event: Any) -> tuple[int, int, str] | None:
        if not isinstance(event, types.Message):
            return None
        user_id = event.from_user.id if event.from_user else 0
        chat_id = event.chat.id if event.chat else 0
        payload = (event.text or event.caption or "").strip()
        if not payload:
            return None
        return chat_id, user_id, payload


class DuplicateCallbackGuardMiddleware(_BaseDuplicateGuardMiddleware):
    event_name = "callback"

    def _extract_identity(self, event: Any) -> tuple[int, int, str] | None:
        if not isinstance(event, types.CallbackQuery):
            return None
        user_id = event.from_user.id if event.from_user else 0
        chat_id = event.message.chat.id if event.message and event.message.chat else 0
        payload = (event.data or "").strip()
        if not payload:
            return None
        return chat_id, user_id, payload

    async def _on_duplicate(self, event: Any) -> None:
        if isinstance(event, types.CallbackQuery):
            await event.answer()
