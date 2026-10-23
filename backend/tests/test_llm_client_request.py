"""What we send to / read from the Anthropic SDK — checked against the real SDK signature.

The LLM tests elsewhere replace the client with a mock, which cannot notice when the SDK
stops accepting an argument (it did: ``temperature``) or when the reply starts with a
thinking block. These tests pin both ends of that boundary.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from app.config import settings
from app.services import llm_client

MSGS = [{"role": "user", "content": "hi"}]


def _create_params() -> set[str]:
    return set(inspect.signature(anthropic.AsyncAnthropic(api_key="x").messages.create).parameters)


class TestRequest:
    def test_every_argument_exists_on_the_real_sdk_method(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_effort", "low")
        kwargs = llm_client._anthropic_request(MSGS, "be brief", 256)
        assert set(kwargs) <= _create_params()

    def test_no_sampling_parameters_are_sent(self):
        kwargs = llm_client._anthropic_request(MSGS, None, None)
        assert not {"temperature", "top_p", "top_k"} & set(kwargs)

    def test_effort_is_configurable_and_can_be_omitted(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_effort", "medium")
        assert llm_client._anthropic_request(MSGS, None, None)["output_config"] == {"effort": "medium"}
        monkeypatch.setattr(settings, "llm_effort", "")
        assert "output_config" not in llm_client._anthropic_request(MSGS, None, None)

    def test_thinking_cannot_eat_the_whole_output_budget(self):
        assert llm_client._anthropic_request(MSGS, None, 256)["max_tokens"] > 256

    def test_system_prompt_only_when_given(self):
        assert "system" not in llm_client._anthropic_request(MSGS, None, None)
        assert llm_client._anthropic_request(MSGS, "s", None)["system"] == "s"

    async def test_chat_completion_does_not_pass_temperature_to_the_client(self, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_provider", "anthropic")
        monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
        monkeypatch.setattr(llm_client, "_daily", {"date": "", "input": 0, "output": 0})
        response = MagicMock()
        response.usage.input_tokens, response.usage.output_tokens = 1, 1
        response.content = [SimpleNamespace(type="text", text="ok")]
        create = MagicMock(return_value=_awaitable(response))
        client = MagicMock()
        client.messages.create = create
        with patch.object(llm_client, "_get_client", return_value=client), \
                patch.object(llm_client, "_record_usage"):
            await llm_client.chat_completion(MSGS, temperature=0.3)
        assert "temperature" not in create.call_args.kwargs


async def _awaitable(value):
    return value


class TestResponseText:
    def test_skips_a_leading_thinking_block(self):
        response = SimpleNamespace(content=[
            SimpleNamespace(type="thinking", thinking="", signature="s"),
            SimpleNamespace(type="text", text="the answer"),
        ], stop_reason="end_turn")
        assert llm_client._response_text(response) == "the answer"

    def test_joins_multiple_text_blocks(self):
        response = SimpleNamespace(content=[
            SimpleNamespace(type="text", text="a"), SimpleNamespace(type="text", text="b"),
        ], stop_reason="end_turn")
        assert llm_client._response_text(response) == "ab"

    def test_no_text_is_an_error_not_an_attribute_error(self):
        response = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="")],
                                   stop_reason="max_tokens")
        with pytest.raises(ValueError, match="max_tokens"):
            llm_client._response_text(response)


class TestUsageFileShape:
    @pytest.fixture(autouse=True)
    def _paths(self, tmp_path, monkeypatch):
        self.path = tmp_path / "llm_usage.json"
        monkeypatch.setattr(llm_client, "_daily_usage_path", self.path)
        monkeypatch.setattr(llm_client, "_daily", {"date": "", "input": 0, "output": 0})

    @pytest.mark.parametrize("content", [
        "[]", "null", '"text"', "42", "{}", "not json",
        '{"date": "%TODAY%"}',
        '{"date": "%TODAY%", "input": "x", "output": 1}',
        '{"date": "%TODAY%", "input": -5, "output": 1}',
        '{"date": "%TODAY%", "input": true, "output": 1}',
        '{"date": 20261022, "input": 1, "output": 1}',
    ])
    def test_malformed_files_mean_a_fresh_start_not_a_crash(self, content):
        self.path.write_text(content.replace("%TODAY%", datetime.now(UTC).date().isoformat()))
        assert llm_client.daily_tokens_used() == 0
        assert llm_client.budget_exhausted() in (False, True)  # must not raise

    def test_valid_file_is_honoured(self):
        today = datetime.now(UTC).date().isoformat()
        self.path.write_text(json.dumps({"date": today, "input": 7, "output": 3}))
        assert llm_client.daily_tokens_used() == 10


class TestOpenAIClient:
    def test_uses_the_configured_timeout(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_timeout", 12)
        monkeypatch.setattr(llm_client, "_openai_client", None)
        with patch("openai.AsyncOpenAI") as ctor:
            llm_client._get_openai_client()
        assert ctor.call_args.kwargs["timeout"] == 12
        monkeypatch.setattr(llm_client, "_openai_client", None)


class TestUnusableReplies:
    @pytest.mark.parametrize("stop", ["max_tokens", "refusal", "pause_turn", "model_context_window_exceeded"])
    def test_text_from_an_unfinished_or_refused_reply_is_not_an_answer(self, stop):
        response = SimpleNamespace(content=[SimpleNamespace(type="text", text="Partial answ")], stop_reason=stop)
        with pytest.raises(llm_client.LLMResponseError, match=stop):
            llm_client._response_text(response)

    def test_end_turn_and_stop_sequence_are_fine(self):
        for stop in ("end_turn", "stop_sequence", "tool_use", None):
            response = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason=stop)
            assert llm_client._response_text(response) == "ok"

    async def test_a_refusal_is_not_cached_and_does_not_open_the_circuit_breaker(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_provider", "anthropic")
        monkeypatch.setattr(settings, "llm_daily_token_cap", 0)
        monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
        monkeypatch.setattr(llm_client, "_consecutive_failures", 0)
        monkeypatch.setattr(llm_client, "_daily_usage_path", tmp_path / "u.json")
        monkeypatch.setattr(llm_client, "_daily", {"date": "", "input": 0, "output": 0})
        monkeypatch.setattr(llm_client, "_description_cache", llm_client.DiskCache(cache_dir=str(tmp_path / "c"), ttl=0))
        response = MagicMock()
        response.usage.input_tokens, response.usage.output_tokens = 5, 5
        response.content = [SimpleNamespace(type="text", text="I can't help with that")]
        response.stop_reason = "refusal"
        client = MagicMock()
        client.messages.create = MagicMock(side_effect=lambda **_: _awaitable(response))
        with patch.object(llm_client, "_get_client", return_value=client):
            for _ in range(5):  # more than the breaker threshold
                with pytest.raises(llm_client.LLMResponseError):
                    await llm_client.cached_completion("k", MSGS)
        assert llm_client._consecutive_failures == 0 and llm_client._circuit_open_until == 0.0
        assert await llm_client._description_cache.get("k") is None
        assert client.messages.create.call_count == 5  # nothing was served from a poisoned cache


class TestOpenAIReplies:
    @pytest.mark.parametrize("finish", ["length", "content_filter"])
    async def test_cut_off_or_filtered_replies_are_failures(self, monkeypatch, finish):
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(llm_client, "_circuit_open_until", 0.0)
        response = MagicMock()
        response.usage.prompt_tokens, response.usage.completion_tokens = 1, 1
        response.choices = [SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content="partial"))]
        oai = MagicMock()
        oai.chat.completions.create = MagicMock(return_value=_awaitable(response))
        with patch.object(llm_client, "_get_openai_client", return_value=oai):
            with pytest.raises(llm_client.LLMResponseError):
                await llm_client._openai_completion(MSGS)


def test_the_usage_counter_is_not_in_the_directory_the_cache_prunes():
    from app.services.cache import DiskCache

    assert llm_client._daily_usage_path.parent != llm_client.Path(settings.cache_dir)
    cache = DiskCache(cache_dir=str(llm_client._daily_usage_path.parent.parent), ttl=0, max_entries=1)
    cache._dir.mkdir(parents=True, exist_ok=True)
    llm_client._daily_usage_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel = llm_client._daily_usage_path.parent / "sentinel.json"
    sentinel.write_text("{}")
    try:
        for i in range(3):
            (cache._dir / f"e{i}.json").write_text("{}")
        cache._prune()
        assert sentinel.exists()
    finally:
        sentinel.unlink(missing_ok=True)
        for f in cache._dir.glob("e?.json"):
            f.unlink()
