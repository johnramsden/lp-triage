from __future__ import annotations

import asyncio
import logging
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)


class RepoError(Exception):
    pass


class PathScopeError(RepoError):
    pass


class RepoManager:
    def __init__(self, cache_dir: Path):
        self.repos_dir = cache_dir / "repos"
        self.repos_dir.mkdir(parents=True, exist_ok=True)

    def repo_path(self, name: str) -> Path:
        return self.repos_dir / name

    async def ensure_cloned(self, name: str, url: str) -> Path:
        dest = self.repo_path(name)
        if not dest.exists():
            await self._clone(url, dest)
        return dest

    async def fetch_all(self, names: list[str]) -> None:
        tasks = [self._fetch(self.repo_path(n)) for n in names if self.repo_path(n).exists()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def get_log(self, repo_dir: Path, branch: str, subdir: str, n: int) -> str:
        cmd = ["log", f"-{n}", "--oneline", branch]
        if subdir:
            cmd += ["--", subdir]
        return await self._git(repo_dir, cmd)

    async def get_commit(self, repo_dir: Path, commit_hash: str) -> str:
        return await self._git(repo_dir, ["show", "--stat", commit_hash])

    async def read_file(self, repo_dir: Path, branch: str, subdir: str, path: str) -> str:
        safe = self._scoped_path(subdir, path)
        return await self._git(repo_dir, ["show", f"{branch}:{safe}"])

    def _scoped_path(self, subdir: str, path: str) -> str:
        clean = PurePosixPath(path)
        if clean.is_absolute():
            raise PathScopeError(f"absolute path not allowed: {path}")
        combined = PurePosixPath(subdir) / clean
        # Reject any path that contains '..' components — they can escape the subdir.
        if ".." in combined.parts:
            raise PathScopeError(f"path escapes subdir '{subdir}': {path}")
        return str(combined)

    async def _clone(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._run(
                ["git", "clone", "--filter=blob:none", "--no-checkout", url, str(dest)]
            )
        except RepoError:
            logger.warning("blobless clone failed for %s, falling back to full clone", url)
            await self._run(["git", "clone", url, str(dest)])

    async def _fetch(self, repo_dir: Path) -> None:
        try:
            await self._git(repo_dir, ["fetch", "--all", "--quiet"])
        except RepoError as e:
            logger.warning("fetch failed for %s: %s", repo_dir, e)

    async def _git(self, repo_dir: Path, args: list[str]) -> str:
        return await self._run(["git", "-C", str(repo_dir)] + args)

    async def _run(self, cmd: list[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RepoError(f"{' '.join(cmd[:3])} failed: {stderr.decode().strip()}")
        return stdout.decode()
