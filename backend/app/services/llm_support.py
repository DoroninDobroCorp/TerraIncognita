"""Shared plumbing for optional, cheap LLM features (daily story, ask-the-place, walk report).

Everything here is about *degrading gracefully*: callers learn "the LLM is not usable right
now" through one exception type and never see provider error text (which could echo prompts).
All calls still go through ``services.llm_client`` so the daily token budget, circuit breaker
and usage accounting keep applying.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable

from app.services import llm_client
from app.services.cache import DiskCache
from app.utils.sanitize import sanitize_user_input

logger = logging.getLogger(__name__)

# Languages the model is explicitly asked to write in. Anything else falls back, so the
# number of distinct cache entries (and LLM calls) per place is bounded by this table.
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "ru": "Russian", "uk": "Ukrainian", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese", "pl": "Polish", "cs": "Czech",
    "sr": "Serbian", "hr": "Croatian", "tr": "Turkish", "nl": "Dutch",
}

UNAVAILABLE_MESSAGES: dict[str, str] = {
    "no_key": "AI features are not configured on this server (no API key).",
    "budget": "Daily AI budget reached, try again after 00:00 UTC.",
    "circuit_open": "The AI service is temporarily unavailable, try again in a minute.",
    "error": "The AI service could not answer right now, try again later.",
}


class LLMUnavailableError(RuntimeError):
    """The LLM cannot be used for this call. ``reason`` is a key of UNAVAILABLE_MESSAGES."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @property
    def message(self) -> str:
        return UNAVAILABLE_MESSAGES.get(self.reason, UNAVAILABLE_MESSAGES["error"])


def language_instruction(code: str) -> str:
    """System-prompt sentence for an already validated language code."""
    name = LANGUAGE_NAMES.get(code)
    return f"Answer in {name}." if name else "Answer in the same language as the question."


def effective_language(code: str | None, default: str = "en") -> str:
    """Map a request language to one we generate for (``default`` for auto/unknown codes)."""
    code = (code or "").lower()
    return code if code in LANGUAGE_NAMES else default


async def llm_unavailable_reason() -> str | None:
    """Why the LLM cannot be called right now, or None if it can. Never calls the network."""
    if llm_client.budget_exhausted():
        return "budget"
    status = (await llm_client.check_llm_health()).get("status")
    if status == "no_api_key":
        return "no_key"
    if status == "circuit_breaker_open":
        return "circuit_open"
    return None


async def complete_text(
    system: str, user: str, *, max_tokens: int, temperature: float = 0.5
) -> str:
    """One LLM completion. Any failure becomes LLMUnavailableError (details only in the log)."""
    try:
        text = await llm_client.chat_completion(
            [{"role": "user", "content": user}],
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except llm_client.LLMBudgetError as exc:
        raise LLMUnavailableError("budget") from exc
    except ConnectionError as exc:  # circuit breaker open
        raise LLMUnavailableError("circuit_open") from exc
    except llm_client.LLMResponseError as exc:  # cut off, refused or empty: the call worked, the answer is unusable
        logger.info("LLM reply not usable: %s", exc)
        raise LLMUnavailableError("error") from exc
    except ValueError as exc:  # llm_client raises ValueError when no API key is configured
        raise LLMUnavailableError("no_key" if "key" in str(exc).lower() else "error") from exc
    except Exception as exc:
        # Log the type only: provider messages may quote request content.
        logger.warning("LLM call failed: %s", type(exc).__name__)
        raise LLMUnavailableError("error") from exc
    text = (text or "").strip()
    if not text:
        raise LLMUnavailableError("error")
    return text


# Concurrent identical requests share one LLM call. Keyed per event loop so a task left over
# from a closed loop (tests) can never be awaited from another one.
_inflight: dict[tuple[int, str], asyncio.Future[str]] = {}


async def cached_llm_text(
    cache: DiskCache, key: str, generate: Callable[[], Awaitable[str]]
) -> str:
    """Return the cached text for ``key``, else generate it once and cache it.

    A cache hit never needs the LLM (so it works with no key / spent budget). Raises
    LLMUnavailableError when generation is needed but impossible. Failures are not cached.
    """
    hit = await cache.get(key)
    if hit:
        return hit
    reason = await llm_unavailable_reason()
    if reason:
        raise LLMUnavailableError(reason)

    slot = (id(asyncio.get_running_loop()), key)
    task = _inflight.get(slot)
    if task is None:

        async def _run() -> str:
            text = await generate()
            await cache.set(key, text)
            return text

        task = asyncio.ensure_future(_run())
        _inflight[slot] = task

        def _cleanup(t: asyncio.Future[str]) -> None:
            _inflight.pop(slot, None)
            if not t.cancelled():
                t.exception()  # mark retrieved even if every waiter went away

        task.add_done_callback(_cleanup)
    # shield: one impatient client disconnecting must not cancel the shared generation
    return await asyncio.shield(task)


def cache_key(*parts: str) -> str:
    """Stable key from arbitrary text parts (hashing keeps user text out of file names)."""
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return digest[:40]


# ── Prompt hygiene ───────────────────────────────────────────────

_ANGLE_BRACKETS = re.compile(r"[<>]")
_WHITESPACE = re.compile(r"\s+")


def clean_field(text: object, max_len: int) -> str:
    """Make an untrusted string safe to quote inside a prompt.

    Single line (a newline could fake a new "role: ..." line), no control or invisible
    formatting characters, no ``<``/``>`` (so it can never close or forge the XML-ish
    delimiters we wrap data in), known injection markers neutralised, length-limited.
    """
    if not isinstance(text, str) or not text:
        return ""
    text = text[: max_len * 4]  # bound the work below; the real cut comes after cleaning
    text = "".join(
        ch for ch in text
        if ch.isspace() or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn")
    )
    text = sanitize_user_input(text)
    text = _ANGLE_BRACKETS.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:max_len].strip()


def trim_to_sentence(text: str, limit: int) -> str:
    """Cap ``text`` at ``limit`` characters, preferring to end on a sentence boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(mark) for mark in (".", "!", "?", "…", "。"))
    if end >= limit // 2:
        return cut[: end + 1]
    return cut.rstrip() + "…"
