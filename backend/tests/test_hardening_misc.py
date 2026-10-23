"""Smaller defects: category styles for every category, surprise-mode aliasing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.models.place import Coordinates, Place, PlaceCategory, PlaceSource
from app.services.route_builder import _apply_surprise_mode

client = TestClient(app)


def test_every_place_category_has_a_style_and_is_in_the_all_preset():
    """culinary / underwater (deep research) had no style: the UI builds its category list and
    its 'all' filter from this endpoint, so those places could never be shown."""
    config = client.get("/api/map/config").json()
    presets = client.get("/api/map/presets").json()
    for cat in PlaceCategory:
        assert cat.value in config["category_styles"], cat.value
        assert cat.value in presets["all"]["categories"], cat.value


def test_surprise_mode_does_not_write_into_the_original_places():
    """model_copy() is shallow: the 'hidden' copy shared the original's metadata dict, so the
    real name/description were written into the (possibly cached) source place."""
    original = Place(
        id="p1", source=PlaceSource.OSM, name="Secret Bunker", description="Real description",
        coordinates=Coordinates(lat=42.4, lng=18.7), metadata={"osm_tags": {"military": "bunker"}},
    )
    hidden = _apply_surprise_mode([original])[0]
    assert hidden.name == "Mystery Stop #1"
    assert "_surprise_original_name" not in original.metadata
    assert "_surprise_original_description" not in original.metadata
    assert original.name == "Secret Bunker"
    assert hidden.metadata["_surprise_original_name"] == "Secret Bunker"
    assert hidden.metadata["osm_tags"] == {"military": "bunker"}
