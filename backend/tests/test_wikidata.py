"""Tests for Story 1.3 — Wikidata source."""

from __future__ import annotations

import pytest

from app.models.place import PlaceCategory, PlaceSource
from app.sources.wikidata import WikidataSource


_SPARQL_RESPONSE = {
    "results": {
        "bindings": [
            {
                "item": {"value": "http://www.wikidata.org/entity/Q12345"},
                "itemLabel": {"value": "Ancient Fortress"},
                "itemDescription": {"value": "A medieval fortress"},
                "coord": {"value": "Point(18.53 42.45)"},
                "type": {"value": "http://www.wikidata.org/entity/Q57821"},
                "sitelink": {"value": "https://en.wikipedia.org/wiki/Ancient_Fortress"},
            },
            {
                "item": {"value": "http://www.wikidata.org/entity/Q67890"},
                "itemLabel": {"value": "Hidden Cave"},
                "itemDescription": {"value": "A natural cave system"},
                "coord": {"value": "Point(18.52 42.44)"},
                "type": {"value": "http://www.wikidata.org/entity/Q35509"},
            },
            {
                "item": {"value": "http://www.wikidata.org/entity/Q11111"},
                "itemLabel": {"value": "Q11111"},
                "coord": {"value": "Point(18.51 42.43)"},
                "type": {"value": "http://www.wikidata.org/entity/Q82117"},
            },
        ]
    }
}


class TestWikidata:
    def setup_method(self):
        self.source = WikidataSource()

    def test_parse_results_count(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert len(places) == 3

    def test_place_id_format(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[0].id == "wiki_Q12345"

    def test_source_set(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[0].source == PlaceSource.WIKIDATA

    def test_coordinates_parsing(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        # Point(lng lat) → lat=42.45, lng=18.53
        assert places[0].coordinates.lat == pytest.approx(42.45)
        assert places[0].coordinates.lng == pytest.approx(18.53)

    def test_category_mapping_fortification(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert PlaceCategory.MILITARY in places[0].categories

    def test_category_mapping_cave(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert PlaceCategory.CAVE in places[1].categories
        assert PlaceCategory.UNDERGROUND in places[1].categories

    def test_category_mapping_ruin(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert PlaceCategory.RUINS in places[2].categories

    def test_confidence_with_wikipedia(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        # Has Wikipedia → less unusual → lower confidence
        assert places[0].confidence == 0.6

    def test_confidence_without_wikipedia(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        # No Wikipedia → more unusual → higher confidence
        assert places[1].confidence == 0.7

    def test_name_set_to_none_when_equals_qid(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[2].name is None

    def test_metadata_contains_wikidata_id(self):
        places = self.source._parse_results(_SPARQL_RESPONSE)
        assert places[0].metadata["wikidata_id"] == "Q12345"
        assert places[0].metadata["wikipedia_url"].startswith("https://")

    def test_dedup_same_qid(self):
        data = {
            "results": {
                "bindings": [
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q999"},
                        "itemLabel": {"value": "Place"},
                        "coord": {"value": "Point(18.5 42.4)"},
                        "type": {"value": "http://www.wikidata.org/entity/Q82117"},
                    },
                    {
                        "item": {"value": "http://www.wikidata.org/entity/Q999"},
                        "itemLabel": {"value": "Place"},
                        "coord": {"value": "Point(18.5 42.4)"},
                        "type": {"value": "http://www.wikidata.org/entity/Q57821"},
                    },
                ]
            }
        }
        places = self.source._parse_results(data)
        assert len(places) == 1

    def test_parse_point_invalid(self):
        lat, lng = WikidataSource._parse_point("invalid")
        assert lat is None
        assert lng is None

    def test_parse_point_valid(self):
        lat, lng = WikidataSource._parse_point("Point(18.53 42.45)")
        assert lat == pytest.approx(42.45)
        assert lng == pytest.approx(18.53)
