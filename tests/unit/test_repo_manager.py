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


@pytest.fixture
def real_git_repo(tmp_path):
    """Create a minimal real git repo with one commit and one file."""
    repo = tmp_path / "testrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "hello.txt").write_text("hello world\n")
    subprocess.run(["git", "add", "hello.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.mark.asyncio
async def test_get_log_returns_commits(real_git_repo, tmp_path):
    rm = RepoManager(tmp_path)
    result = await rm.get_log(real_git_repo, "main", "", 5)
    assert "initial commit" in result


@pytest.mark.asyncio
async def test_get_log_with_subdir(real_git_repo, tmp_path):
    rm = RepoManager(tmp_path)
    result = await rm.get_log(real_git_repo, "main", ".", 5)
    assert "initial commit" in result


@pytest.mark.asyncio
async def test_get_commit_returns_diff(real_git_repo, tmp_path):
    rm = RepoManager(tmp_path)
    log = await rm.get_log(real_git_repo, "main", "", 1)
    commit_hash = log.split()[0]
    result = await rm.get_commit(real_git_repo, commit_hash)
    assert "hello.txt" in result


@pytest.mark.asyncio
async def test_get_commit_rejects_invalid_hash(real_git_repo, tmp_path):
    rm = RepoManager(tmp_path)
    with pytest.raises(RepoError, match="invalid commit hash"):
        await rm.get_commit(real_git_repo, "--upload-pack=evil")


@pytest.mark.asyncio
async def test_get_log_rejects_invalid_branch(real_git_repo, tmp_path):
    rm = RepoManager(tmp_path)
    with pytest.raises(RepoError, match="invalid branch name"):
        await rm.get_log(real_git_repo, "-bad-branch", "", 5)


@pytest.mark.asyncio
async def test_read_file_returns_content(real_git_repo, tmp_path):
    rm = RepoManager(tmp_path)
    result = await rm.read_file(real_git_repo, "main", "", "hello.txt")
    assert "hello world" in result
