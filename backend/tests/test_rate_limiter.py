"""Tests for rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.utils.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_allows_within_limit():
    rl = RateLimiter(max_requests=3, window_seconds=1.0)
    t0 = time.monotonic()
    for _ in range(3):
        await rl.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5  # should be near-instant


@pytest.mark.asyncio
async def test_throttles_over_limit():
    rl = RateLimiter(max_requests=2, window_seconds=0.5)
    await rl.acquire()
    await rl.acquire()
    t0 = time.monotonic()
    await rl.acquire()  # should wait ~0.5s
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.3
