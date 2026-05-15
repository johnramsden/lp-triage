# AI Provider Abstraction

**Files:** `lp_triage/engine/providers/`

## Protocol

`Provider` is a `typing.Protocol`:

```python
class Provider(Protocol):
    async def stream_completion(
        self,
        messages: list[dict],
        tools: list[dict],
        model: str,
    ) -> AsyncIterator[ProviderEvent]: ...
```

Provider events:

| Class | Meaning |
|-------|---------|
| `TextChunk(text)` | Streaming text fragment |
| `ToolCall(id, name, arguments)` | Model wants to call a tool |
| `Usage(input_tokens, output_tokens)` | Token counts for the response |

The agent loop consumes these events; it never imports a concrete provider
directly.

## OpenRouter (`openai_provider.py`)

Uses the `openai` SDK with `base_url` overridden to `https://openrouter.ai/api/v1`.

Streaming pattern (important — `async with await create()` fails with mocks):

```python
stream = await self._client.chat.completions.create(**kwargs)
async for chunk in stream:
    ...
```

Tool call fragments arrive with a `tc.index` field; the loop accumulates all
fragments for a given index before yielding a complete `ToolCall`.
A `ToolCall` is yielded when `finish_reason == "tool_calls"`.

## Gemini (`gemini_provider.py`)

Uses the `google-genai` SDK. Converts OpenAI-format messages and tool schemas
to Gemini's `gtypes.Content` / `gtypes.FunctionDeclaration` schema before
calling `generate_content_stream`.

## Selecting a provider

The CLI and web server instantiate providers based on the `provider` config key
(`openrouter` or `gemini`). The provider instance is passed into `run_triage()`
and from there into each `classify_bug()` call.
