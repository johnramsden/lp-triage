from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable


@dataclass
class TextChunk:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


ProviderEvent = TextChunk | ToolCall | Usage


@runtime_checkable
class Provider(Protocol):
    async def stream_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
    ) -> AsyncIterator[ProviderEvent]: ...
