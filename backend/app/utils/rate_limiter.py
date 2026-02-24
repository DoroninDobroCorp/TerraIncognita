"""Async rate limiter using a sliding-window token bucket."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Simple sliding-window rate limiter.

    Allows `max_requests` within every `window_seconds` period.
    Async-safe; callers ``await limiter.acquire()`` before making a request.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # Purge timestamps outside the window
            self._timestamps = [t for t in self._timestamps if now - t < self._window]
            if len(self._timestamps) >= self._max:
                sleep_for = self._window - (now - self._timestamps[0])
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
            self._timestamps.append(time.monotonic())
