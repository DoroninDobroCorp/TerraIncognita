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


@pytest.mark.asyncio
async def test_old_entries_are_evicted_beyond_max_entries(tmp_path):
    cache = DiskCache(cache_dir=str(tmp_path), ttl=0, max_entries=10)
    for i in range(DiskCache._PRUNE_EVERY):
        await cache.set(f"k{i}", i)
        # distinct mtimes so "oldest" is well defined
        import os
        os.utime(cache._path(f"k{i}"), (i, i))
    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 10
    assert await cache.get("k49") == 49  # newest survives
    assert await cache.get("k0") is None  # oldest evicted


@pytest.mark.asyncio
async def test_eviction_works_when_every_write_uses_a_fresh_instance(tmp_path):
    """place_ask / walk_report / daily_place build a DiskCache per request; the bound must still hold."""
    DiskCache._writes_by_dir.pop(str(tmp_path), None)
    for i in range(120):
        await DiskCache(cache_dir=str(tmp_path), ttl=0, max_entries=10).set(f"k{i}", i)
    assert len(list(tmp_path.glob("*.json"))) <= 10 + DiskCache._PRUNE_EVERY


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"\xff\xfe\x00 not utf-8", b"null", b"[1, 2]", b'"text"', b"42", b'{"ts": 1}', b'{"data": 1, "ts": "x"}'])
async def test_any_unreadable_entry_is_a_miss_and_is_removed(temp_cache, content):
    await temp_cache.set("k", {"v": 1})
    [entry] = list(temp_cache._dir.glob("*.json"))
    entry.write_bytes(content)
    assert await temp_cache.get("k") in (None, 1)  # the last case is a valid entry holding the value 1
    if await temp_cache.get("k") is None:
        assert not entry.exists()  # it does not fail again on every request
