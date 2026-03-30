from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from config import logger

WorkerCoroutine = Callable[[], Awaitable[None]]
WorkerCleanup = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    coroutine_factory: WorkerCoroutine
    on_cancel: WorkerCleanup | None = None


class WorkerPool:
    """Manages lifecycle of long-running background workers."""

    def __init__(self, shutdown_timeout_seconds: float = 5.0) -> None:
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._specs: dict[str, WorkerSpec] = {}

    def start(self, workers: list[WorkerSpec]) -> None:
        for worker in workers:
            if worker.name in self._tasks:
                raise RuntimeError(f"Worker {worker.name!r} already started")
            self._specs[worker.name] = worker
            self._tasks[worker.name] = asyncio.create_task(worker.coroutine_factory(), name=worker.name)
            logger.info("Worker started: %s", worker.name)

    async def stop(self) -> None:
        if not self._tasks:
            return

        task_items = list(self._tasks.items())
        for _, task in task_items:
            task.cancel()

        for name, task in task_items:
            spec = self._specs.get(name)
            try:
                await asyncio.wait_for(task, timeout=self._shutdown_timeout_seconds)
                logger.info("Worker stopped: %s", name)
            except asyncio.CancelledError:
                logger.info("Worker cancelled: %s", name)
            except TimeoutError:
                logger.warning("Worker stop timeout exceeded: %s", name)
            except Exception as error:
                logger.exception("Worker %s завершился с ошибкой: %s", name, error)
            finally:
                if spec and spec.on_cancel:
                    try:
                        await spec.on_cancel()
                    except Exception as cleanup_error:
                        logger.exception("Worker %s cleanup error: %s", name, cleanup_error)

        self._tasks.clear()
        self._specs.clear()
