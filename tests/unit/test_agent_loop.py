"""Unit tests for the agent loop — mock provider and repo manager."""

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lp_triage.engine.agent_loop import classify_bug
from lp_triage.engine.config import ProjectCfg
from lp_triage.engine.events import (
    BugErrorEvent,
    ClassificationEvent,
    TokenUsageEvent,
)
from lp_triage.engine.providers.base import TextChunk, ToolCall, Usage
from lp_triage.engine.repo_manager import RepoManager


def _make_project():
    return ProjectCfg(
        lp_project="charm-ceph-mon",
        url="https://github.com/canonical/ceph-charms",
        branch="main",
        subdir="ceph-mon",
    )


def _make_bug(bug_id=12345):
    return {
        "id": bug_id,
        "title": "Crash on startup",
        "description": "The charm crashes when upgraded",
        "status": "New",
        "importance": "High",
        "web_link": f"https://bugs.launchpad.net/bugs/{bug_id}",
        "messages": [],
    }


class _MockProvider:
    def __init__(self, events_sequence):
        self._seq = events_sequence
        self._call_count = 0

    async def stream_completion(self, messages, tools, model) -> AsyncIterator:
        events = self._seq[self._call_count % len(self._seq)]
        self._call_count += 1
        for ev in events:
            yield ev


@pytest.mark.asyncio
async def test_classify_emits_classification_event(tmp_path):
    classify_call = ToolCall(
        id="call_1",
        name="classify_bug",
        arguments={
            "category": "bug",
            "evidence": ["https://github.com/org/repo/commit/abc"],
            "summary": "Crash on startup",
            "recommended_action": "Investigate",
            "potential_resolution_detail": "The issue is in charm.py",
            "fix_reference": None,
        },
    )
    usage = Usage(input_tokens=100, output_tokens=50)
    provider = _MockProvider([[classify_call, usage]])

    repo_manager = MagicMock(spec=RepoManager)
    repo_manager.repo_path.return_value = tmp_path / "repos" / "ceph-charms"

    events = []
    async for ev in classify_bug(_make_bug(), _make_project(), repo_manager, provider, "test-model"):
        events.append(ev)

    classifications = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classifications) == 1
    assert classifications[0].result["category"] == "bug"
    assert classifications[0].result["schema"] == 1

    usages = [e for e in events if isinstance(e, TokenUsageEvent)]
    assert len(usages) == 1
    assert usages[0].input == 100


@pytest.mark.asyncio
async def test_classify_dispatches_get_log(tmp_path):
    get_log_call = ToolCall(id="call_1", name="get_log", arguments={"n": 10})
    classify_call = ToolCall(
        id="call_2",
        name="classify_bug",
        arguments={
            "category": "enhancement",
            "evidence": [],
            "summary": "Enhancement request",
            "recommended_action": "Tag as enhancement",
            "potential_resolution_detail": "No fix needed",
            "fix_reference": None,
        },
    )

    call_count = [0]

    class _TwoTurnProvider:
        async def stream_completion(self, messages, tools, model) -> AsyncIterator:
            call_count[0] += 1
            if call_count[0] == 1:
                yield get_log_call
            else:
                yield classify_call

    repo_dir = tmp_path / "repos" / "ceph-charms"
    repo_dir.mkdir(parents=True)
    repo_manager = MagicMock(spec=RepoManager)
    repo_manager.repo_path.return_value = repo_dir

    with patch("lp_triage.engine.agent_loop._dispatch_tool", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = "abc123 Fix crash\ndef456 Add feature"

        events = []
        async for ev in classify_bug(
            _make_bug(), _make_project(), repo_manager, _TwoTurnProvider(), "test"
        ):
            events.append(ev)

    assert mock_dispatch.called
    assert mock_dispatch.call_args[0][0].name == "get_log"

    classifications = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classifications) == 1


@pytest.mark.asyncio
async def test_classify_returns_error_on_max_turns(tmp_path):
    class _NeverClassifyProvider:
        async def stream_completion(self, messages, tools, model) -> AsyncIterator:
            yield TextChunk(text="hmm let me think")

    repo_manager = MagicMock(spec=RepoManager)
    repo_manager.repo_path.return_value = tmp_path / "x"

    events = []
    async for ev in classify_bug(
        _make_bug(), _make_project(), repo_manager, _NeverClassifyProvider(), "test"
    ):
        events.append(ev)

    errors = [e for e in events if isinstance(e, BugErrorEvent)]
    assert len(errors) == 1
    assert "max turns" in errors[0].error
