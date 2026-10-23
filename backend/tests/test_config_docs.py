"""The shipped .env.example and the docs must describe settings that really exist."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / "backend" / ".env.example"
FIELDS = {name.upper() for name in Settings.model_fields}


def _assignments() -> list[tuple[str, str]]:
    """(KEY, raw value) for every `KEY=value` line, commented-out or not."""
    return re.findall(r"^#?\s*TERRA_([A-Z0-9_]+)=(.*)$", ENV_EXAMPLE.read_text(), flags=re.M)


def test_every_documented_key_is_a_real_setting():
    unknown = [key for key, _ in _assignments() if key not in FIELDS]
    assert not unknown, f".env.example documents settings that do not exist: {unknown}"


def test_no_inline_comments_after_values():
    """systemd EnvironmentFile= and docker --env-file keep a trailing '# ...' as part of the value."""
    bad = [(key, val) for key, val in _assignments() if re.search(r"\s#", val)]
    assert not bad, f"inline comments would end up inside the values: {bad}"


def test_uncommented_example_loads_cleanly(tmp_path, monkeypatch):
    """Uncomment every assignment, parse it the way systemd would (no comment stripping), and load Settings."""
    for key, val in _assignments():
        monkeypatch.setenv(f"TERRA_{key}", val)
    monkeypatch.chdir(tmp_path)  # no stray .env
    settings = Settings(_env_file=None)
    assert settings.llm_daily_token_cap == 500000
    assert settings.llm_effort == "low"
    assert settings.csp == "default"


def test_documented_names_in_markdown_exist():
    names: set[str] = set()
    for md in ROOT.rglob("*.md"):
        if ".git" in md.parts or "node_modules" in md.parts:
            continue
        names |= set(re.findall(r"TERRA_([A-Z][A-Z0-9_]{3,})", md.read_text(encoding="utf-8")))
    unknown = sorted(n for n in names if n not in FIELDS and not n.endswith("_"))
    assert not unknown, f"docs mention settings that do not exist: {unknown}"
