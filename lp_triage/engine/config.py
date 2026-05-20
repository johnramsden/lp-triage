"""Single config file: ~/.config/lp-triage/config.toml.

Contains both personal settings (API keys, provider, model) and project
definitions ([[projects]] entries). Precedence:
  ~/.config/lp-triage/config.toml > built-in defaults

OPENROUTER_API_KEY and GEMINI_API_KEY env vars override the merged result.
CLI flags and web-UI options are applied by the caller, not here.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

_USER_CONFIG = Path.home() / ".config" / "lp-triage" / "config.toml"

_DEFAULTS: dict[str, Any] = {
    "auth": {
        "openrouter_api_key": "",
        "gemini_api_key": "",
        "lp_credentials_file": str(Path.home() / ".config" / "lp-triage" / "lp-credentials"),
    },
    "defaults": {
        "provider": "openrouter",
        "cache_dir": str(Path.home() / ".cache" / "lp-triage"),
        "bug_list_ttl": 3600,
        "concurrency": 4,
        "max_turns": 30,
        "lp_instance": "production",
    },
    "openrouter": {
        "model": "openrouter/auto",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "gemini": {
        "model": "gemini-2.0-flash",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(data, f)
    tmp.replace(path)


def load_config(config_path: Path | None = None) -> dict:
    cfg = _deep_merge({}, _DEFAULTS)
    user_data = _load_toml(config_path or _USER_CONFIG)
    cfg = _deep_merge(cfg, user_data)

    if key := os.environ.get("OPENROUTER_API_KEY"):
        cfg.setdefault("auth", {})["openrouter_api_key"] = key
    if key := os.environ.get("GEMINI_API_KEY"):
        cfg.setdefault("auth", {})["gemini_api_key"] = key

    return cfg


def load_user_config() -> dict:
    return _load_toml(_USER_CONFIG)


def save_user_config(data: dict) -> None:
    _atomic_write(_USER_CONFIG, data)


@dataclass
class ProjectCfg:
    lp_project: str
    url: str
    branch: str
    subdir: str


def repo_dir_name(url: str) -> str:
    """Derive a stable local clone directory name from a git URL."""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def get_projects(cfg: dict) -> list[ProjectCfg]:
    return [
        ProjectCfg(
            lp_project=p["lp_project"],
            url=p["url"],
            branch=p["branch"],
            subdir=p.get("subdir", ""),
        )
        for p in cfg.get("projects", [])
    ]
