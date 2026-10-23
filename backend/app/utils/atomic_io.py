"""Crash-safe file helpers shared by the JSON-file stores.

* :func:`atomic_write_text` — write to a uniquely named temp file in the same directory,
  fsync, then ``os.replace``: readers never see a half-written file and two writers never
  share one temp file.
* :func:`load_json_file` — parse a JSON file; if it exists but cannot be parsed, keep a
  timestamped copy next to it (``<name>.corrupt-<utc>``) before the caller starts "fresh",
  so the next save does not silently destroy the only copy of the user's data.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, data: str) -> None:
    """Atomically replace ``path`` with ``data`` (UTF-8)."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:  # pragma: no cover - fsync unsupported on some filesystems
                pass
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def quarantine_corrupt(path: Path, reason: str) -> Path | None:
    """Copy an unreadable store file to ``<name>.corrupt-<utc timestamp>``; returns the copy."""
    path = Path(path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        shutil.copy2(path, backup)
    except OSError:
        logger.error("Could not back up unreadable store file %s (%s)", path, reason)
        return None
    logger.error("Store file %s is unreadable (%s); original kept as %s", path, reason, backup)
    return backup


def load_json_file(path: Path) -> Any | None:
    """Return the parsed JSON document, or ``None`` if missing/unreadable.

    An existing-but-unparseable file is quarantined (see module docstring) exactly once per
    call, so callers must load each store once (not on every request).
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        quarantine_corrupt(path, str(exc))
        return None


def load_model_records(path: Path, model: type[BaseModel]) -> dict[str, Any]:
    """Load a ``{id: record}`` store file into validated pydantic models.

    Individual invalid records are skipped (the rest of the store stays usable) and the
    original file is quarantined first, because the next save rewrites it from memory.
    """
    raw = load_json_file(path)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        quarantine_corrupt(path, "top-level JSON value is not an object")
        return {}
    records: dict[str, Any] = {}
    bad = 0
    for key, value in raw.items():
        try:
            records[key] = model.model_validate(value)
        except Exception:
            bad += 1
    if bad:
        quarantine_corrupt(path, f"{bad} record(s) failed validation")
    return records
