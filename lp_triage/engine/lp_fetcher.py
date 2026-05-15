"""Launchpad fetcher with two-level cache.

Level 1: bug list per project — TTL-based (default 1 hour).
Level 2: individual bug detail — keyed on (bug_id, date_last_updated).

The "already commented" check is always live.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LP_API = "https://api.launchpad.net/1.0"
LP_DISCLAIMER = "[lp-triage AI report — informational only; a human must decide final actions]"

ACTIVE_STATUSES = [
    "New",
    "Incomplete",
    "Confirmed",
    "Triaged",
    "In Progress",
    "Fix Committed",
]


class LPFetcher:
    def __init__(
        self,
        cache_dir: Path,
        bug_list_ttl: int = 3600,
        refresh: bool = False,
        lp_credentials_file: str | None = None,
    ):
        self._cache_dir = cache_dir / "lp"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._bug_list_ttl = bug_list_ttl
        self._refresh = refresh
        self._lp_credentials_file = lp_credentials_file
        self._lp: Any = None  # launchpadlib Launchpad instance (authenticated)

    # ------------------------------------------------------------------ public

    async def get_active_bugs(self, lp_project: str) -> list[dict]:
        if not self._refresh:
            cached = self._load_bug_list_cache(lp_project)
            if cached is not None:
                return cached

        bugs = await asyncio.to_thread(self._fetch_bug_list, lp_project)
        self._save_bug_list_cache(lp_project, bugs)
        return bugs

    async def get_bug_detail(self, bug_id: int, date_last_updated: str) -> dict:
        if not self._refresh:
            cached = self._load_bug_detail_cache(bug_id, date_last_updated)
            if cached is not None:
                return cached

        detail = await asyncio.to_thread(self._fetch_bug_detail, bug_id)
        self._save_bug_detail_cache(bug_id, date_last_updated, detail)
        return detail

    async def has_existing_ai_comment(self, bug_id: int) -> bool:
        return await asyncio.to_thread(self._check_existing_comment, bug_id)

    async def post_comment(self, bug_id: int, body: str, dry_run: bool = False) -> str:
        if dry_run:
            url = f"https://bugs.launchpad.net/bugs/{bug_id}"
            logger.info("[dry-run] would post comment to bug %d", bug_id)
            return url
        return await asyncio.to_thread(self._do_post_comment, bug_id, body)

    # ----------------------------------------------------------------- caching

    def _bug_list_cache_path(self, lp_project: str) -> Path:
        return self._cache_dir / f"{lp_project}_buglist.json"

    def _bug_detail_cache_path(self, bug_id: int, date_last_updated: str) -> Path:
        safe = date_last_updated.replace(":", "-").replace("+", "p")
        return self._cache_dir / f"bug_{bug_id}_{safe}.json"

    def _load_bug_list_cache(self, lp_project: str) -> list[dict] | None:
        path = self._bug_list_cache_path(lp_project)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if time.time() - data["saved_at"] > self._bug_list_ttl:
                return None
            return data["bugs"]
        except (KeyError, json.JSONDecodeError):
            return None

    def _save_bug_list_cache(self, lp_project: str, bugs: list[dict]) -> None:
        path = self._bug_list_cache_path(lp_project)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"saved_at": time.time(), "bugs": bugs}))
        tmp.replace(path)

    def _load_bug_detail_cache(self, bug_id: int, date_last_updated: str) -> dict | None:
        path = self._bug_detail_cache_path(bug_id, date_last_updated)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def _save_bug_detail_cache(self, bug_id: int, date_last_updated: str, detail: dict) -> None:
        path = self._bug_detail_cache_path(bug_id, date_last_updated)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(detail))
        tmp.replace(path)

    # --------------------------------------------------------- LP API (sync)

    def _get_lp_anonymous(self) -> Any:
        from launchpadlib.launchpad import Launchpad

        return Launchpad.login_anonymously(
            "lp-triage",
            "production",
            launchpadlib_dir=str(self._cache_dir / "launchpadlib"),
            version="devel",
        )

    def _get_lp_authenticated(self) -> Any:
        if self._lp is not None:
            return self._lp
        from launchpadlib.launchpad import Launchpad

        creds = self._lp_credentials_file
        if creds and Path(creds).exists():
            self._lp = Launchpad.login_with(
                "lp-triage",
                "production",
                credentials_file=creds,
                launchpadlib_dir=str(self._cache_dir / "launchpadlib"),
                version="devel",
            )
        else:
            self._lp = self._get_lp_anonymous()
        return self._lp

    def _fetch_bug_list(self, lp_project: str) -> list[dict]:
        lp = self._get_lp_anonymous()
        project = lp.projects[lp_project]
        tasks = project.searchTasks(
            status=ACTIVE_STATUSES,
            omit_duplicates=True,
        )
        bugs = []
        for task in tasks:
            bug = task.bug
            dlu = bug.date_last_updated
            if hasattr(dlu, "isoformat"):
                dlu_str = dlu.isoformat()
            else:
                dlu_str = str(dlu)
            bugs.append(
                {
                    "id": bug.id,
                    "title": bug.title,
                    "status": task.status,
                    "importance": task.importance,
                    "date_last_updated": dlu_str,
                    "web_link": bug.web_link,
                }
            )
        return bugs

    def _fetch_bug_detail(self, bug_id: int) -> dict:
        lp = self._get_lp_anonymous()
        bug = lp.bugs[bug_id]
        messages = []
        for msg in bug.messages:
            messages.append(
                {
                    "author": str(msg.owner.name) if msg.owner else "unknown",
                    "content": msg.content,
                    "date_created": (
                        msg.date_created.isoformat()
                        if hasattr(msg.date_created, "isoformat")
                        else str(msg.date_created)
                    ),
                }
            )
        dlu = bug.date_last_updated
        return {
            "id": bug.id,
            "title": bug.title,
            "description": bug.description,
            "web_link": bug.web_link,
            "date_last_updated": dlu.isoformat() if hasattr(dlu, "isoformat") else str(dlu),
            "messages": messages,
        }

    def _check_existing_comment(self, bug_id: int) -> bool:
        lp = self._get_lp_anonymous()
        bug = lp.bugs[bug_id]
        for msg in bug.messages:
            if msg.content.startswith(LP_DISCLAIMER):
                return True
        return False

    def _do_post_comment(self, bug_id: int, body: str) -> str:
        lp = self._get_lp_authenticated()
        bug = lp.bugs[bug_id]
        msg = bug.newMessage(content=body, subject="lp-triage AI report")
        return msg.self_link


def build_comment_body(result: dict, bug_id: int) -> str:
    lines = [
        LP_DISCLAIMER,
        "",
        f"**Category**: {result.get('category', 'unknown')}",
        "",
        f"**Summary**: {result.get('summary', '')}",
        "",
        f"**Recommended action**: {result.get('recommended_action', '')}",
        "",
    ]
    if result.get("potential_resolution_detail"):
        lines += [
            "**Potential resolution detail**:",
            result["potential_resolution_detail"],
            "",
        ]
    if result.get("fix_reference"):
        lines += [f"**Fix reference**: {result['fix_reference']}", ""]
    evidence = result.get("evidence", [])
    if evidence:
        lines.append("**Evidence**:")
        for url in evidence:
            lines.append(f"- {url}")
        lines.append("")
    return "\n".join(lines).strip()
