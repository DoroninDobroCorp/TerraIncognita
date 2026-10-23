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

    _PRUNE_EVERY = 50  # writes between size checks

    def __init__(
        self,
        cache_dir: str | None = None,
        ttl: int | None = None,
        max_entries: int | None = None,
    ) -> None:
        """``ttl`` in seconds; ``None`` = default from settings, ``0`` = never expires.

        ``max_entries`` bounds the directory (``None`` = settings default): when exceeded,
        the least recently written files are removed.
        """
        self._dir = Path(cache_dir or settings.cache_dir)
        self._ttl = settings.cache_ttl_seconds if ttl is None else ttl
        self._max_entries = settings.cache_max_entries if max_entries is None else max_entries
        self._writes = 0
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
            if self._ttl and time.time() - raw.get("ts", 0) > self._ttl:
                path.unlink(missing_ok=True)
                return None
            return raw["data"]
        except (json.JSONDecodeError, KeyError):
            path.unlink(missing_ok=True)
            return None

    async def set(self, key: str, data: Any) -> None:
        path = self._path(key)
        payload = {"ts": time.time(), "key": key, "data": data}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)  # atomic: readers never see a half-written entry
        self._writes += 1
        if self._max_entries and self._writes % self._PRUNE_EVERY == 0:
            self._prune()

    def _prune(self) -> None:
        files = sorted(self._dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for f in files[: max(len(files) - self._max_entries, 0)]:
            f.unlink(missing_ok=True)

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
