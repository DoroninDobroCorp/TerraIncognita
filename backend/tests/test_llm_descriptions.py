"""Tests for Story 2.2 — Place Description Generation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.chat import DescriptionRequest
from app.models.place import PlaceCategory
from app.services.llm_descriptions import (
    _extract_useful_metadata,
    _fallback_description,
    _parse_description_response,
    generate_description,
)


# ── Parse Description Response ───────────────────────────────


class TestParseDescriptionResponse:
    def test_structured_response(self):
        raw = """DESCRIPTION:
A haunting industrial ruin stands by the river. The old factory
whispers stories of its former glory.

PRACTICAL:
Best visited in daylight. Bring sturdy shoes and a flashlight."""

        desc, practical = _parse_description_response(raw)
        assert "haunting" in desc
        assert "daylight" in practical

    def test_unstructured_response(self):
        raw = "This is a beautiful old ruin by the sea."
        desc, practical = _parse_description_response(raw)
        assert "beautiful" in desc
        assert practical is None

    def test_empty_sections(self):
        raw = "DESCRIPTION:\nSome place.\n\nPRACTICAL:\nBring water."
        desc, practical = _parse_description_response(raw)
        assert desc == "Some place."
        assert practical == "Bring water."


# ── Metadata Extraction ──────────────────────────────────────


class TestExtractMetadata:
    def test_osm_tags(self):
        meta = {"osm_tags": {"historic": "castle", "tourism": "attraction"}}
        result = _extract_useful_metadata(meta)
        assert "castle" in result
        assert "attraction" in result

    def test_wikipedia_url(self):
        meta = {"wikipedia_url": "https://en.wikipedia.org/wiki/Something"}
        result = _extract_useful_metadata(meta)
        assert "wikipedia" in result.lower()

    def test_atlas_categories(self):
        meta = {"atlas_categories": "Abandoned, Underground"}
        result = _extract_useful_metadata(meta)
        assert "Abandoned" in result

    def test_empty_metadata(self):
        result = _extract_useful_metadata({})
        assert "minimal" in result


# ── Fallback Description ─────────────────────────────────────


class TestFallbackDescription:
    def test_with_name_and_categories(self):
        req = DescriptionRequest(
            place_id="p1",
            place_name="Old Fort",
            place_categories=[PlaceCategory.RUINS, PlaceCategory.MILITARY],
            lat=42.0, lng=19.0,
        )
        result = _fallback_description(req)
        assert result.place_id == "p1"
        assert "Old Fort" in result.description
        assert result.ai_generated is False

    def test_without_name(self):
        req = DescriptionRequest(
            place_id="p2",
            lat=42.0, lng=19.0,
        )
        result = _fallback_description(req)
        assert "This place" in result.description or "Unnamed" in result.description

    def test_fallback_has_practical_info(self):
        req = DescriptionRequest(
            place_id="p3",
            place_name="Cave System",
            place_categories=[PlaceCategory.CAVE],
            lat=42.0, lng=19.0,
        )
        result = _fallback_description(req)
        assert result.practical_info is not None


# ── Full Generation (mocked LLM) ────────────────────────────


class TestGenerateDescription:
    @pytest.fixture
    def desc_request(self):
        return DescriptionRequest(
            place_id="test-place-1",
            place_name="Abandoned Factory",
            place_categories=[PlaceCategory.ABANDONED, PlaceCategory.INDUSTRIAL],
            place_tags=["industrial", "ruins", "graffiti"],
            place_metadata={"osm_tags": {"building": "industrial", "abandoned": "yes"}},
            lat=42.65,
            lng=18.09,
            language="en",
        )

    @pytest.mark.asyncio
    async def test_successful_generation(self, desc_request):
        mock_response = """DESCRIPTION:
The abandoned factory looms like a steel cathedral against the sky.

PRACTICAL:
Visit during daylight. Bring a flashlight."""

        with patch(
            "app.services.llm_descriptions.cached_completion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await generate_description(desc_request)
            assert result.place_id == "test-place-1"
            assert "cathedral" in result.description
            assert result.practical_info is not None
            assert result.ai_generated is True

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self, desc_request):
        with patch(
            "app.services.llm_descriptions.cached_completion",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            result = await generate_description(desc_request)
            assert result.place_id == "test-place-1"
            assert result.ai_generated is False
            assert result.description  # Fallback should produce something

    @pytest.mark.asyncio
    async def test_auto_language_defaults_to_russian(self, desc_request):
        desc_request.language = "auto"

        with patch(
            "app.services.llm_descriptions.cached_completion",
            new_callable=AsyncMock,
            return_value="DESCRIPTION:\nОписание.\n\nPRACTICAL:\nСовет.",
        ) as mock_llm:
            result = await generate_description(desc_request)
            call_args = mock_llm.call_args
            messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
            prompt_text = messages[0]["content"]
            assert "Russian" in prompt_text
