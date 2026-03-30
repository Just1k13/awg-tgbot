from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from config import logger


WorkerCoroutine = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    coroutine_factory: WorkerCoroutine


class WorkerPool:
    """Manages lifecycle of long-running background workers."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self, workers: list[WorkerSpec]) -> None:
        for worker in workers:
            if worker.name in self._tasks:
                raise RuntimeError(f"Worker {worker.name!r} already started")
            self._tasks[worker.name] = asyncio.create_task(worker.coroutine_factory(), name=worker.name)
            logger.info("Worker started: %s", worker.name)

    async def stop(self) -> None:
        if not self._tasks:
            return

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(self._tasks, results, strict=True):
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.exception("Worker %s завершился с ошибкой: %s", name, result)
            else:
                logger.info("Worker stopped: %s", name)

        self._tasks.clear()
