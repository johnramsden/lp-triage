from __future__ import annotations

import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import ProviderEvent, TextChunk, ToolCall, Usage

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenAIProvider:
    def __init__(self, api_key: str, base_url: str = _OPENROUTER_BASE):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def stream_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
    ) -> AsyncIterator[ProviderEvent]:
        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools

        tool_bufs: dict[int, dict] = {}

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
                if not chunk.choices:
                    if chunk.usage:
                        yield Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                        )
                    continue

                delta = chunk.choices[0].delta
                finish = chunk.choices[0].finish_reason

                if delta.content:
                    yield TextChunk(text=delta.content)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        buf = tool_bufs.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            buf["id"] += tc.id
                        if tc.function:
                            if tc.function.name:
                                buf["name"] += tc.function.name
                            if tc.function.arguments:
                                buf["args"] += tc.function.arguments

                if finish == "tool_calls":
                    for idx in sorted(tool_bufs):
                        buf = tool_bufs[idx]
                        try:
                            args = json.loads(buf["args"]) if buf["args"] else {}
                        except json.JSONDecodeError:
                            import logging
                            logging.getLogger(__name__).warning(
                                "Malformed tool-call JSON for %s: %r", buf["name"], buf["args"]
                            )
                            raise
                        yield ToolCall(id=buf["id"], name=buf["name"], arguments=args)
                    tool_bufs.clear()
