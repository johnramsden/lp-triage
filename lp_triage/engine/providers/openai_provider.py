from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import ProviderEvent, TextChunk, ToolCall

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

logger = logging.getLogger(__name__)


def _repair_tool_json(s: str) -> str:
    # Fix "evidence": <unquoted-url>  →  "evidence": ["url"]
    # Fix "evidence": ,               →  "evidence": []
    # Both patterns crash json.loads; we've seen both from weaker models.
    #
    # The negative lookahead includes \s* so that backtracking in the preceding
    # \s* can't land the lookahead on a space instead of the actual value start.
    def _fix(m: re.Match) -> str:
        val = m.group(1).strip()
        return f'"evidence": ["{val}"]' if val else '"evidence": []'

    return re.sub(
        r'"evidence"\s*:\s*(?!\s*[\["tfn\d])(.*?)(?=,|\s*})',
        _fix,
        s,
        flags=re.DOTALL,
    )


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
        }
        if tools:
            kwargs["tools"] = tools

        tool_bufs: dict[int, dict] = {}

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
                if not chunk.choices:
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
                        except json.JSONDecodeError as exc:
                            logger.warning(
                                "Malformed tool-call JSON for %s: %r", buf["name"], buf["args"]
                            )
                            repaired = _repair_tool_json(buf["args"])
                            try:
                                args = json.loads(repaired)
                            except json.JSONDecodeError:
                                raise exc
                        yield ToolCall(id=buf["id"], name=buf["name"], arguments=args)
                    tool_bufs.clear()
