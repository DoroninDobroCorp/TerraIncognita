"""Crash-safe file helpers shared by the JSON-file stores.

* :func:`atomic_write_text` — write to a uniquely named temp file in the same directory,
  fsync, then ``os.replace``: readers never see a half-written file and two writers never
  share one temp file.
* :func:`load_json_file` — parse a JSON file; if it exists but cannot be parsed, keep a
  timestamped copy next to it (``<name>.corrupt-<utc>``) before the caller starts "fresh",
  so the next save does not silently destroy the only copy of the user's data.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


_scratch_root: Path | None = None


def scratch_dir() -> Path:
    """A fresh empty directory under one per-process temp root that is deleted at exit (for the
    test-only ``_reset_store`` helpers, which used to leave an empty directory per call)."""
    global _scratch_root
    if _scratch_root is None:
        _scratch_root = Path(tempfile.mkdtemp(prefix="terra-scratch-"))
        atexit.register(shutil.rmtree, _scratch_root, ignore_errors=True)
    return Path(tempfile.mkdtemp(dir=_scratch_root))


def atomic_write_text(path: Path, data: str) -> None:
    """Atomically replace ``path`` with ``data`` (UTF-8)."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        # errors="replace": a lone surrogate (valid in a Python str, impossible in UTF-8) must not make the
        # save fail half-way — with the in-memory state already changed, the store would stay broken.
        with open(tmp, "w", encoding="utf-8", errors="replace") as f:
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


class StoreUnreadableError(RuntimeError):
    """A store file exists but can be neither read nor moved aside: starting from an empty store
    would let the next save overwrite the only copy, so the service refuses to start instead."""


def quarantine_corrupt(path: Path, reason: str, *, keep_original: bool = False) -> Path:
    """Preserve an unreadable store file as ``<name>.corrupt-<utc timestamp>`` and return that path.

    By default the file is *moved*: one that cannot be read (permissions, I/O error) can often still
    be renamed, and a rename leaves nothing behind for the next save to overwrite. With
    ``keep_original`` it is copied instead — for a file that is only partly damaged, whose valid
    records are about to be used: removing it would show an empty store after a restart that comes
    before the next save. If preserving fails, ``StoreUnreadableError`` stops the load.
    """
    path = Path(path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        if keep_original:
            shutil.copy2(path, backup)
        else:
            os.replace(path, backup)
    except OSError as exc:
        logger.error("Could not move aside unreadable store file %s (%s): %s", path, reason, exc)
        raise StoreUnreadableError(f"{path} is unreadable ({reason}) and cannot be preserved") from exc
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
        quarantine_corrupt(path, f"{bad} record(s) failed validation", keep_original=True)
    return records
