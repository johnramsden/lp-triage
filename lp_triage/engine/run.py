"""Orchestration: fetch bugs, run agent loops, post comments, emit events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from .agent_loop import classify_bug
from .config import ProjectCfg, get_projects, repo_dir_name
from .events import (
    BugErrorEvent,
    BugProgressEvent,
    BugStartEvent,
    ClassificationEvent,
    CommentPostedEvent,
    ProjectDoneEvent,
    ProjectStartEvent,
    RunDoneEvent,
    RunStartEvent,
    StreamEvent,
    TokenUsageEvent,
)
from .lp_fetcher import LPFetcher, build_comment_body
from .providers.base import Provider
from .repo_manager import RepoManager

logger = logging.getLogger(__name__)

_IMPORTANCE_ORDER = {
    "Critical": 0,
    "High": 1,
    "Medium": 2,
    "Low": 3,
    "Wishlist": 4,
    "Undecided": 5,
}

_SENTINEL = object()


async def run_triage(
    cfg: dict,
    *,
    projects_filter: list[str] | None = None,
    limit: int | None = None,
    refresh: bool = False,
    post_comment: bool = False,
    dry_run: bool = False,
    max_posts: int = 20,
    concurrency: int = 4,
    parallel_projects: bool = False,
    provider: Provider,
    model: str,
    debug: bool = False,
    lp_login: str = "",
    max_turns: int = 10,
) -> AsyncIterator[StreamEvent]:
    all_projects = get_projects(cfg)
    if projects_filter:
        all_projects = [p for p in all_projects if p.lp_project in projects_filter]

    cache_dir = Path(cfg["defaults"]["cache_dir"]).expanduser()
    output_dir = Path(cfg["defaults"]["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    repo_manager = RepoManager(cache_dir)
    fetcher = LPFetcher(
        cache_dir=cache_dir,
        bug_list_ttl=cfg["defaults"].get("bug_list_ttl", 3600),
        refresh=refresh,
        lp_credentials_file=cfg.get("auth", {}).get("lp_credentials_file"),
    )

    project_names = [p.lp_project for p in all_projects]
    yield RunStartEvent(projects=project_names)

    # Deduplicate repos by URL so we clone/fetch each once.
    unique_urls = {p.url for p in all_projects}
    await asyncio.gather(*[
        repo_manager.ensure_cloned(repo_dir_name(url), url) for url in unique_urls
    ])
    await repo_manager.fetch_all([repo_dir_name(url) for url in unique_urls])

    stats: dict = {
        "bugs": 0,
        "posted": 0,
        "errors": 0,
        "posts_skipped_cap": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }
    posts_made = 0

    async def process_bug_into_queue(
        bug_summary: dict,
        project: ProjectCfg,
        q: asyncio.Queue,
        sem: asyncio.Semaphore,
    ) -> None:
        bug_id = bug_summary["id"]
        async with sem:
            try:
                await q.put(
                    BugStartEvent(
                        project=project.lp_project,
                        bug_id=bug_id,
                        title=bug_summary["title"],
                    )
                )
                await q.put(BugProgressEvent(bug_id=bug_id, step="fetching bug"))

                bug_detail = await fetcher.get_bug_detail(
                    bug_id, bug_summary["date_last_updated"]
                )
                bug_detail.setdefault("status", bug_summary.get("status"))
                bug_detail.setdefault("importance", bug_summary.get("importance"))

                await q.put(BugProgressEvent(bug_id=bug_id, step="gathering context"))

                classification_result: dict | None = None
                async for ev in classify_bug(
                    bug_detail, project, repo_manager, provider, model,
                    debug=debug, max_turns=max_turns,
                ):
                    await q.put(ev)
                    if isinstance(ev, ClassificationEvent):
                        classification_result = ev.result

                nonlocal posts_made
                if (
                    classification_result
                    and post_comment
                    and classification_result.get("evidence")
                ):
                    await q.put(
                        BugProgressEvent(bug_id=bug_id, step="checking for existing comment")
                    )
                    already = await fetcher.has_existing_ai_comment(bug_id, lp_login)
                    if not already:
                        if posts_made < max_posts:
                            await q.put(BugProgressEvent(bug_id=bug_id, step="posting comment"))
                            body = build_comment_body(classification_result, bug_id)
                            url = await fetcher.post_comment(bug_id, body, dry_run=dry_run)
                            posts_made += 1
                            await q.put(CommentPostedEvent(bug_id=bug_id, url=url))
                        else:
                            stats["posts_skipped_cap"] += 1

            except Exception as e:
                logger.exception("Error processing bug %d", bug_id)
                await q.put(BugErrorEvent(bug_id=bug_id, error=str(e)))

        await q.put(_SENTINEL)

    async def process_project(project: ProjectCfg) -> AsyncIterator[StreamEvent]:
        yield ProjectStartEvent(project=project.lp_project)

        bugs = await fetcher.get_active_bugs(project.lp_project)
        if limit:
            bugs = bugs[:limit]

        bugs_sorted = sorted(
            bugs, key=lambda b: _IMPORTANCE_ORDER.get(b.get("importance", ""), 99)
        )

        sem = asyncio.Semaphore(concurrency)
        q: asyncio.Queue = asyncio.Queue()

        tasks = [
            asyncio.create_task(process_bug_into_queue(b, project, q, sem))
            for b in bugs_sorted
        ]
        remaining = len(tasks)

        while remaining > 0:
            item = await q.get()
            if item is _SENTINEL:
                remaining -= 1
            else:
                yield item
                if isinstance(item, ClassificationEvent):
                    stats["bugs"] += 1
                elif isinstance(item, BugErrorEvent):
                    stats["errors"] += 1
                elif isinstance(item, CommentPostedEvent):
                    stats["posted"] += 1
                elif isinstance(item, TokenUsageEvent):
                    stats["total_input_tokens"] += item.input
                    stats["total_output_tokens"] += item.output

        for t in tasks:
            await t

        yield ProjectDoneEvent(project=project.lp_project)

    for project in all_projects:
        async for ev in process_project(project):
            yield ev

    stats["posts_made"] = posts_made
    yield RunDoneEvent(stats=stats)
