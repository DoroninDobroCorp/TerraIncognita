"""Tests for disk cache."""

from __future__ import annotations

import time

import pytest

from app.services.cache import DiskCache


@pytest.fixture
def temp_cache(tmp_path):
    return DiskCache(cache_dir=str(tmp_path), ttl=2)


@pytest.mark.asyncio
async def test_get_miss(temp_cache):
    result = await temp_cache.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get(temp_cache):
    await temp_cache.set("key1", {"hello": "world"})
    result = await temp_cache.get("key1")
    assert result == {"hello": "world"}


@pytest.mark.asyncio
async def test_ttl_expiration(temp_cache):
    cache = DiskCache(cache_dir=str(temp_cache._dir), ttl=1)
    await cache.set("key2", "value")
    time.sleep(1.1)  # wait past TTL
    result = await cache.get("key2")
    assert result is None


@pytest.mark.asyncio
async def test_invalidate(temp_cache):
    await temp_cache.set("key3", "data")
    await temp_cache.invalidate("key3")
    result = await temp_cache.get("key3")
    assert result is None


@pytest.mark.asyncio
async def test_clear(temp_cache):
    await temp_cache.set("a", 1)
    await temp_cache.set("b", 2)
    count = await temp_cache.clear()
    assert count == 2
    assert await temp_cache.get("a") is None


@pytest.mark.asyncio
async def test_corrupted_cache_file(temp_cache):
    await temp_cache.set("bad", "data")
    path = temp_cache._path("bad")
    path.write_text("not json!!!")
    result = await temp_cache.get("bad")
    assert result is None
    assert not path.exists()  # should be cleaned up


@pytest.mark.asyncio
async def test_ttl_zero_never_expires(tmp_path, monkeypatch):
    import time as _time

    cache = DiskCache(cache_dir=str(tmp_path), ttl=0)
    await cache.set("k", {"v": 1})
    real = _time.time()
    monkeypatch.setattr("app.services.cache.time.time", lambda: real + 10 * 365 * 86400)
    assert await cache.get("k") == {"v": 1}


@pytest.mark.asyncio
async def test_set_leaves_no_tmp_files(tmp_path):
    cache = DiskCache(cache_dir=str(tmp_path), ttl=60)
    await cache.set("k", "v")
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]
