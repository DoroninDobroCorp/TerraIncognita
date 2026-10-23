"""Share one in-flight computation between concurrent callers with the same key."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class SingleFlight:
    """``await flight.run(key, factory)``: the first caller starts ``factory()``, later callers
    with the same key (while it runs) await the same task instead of repeating the work.

    The shared task is shielded: one caller being cancelled (a client disconnecting) does not
    cancel the work the others — or a background cache warm-up — still need.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._tasks[key] = task
            task.add_done_callback(lambda t, k=key: self._finished(k, t))
        return await asyncio.shield(task)

    def _finished(self, key: str, task: asyncio.Task) -> None:
        if self._tasks.get(key) is task:
            del self._tasks[key]
        if not task.cancelled():
            task.exception()  # mark retrieved: callers that gave up must not trigger a warning

    def __len__(self) -> int:
        return len(self._tasks)
