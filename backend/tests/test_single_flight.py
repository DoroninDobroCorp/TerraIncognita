"""SingleFlight: concurrent callers with one key share one computation."""

from __future__ import annotations

import asyncio

import pytest

from app.utils.single_flight import SingleFlight


async def test_concurrent_callers_share_one_run():
    flight, calls = SingleFlight(), 0

    async def work():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "result"

    results = await asyncio.gather(*(flight.run("k", work) for _ in range(5)))
    assert results == ["result"] * 5 and calls == 1
    assert len(flight) == 0  # forgotten once finished


async def test_different_keys_run_independently():
    flight = SingleFlight()

    async def work(n):
        await asyncio.sleep(0.01)
        return n

    assert await asyncio.gather(flight.run("a", lambda: work(1)), flight.run("b", lambda: work(2))) == [1, 2]


async def test_a_later_call_after_completion_runs_again():
    flight, calls = SingleFlight(), 0

    async def work():
        nonlocal calls
        calls += 1

    await flight.run("k", work)
    await flight.run("k", work)
    assert calls == 2


async def test_failure_reaches_every_waiter_and_is_not_cached():
    flight, calls = SingleFlight(), 0

    async def boom():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise RuntimeError("upstream down")

    results = await asyncio.gather(*(flight.run("k", boom) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(r, RuntimeError) for r in results) and calls == 1
    with pytest.raises(RuntimeError):
        await flight.run("k", boom)
    assert calls == 2


async def test_a_cancelled_caller_does_not_cancel_the_shared_work():
    flight, finished = SingleFlight(), asyncio.Event()

    async def work():
        await asyncio.sleep(0.05)
        finished.set()
        return "done"

    first = asyncio.ensure_future(flight.run("k", work))
    second = asyncio.ensure_future(flight.run("k", work))
    await asyncio.sleep(0.01)
    first.cancel()
    assert await second == "done" and finished.is_set()
