"""Integration tests — run against a real FastAPI server started in-process."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from lp_triage.engine.events import (
    ClassificationEvent,
    RunDoneEvent,
    RunStartEvent,
)
from lp_triage.web.server import create_app


@pytest.fixture
def minimal_cfg(tmp_path):
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
        "repositories": [{"name": "ceph-charms", "url": "https://github.com/canonical/ceph-charms", "branches": ["main"]}],
        "projects": [{"lp_project": "charm-ceph-mon", "repo": "ceph-charms", "branch": "main", "subdir": "ceph-mon"}],
    }


@pytest.fixture
def test_client(minimal_cfg):
    app = create_app(minimal_cfg)
    with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
        with TestClient(app) as client:
            yield client


def test_index_returns_html(test_client):
    resp = test_client.get("/")
    assert resp.status_code == 200
    assert "lp-triage" in resp.text
    assert "text/html" in resp.headers["content-type"]


def test_get_config(test_client, minimal_cfg):
    with patch("lp_triage.web.server.load_user_config", return_value=minimal_cfg):
        with patch("lp_triage.web.server.load_project_config", return_value=minimal_cfg):
            resp = test_client.get("/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "user" in data
    assert "project" in data


def test_put_config(test_client, tmp_path):
    with patch("lp_triage.web.server.load_user_config", return_value={}):
        with patch("lp_triage.web.server.save_user_config") as mock_save_user:
            with patch("lp_triage.web.server.save_project_config") as mock_save_proj:
                resp = test_client.put(
                    "/config",
                    json={
                        "user": {"auth": {"openrouter_api_key": "new-key"}},
                        "project": {"projects": []},
                    },
                )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_save_user.assert_called_once()
    mock_save_proj.assert_called_once()


def test_post_run_returns_run_id(test_client, minimal_cfg, tmp_path):
    async def _fake_run(**kwargs) -> AsyncIterator:
        yield RunStartEvent(projects=["charm-ceph-mon"])
        yield RunDoneEvent(stats={"bugs": 0, "posted": 0, "errors": 0})

    with patch("lp_triage.web.server.run_triage", side_effect=lambda *a, **kw: _fake_run(**kw)):
        with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
            resp = test_client.post("/run", json={"projects": ["charm-ceph-mon"]})

    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert len(data["run_id"]) > 10


def test_get_results_404_unknown_run(test_client):
    resp = test_client.get("/run/nonexistent-id/results")
    assert resp.status_code == 404


def test_get_bug_404_unknown(test_client):
    resp = test_client.get("/run/nonexistent/bugs/12345")
    assert resp.status_code == 404


def test_stop_run(test_client, minimal_cfg):
    async def _fake_run(**kwargs) -> AsyncIterator:
        yield RunStartEvent(projects=["charm-ceph-mon"])
        await asyncio.sleep(60)  # would run forever without stop

    with patch("lp_triage.web.server.run_triage", side_effect=lambda *a, **kw: _fake_run(**kw)):
        with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
            start = test_client.post("/run", json={})
    assert start.status_code == 200
    run_id = start.json()["run_id"]

    stop = test_client.post(f"/run/{run_id}/stop")
    assert stop.status_code == 200
    assert stop.json()["status"] == "stopped"

    # Stopping again is a no-op
    stop2 = test_client.post(f"/run/{run_id}/stop")
    assert stop2.status_code == 200

    # Unknown run_id → 404
    assert test_client.post("/run/nonexistent/stop").status_code == 404


@pytest.mark.asyncio
async def test_post_bug_dry_run(test_client, minimal_cfg, tmp_path):
    """Post a comment in dry-run mode — no LP write should occur."""
    # First inject a classification into the store
    from lp_triage.web.server import _store
    import uuid

    run_id = str(uuid.uuid4())
    _store.create(run_id, minimal_cfg)
    result = {
        "schema": 1,
        "category": "bug",
        "evidence": ["https://github.com/org/repo/commit/abc"],
        "summary": "Test",
        "recommended_action": "Fix it",
        "potential_resolution_detail": "Details",
        "fix_reference": None,
    }
    _store._runs[run_id]["results"][12345] = result

    with patch("lp_triage.web.server.load_config", return_value=minimal_cfg):
        resp = test_client.post(
            f"/run/{run_id}/bugs/12345/post",
            json={"dry_run": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["dry_run"] is True
    assert "12345" in data["url"]
