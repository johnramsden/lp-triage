# Web Server and UI

**Files:** `lp_triage/web/server.py`, `lp_triage/web/static/index.html`, `lp_triage/web/static/app.js`, `lp_triage/web/static/style.css`

## FastAPI app

Created by `create_app()`. Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serves `index.html` |
| GET | `/config` | Merged config (secrets masked) |
| PUT | `/config` | Save user config (including projects list) |
| POST | `/run` | Start a triage run, returns `{run_id}` |
| POST | `/run/{id}/stop` | Cancel a running task |
| GET | `/run/{id}/stream` | SSE event stream |
| GET | `/run/{id}/results` | Final results dict |
| GET | `/run/{id}/bugs/{bug_id}` | Single bug result |
| POST | `/run/{id}/bugs/{bug_id}/post` | Post comment for one bug |
| GET | `/run/{id}/replay` | Full event list from memory (for reload recovery) |
| GET | `/auth/lp` | Start LP OAuth — returns `{auth_url, token_key}` |
| POST | `/auth/lp/complete` | Complete LP OAuth after user authorises |

## `RunStore`

In-memory store for active and completed runs. Each run entry holds:

- `status` — `"running"` | `"done"` | `"stopped"`
- `events` — list of all emitted event dicts (appended as they arrive; replayed to new SSE subscribers)
- `results` — dict of `bug_id → classification result`
- `queue` — `asyncio.Queue` that SSE generator reads from
- `task` — the `asyncio.Task` running `_bg()`

## SSE stream

`GET /run/{id}/stream` replays already-emitted events first, then tails the
live queue. Disconnection is detected with `request.is_disconnected()`. The
generator exits on `run_done` or `run_stopped` events.

## Single-page UI

Three static files — no build step:

- `index.html` — markup shell only; loads `app.js` and `style.css`
- `app.js` — all UI logic; vanilla JS
- `style.css` — custom variables and component styles layered over Vanilla Framework

Uses [Vanilla Framework](https://vanillajs.org/) CSS for Canonical styling, HTMX for
lightweight interactivity, and Ubuntu / Ubuntu Mono fonts.

### Panels

- **Run** — controls (mode, provider, limits, post flags) + live log + summary table
- **Review queue** — cards for each classification pending human approval;
  comment body is editable inline; **Approve & post** / **Skip** actions
- **Configuration** — project table (LP project, repo URL, branch, subdir),
  personal settings (API keys, provider/model), LP OAuth connect flow

### Reload recovery

On page load, `init()` checks `localStorage` for a `lp-triage-run-id`. If
found, it fetches `/run/{id}/replay` — which returns the full in-memory event
list — and replays all events, calling `onRunDone()` if a `run_done` or
`run_stopped` event is present. This restores the summary table and stops the
spinner after a browser refresh.

### Review mode

When mode is set to **Review**, each `ClassificationEvent` is added to
`reviewQueue` instead of being auto-posted. `renderReviewQueue()` builds a
card per item with an editable textarea pre-filled with the draft comment.
Clicking **Approve & post** calls `POST /run/{id}/bugs/{bug_id}/post`.
