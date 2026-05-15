from __future__ import annotations

import json
from collections.abc import AsyncIterator

from google import genai
from google.genai import types as gtypes

from .base import ProviderEvent, TextChunk, ToolCall, Usage


def _openai_tool_to_gemini(tool: dict) -> gtypes.Tool:
    fn = tool["function"]
    params = fn.get("parameters", {})
    props = {}
    for name, schema in params.get("properties", {}).items():
        props[name] = gtypes.Schema(
            type=_map_type(schema.get("type", "string")),
            description=schema.get("description", ""),
            enum=schema.get("enum"),
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


def _map_type(t: str) -> gtypes.Type:
    return {
        "string": gtypes.Type.STRING,
        "integer": gtypes.Type.INTEGER,
        "number": gtypes.Type.NUMBER,
        "boolean": gtypes.Type.BOOLEAN,
        "array": gtypes.Type.ARRAY,
        "object": gtypes.Type.OBJECT,
    }.get(t, gtypes.Type.STRING)


def _openai_messages_to_gemini(messages: list[dict]) -> tuple[str | None, list[gtypes.Content]]:
    system = None
    contents: list[gtypes.Content] = []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system = msg["content"]
            continue
        if role == "user":
            contents.append(gtypes.Content(role="user", parts=[gtypes.Part(text=msg["content"])]))
        elif role == "assistant":
            parts: list[gtypes.Part] = []
            if msg.get("content"):
                parts.append(gtypes.Part(text=msg["content"]))
            for tc in msg.get("tool_calls", []):
                parts.append(
                    gtypes.Part(
                        function_call=gtypes.FunctionCall(
                            name=tc["function"]["name"],
                            args=json.loads(tc["function"]["arguments"]),
                        )
                    )
                )
            contents.append(gtypes.Content(role="model", parts=parts))
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
        system, contents = _openai_messages_to_gemini(messages)
        gemini_tools = [_openai_tool_to_gemini(t) for t in tools] if tools else None

        config = gtypes.GenerateContentConfig(
            system_instruction=system,
            tools=gemini_tools,
        )

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
                if part.text:
                    yield TextChunk(text=part.text)
                if part.function_call:
                    fc = part.function_call
                    yield ToolCall(
                        id=fc.name,
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )
