import json

import pytest

from lp_triage.engine.events import (
    BugErrorEvent,
    BugProgressEvent,
    BugStartEvent,
    ClassificationEvent,
    CommentPostedEvent,
    ProjectDoneEvent,
    ProjectStartEvent,
    RunDoneEvent,
    RunStartEvent,
    TokenUsageEvent,
    to_ndjson,
)


def test_run_start_event_type():
    e = RunStartEvent(projects=["charm-ceph-mon"])
    d = e.to_dict()
    assert d["t"] == "run_start"
    assert d["projects"] == ["charm-ceph-mon"]
    assert "ts" in d


def test_bug_start_event():
    e = BugStartEvent(project="charm-ceph-mon", bug_id=12345, title="Test bug")
    d = e.to_dict()
    assert d["t"] == "bug_start"
    assert d["bug_id"] == 12345
    assert d["title"] == "Test bug"
    assert d["project"] == "charm-ceph-mon"


def test_bug_progress_event():
    e = BugProgressEvent(bug_id=1, step="gathering context")
    d = e.to_dict()
    assert d["t"] == "bug_progress"
    assert d["step"] == "gathering context"


def test_classification_event_schema_version():
    result = {
        "schema": 1,
        "category": "bug",
        "evidence": [],
        "summary": "s",
        "recommended_action": "r",
        "potential_resolution_detail": "d",
        "fix_reference": None,
    }
    e = ClassificationEvent(bug_id=42, result=result)
    d = e.to_dict()
    assert d["t"] == "classification"
    assert d["result"]["schema"] == 1


def test_token_usage_event():
    e = TokenUsageEvent(bug_id=1, input=100, output=50)
    d = e.to_dict()
    assert d["t"] == "token_usage"
    assert d["input"] == 100
    assert d["output"] == 50


def test_run_done_event():
    stats = {"bugs": 3, "posted": 1, "errors": 0}
    e = RunDoneEvent(stats=stats)
    d = e.to_dict()
    assert d["t"] == "run_done"
    assert d["stats"]["bugs"] == 3


def test_to_ndjson_is_single_line():
    e = RunStartEvent(projects=["a", "b"])
    line = to_ndjson(e)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["t"] == "run_start"


def test_all_event_types_serialise():
    events = [
        RunStartEvent(projects=["p"]),
        ProjectStartEvent(project="p"),
        BugStartEvent(project="p", bug_id=1, title="t"),
        BugProgressEvent(bug_id=1, step="fetching bug"),
        TokenUsageEvent(bug_id=1, input=1, output=1),
        ClassificationEvent(bug_id=1, result={"schema": 1, "category": "bug"}),
        CommentPostedEvent(bug_id=1, url="https://lp/1"),
        BugErrorEvent(bug_id=1, error="oops"),
        ProjectDoneEvent(project="p"),
        RunDoneEvent(stats={}),
    ]
    for e in events:
        line = to_ndjson(e)
        d = json.loads(line)
        assert "t" in d
        assert "ts" in d
