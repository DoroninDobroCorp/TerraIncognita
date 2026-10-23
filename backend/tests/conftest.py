"""Shared test fixtures."""

from __future__ import annotations

import pytest

from app.middleware import limiter
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource


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
