"""Unit tests for LP fetcher — mock launchpadlib at the boundary."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lp_triage.engine.lp_fetcher import LP_DISCLAIMER, LPFetcher, _clean_text, build_comment_body


@pytest.fixture
def fetcher(tmp_path):
    return LPFetcher(cache_dir=tmp_path, bug_list_ttl=3600)


def _make_bug(bug_id=1001, title="Test bug", status="New", importance="Medium"):
    task = MagicMock()
    task.status = status
    task.importance = importance
    bug = MagicMock()
    bug.id = bug_id
    bug.title = title
    bug.description = "A test bug"
    bug.web_link = f"https://bugs.launchpad.net/bugs/{bug_id}"
    import datetime
    bug.date_last_updated = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    bug.messages = []
    task.bug = bug
    return task, bug


def test_bug_list_cache_miss_then_hit(fetcher, tmp_path):
    task, bug = _make_bug()
    mock_project = MagicMock()
    mock_project.searchTasks.return_value = [task]

    mock_lp = MagicMock()
    mock_lp.projects.__getitem__ = MagicMock(return_value=mock_project)

    with patch.object(fetcher, "_get_lp_anonymous", return_value=mock_lp):
        bugs1 = fetcher._fetch_bug_list("charm-ceph-mon")
        fetcher._save_bug_list_cache("charm-ceph-mon", bugs1)

    # Cache hit — no LP call needed
    with patch.object(fetcher, "_fetch_bug_list") as mock_fetch:
        cached = fetcher._load_bug_list_cache("charm-ceph-mon")
        assert cached is not None
        assert not mock_fetch.called


def test_bug_list_cache_expires(fetcher):
    fetcher._save_bug_list_cache("charm-ceph-mon", [{"id": 1}])
    # Manually overwrite saved_at to an old timestamp
    path = fetcher._bug_list_cache_path("charm-ceph-mon")
    data = json.loads(path.read_text())
    data["saved_at"] = time.time() - 7200  # 2 hours ago
    path.write_text(json.dumps(data))

    result = fetcher._load_bug_list_cache("charm-ceph-mon")
    assert result is None  # expired


def test_bug_detail_cache_keyed_on_date(fetcher):
    detail = {"id": 42, "title": "Test"}
    fetcher._save_bug_detail_cache(42, "2026-01-01T00:00:00+00:00", detail)

    # Same key hits
    cached = fetcher._load_bug_detail_cache(42, "2026-01-01T00:00:00+00:00")
    assert cached == detail

    # Different date misses
    not_cached = fetcher._load_bug_detail_cache(42, "2026-02-01T00:00:00+00:00")
    assert not_cached is None


def test_refresh_flag_bypasses_bug_list_cache(fetcher):
    fetcher._save_bug_list_cache("charm-ceph-mon", [{"id": 999}])
    fetcher._refresh = True

    task, bug = _make_bug(bug_id=888)
    mock_project = MagicMock()
    mock_project.searchTasks.return_value = [task]
    mock_lp = MagicMock()
    mock_lp.projects.__getitem__ = MagicMock(return_value=mock_project)

    with patch.object(fetcher, "_get_lp_anonymous", return_value=mock_lp):
        bugs = fetcher._fetch_bug_list("charm-ceph-mon")
        assert bugs[0]["id"] == 888


def test_already_commented_false(fetcher):
    mock_lp = MagicMock()
    bug = MagicMock()
    msg = MagicMock()
    msg.content = "Some other comment"
    msg.owner = MagicMock()
    msg.owner.name = "someone"
    bug.messages = [msg]
    mock_lp.bugs.__getitem__ = MagicMock(return_value=bug)

    with patch.object(fetcher, "_get_lp_anonymous", return_value=mock_lp):
        result = fetcher._check_existing_comment(42)
    assert result is False


def test_already_commented_true(fetcher):
    mock_lp = MagicMock()
    bug = MagicMock()
    msg = MagicMock()
    msg.content = LP_DISCLAIMER + "\n\nMore content"
    msg.owner = MagicMock()
    msg.owner.name = "lp-triage-bot"
    bug.messages = [msg]
    mock_lp.bugs.__getitem__ = MagicMock(return_value=bug)

    with patch.object(fetcher, "_get_lp_anonymous", return_value=mock_lp):
        result = fetcher._check_existing_comment(42)
    assert result is True


def test_build_comment_body_contains_disclaimer():
    result = {
        "schema": 1,
        "category": "bug",
        "evidence": ["https://github.com/org/repo/commit/abc123"],
        "summary": "A bug was found",
        "recommended_action": "Investigate",
        "potential_resolution_detail": "The issue is in charm.py",
        "fix_reference": None,
    }
    body = build_comment_body(result, 12345)
    assert body.startswith(LP_DISCLAIMER)
    assert "bug" in body
    assert "https://github.com/org/repo/commit/abc123" in body


def test_clean_text_expands_bare_sha():
    sha = "a47da6b0256d4e488ca0ddbccd17c030035881c2"
    text = f"The fix is in commit {sha} on main."
    result = _clean_text(text, "https://github.com/org/repo")
    assert f"https://github.com/org/repo/commit/{sha}" in result
    assert sha not in result.replace(f"commit/{sha}", "")


def test_clean_text_does_not_expand_sha_in_url():
    sha = "a47da6b0256d4e488ca0ddbccd17c030035881c2"
    url = f"https://github.com/org/repo/commit/{sha}"
    result = _clean_text(url, "https://github.com/org/repo")
    # URL should appear once, not double-expanded
    assert result.count(sha) == 1
    assert result.count("commit/commit") == 0


def test_clean_text_converts_markdown_links():
    text = "See [the fix](https://github.com/org/repo/pull/42) for details."
    result = _clean_text(text, None)
    assert "[the fix]" not in result
    assert "the fix (https://github.com/org/repo/pull/42)" in result



def test_build_comment_body_expands_sha_in_detail():
    sha = "a47da6b0256d4e488ca0ddbccd17c030035881c2"
    result = {
        "schema": 1,
        "_project_url": "https://github.com/org/repo",
        "category": "already_fixed",
        "evidence": [],
        "summary": "Fixed in main",
        "recommended_action": "Upgrade",
        "potential_resolution_detail": f"Commit {sha} resolves this.",
        "fix_reference": sha,
    }
    body = build_comment_body(result, 99)
    assert f"https://github.com/org/repo/commit/{sha}" in body
    # bare SHA should not appear outside a URL context
    assert f"Commit {sha}" not in body


def test_post_comment_dry_run_returns_url(fetcher):
    import asyncio
    url = asyncio.get_event_loop().run_until_complete(
        fetcher.post_comment(12345, "test body", dry_run=True)
    )
    assert "12345" in url
