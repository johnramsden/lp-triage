"""Unit tests for provider adapters — mock at the SDK boundary."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lp_triage.engine.providers.base import TextChunk, ToolCall, Usage
from lp_triage.engine.providers.openai_provider import OpenAIProvider


class _FakeChunk:
    def __init__(self, content=None, tool_calls=None, finish=None, usage=None):
        self.choices = []
        self.usage = usage
        if content is not None or tool_calls is not None or finish is not None:
            choice = MagicMock()
            choice.delta = MagicMock()
            choice.delta.content = content
            choice.delta.tool_calls = tool_calls or []
            choice.finish_reason = finish
            self.choices = [choice]


async def _fake_stream(chunks):
    for chunk in chunks:
        yield chunk


def _make_create_mock(chunks):
    """Return an AsyncMock for client.chat.completions.create that yields chunks."""
    mock = AsyncMock(return_value=_fake_stream(chunks))
    return mock


@pytest.mark.asyncio
async def test_openai_provider_text_chunks():
    prov = OpenAIProvider(api_key="test")
    chunks = [
        _FakeChunk(content="Hello"),
        _FakeChunk(content=" world"),
        _FakeChunk(finish="stop"),
    ]
    with patch.object(prov._client.chat.completions, "create", _make_create_mock(chunks)):
        events = []
        async for ev in prov.stream_completion([{"role": "user", "content": "hi"}], [], "test-model"):
            events.append(ev)

    text_events = [e for e in events if isinstance(e, TextChunk)]
    assert len(text_events) == 2
    assert text_events[0].text == "Hello"
    assert text_events[1].text == " world"


@pytest.mark.asyncio
async def test_openai_provider_tool_call():
    prov = OpenAIProvider(api_key="test")

    tc0 = MagicMock()
    tc0.index = 0
    tc0.id = "call_abc"
    tc0.function = MagicMock()
    tc0.function.name = "get_log"
    tc0.function.arguments = '{"n": 10}'

    finish_chunk = _FakeChunk(finish="tool_calls")
    finish_chunk.choices[0].delta.tool_calls = []

    chunks = [_FakeChunk(tool_calls=[tc0]), finish_chunk]
    with patch.object(prov._client.chat.completions, "create", _make_create_mock(chunks)):
        events = []
        async for ev in prov.stream_completion([], [{"type": "function", "function": {"name": "get_log"}}], "test"):
            events.append(ev)

    tool_events = [e for e in events if isinstance(e, ToolCall)]
    assert len(tool_events) == 1
    assert tool_events[0].name == "get_log"
    assert tool_events[0].arguments == {"n": 10}


@pytest.mark.asyncio
async def test_openai_provider_usage():
    prov = OpenAIProvider(api_key="test")

    usage_mock = MagicMock()
    usage_mock.prompt_tokens = 100
    usage_mock.completion_tokens = 50
    usage_chunk = _FakeChunk(usage=usage_mock)

    chunks = [usage_chunk]
    with patch.object(prov._client.chat.completions, "create", _make_create_mock(chunks)):
        events = []
        async for ev in prov.stream_completion([], [], "test"):
            events.append(ev)

    usage_events = [e for e in events if isinstance(e, Usage)]
    assert len(usage_events) == 1
    assert usage_events[0].input_tokens == 100
    assert usage_events[0].output_tokens == 50
