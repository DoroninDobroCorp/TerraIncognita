"""Tests for disk cache."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

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
