"""Tests for Story 1.1 — OSM/Overpass source."""

from __future__ import annotations

import json

import pytest

from app.models.place import PlaceCategory, PlaceSource
from app.sources.osm import OSMSource


# Sample Overpass response
_OVERPASS_RESPONSE = {
    "elements": [
        {
            "type": "node",
            "id": 12345,
            "lat": 42.45,
            "lon": 18.53,
            "tags": {
                "name": "Old Bunker",
                "military": "bunker",
                "historic": "bunker",
                "abandoned": "yes",
            },
        },
        {
            "type": "way",
            "id": 67890,
            "center": {"lat": 42.44, "lon": 18.52},
            "tags": {
                "name": "Fortress Ruins",
                "historic": "ruins",
            },
        },
        {
            "type": "node",
            "id": 11111,
            "lat": 42.43,
            "lon": 18.51,
            "tags": {
                "name": "St. Nicholas Cathedral",
                "building": "cathedral",
            },
        },
        {
            "type": "node",
            "id": 22222,
            "lat": 42.46,
            "lon": 18.54,
            "tags": {"name": "Viewpoint"},  # no matching tags → empty categories
        },
    ],
}


class TestOSMParsing:
    def setup_method(self):
        self.source = OSMSource()

    def test_parse_node_with_coords(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        assert len(places) == 1
        p = places[0]
        assert p.id == "osm_node_12345"
        assert p.source == PlaceSource.OSM
        assert p.name == "Old Bunker"
        assert p.coordinates.lat == 42.45
        assert p.coordinates.lng == 18.53

    def test_parse_way_with_center(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][1]])
        assert len(places) == 1
        assert places[0].coordinates.lat == 42.44

    def test_category_classification_military(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        cats = places[0].categories
        assert PlaceCategory.MILITARY in cats
        assert PlaceCategory.ABANDONED in cats

    def test_category_classification_ruins(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][1]])
        assert PlaceCategory.RUINS in places[0].categories

    def test_category_classification_cathedral(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][2]])
        cats = places[2].categories if len(places) > 2 else []
        # cathedral is a classic landmark
        places_all = self.source._parse_elements(_OVERPASS_RESPONSE["elements"])
        cathedral = [p for p in places_all if "Cathedral" in (p.name or "")]
        assert len(cathedral) == 1
        assert PlaceCategory.RELIGIOUS in cathedral[0].categories

    def test_skip_element_without_coords(self):
        el = {"type": "relation", "id": 999, "tags": {"name": "X"}}
        places = self.source._parse_elements([el])
        assert len(places) == 0

    def test_all_elements_parsed(self):
        places = self.source._parse_elements(_OVERPASS_RESPONSE["elements"])
        assert len(places) == 4

    def test_confidence_with_categories(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        assert places[0].confidence == 0.7

    def test_confidence_without_categories(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][3]])
        assert places[0].confidence == 0.4

    def test_tags_extraction(self):
        places = self.source._parse_elements([_OVERPASS_RESPONSE["elements"][0]])
        tags = places[0].tags
        assert any("military" in t for t in tags)
