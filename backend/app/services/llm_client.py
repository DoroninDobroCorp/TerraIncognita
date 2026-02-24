"""LLM client wrapper with Anthropic (primary) + OpenAI (fallback). Error handling, caching, circuit breaker."""

from __future__ import annotations

import logging
import time
from typing import Any

import anthropic

from app.config import settings
from app.services.cache import DiskCache

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None
_openai_client: Any = None
_description_cache = DiskCache(
    cache_dir=f"{settings.cache_dir}/llm_descriptions",
    ttl=settings.llm_cache_ttl_seconds,
)

# Circuit breaker state
_circuit_open_until: float = 0.0
_consecutive_failures: int = 0
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_RESET_SECONDS = 60

# Token usage tracking
_total_input_tokens: int = 0
_total_output_tokens: int = 0


def _get_provider() -> str:
    """Determine which provider to use based on config and available keys."""
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return "openai"
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.openai_api_key:
        return "openai"
    raise ValueError("No LLM API key configured (set TERRA_ANTHROPIC_API_KEY or TERRA_OPENAI_API_KEY)")


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise ValueError("TERRA_ANTHROPIC_API_KEY is not set")
        _client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout,
        )
    return _client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        import openai
        if not settings.openai_api_key:
            raise ValueError("TERRA_OPENAI_API_KEY is not set")
        _openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _check_circuit_breaker() -> None:
    """Raise if circuit breaker is open (too many recent failures)."""
    global _circuit_open_until
    if _circuit_open_until > 0 and time.monotonic() < _circuit_open_until:
        raise ConnectionError(
            f"LLM circuit breaker open, retry after {_circuit_open_until - time.monotonic():.0f}s"
        )


def _record_success() -> None:
    global _consecutive_failures, _circuit_open_until
    _consecutive_failures = 0
    _circuit_open_until = 0.0


def _record_failure() -> None:
    global _consecutive_failures, _circuit_open_until
    _consecutive_failures += 1
    if _consecutive_failures >= _CIRCUIT_FAILURE_THRESHOLD:
        _circuit_open_until = time.monotonic() + _CIRCUIT_RESET_SECONDS
        logger.warning(
            "LLM circuit breaker OPEN after %d failures, reset in %ds",
            _consecutive_failures, _CIRCUIT_RESET_SECONDS,
        )


async def chat_completion(
    messages: list[dict[str, str]],
    system: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Send messages to LLM (Anthropic or OpenAI) and return the text response."""
    global _total_input_tokens, _total_output_tokens

    _check_circuit_breaker()
    provider = _get_provider()

    if provider == "openai":
        return await _openai_completion(messages, system, temperature, max_tokens)

    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature

    try:
        response = await client.messages.create(**kwargs)
        _record_success()

        # Track token usage
        if hasattr(response, "usage") and response.usage:
            _total_input_tokens += response.usage.input_tokens
            _total_output_tokens += response.usage.output_tokens
            logger.info(
                "LLM tokens: in=%d out=%d (total: in=%d out=%d)",
                response.usage.input_tokens, response.usage.output_tokens,
                _total_input_tokens, _total_output_tokens,
            )

        return response.content[0].text
    except anthropic.APIError as e:
        _record_failure()
        logger.error("Anthropic API error: %s", e)
        raise
    except Exception:
        _record_failure()
        logger.exception("Unexpected LLM error")
        raise


async def _openai_completion(
    messages: list[dict[str, str]],
    system: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """OpenAI fallback completion."""
    global _total_input_tokens, _total_output_tokens
    client = _get_openai_client()

    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    oai_messages.extend(messages)

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=oai_messages,
            temperature=temperature or settings.llm_temperature,
            max_tokens=max_tokens or settings.llm_max_tokens,
        )
        _record_success()

        if response.usage:
            _total_input_tokens += response.usage.prompt_tokens
            _total_output_tokens += response.usage.completion_tokens
            logger.info(
                "OpenAI tokens: in=%d out=%d (total: in=%d out=%d)",
                response.usage.prompt_tokens, response.usage.completion_tokens,
                _total_input_tokens, _total_output_tokens,
            )

        return response.choices[0].message.content
    except Exception as e:
        _record_failure()
        logger.error("OpenAI API error: %s", e)
        raise


async def cached_completion(
    cache_key: str,
    messages: list[dict[str, str]],
    system: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Chat completion with disk cache lookup."""
    cached = await _description_cache.get(cache_key)
    if cached is not None:
        logger.debug("LLM cache hit: %s", cache_key)
        return cached

    result = await chat_completion(messages, system, temperature, max_tokens)
    await _description_cache.set(cache_key, result)
    return result


def get_llm_usage() -> dict[str, Any]:
    """Return current LLM usage statistics."""
    return {
        "total_input_tokens": _total_input_tokens,
        "total_output_tokens": _total_output_tokens,
        "circuit_breaker_open": _circuit_open_until > 0 and time.monotonic() < _circuit_open_until,
        "consecutive_failures": _consecutive_failures,
        "api_key_configured": bool(settings.anthropic_api_key),
    }


async def check_llm_health() -> dict[str, Any]:
    """Quick health check for LLM connectivity."""
    try:
        provider = _get_provider()
    except ValueError:
        return {"status": "no_api_key", "provider": "none"}

    status: dict[str, Any] = {
        "provider": provider,
        "model": settings.openai_model if provider == "openai" else settings.llm_model,
        "api_key_configured": True,
    }
    if _circuit_open_until > 0 and time.monotonic() < _circuit_open_until:
        status["status"] = "circuit_breaker_open"
        return status
    status["status"] = "configured"
    return status


async def close_llm_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
