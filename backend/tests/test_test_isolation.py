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


def test_a_visit_in_a_test_writes_no_files_into_the_real_data_dirs(tmp_path):
    """The journal hook feeds gamification; both must run against temporary stores."""
    import asyncio
    from pathlib import Path

    from app.services import journal

    real = [Path(settings.journal_data_dir), Path(settings.gamification_data_dir)]
    before = {str(p): sorted(x.name for x in p.glob("*.json")) if p.exists() else None for p in real}
    asyncio.run(journal.create_visit("p1", 42.0, 18.0, "Isolation check"))
    after = {str(p): sorted(x.name for x in p.glob("*.json")) if p.exists() else None for p in real}
    assert before == after


def test_the_suite_runs_on_shipped_defaults_whatever_the_developers_environment():
    """A developer's backend/.env or TERRA_* variables (the README tells people to create one) must not
    change what the tests see: conftest clears them before `app` is imported."""
    from app.config import Settings

    defaults = Settings(_env_file=None)
    drifted = {
        name: (getattr(settings, name), getattr(defaults, name))
        for name in Settings.model_fields
        if getattr(settings, name) != getattr(defaults, name)
        and name != "community_data_dir"  # redirected to a temp dir on purpose (see conftest)
    }
    assert not drifted, drifted
