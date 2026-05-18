"""Agentic loop for classifying a single Launchpad bug.

The agent has three tools for gathering code context (get_log, get_commit,
read_file) and one tool for submitting its classification (classify_bug).
The loop terminates when classify_bug is called.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .config import ProjectCfg, repo_dir_name
from .events import (
    BugErrorEvent,
    BugProgressEvent,
    ClassificationEvent,
    StreamEvent,
    TokenUsageEvent,
)
from .providers.base import Provider, TextChunk, ToolCall, Usage
from .repo_manager import RepoManager

logger = logging.getLogger(__name__)

_CLASSIFY_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_bug",
        "description": (
            "Submit your final classification for this bug. Call this exactly once "
            "after gathering sufficient code context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "bug",
                        "enhancement",
                        "question",
                        "support",
                        "documentation",
                        "invalid",
                        "already_fixed",
                        "unknown",
                    ],
                    "description": "Classification category",
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Full GitHub commit or PR URLs supporting the classification, "
                        "e.g. https://github.com/org/repo/commit/<full-sha> or "
                        "https://github.com/org/repo/pull/<number>. "
                        "Never use bare commit hashes — always construct the full URL."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "One or two sentences describing the issue",
                },
                "recommended_action": {
                    "type": "string",
                    "description": "Short advisory for a human reviewer",
                },
                "potential_resolution_detail": {
                    "type": "string",
                    "description": (
                        "Long-form root cause analysis, what a fix looks like, "
                        "which files are involved. Plain text, no markdown."
                    ),
                },
                "fix_reference": {
                    "type": ["string", "null"],
                    "description": "URL to fix commit/PR for already_fixed; null otherwise",
                },
            },
            "required": [
                "category",
                "evidence",
                "summary",
                "recommended_action",
                "potential_resolution_detail",
                "fix_reference",
            ],
        },
    },
}

_GET_LOG_TOOL = {
    "type": "function",
    "function": {
        "name": "get_log",
        "description": "Return the last N commit summaries scoped to the charm subdirectory",
        "parameters": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of recent commits to return (max 50)",
                }
            },
            "required": ["n"],
        },
    },
}

_GET_COMMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "get_commit",
        "description": "Return full detail for a commit: message, changed files, diff",
        "parameters": {
            "type": "object",
            "properties": {
                "hash": {
                    "type": "string",
                    "description": "Git commit hash (short or full)",
                }
            },
            "required": ["hash"],
        },
    },
}

_READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Return the contents of a file from the local clone. "
            "Path is relative to the configured subdirectory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the charm subdirectory",
                }
            },
            "required": ["path"],
        },
    },
}

ALL_TOOLS = [_GET_LOG_TOOL, _GET_COMMIT_TOOL, _READ_FILE_TOOL, _CLASSIFY_TOOL]

_SYSTEM_PROMPT = """You are a senior engineer triaging Launchpad bugs for the {lp_project} charm.

The source code lives in the '{repo}' repository (branch: {branch}){subdir_clause}.
All your file access tools are scoped to that location.

Your task:
1. Use get_log, get_commit, and read_file to gather enough context to classify the bug.
2. Call classify_bug exactly once with a well-reasoned classification.

Classification categories:
- bug: reproducible defect in the charm code
- enhancement: request for new functionality
- question: user asking how to do something
- support: user needs help with their deployment
- documentation: issue with docs, not code
- invalid: not a real bug / spam / test
- already_fixed: the fix is committed on the tracked branch (set fix_reference)
- unknown: cannot determine (use only as last resort)

For 'already_fixed', evidence and fix_reference are required.
For any other classification, evidence should list supporting commit/PR URLs if found.
If you find no supporting evidence, leave evidence empty and avoid posting (the posting gate
will prevent it).

URL format: whenever you cite a commit, always use the full URL:
  {repo}/commit/<full-40-char-sha>
Never include bare commit hashes — always expand them to full URLs.
All text fields are posted as plain text to Launchpad; do not use Markdown link syntax [text](url).

Be thorough but efficient: check the recent git log first, then dive into specific commits
or files as needed. Do not fabricate commit hashes or URLs."""


def _build_system(project: ProjectCfg) -> str:
    subdir_clause = (
        f", scoped to the '{project.subdir}' subdirectory" if project.subdir else ""
    )
    return _SYSTEM_PROMPT.format(
        lp_project=project.lp_project,
        subdir_clause=subdir_clause,
        repo=project.url,
        branch=project.branch,
    )


def _build_user_message(bug: dict) -> str:
    messages_text = ""
    for msg in (bug.get("messages") or [])[:5]:  # include up to 5 messages for context
        messages_text += f"\n---\n**{msg['author']}** ({msg['date_created']}):\n{msg['content']}\n"

    return (
        f"Bug #{bug['id']}: {bug['title']}\n\n"
        f"Status: {bug.get('status', 'Unknown')} | Importance: {bug.get('importance', 'Unknown')}\n"
        f"URL: {bug.get('web_link', '')}\n\n"
        f"Description:\n{bug.get('description', '(no description)')}"
        + (f"\n\nComments:{messages_text}" if messages_text else "")
    )


async def classify_bug(
    bug: dict,
    project: ProjectCfg,
    repo_manager: RepoManager,
    provider: Provider,
    model: str,
    debug: bool = False,
    max_turns: int = 10,
) -> AsyncIterator[StreamEvent]:
    bug_id = bug["id"]
    repo_dir = repo_manager.repo_path(repo_dir_name(project.url))

    messages: list[dict] = [
        {"role": "system", "content": _build_system(project)},
        {"role": "user", "content": _build_user_message(bug)},
    ]
    classification: dict | None = None
    total_tool_calls = 0
    _MAX_TOOL_CALLS = max_turns * 5  # hard cap across all turns
    _MAX_TOOL_OUTPUT = 32 * 1024  # 32 KiB per tool result

    for _turn in range(max_turns):
        tool_calls_this_turn: list[ToolCall] = []
        text_buf = ""

        async for ev in provider.stream_completion(messages, ALL_TOOLS, model):
            if isinstance(ev, TextChunk):
                text_buf += ev.text
            elif isinstance(ev, ToolCall):
                tool_calls_this_turn.append(ev)
                if debug:
                    yield BugProgressEvent(
                        bug_id=bug_id, step=f"tool_call:{ev.name}:{json.dumps(ev.arguments)[:120]}"
                    )
            elif isinstance(ev, Usage):
                yield TokenUsageEvent(
                    bug_id=bug_id, input=ev.input_tokens, output=ev.output_tokens
                )

        if not tool_calls_this_turn:
            logger.warning("Bug %d: no tool call on turn %d", bug_id, _turn)
            if _turn == max_turns - 1:
                yield BugErrorEvent(
                    bug_id=bug_id,
                    error=f"exceeded max turns ({max_turns}) without classify_bug call",
                )
                return
            continue

        # Build assistant message with all tool calls
        assistant_msg: dict = {"role": "assistant", "content": text_buf or None}
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in tool_calls_this_turn
        ]
        messages.append(assistant_msg)

        # Dispatch each tool call
        total_tool_calls += len(tool_calls_this_turn)
        if total_tool_calls > _MAX_TOOL_CALLS:
            yield BugErrorEvent(
                bug_id=bug_id,
                error=f"exceeded max tool calls ({_MAX_TOOL_CALLS}) without classify_bug call",
            )
            return

        for tc in tool_calls_this_turn:
            if tc.name == "classify_bug":
                classification = {**tc.arguments, "schema": 1, "_project_url": project.url}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": "Classification recorded.",
                    }
                )
            else:
                result = await _dispatch_tool(tc, repo_dir, project, repo_manager)
                if len(result) > _MAX_TOOL_OUTPUT:
                    result = result[:_MAX_TOOL_OUTPUT] + f"\n[truncated — output exceeded {_MAX_TOOL_OUTPUT // 1024} KiB]"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result,
                    }
                )

        if classification is not None:
            yield ClassificationEvent(bug_id=bug_id, result=classification)
            return

        yield BugProgressEvent(bug_id=bug_id, step="classifying")

    if classification is None:
        yield BugErrorEvent(bug_id=bug_id, error="exceeded max turns without classification")


async def _dispatch_tool(tc: ToolCall, repo_dir: Path, project: ProjectCfg, repo_manager: RepoManager) -> str:
    from .repo_manager import RepoError

    try:
        if tc.name == "get_log":
            n = min(int(tc.arguments.get("n", 20)), 50)
            return await repo_manager.get_log(repo_dir, project.branch, project.subdir, n)
        elif tc.name == "get_commit":
            h = str(tc.arguments.get("hash", ""))
            return await repo_manager.get_commit(repo_dir, h)
        elif tc.name == "read_file":
            p = str(tc.arguments.get("path", ""))
            return await repo_manager.read_file(repo_dir, project.branch, project.subdir, p)
        else:
            return f"unknown tool: {tc.name}"
    except RepoError as e:
        return f"error: {e}"
    except Exception as e:
        logger.exception("Tool dispatch error for %s", tc.name)
        return f"error: {e}"
