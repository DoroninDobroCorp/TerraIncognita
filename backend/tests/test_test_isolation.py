"""The suite must never touch the data a developer's running server uses."""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.offline_manager import offline_manager


def test_offline_store_is_redirected_away_from_the_real_data_dir():
    real = Path(settings.offline_data_dir).resolve()
    assert Path(offline_manager._base).resolve() != real
    assert not Path(offline_manager._base).resolve().is_relative_to(real)


def test_resetting_the_store_leaves_a_real_regions_file_alone(tmp_path):
    real_dir = Path(settings.offline_data_dir) / "regions"
    real_dir.mkdir(parents=True, exist_ok=True)
    sentinel = real_dir / "isolation-sentinel.json"
    sentinel.write_text("{}")
    try:
        offline_manager._reset_store()
        assert sentinel.exists()
    finally:
        sentinel.unlink(missing_ok=True)
