# Configuration System

**File:** `lp_triage/engine/config.py`

## Two-file split

| File | Owner | Contents |
|------|-------|---------|
| `~/.config/lp-triage/config.toml` | User | API keys, provider, model, personal defaults |
| `./lp-triage.toml` | Project | `[[projects]]` entries, project-level overrides |

Merge order (highest wins): env vars → `lp-triage.toml` → `~/.config/…/config.toml` → built-in defaults.

Env vars recognised: `OPENROUTER_API_KEY`, `GEMINI_API_KEY`.

## Project schema

```toml
[[projects]]
lp_project = "charm-ceph-mon"   # Launchpad project name
url        = "https://github.com/canonical/ceph-charms"  # git clone URL
branch     = "main"             # branch the agent reads code from
subdir     = "ceph-mon"         # optional — scopes file access to this path
```

`subdir` may be omitted or left blank; the agent then has access to the whole
repository. Multiple projects may share the same `url` — the repo is cloned
once and the directory name is derived from the last URL path segment.

## `ProjectCfg` dataclass

```python
@dataclass
class ProjectCfg:
    lp_project: str
    url: str
    branch: str
    subdir: str
```

`repo_dir_name(url)` derives the local clone directory name from the URL
(strips `.git` suffix if present).

## Atomic writes

`save_user_config` and `save_project_config` write to a `.tmp` file then
`rename()` it into place, so a crash mid-write never leaves a corrupt file.

## Web UI persistence

`PUT /config` in the web server calls `save_user_config` and
`save_project_config` directly. The project payload is `{ projects: [...] }`;
only the `projects` key is written.
