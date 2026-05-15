# Agentic Classification Loop

**File:** `lp_triage/engine/agent_loop.py`

## Purpose

`classify_bug(bug, project, repo_manager, provider, model, ...)` is an async
generator that runs an LLM tool-use loop for a single bug and yields
`StreamEvent` instances. It terminates when the model calls `classify_bug` or
when `max_turns` is exceeded.

## Tools available to the model

| Tool | Description |
|------|-------------|
| `get_log` | Last N commits scoped to the project's branch and subdir |
| `get_commit` | Full diff + stat for a specific commit hash |
| `read_file` | Contents of a file at a path relative to the subdir |
| `classify_bug` | Submit the classification — terminates the loop |

All file access goes through `RepoManager`, which enforces path scope (no
absolute paths, no `..` escapes).

## Loop mechanics

1. Build a system prompt that names the LP project, repo URL, branch, and
   subdir (if set).
2. Build a user message containing the bug title, description, importance,
   status, and up to five LP comments.
3. Call the provider's `stream_completion`. Accumulate `TextChunk` and
   `ToolCall` events.
4. Execute any tool calls, append results as a `tool` role message.
5. If `classify_bug` was called, add `"schema": 1` to the result, yield a
   `ClassificationEvent`, and return.
6. If `max_turns` is reached without a `classify_bug` call, yield a
   `BugErrorEvent` with message `"exceeded max turns (N) without classify_bug
   call"`.

## System prompt

The system prompt scopes the model to the charm:

```
You are a senior engineer triaging Launchpad bugs for the {lp_project} charm.

The source code lives in the '{url}' repository (branch: {branch}){subdir_clause}.
All your file access tools are scoped to that location.
```

When `subdir` is set, `{subdir_clause}` expands to `, scoped to the '{subdir}'
subdirectory`. When blank, it is omitted entirely.

## Token usage

After each LLM response the loop yields a `TokenUsageEvent` with the input and
output token counts reported by the provider.
