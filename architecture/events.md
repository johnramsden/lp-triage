# Event Stream

**File:** `lp_triage/engine/events.py`

Every triage run is modelled as an ordered stream of typed events. The CLI
writes them as NDJSON to stdout (and to a timestamped log file in the current
working directory); the web server fans them through an `asyncio.Queue` to SSE
subscribers and appends each event to an in-memory list for replay.

## Event types

| Class | `t` field | Emitted when |
|-------|-----------|-------------|
| `RunStartEvent` | `run_start` | Run begins; includes project list |
| `ProjectStartEvent` | `project_start` | Processing moves to a new LP project |
| `BugStartEvent` | `bug_start` | A bug enters the queue |
| `BugProgressEvent` | `bug_progress` | Intermediate status (fetching, posting…) |
| `TokenUsageEvent` | `token_usage` | After each LLM call; input + output counts |
| `ClassificationEvent` | `classification` | Agent called `classify_bug` tool |
| `CommentPostedEvent` | `comment_posted` | Comment written to LP (or dry-run URL) |
| `BugErrorEvent` | `bug_error` | Unhandled exception for a bug |
| `ProjectDoneEvent` | `project_done` | All bugs in a project finished |
| `RunDoneEvent` | `run_done` | Run complete; includes aggregate stats |

All events carry a `ts` ISO-8601 timestamp. `to_dict()` converts an event to a
plain dict; `to_ndjson(event)` serialises it to a single JSON line.

## `ClassificationEvent` result shape

```python
{
  "category":           "bug" | "enhancement" | "question" | "support"
                        | "documentation" | "invalid" | "already_fixed",
  "summary":            str,   # one-sentence plain English
  "evidence":           list[str],  # commit hashes / file paths / quotes
  "recommended_action": str,
  "fix_reference":      str | None,  # commit hash if already_fixed
  "schema":             1,
}
```

`evidence` being non-empty is the gate for comment posting.

## CLI NDJSON log

Each CLI run writes a timestamped NDJSON file (`run-<ts>.ndjson`) and a
plain-text summary (`run-<ts>-summary.txt`) to the current working directory.
The web server does not write any files; replay comes from the in-memory event
list via `GET /run/{id}/replay`.
