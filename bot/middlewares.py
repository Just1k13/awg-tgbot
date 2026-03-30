from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiogram import BaseMiddleware, types

from config import logger

Handler = Callable[[Any, dict[str, Any]], Awaitable[Any]]


class _BaseDuplicateGuardMiddleware(BaseMiddleware):
    """Drops repeated updates with same payload in a small time window."""

    event_name: str

    def __init__(self, ttl_seconds: float = 1.5) -> None:
        self.ttl_seconds = ttl_seconds
        self._seen: dict[tuple[int, int, str], float] = {}

    def _extract_identity(self, event: Any) -> tuple[int, int, str] | None:
        raise NotImplementedError

    async def _on_duplicate(self, event: Any) -> None:
        return None

    async def __call__(self, handler: Handler, event: Any, data: dict[str, Any]) -> Any:
        identity = self._extract_identity(event)
        if identity is None:
            return await handler(event, data)

        now = monotonic()
        stale_keys = [key for key, ts in self._seen.items() if now - ts > self.ttl_seconds]
        for key in stale_keys:
            self._seen.pop(key, None)

        last_seen = self._seen.get(identity)
        self._seen[identity] = now
        if last_seen is not None and (now - last_seen) < self.ttl_seconds:
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
