"""Shared test fixtures."""

from __future__ import annotations

import os

# The suite assumes the shipped defaults. A developer's own configuration (TERRA_* variables,
# backend/.env — README tells newcomers to create one) must not change test outcomes, so it is
# cleared before anything from `app` is imported and the settings object is reset to defaults.
for _key in [k for k in os.environ if k.startswith("TERRA_")]:
    del os.environ[_key]

import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402

from app.config import Settings, settings  # noqa: E402

_defaults = Settings(_env_file=None)
for _name in Settings.model_fields:
    setattr(settings, _name, getattr(_defaults, _name))

# Module-level caches and stores (disk_cache, offline_manager, the LLM caches, ...) are built at import time
# from these relative paths. Pointing them into a throw-away directory first means a test run never reads,
# writes or deletes the cache/ and data/ of the checkout it runs in — which on a server is the live one.
SESSION_ROOT = tempfile.mkdtemp(prefix="terra-tests-")
atexit.register(shutil.rmtree, SESSION_ROOT, ignore_errors=True)
settings.cache_dir = f"{SESSION_ROOT}/cache"
for _name in ("journal", "gamification", "community", "offline"):
    setattr(settings, f"{_name}_data_dir", f"{SESSION_ROOT}/{_name}")

from app.middleware import limiter  # noqa: E402
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_offline_store(tmp_path_factory, monkeypatch):
    """The offline manager is a module-level singleton bound to ``data/offline`` at import time,
    and its tests wipe the store (``_reset_store``) — which would delete a developer's real
    offline regions when the suite runs from the same directory as the server."""
    from app.services.offline_manager import offline_manager

    base = tmp_path_factory.mktemp("offline")  # not tmp_path: tests list that directory
    for attr, path in {
        "_base": base,
        "_regions_dir": base / "regions",
        "_places_dir": base / "places",
        "_queue_dir": base / "sync_queue",
        "_meta_file": base / "meta.json",
    }.items():
        monkeypatch.setattr(offline_manager, attr, path)
    for d in (base / "regions", base / "places", base / "sync_queue"):
        d.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture(autouse=True)
def _isolated_user_data(tmp_path_factory, monkeypatch):
    """Journal, gamification and community are module-level singletons bound to ``data/…`` at first use.
    A journal test creates visits, a visit triggers the gamification hook: without this, running
    ``pytest`` from the server's directory writes XP/fog/visits into the real stores."""
    from app.services import community as community_module
    from app.services import gamification, journal, llm_client

    monkeypatch.setattr(settings, "community_data_dir", str(tmp_path_factory.mktemp("community")))
    monkeypatch.setattr(community_module, "_community_service", None)
    # the persisted daily LLM token counter: tests that record usage must not touch the real one
    monkeypatch.setattr(llm_client, "_daily_usage_path", tmp_path_factory.mktemp("llm_state") / "usage.json")
    monkeypatch.setattr(llm_client, "_daily", {"date": "", "input": 0, "output": 0})
    journal._reset_store()  # both resets also point their store at a fresh temp directory
    gamification._reset_store()
    yield


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """The limiter is process-global; without a reset, tests starve each other."""
    limiter.reset()
    yield


@pytest.fixture
def sample_place() -> Place:
    return Place(
        id="test_1",
        source=PlaceSource.OSM,
        sources=[PlaceSource.OSM],
        name="Test Bunker",
        description="An old military bunker",
        categories=[PlaceCategory.MILITARY, PlaceCategory.UNDERGROUND],
        coordinates=Coordinates(lat=42.45, lng=18.53),
        confidence=0.8,
        tags=["military=bunker", "historic=yes"],
        photos=["https://example.com/photo.jpg"],
    )


@pytest.fixture
def sample_places() -> list[Place]:
    return [
        Place(
            id=f"test_{i}",
            source=PlaceSource.OSM,
            name=f"Place {i}",
            categories=[PlaceCategory.LANDMARK],
            coordinates=Coordinates(lat=42.45 + i * 0.01, lng=18.53 + i * 0.01),
            confidence=0.5 + i * 0.1,
        )
        for i in range(5)
    ]
