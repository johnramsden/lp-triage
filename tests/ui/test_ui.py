"""Playwright UI tests — drive a real browser against the locally-served web UI."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import pytest
import uvicorn
from playwright.sync_api import Page, expect

from lp_triage.engine.events import (
    BugStartEvent,
    ClassificationEvent,
    ProjectStartEvent,
    RunDoneEvent,
    RunStartEvent,
    to_ndjson,
)
from lp_triage.web.server import _store, create_app

PORT = 18080
BASE_URL = f"http://localhost:{PORT}"


@pytest.fixture(scope="module")
def minimal_cfg(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("ui_test")
    return {
        "auth": {"openrouter_api_key": "test", "gemini_api_key": "", "lp_credentials_file": ""},
        "defaults": {
            "provider": "openrouter",
            "cache_dir": str(tmp_path / "cache"),
            "output_dir": str(tmp_path / "output"),
            "bug_list_ttl": 3600,
            "concurrency": 1,
        },
        "openrouter": {"model": "openrouter/auto", "base_url": "https://openrouter.ai/api/v1"},
        "gemini": {"model": "gemini-2.0-flash"},
        "repositories": [],
        "projects": [],
    }


@pytest.fixture(scope="module")
def live_server(minimal_cfg):
    """Start a real uvicorn server for the duration of the test module."""
    with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
        with patch("lp_triage.web.server.load_user_config", return_value=minimal_cfg):
            with patch("lp_triage.web.server.load_project_config", return_value={}):
                app = create_app(minimal_cfg)

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for server to start
    for _ in range(30):
        try:
            import httpx
            httpx.get(f"{BASE_URL}/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    yield BASE_URL

    server.should_exit = True


def test_auto_mode_run(live_server, page: Page, minimal_cfg, tmp_path):
    """Start a run in auto mode, observe progress feed, summary on run_done."""
    result = {
        "schema": 1,
        "category": "bug",
        "evidence": ["https://github.com/canonical/ceph-charms/commit/abc123"],
        "summary": "Crash on upgrade",
        "recommended_action": "Investigate logs",
        "potential_resolution_detail": "The issue is in src/charm.py",
        "fix_reference": None,
    }

    async def _fake_run(*args, **kwargs) -> AsyncIterator:
        yield RunStartEvent(projects=["charm-ceph-mon"])
        yield ProjectStartEvent(project="charm-ceph-mon")
        yield BugStartEvent(project="charm-ceph-mon", bug_id=12345, title="Crash on upgrade")
        yield ClassificationEvent(bug_id=12345, result=result)
        yield RunDoneEvent(stats={"bugs": 1, "posted": 0, "errors": 0,
                                   "total_input_tokens": 500, "total_output_tokens": 100})

    with patch("lp_triage.web.server.run_triage", side_effect=lambda *a, **kw: _fake_run(**kw)):
        with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
            page.goto(live_server)
            page.wait_for_selector("#btn-start-run")
            page.click("#btn-start-run")

            # Summary table should appear after run_done
            expect(page.locator("#summary-section")).to_be_visible(timeout=10000)
            expect(page.locator("#summary-tbody tr")).to_have_count(1, timeout=10000)

            # Category badge should show 'bug'
            badge = page.locator(".badge-bug")
            expect(badge).to_be_visible()


def test_review_mode_queue(live_server, page: Page, minimal_cfg):
    """Switch to review mode, classification appears in queue, skip removes it."""
    result = {
        "schema": 1,
        "category": "enhancement",
        "evidence": [],
        "summary": "Add new feature",
        "recommended_action": "Consider for next milestone",
        "potential_resolution_detail": "No code changes needed now",
        "fix_reference": None,
    }

    async def _fake_run(*args, **kwargs) -> AsyncIterator:
        yield RunStartEvent(projects=["charm-ceph-mon"])
        yield BugStartEvent(project="charm-ceph-mon", bug_id=99999, title="Add feature X")
        yield ClassificationEvent(bug_id=99999, result=result)
        yield RunDoneEvent(stats={"bugs": 1, "posted": 0, "errors": 0,
                                   "total_input_tokens": 0, "total_output_tokens": 0})

    with patch("lp_triage.web.server.run_triage", side_effect=lambda *a, **kw: _fake_run(**kw)):
        with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
            page.goto(live_server)
            # Select review mode
            page.select_option("#run-mode", "review")
            page.click("#btn-start-run")
            time.sleep(1.5)

            # Navigate to review panel
            page.click("[data-panel='review']")
            expect(page.locator(".review-card")).to_be_visible(timeout=8000)

            # Skip the item
            page.click(".review-card >> text=Skip")
            expect(page.locator("#review-empty")).to_be_visible(timeout=5000)


def test_config_editor_save(live_server, page: Page, minimal_cfg):
    """Change a field in config editor, save, verify no JS error."""
    with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
        with patch("lp_triage.web.server.save_user_config"):
            with patch("lp_triage.web.server.save_project_config"):
                page.goto(live_server)
                page.click("[data-panel='config']")

                # Change model field
                page.wait_for_selector("#cfg-or-model", timeout=5000)
                page.fill("#cfg-or-model", "openrouter/auto")
                page.click("text=Save")

                # Button should momentarily show "Saved!"
                expect(page.get_by_text("Saved!")).to_be_visible(timeout=3000)


def test_lp_connect_button_present(live_server, page: Page):
    """LP Connect button is visible in config panel."""
    page.goto(live_server)
    page.click("[data-panel='config']")
    expect(page.get_by_text("Connect Launchpad")).to_be_visible()


def test_reload_recovery(live_server, page: Page, minimal_cfg, tmp_path):
    """Inject a completed run into the store; reloading the page re-populates state."""
    import uuid as _uuid

    run_id = str(_uuid.uuid4())
    # Inject run with a classification result
    _store.create(run_id, minimal_cfg)
    ev = ClassificationEvent(
        bug_id=55555,
        result={
            "schema": 1, "category": "question", "evidence": [],
            "summary": "User asking how to configure X",
            "recommended_action": "Refer to docs",
            "potential_resolution_detail": "No code change",
            "fix_reference": None,
        }
    )
    _store.append_event(run_id, RunStartEvent(projects=["charm-ceph-mon"]))
    _store.append_event(run_id, ev)
    _store.append_event(
        run_id,
        RunDoneEvent(stats={"bugs": 1, "posted": 0, "errors": 0,
                             "total_input_tokens": 0, "total_output_tokens": 0}),
    )

    # Set localStorage so the page knows to replay
    page.goto(live_server)
    page.evaluate(f"localStorage.setItem('lp-triage-run-id', '{run_id}')")
    page.reload()
    page.wait_for_timeout(2000)

    # The replay endpoint should have been called and the summary populated
    expect(page.locator("#summary-section")).to_be_visible(timeout=8000)
