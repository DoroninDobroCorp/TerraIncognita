"""Shared test fixtures."""

from __future__ import annotations

import pytest

from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource


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
