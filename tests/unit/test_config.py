import os
from pathlib import Path

import pytest

from lp_triage.engine.config import (
    _atomic_write,
    _deep_merge,
    _load_toml,
    get_projects,
    load_config,
    repo_dir_name,
)


def test_deep_merge_basic():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"d": 99, "e": 5}}
    result = _deep_merge(base, override)
    assert result["a"] == 1
    assert result["b"]["c"] == 2
    assert result["b"]["d"] == 99
    assert result["b"]["e"] == 5


def test_deep_merge_does_not_mutate_base():
    base = {"a": {"x": 1}}
    _deep_merge(base, {"a": {"y": 2}})
    assert "y" not in base["a"]


def test_load_toml_missing_file(tmp_path):
    result = _load_toml(tmp_path / "nonexistent.toml")
    assert result == {}


def test_load_toml_valid(tmp_path):
    p = tmp_path / "test.toml"
    p.write_text('[section]\nkey = "value"\n')
    result = _load_toml(p)
    assert result["section"]["key"] == "value"


def test_atomic_write(tmp_path):
    path = tmp_path / "out.toml"
    _atomic_write(path, {"key": "val"})
    assert path.exists()
    assert not (tmp_path / "out.tmp").exists()
    result = _load_toml(path)
    assert result["key"] == "val"


def test_env_var_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    cfg = load_config(project_config_path=tmp_path / "nonexistent.toml")
    assert cfg["auth"]["openrouter_api_key"] == "test-key-123"
    assert cfg["auth"]["gemini_api_key"] == "gemini-key"


def test_get_projects(tmp_path):
    toml = tmp_path / "lp-triage.toml"
    toml.write_text(
        '[[projects]]\nlp_project="charm-ceph-mon"\nurl="https://github.com/canonical/ceph-charms"\nbranch="main"\nsubdir="ceph-mon"\n'
    )
    cfg = load_config(project_config_path=toml)
    projects = get_projects(cfg)
    assert len(projects) == 1
    assert projects[0].lp_project == "charm-ceph-mon"
    assert projects[0].url == "https://github.com/canonical/ceph-charms"
    assert projects[0].branch == "main"


def test_repo_dir_name():
    assert repo_dir_name("https://github.com/canonical/ceph-charms") == "ceph-charms"
    assert repo_dir_name("https://github.com/canonical/ceph-charms.git") == "ceph-charms"
    assert repo_dir_name("https://github.com/canonical/ceph-charms/") == "ceph-charms"


def test_defaults_are_present():
    cfg = load_config(project_config_path=Path("/tmp/nonexistent-lp-triage.toml"))
    assert "defaults" in cfg
    assert "concurrency" in cfg["defaults"]
    assert cfg["defaults"]["concurrency"] == 4
