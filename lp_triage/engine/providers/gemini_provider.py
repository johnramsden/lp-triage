from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from google import genai
from google.genai import types as gtypes

from .base import NativeModelContent, ProviderEvent, TextChunk, ToolCall, Usage


def _openai_tool_to_gemini(tool: dict) -> gtypes.Tool:
    fn = tool["function"]
    params = fn.get("parameters", {})
    props = {}
    for name, schema in params.get("properties", {}).items():
        items_schema = None
        if schema.get("items"):
            items_schema = gtypes.Schema(type=_map_type(schema["items"].get("type", "string")))
        props[name] = gtypes.Schema(
            type=_map_type(schema.get("type", "string")),
            description=schema.get("description", ""),
            enum=schema.get("enum"),
            items=items_schema,
        )
    fd = gtypes.FunctionDeclaration(
        name=fn["name"],
        description=fn.get("description", ""),
        parameters=gtypes.Schema(
            type=gtypes.Type.OBJECT,
            properties=props,
            required=params.get("required", []),
        ),
    )
    return gtypes.Tool(function_declarations=[fd])


def _map_type(t: str | list) -> gtypes.Type:
    # JSON Schema allows "type": ["string", "null"] — pick the first non-null type.
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    return {
        "string": gtypes.Type.STRING,
        "integer": gtypes.Type.INTEGER,
        "number": gtypes.Type.NUMBER,
        "boolean": gtypes.Type.BOOLEAN,
        "array": gtypes.Type.ARRAY,
        "object": gtypes.Type.OBJECT,
    }.get(t, gtypes.Type.STRING)


def _build_gemini_contents(
    messages: list[dict],
) -> tuple[str | None, list[gtypes.Content]]:
    """Convert OpenAI-format messages to Gemini contents.

    For assistant turns, use the stored _native Gemini Content (which preserves
    thought parts and thought_signatures) instead of re-converting from the
    OpenAI representation, which would drop that information.
    """
    system = None
    contents: list[gtypes.Content] = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system = msg["content"]
        elif role == "user":
            contents.append(
                gtypes.Content(role="user", parts=[gtypes.Part(text=msg["content"])])
            )
        elif role == "assistant":
            native = msg.get("_native")
            if native is not None:
                contents.append(native)
        elif role == "tool":
            contents.append(
                gtypes.Content(
                    role="user",
                    parts=[
                        gtypes.Part(
                            function_response=gtypes.FunctionResponse(
                                name=msg.get("name", "tool"),
                                response={"result": msg["content"]},
                            )
                        )
                    ],
                )
            )
    return system, contents


class GeminiProvider:
    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    async def stream_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
    ) -> AsyncIterator[ProviderEvent]:
        system, contents = _build_gemini_contents(messages)
        gemini_tools = [_openai_tool_to_gemini(t) for t in tools] if tools else None

        config = gtypes.GenerateContentConfig(
            system_instruction=system,
            tools=gemini_tools,
        )

        model_parts: list[gtypes.Part] = []

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            if chunk.usage_metadata:
                yield Usage(
                    input_tokens=chunk.usage_metadata.prompt_token_count or 0,
                    output_tokens=chunk.usage_metadata.candidates_token_count or 0,
                )

            for part in chunk.parts or []:
                model_parts.append(part)
                if getattr(part, "thought", False):
                    continue  # internal reasoning — not content
                if part.text:
                    yield TextChunk(text=part.text)
                if part.function_call:
                    fc = part.function_call
                    yield ToolCall(
                        id=str(uuid.uuid4()),
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )

        # Yield the complete native model turn so the caller can store it
        # and replay it verbatim on the next turn, preserving thought parts
        # and thought_signatures without lossy conversion.
        if model_parts:
            yield NativeModelContent(
                content=gtypes.Content(role="model", parts=model_parts)
            )
