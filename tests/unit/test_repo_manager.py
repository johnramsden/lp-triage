import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lp_triage.engine.repo_manager import PathScopeError, RepoError, RepoManager


@pytest.fixture
def repo_manager(tmp_path):
    return RepoManager(tmp_path)


def test_scoped_path_valid(repo_manager):
    assert repo_manager._scoped_path("ceph-mon", "src/main.py") == "ceph-mon/src/main.py"
    assert repo_manager._scoped_path("ceph-mon", "README.md") == "ceph-mon/README.md"


def test_scoped_path_rejects_traversal(repo_manager):
    with pytest.raises(PathScopeError):
        repo_manager._scoped_path("ceph-mon", "../etc/passwd")


def test_scoped_path_rejects_absolute(repo_manager):
    with pytest.raises(PathScopeError):
        repo_manager._scoped_path("ceph-mon", "/etc/passwd")


def test_scoped_path_rejects_deep_traversal(repo_manager):
    with pytest.raises(PathScopeError):
        repo_manager._scoped_path("ceph-mon", "src/../../etc/passwd")


def test_repo_path(tmp_path):
    rm = RepoManager(tmp_path)
    assert rm.repo_path("ceph-charms") == tmp_path / "repos" / "ceph-charms"


@pytest.mark.asyncio
async def test_run_git_failure(repo_manager, tmp_path):
    fake_repo = tmp_path / "repos" / "test"
    fake_repo.mkdir(parents=True, exist_ok=True)
    with pytest.raises(RepoError):
        await repo_manager.get_log(fake_repo, "main", "subdir", 5)


@pytest.mark.asyncio
async def test_fetch_all_skips_missing_repos(tmp_path):
    rm = RepoManager(tmp_path)
    # Should not raise even if repos don't exist
    await rm.fetch_all(["nonexistent-repo"])


@pytest.mark.asyncio
async def test_run_raises_repo_error_on_nonzero(repo_manager):
    with pytest.raises(RepoError):
        await repo_manager._run(["false"])
