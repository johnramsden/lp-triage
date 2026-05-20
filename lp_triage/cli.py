from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from .engine.config import get_projects, load_config
from .engine.events import RunDoneEvent, StreamEvent, to_ndjson
from .engine.run import run_triage

app = typer.Typer(help="Launchpad bug triage tool", no_args_is_help=True)


def _make_provider(cfg: dict, provider_name: str, model: str | None) -> tuple:
    if provider_name == "gemini":
        from .engine.providers.gemini_provider import GeminiProvider

        key = cfg.get("auth", {}).get("gemini_api_key") or ""
        prov = GeminiProvider(api_key=key)
        m = model or cfg.get("gemini", {}).get("model", "gemini-2.0-flash")
    else:
        from .engine.providers.openai_provider import OpenAIProvider

        key = cfg.get("auth", {}).get("openrouter_api_key") or ""
        base_url = cfg.get("openrouter", {}).get("base_url", "https://openrouter.ai/api/v1")
        prov = OpenAIProvider(api_key=key, base_url=base_url)
        m = model or cfg.get("openrouter", {}).get("model", "openrouter/auto")
    return prov, m


def _human_format(event: StreamEvent) -> str | None:
    from .engine.events import (
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
    )

    if isinstance(event, RunStartEvent):
        return f"[run] starting — projects: {', '.join(event.projects)}"
    if isinstance(event, ProjectStartEvent):
        return f"\n[project] {event.project}"
    if isinstance(event, BugStartEvent):
        return f"  bug #{event.bug_id}: {event.title}"
    if isinstance(event, BugProgressEvent) and event.bug_id:
        return f"    ... {event.step}"
    if isinstance(event, TokenUsageEvent):
        return f"    tokens: in={event.input} out={event.output}"
    if isinstance(event, ClassificationEvent):
        r = event.result
        cat = r.get("category", "?")
        summary = r.get("summary", "")
        evidence = r.get("evidence", [])
        lines = [f"    → {cat}: {summary}"]
        if evidence:
            lines.append(f"      evidence: {', '.join(evidence[:3])}")
        return "\n".join(lines)
    if isinstance(event, CommentPostedEvent):
        return f"    ✓ comment posted: {event.url}"
    if isinstance(event, BugErrorEvent):
        return f"    ✗ error: {event.error}"
    if isinstance(event, ProjectDoneEvent):
        return f"  [done] {event.project}"
    if isinstance(event, RunDoneEvent):
        s = event.stats
        skipped = s.get('posts_skipped_cap', 0)
        skipped_str = f" skipped={skipped}" if skipped else ""
        return (
            f"\n[run done] bugs={s.get('bugs', 0)} posted={s.get('posted', 0)}"
            f"{skipped_str} errors={s.get('errors', 0)} "
            f"tokens={s.get('total_input_tokens', 0)}in/{s.get('total_output_tokens', 0)}out"
        )
    return None


@app.command()
def run(
    projects: Optional[list[str]] = typer.Option(None, "--projects", help="LP project names"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max bugs per project"),
    refresh: bool = typer.Option(False, "--refresh", help="Bust cache"),
    post_comment: bool = typer.Option(False, "--post-comment", help="Post LP comments"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Dry-run (no LP writes)"),
    max_posts: int = typer.Option(20, "--max-posts"),
    human: bool = typer.Option(False, "--human", help="Human-readable output"),
    debug: bool = typer.Option(False, "--debug", help="Include tool-call events"),
    concurrency: int = typer.Option(4, "--concurrency"),
    max_turns: Optional[int] = typer.Option(None, "--max-turns", help="Max agent loop turns per bug (default from config)"),
    provider_name: str = typer.Option("", "--provider", help="openrouter or gemini"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    """Run triage on active Launchpad bugs."""
    cfg = load_config()
    pname = provider_name or cfg["defaults"].get("provider", "openrouter")
    provider, resolved_model = _make_provider(cfg, pname, model)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ndjson_path = Path(f"run-{ts}.ndjson")
    summary_path = Path(f"run-{ts}-summary.txt")

    classifications: list[dict] = []

    resolved_max_turns = max_turns if max_turns is not None else cfg["defaults"].get("max_turns", 30)
    if resolved_max_turns < 1:
        raise typer.BadParameter("--max-turns must be >= 1")

    async def _run() -> None:
        with open(ndjson_path, "w") as ndjson_f:
            async for event in run_triage(
                cfg,
                projects_filter=projects,
                limit=limit,
                refresh=refresh,
                post_comment=post_comment,
                dry_run=dry_run,
                max_posts=max_posts,
                concurrency=concurrency,
                provider=provider,
                model=resolved_model,
                debug=debug,
                max_turns=resolved_max_turns,
            ):
                line = to_ndjson(event)
                ndjson_f.write(line + "\n")
                ndjson_f.flush()

                if human:
                    msg = _human_format(event)
                    if msg:
                        typer.echo(msg)
                else:
                    typer.echo(line)

                from .engine.events import ClassificationEvent, BugStartEvent

                if isinstance(event, ClassificationEvent):
                    classifications.append({"bug_id": event.bug_id, "result": event.result})

    asyncio.run(_run())
    _write_summary(summary_path, classifications)
    if human:
        typer.echo(f"\nOutput: {ndjson_path}")


def _write_summary(path: Path, classifications: list[dict]) -> None:
    if not classifications:
        return
    importance_order = ["Critical", "High", "Medium", "Low", "Wishlist", "Undecided"]
    lines = []
    for item in classifications:
        bug_id = item["bug_id"]
        r = item["result"]
        lines.append(
            f"Bug #{bug_id}\n"
            f"  Category: {r.get('category', '?')}\n"
            f"  Summary: {r.get('summary', '')}\n"
            f"  Action: {r.get('recommended_action', '')}\n"
            f"  Fix ref: {r.get('fix_reference') or 'n/a'}\n"
        )
    path.write_text("\n".join(lines))


@app.command()
def serve(
    port: int = typer.Option(8080, "--port"),
    open_browser: bool = typer.Option(False, "--open"),
) -> None:
    """Start the local web UI server."""
    import uvicorn

    from .web.server import create_app

    cfg = load_config()
    web_app = create_app()

    if open_browser:
        import webbrowser

        webbrowser.open(f"http://localhost:{port}")

    uvicorn.run(web_app, host="127.0.0.1", port=port)


@app.command()
def config(
    show: bool = typer.Option(True, "--show/--no-show", help="Print merged config"),
) -> None:
    """Show or edit configuration."""
    if show:
        cfg = load_config()
        # Mask secrets
        masked = json.loads(json.dumps(cfg))
        if masked.get("auth", {}).get("openrouter_api_key"):
            masked["auth"]["openrouter_api_key"] = "***"
        if masked.get("auth", {}).get("gemini_api_key"):
            masked["auth"]["gemini_api_key"] = "***"
        typer.echo(json.dumps(masked, indent=2))
