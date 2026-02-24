"""Smoke tests for explorer.html — validates structure, SRI, and critical elements."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_MAP_DIR = Path(__file__).resolve().parent.parent.parent / "map"
_HTML_PATH = _MAP_DIR / "explorer.html"


@pytest.fixture
def html_content() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


def test_html_is_valid_document(html_content: str):
    """Check basic HTML5 structure."""
    assert html_content.startswith("<!DOCTYPE html>")
    assert "<html" in html_content
    assert "</html>" in html_content
    assert "<head>" in html_content
    assert "</head>" in html_content
    assert "<body" in html_content
    assert "</body>" in html_content


def test_cdn_resources_have_sri(html_content: str):
    """All external CDN resources must have SRI integrity hashes."""
    # Find all script/link tags with external URLs
    external_scripts = re.findall(r'<script[^>]+src="https?://[^"]+[^>]*>', html_content)
    for tag in external_scripts:
        assert "integrity=" in tag, f"External script missing SRI: {tag[:80]}"
        assert 'crossorigin="anonymous"' in tag, f"Missing crossorigin: {tag[:80]}"

    external_links = re.findall(r'<link[^>]+href="https?://[^"]+[^>]*>', html_content)
    for tag in external_links:
        assert "integrity=" in tag, f"External link missing SRI: {tag[:80]}"
        assert 'crossorigin="anonymous"' in tag, f"Missing crossorigin: {tag[:80]}"


def test_critical_ui_elements_exist(html_content: str):
    """Key UI elements required by Epic 3 stories must be present."""
    required_ids = [
        "map",            # Main map container (Story 3.1)
        "search-input",   # Search bar (Story 3.2)
        "filter-panel",   # Category filter panel (Story 3.3)
        "place-card",     # Place detail card (Story 3.2)
    ]
    for elem_id in required_ids:
        assert f'id="{elem_id}"' in html_content, f"Missing required element: #{elem_id}"


def test_fog_of_war_elements(html_content: str):
    """Fog of War (Story 3.4) requires specific UI elements."""
    assert "fog" in html_content.lower()
    assert "fog-stats" in html_content
    assert "fogEnabled" in html_content


def test_historical_overlay_elements(html_content: str):
    """Historical overlay (Story 3.5) requires control elements."""
    assert "historical" in html_content.lower()
    assert "opacity" in html_content.lower()


def test_viewport_meta_tag(html_content: str):
    """Mobile viewport meta must be present for responsive design."""
    assert 'name="viewport"' in html_content
    assert "width=device-width" in html_content


def test_no_inline_api_keys(html_content: str):
    """No hardcoded API keys in the HTML."""
    # Common patterns for API keys
    assert "sk-" not in html_content, "Potential API key found"
    assert "api_key=" not in html_content.lower(), "Potential API key param found"


def test_maplibre_version_pinned(html_content: str):
    """MapLibre GL JS version should be pinned (not latest)."""
    match = re.search(r'maplibre-gl@([\d.]+)', html_content)
    assert match, "MapLibre GL JS version not found"
    version = match.group(1)
    assert version == "4.1.2", f"Expected pinned version 4.1.2, got {version}"


def test_category_icons_defined(html_content: str):
    """Category icon mapping must be defined for place rendering."""
    assert "CATEGORIES" in html_content


def test_accessibility_lang_attribute(html_content: str):
    """HTML lang attribute should be set."""
    assert re.search(r'<html\s+lang="[a-z]{2}"', html_content)
