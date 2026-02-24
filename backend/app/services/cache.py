"""Disk-based async cache with TTL support."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class DiskCache:
    """Simple file-system cache. Each entry is a JSON file with metadata."""

    def __init__(self, cache_dir: str | None = None, ttl: int | None = None) -> None:
        self._dir = Path(cache_dir or settings.cache_dir)
        self._ttl = ttl or settings.cache_ttl_seconds
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self._dir / f"{h}.json"

    async def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - raw.get("ts", 0) > self._ttl:
                path.unlink(missing_ok=True)
                return None
            return raw["data"]
        except (json.JSONDecodeError, KeyError):
            path.unlink(missing_ok=True)
            return None

    async def set(self, key: str, data: Any) -> None:
        path = self._path(key)
        payload = {"ts": time.time(), "key": key, "data": data}
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")

    async def invalidate(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def clear(self) -> int:
        """Remove all cache files. Returns count removed."""
        count = 0
        for f in self._dir.glob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
        return count


# Module-level singleton
disk_cache = DiskCache()
