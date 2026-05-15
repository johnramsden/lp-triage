# Architecture Overview

lp-triage is a Python 3.12 application built around an async event stream.
Every triage run emits a sequence of typed events (NDJSON over stdout in CLI
mode, SSE in web mode) so that the CLI, web UI, and test suite all consume the
same data path.

## Module map

```
lp_triage/
  cli.py               — Typer entry point; drives run_triage(), writes NDJSON log
  engine/
    config.py          — Two-file TOML config, typed dataclasses, atomic writes
    events.py          — All stream event types and NDJSON serialisation
    run.py             — Orchestration: clone repos, schedule bugs, aggregate stats
    agent_loop.py      — Agentic tool-use loop for a single bug
    lp_fetcher.py      — Launchpad REST API client with two-level cache
    lp_auth.py         — Launchpad OAuth 1.0a OOB desktop flow
    repo_manager.py    — Git clone/fetch/read with path-scope enforcement
    providers/
      base.py          — Provider protocol and event types
      openai_provider.py  — OpenRouter (openai SDK, base_url override)
      gemini_provider.py  — Google Gemini (google-genai SDK)
  web/
    server.py          — FastAPI app: REST endpoints, SSE stream, OAuth routes
    static/index.html  — Single-page UI (vanilla JS, HTMX, Vanilla Framework CSS)
```

Detailed write-ups for each subsystem live alongside this file:

- [config.md](config.md) — configuration system
- [events.md](events.md) — event stream
- [agent-loop.md](agent-loop.md) — agentic classification
- [providers.md](providers.md) — AI provider abstraction
- [lp-fetcher.md](lp-fetcher.md) — Launchpad data layer
- [repo-manager.md](repo-manager.md) — git repository access
- [web-server.md](web-server.md) — web server and UI
- [auth.md](auth.md) — Launchpad OAuth flow

## Data flow

```
lp-triage run
     │
     ▼
run_triage()                         ← async generator, yields StreamEvents
     │
     ├─ LPFetcher.get_active_bugs()  ← LP REST, cached by project/TTL
     │
     ├─ RepoManager.ensure_cloned()  ← blobless git clone per unique URL
     │
     └─ per bug (asyncio.Semaphore):
           │
           ├─ LPFetcher.get_bug_detail()   ← cached by (bug_id, last_updated)
           │
           └─ classify_bug()              ← agent loop, yields events
                 │
                 ├─ get_log tool
                 ├─ get_commit tool
                 ├─ read_file tool
                 └─ classify_bug tool  → ClassificationEvent, loop exits
```

The web server wraps `run_triage()` in an `asyncio.Task`, fans events into a
per-run `asyncio.Queue`, and serves them to the browser over SSE. The same
events are written to an NDJSON file on disk so the browser can replay them
after a page reload.
