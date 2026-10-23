"""The suite must never touch the cache/ and data/ a developer's running server uses."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.config import Settings, settings
from app.services.offline_manager import offline_manager

DEFAULTS = Settings(_env_file=None)  # the relative paths a real run uses: data/offline, cache, ...
REAL_DIRS = [
    Path(DEFAULTS.offline_data_dir), Path(DEFAULTS.journal_data_dir), Path(DEFAULTS.gamification_data_dir),
    Path(DEFAULTS.community_data_dir), Path(DEFAULTS.cache_dir),
]


def _snapshot() -> dict[str, list[str] | None]:
    return {
        str(p): sorted(str(x.relative_to(p)) for x in p.rglob("*")) if p.exists() else None for p in REAL_DIRS
    }


def test_offline_store_is_redirected_away_from_the_real_data_dir():
    real = Path(DEFAULTS.offline_data_dir).resolve()
    assert Path(offline_manager._base).resolve() != real
    assert not Path(offline_manager._base).resolve().is_relative_to(real)


def test_the_configured_cache_and_data_dirs_are_temporary_for_the_whole_session():
    tmp = Path(tempfile.gettempdir()).resolve()
    for name in ("cache_dir", "journal_data_dir", "gamification_data_dir", "offline_data_dir"):
        path = Path(getattr(settings, name)).resolve()
        assert path.is_relative_to(tmp) or path.is_relative_to(Path("/private/var")), (name, path)
    assert Path(settings.cache_dir).resolve() != Path(DEFAULTS.cache_dir).resolve()


def test_a_visit_in_a_test_writes_no_files_into_the_real_dirs():
    """The journal hook feeds gamification; both must run against temporary stores."""
    from app.services import journal

    before = _snapshot()
    asyncio.run(journal.create_visit("p1", 42.0, 18.0, "Isolation check"))
    assert _snapshot() == before


def test_the_suite_runs_on_shipped_defaults_whatever_the_developers_environment():
    """A developer's backend/.env or TERRA_* variables (the README tells people to create one) must not
    change what the tests see: conftest clears them before `app` is imported."""
    redirected = {"cache_dir", "journal_data_dir", "gamification_data_dir", "community_data_dir", "offline_data_dir"}
    drifted = {
        name: (getattr(settings, name), getattr(DEFAULTS, name))
        for name in Settings.model_fields
        if name not in redirected and getattr(settings, name) != getattr(DEFAULTS, name)
    }
    assert not drifted, drifted


def test_the_test_only_store_resets_do_not_litter_the_temp_directory():
    from app.services import gamification, journal

    before = len(list(Path(tempfile.gettempdir()).glob("tmp*")))
    for _ in range(25):
        journal._reset_store()
        gamification._reset_store()
    assert len(list(Path(tempfile.gettempdir()).glob("tmp*"))) == before  # they live under one cleaned-up root
