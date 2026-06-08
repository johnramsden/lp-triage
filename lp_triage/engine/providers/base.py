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


@dataclass
class NativeModelContent:
    """Raw provider model turn, preserved opaquely so providers can replay it
    without lossy round-tripping through the OpenAI message format."""
    content: object


ProviderEvent = TextChunk | ToolCall | Usage | NativeModelContent


@runtime_checkable
class Provider(Protocol):
    async def stream_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
    ) -> AsyncIterator[ProviderEvent]: ...
