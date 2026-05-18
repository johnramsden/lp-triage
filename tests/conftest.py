import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def reset_server_state():
    """Clear RunStore and pending OAuth state between every test."""
    from lp_triage.web import server as srv
    srv._store._runs.clear()
    srv._pending_oauth.clear()
    yield
    srv._store._runs.clear()
    srv._pending_oauth.clear()


@pytest.fixture
def minimal_cfg(tmp_path):
    return {
        "auth": {"openrouter_api_key": "test", "gemini_api_key": "", "lp_credentials_file": ""},
        "defaults": {
            "provider": "openrouter",
            "cache_dir": str(tmp_path / "cache"),
            "bug_list_ttl": 3600,
            "concurrency": 1,
        },
        "openrouter": {"model": "openrouter/auto", "base_url": "https://openrouter.ai/api/v1"},
        "gemini": {"model": "gemini-2.0-flash"},
        "projects": [{"lp_project": "charm-ceph-mon", "url": "https://github.com/canonical/ceph-charms", "branch": "main", "subdir": "ceph-mon"}],
    }
