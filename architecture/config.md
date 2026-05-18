# Configuration System

**File:** `lp_triage/engine/config.py`

## Single config file

All configuration lives in `~/.config/lp-triage/config.toml`. This includes
both personal settings (API keys, provider, model) and the `[[projects]]` list.

Precedence: `~/.config/lp-triage/config.toml` → built-in defaults.

Two env vars are recognised as an out-of-band override applied on top of the
merged config: `OPENROUTER_API_KEY` and `GEMINI_API_KEY`. No other env vars
are read. CLI flags and web-UI runtime options (provider, limit, max-turns,
etc.) are applied downstream by the caller — they are not part of `load_config`.

## `defaults` keys

| Key | Default | Description |
|-----|---------|-------------|
| `provider` | `"openrouter"` | AI provider (`openrouter` or `gemini`) |
| `concurrency` | `4` | Parallel bugs per project |
| `max_turns` | `10` | Agent loop iterations per bug |
| `bug_list_ttl` | `3600` | LP bug list cache TTL in seconds |
| `cache_dir` | `~/.cache/lp-triage` | Cache and launchpadlib data directory |
| `lp_instance` | `"production"` | Launchpad instance (`production`, `qastaging`, `staging`) |

`lp_instance` is passed verbatim to launchpadlib, which maps it to the correct service and web roots. Setting it to `"qastaging"` routes all LP API calls and OAuth flows to `https://qastaging.launchpad.net/`.

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
