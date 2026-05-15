from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Union


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base(t: str) -> dict:
    return {"t": t, "ts": _now()}


@dataclass
class RunStartEvent:
    projects: list[str]
    t: str = field(default="run_start", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "run_start"
        return d


@dataclass
class ProjectStartEvent:
    project: str
    t: str = field(default="project_start", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "project_start"
        return d


@dataclass
class BugStartEvent:
    project: str
    bug_id: int
    title: str
    t: str = field(default="bug_start", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "bug_start"
        return d


@dataclass
class BugProgressEvent:
    bug_id: int
    step: str
    t: str = field(default="bug_progress", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "bug_progress"
        return d


@dataclass
class TokenUsageEvent:
    bug_id: int
    input: int
    output: int
    t: str = field(default="token_usage", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "token_usage"
        return d


@dataclass
class ClassificationEvent:
    bug_id: int
    result: dict
    t: str = field(default="classification", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "t": "classification",
            "ts": self.ts,
            "bug_id": self.bug_id,
            "result": self.result,
        }


@dataclass
class CommentPostedEvent:
    bug_id: int
    url: str
    t: str = field(default="comment_posted", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "comment_posted"
        return d


@dataclass
class BugErrorEvent:
    bug_id: int
    error: str
    t: str = field(default="bug_error", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "bug_error"
        return d


@dataclass
class ProjectDoneEvent:
    project: str
    t: str = field(default="project_done", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = "project_done"
        return d


@dataclass
class RunDoneEvent:
    stats: dict
    t: str = field(default="run_done", init=False, repr=False)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "t": "run_done",
            "ts": self.ts,
            "stats": self.stats,
        }


StreamEvent = Union[
    RunStartEvent,
    ProjectStartEvent,
    BugStartEvent,
    BugProgressEvent,
    TokenUsageEvent,
    ClassificationEvent,
    CommentPostedEvent,
    BugErrorEvent,
    ProjectDoneEvent,
    RunDoneEvent,
]


def to_ndjson(event: StreamEvent) -> str:
    return json.dumps(event.to_dict(), ensure_ascii=False)
