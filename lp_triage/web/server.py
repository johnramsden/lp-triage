"""FastAPI web server with SSE event stream and review UI."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..engine.config import load_config, load_project_config, load_user_config, save_project_config, save_user_config
from ..engine.events import ClassificationEvent, RunDoneEvent, StreamEvent, to_ndjson
from ..engine.run import run_triage

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent


class RunStore:
    """In-memory store for active and recent runs."""

    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    def create(self, run_id: str, cfg: dict) -> None:
        output_dir = Path(cfg["defaults"]["output_dir"]).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._runs[run_id] = {
            "id": run_id,
            "status": "running",
            "started_at": ts,
            "events": [],
            "results": {},
            "ndjson_path": output_dir / f"run-{ts}.ndjson",
            "queue": asyncio.Queue(),
        }

    def set_task(self, run_id: str, task: asyncio.Task) -> None:
        if run_id in self._runs:
            self._runs[run_id]["task"] = task

    def append_event(self, run_id: str, event: StreamEvent) -> None:
        run = self._runs.get(run_id)
        if not run:
            return
        d = event.to_dict()
        run["events"].append(d)
        if isinstance(event, ClassificationEvent):
            run["results"][event.bug_id] = event.result
        if isinstance(event, RunDoneEvent):
            run["status"] = "done"
            run["stats"] = event.stats
        # Write to NDJSON log
        with open(run["ndjson_path"], "a") as f:
            f.write(to_ndjson(event) + "\n")
        # Notify SSE subscribers
        run["queue"].put_nowait(d)

    def get(self, run_id: str) -> dict | None:
        return self._runs.get(run_id)

    def get_results(self, run_id: str) -> dict | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        return {"results": run["results"], "stats": run.get("stats")}

    def get_bug_result(self, run_id: str, bug_id: int) -> dict | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        return run["results"].get(bug_id)

    def replay_from_ndjson(self, run_id: str) -> list[dict]:
        run = self._runs.get(run_id)
        if not run:
            return []
        path = run["ndjson_path"]
        if not Path(path).exists():
            return []
        events = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return events


_store = RunStore()
_pending_oauth: dict[str, str] = {}  # token_key -> token_secret


def create_app(initial_cfg: dict | None = None) -> FastAPI:
    app = FastAPI(title="lp-triage")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = _HERE / "static" / "index.html"
        return HTMLResponse(html_path.read_text())

    @app.get("/config")
    async def get_config() -> JSONResponse:
        user = load_user_config()
        project = load_project_config()
        masked = json.loads(json.dumps(user))
        if masked.get("auth", {}).get("openrouter_api_key"):
            masked["auth"]["openrouter_api_key"] = masked["auth"]["openrouter_api_key"][:8] + "***"
        if masked.get("auth", {}).get("gemini_api_key") and masked["auth"]["gemini_api_key"]:
            masked["auth"]["gemini_api_key"] = "***"
        return JSONResponse({"user": masked, "project": project})

    @app.put("/config")
    async def put_config(request: Request) -> JSONResponse:
        body = await request.json()
        if "user" in body:
            existing_user = load_user_config()
            # Don't overwrite masked API keys
            new_user = body["user"]
            if new_user.get("auth", {}).get("openrouter_api_key", "").endswith("***"):
                new_user.setdefault("auth", {})["openrouter_api_key"] = (
                    existing_user.get("auth", {}).get("openrouter_api_key", "")
                )
            if new_user.get("auth", {}).get("gemini_api_key") == "***":
                new_user.setdefault("auth", {})["gemini_api_key"] = (
                    existing_user.get("auth", {}).get("gemini_api_key", "")
                )
            save_user_config(new_user)
        if "project" in body:
            save_project_config(body["project"])
        return JSONResponse({"ok": True})

    @app.post("/run")
    async def start_run(request: Request) -> JSONResponse:
        body = await request.json()
        run_id = str(uuid.uuid4())
        cfg = load_config()
        _store.create(run_id, cfg)

        provider_name = body.get("provider") or cfg["defaults"].get("provider", "openrouter")
        model = body.get("model")

        if provider_name == "gemini":
            from ..engine.providers.gemini_provider import GeminiProvider

            key = cfg.get("auth", {}).get("gemini_api_key") or ""
            prov = GeminiProvider(api_key=key)
            resolved_model = model or cfg.get("gemini", {}).get("model", "gemini-2.0-flash")
        else:
            from ..engine.providers.openai_provider import OpenAIProvider

            key = cfg.get("auth", {}).get("openrouter_api_key") or ""
            base_url = cfg.get("openrouter", {}).get("base_url", "https://openrouter.ai/api/v1")
            prov = OpenAIProvider(api_key=key, base_url=base_url)
            resolved_model = model or cfg.get("openrouter", {}).get("model", "openrouter/auto")

        default_max_turns = cfg["defaults"].get("max_turns", 10)

        async def _bg() -> None:
            async for event in run_triage(
                cfg,
                projects_filter=body.get("projects"),
                limit=body.get("limit"),
                refresh=body.get("refresh", False),
                post_comment=body.get("post_comment", False),
                dry_run=body.get("dry_run", False),
                max_posts=body.get("max_posts", 20),
                concurrency=body.get("concurrency", 4),
                provider=prov,
                model=resolved_model,
                max_turns=body.get("max_turns", default_max_turns),
            ):
                _store.append_event(run_id, event)

        task = asyncio.create_task(_bg())
        _store.set_task(run_id, task)
        return JSONResponse({"run_id": run_id})

    @app.post("/run/{run_id}/stop")
    async def stop_run(run_id: str) -> JSONResponse:
        run = _store.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        if run["status"] != "running":
            return JSONResponse({"ok": True, "status": run["status"]})
        task = run.get("task")
        if task and not task.done():
            task.cancel()
        run["status"] = "stopped"
        run["queue"].put_nowait({"t": "run_stopped", "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return JSONResponse({"ok": True, "status": "stopped"})

    @app.get("/run/{run_id}/stream")
    async def stream_run(run_id: str, request: Request) -> EventSourceResponse:
        run = _store.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")

        async def generator() -> AsyncIterator[dict]:
            # Replay already-emitted events first
            for ev in run["events"]:
                yield {"data": json.dumps(ev)}
            # Then follow the live queue
            if run["status"] in ("done", "stopped"):
                return
            q: asyncio.Queue = run["queue"]
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield {"data": json.dumps(ev)}
                    if ev.get("t") in ("run_done", "run_stopped"):
                        break
                except asyncio.TimeoutError:
                    if run["status"] in ("done", "stopped"):
                        break

        return EventSourceResponse(generator())

    @app.get("/run/{run_id}/results")
    async def get_results(run_id: str) -> JSONResponse:
        results = _store.get_results(run_id)
        if results is None:
            raise HTTPException(404, "run not found")
        return JSONResponse(results)

    @app.get("/run/{run_id}/bugs/{bug_id}")
    async def get_bug(run_id: str, bug_id: int) -> JSONResponse:
        result = _store.get_bug_result(run_id, bug_id)
        if result is None:
            raise HTTPException(404, "bug or run not found")
        return JSONResponse(result)

    @app.post("/run/{run_id}/bugs/{bug_id}/post")
    async def post_bug_comment(run_id: str, bug_id: int, request: Request) -> JSONResponse:
        result = _store.get_bug_result(run_id, bug_id)
        if result is None:
            raise HTTPException(404, "bug classification not found")

        body = await request.json()
        dry_run = body.get("dry_run", False)
        comment_body_override = body.get("comment_body")

        cfg = load_config()
        from ..engine.lp_fetcher import LPFetcher, build_comment_body

        fetcher = LPFetcher(
            cache_dir=Path(cfg["defaults"]["cache_dir"]).expanduser(),
            lp_credentials_file=cfg.get("auth", {}).get("lp_credentials_file"),
        )

        comment_body = comment_body_override or build_comment_body(result, bug_id)
        url = await fetcher.post_comment(bug_id, comment_body, dry_run=dry_run)
        return JSONResponse({"url": url, "dry_run": dry_run})

    @app.get("/run/{run_id}/replay")
    async def replay_run(run_id: str) -> JSONResponse:
        events = _store.replay_from_ndjson(run_id)
        return JSONResponse({"events": events})

    @app.get("/auth/lp")
    async def lp_oauth_start() -> JSONResponse:
        """Get LP request token and authorization URL (OOB desktop flow)."""
        from ..engine.lp_auth import get_request_token

        cfg = load_config()
        auth_url, token_key, token_secret = await asyncio.to_thread(
            get_request_token, cfg
        )
        _pending_oauth[token_key] = token_secret
        return JSONResponse({"auth_url": auth_url, "token_key": token_key})

    @app.post("/auth/lp/complete")
    async def lp_oauth_complete(request: Request) -> JSONResponse:
        """Exchange the already-authorized OOB request token for an access token."""
        from ..engine.lp_auth import exchange_token

        body = await request.json()
        token_key = body.get("token_key", "")
        cfg = load_config()
        token_secret = _pending_oauth.pop(token_key, "")
        if not token_secret:
            return JSONResponse(
                {"ok": False, "error": "Session expired — please start again."},
                status_code=400,
            )
        success = await asyncio.to_thread(
            exchange_token, cfg, token_key, token_secret, ""
        )
        if success:
            return JSONResponse({"ok": True})
        return JSONResponse(
            {"ok": False, "error": "Token exchange failed — check server logs."},
            status_code=400,
        )

    # Mount static files last
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    return app
