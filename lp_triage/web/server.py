"""FastAPI web server with SSE event stream and review UI."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..engine.config import _deep_merge, load_config, load_user_config, save_user_config
from ..engine.events import ClassificationEvent, RunDoneEvent, StreamEvent
from ..engine.run import run_triage

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent


class RunStore:
    """In-memory store for active and recent runs."""

    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    def create(self, run_id: str) -> None:
        self._runs[run_id] = {
            "id": run_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "events": [],
            "results": {},
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
        run["queue"].put_nowait(d)

    def get(self, run_id: str) -> dict | None:
        return self._runs.get(run_id)

    def get_results(self, run_id: str) -> dict | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        return {"results": run["results"], "stats": run.get("stats"), "status": run["status"]}

    def get_bug_result(self, run_id: str, bug_id: int) -> dict | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        return run["results"].get(bug_id)

    def get_events(self, run_id: str) -> list[dict]:
        run = self._runs.get(run_id)
        return run["events"] if run else []


_store = RunStore()
_pending_oauth: dict = {}  # token_key -> Credentials object


def create_app() -> FastAPI:
    app = FastAPI(title="lp-triage")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = _HERE / "static" / "index.html"
        return HTMLResponse(html_path.read_text())

    _SENTINEL_KEY = "__unchanged__"

    @app.get("/status")
    async def get_status() -> JSONResponse:
        cfg = load_config()
        creds_file = cfg.get("auth", {}).get("lp_credentials_file", "")
        lp_connected = bool(creds_file and Path(creds_file).expanduser().exists())
        return JSONResponse({
            "lp_instance": cfg["defaults"].get("lp_instance", "production"),
            "lp_connected": lp_connected,
        })

    @app.get("/config")
    async def get_config() -> JSONResponse:
        user = load_user_config()
        masked = json.loads(json.dumps(user))
        # Replace secrets with a sentinel — never expose any part of the key
        auth = masked.setdefault("auth", {})
        auth["openrouter_api_key"] = _SENTINEL_KEY if auth.get("openrouter_api_key") else ""
        auth["gemini_api_key"] = _SENTINEL_KEY if auth.get("gemini_api_key") else ""
        return JSONResponse({"user": masked})

    @app.put("/config")
    async def put_config(request: Request) -> JSONResponse:
        body = await request.json()
        if "user" in body:
            existing_user = load_user_config()
            new_user = body["user"]
            # Sentinel means "no change"; empty string means "clear the key"
            for key in ("openrouter_api_key", "gemini_api_key"):
                if new_user.get("auth", {}).get(key) == _SENTINEL_KEY:
                    new_user.setdefault("auth", {})[key] = (
                        existing_user.get("auth", {}).get(key, "")
                    )
            # Merge onto existing so keys not shown in the UI are preserved
            save_user_config(_deep_merge(existing_user, new_user))
        return JSONResponse({"ok": True})

    @app.post("/run")
    async def start_run(request: Request) -> JSONResponse:
        body = await request.json()
        run_id = str(uuid.uuid4())
        cfg = load_config()
        _store.create(run_id)

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

        default_max_turns = cfg["defaults"].get("max_turns", 30)

        async def _bg() -> None:
            try:
                async for event in run_triage(
                    cfg,
                    projects_filter=body.get("projects"),
                    limit=body.get("limit"),
                    refresh=body.get("refresh", False),
                    post_comment=body.get("post_comment", False),
                    allow_repost=body.get("allow_repost", False),
                    dry_run=body.get("dry_run", False),
                    max_posts=body.get("max_posts", 20),
                    concurrency=body.get("concurrency", 4),
                    provider=prov,
                    model=resolved_model,
                    max_turns=body.get("max_turns", default_max_turns),
                ):
                    _store.append_event(run_id, event)
            except asyncio.CancelledError:
                # Append terminal event so replay shows the run as stopped.
                run = _store.get(run_id)
                if run:
                    run["events"].append({"t": "run_stopped", "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                raise

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
            lp_instance=cfg["defaults"].get("lp_instance", "production"),
        )

        if not dry_run and not result.get("already_posted"):
            already = await fetcher.has_existing_ai_comment(bug_id)
            if already:
                return JSONResponse(
                    {"ok": False, "error": "Bug already has an lp-triage comment"},
                    status_code=409,
                )

        comment_body = comment_body_override or build_comment_body(result, bug_id)
        url = await fetcher.post_comment(bug_id, comment_body, dry_run=dry_run)
        return JSONResponse({"url": url, "dry_run": dry_run})

    @app.get("/run/{run_id}/replay")
    async def replay_run(run_id: str) -> JSONResponse:
        run = _store.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return JSONResponse({"events": _store.get_events(run_id)})

    @app.get("/auth/lp")
    async def lp_oauth_start() -> JSONResponse:
        """Get LP request token and authorization URL (OOB desktop flow)."""
        from ..engine.lp_auth import get_request_token

        lp_instance = load_config()["defaults"].get("lp_instance", "production")
        auth_url, token_key, creds = await asyncio.to_thread(get_request_token, lp_instance)
        _pending_oauth[token_key] = creds
        return JSONResponse({"auth_url": auth_url, "token_key": token_key})

    @app.post("/auth/lp/complete")
    async def lp_oauth_complete(request: Request) -> JSONResponse:
        """Exchange the already-authorized OOB request token for an access token."""
        from ..engine.lp_auth import exchange_token

        body = await request.json()
        token_key = body.get("token_key", "")
        cfg = load_config()
        creds = _pending_oauth.pop(token_key, None)
        if creds is None:
            return JSONResponse(
                {"ok": False, "error": "Session expired — please start again."},
                status_code=400,
            )
        success = await asyncio.to_thread(
            exchange_token, cfg, creds,
            cfg["defaults"].get("lp_instance", "production"),
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
