# lp-triage

AI-powered Launchpad bug triage for Ceph charms. Fetches active bugs from
Launchpad, runs an agentic classification loop against the charm source
repository, and optionally posts the result as a comment on the bug.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- An [OpenRouter](https://openrouter.ai) API key (or a Gemini API key)

## Installation

```bash
uv sync
```

Install Chromium once for Playwright UI tests:

```bash
uv run playwright install chromium
```

## Configuration

All configuration lives in a single file: `~/.config/lp-triage/config.toml`

This includes both personal settings (API keys, provider, model) and the list
of Launchpad projects to triage.

Minimal config:

```toml
# ~/.config/lp-triage/config.toml
[auth]
openrouter_api_key = "sk-or-..."

[[projects]]
lp_project = "charm-ceph-mon"
url        = "https://github.com/canonical/ceph-charms"
branch     = "main"
subdir     = "ceph-mon"      # optional — omit to scope to the whole repo
```

Or set API keys via environment variables:

```bash
export OPENROUTER_API_KEY="sk-or-..."
export GEMINI_API_KEY="..."
```

Multiple projects can share the same repository URL; the repo is cloned once.

### Full config defaults

```toml
[defaults]
provider      = "openrouter"   # or "gemini"
concurrency   = 4              # parallel bugs per project
max_turns     = 10             # agent loop iterations per bug
bug_list_ttl  = 3600           # LP bug list cache TTL in seconds
cache_dir     = "~/.cache/lp-triage"
lp_instance   = "production"   # LP instance: production, qastaging, staging

[auth]
lp_credentials_file = "~/.config/lp-triage/lp-credentials"  # set by OAuth flow

[openrouter]
model    = "openrouter/auto"
base_url = "https://openrouter.ai/api/v1"

[gemini]
model = "gemini-2.0-flash"
```

## CLI usage

```bash
# Classify bugs, human-readable output
uv run lp-triage run --human

# Specific projects, cap at 5 bugs each
uv run lp-triage run --projects charm-ceph-mon charm-ceph-osd --limit 5 --human

# Generate comments without posting (dry run)
uv run lp-triage run --post-comment --dry-run --human

# Post comments for real (capped at 20 by default)
uv run lp-triage run --post-comment --human

# Use Gemini instead of OpenRouter
uv run lp-triage run --provider gemini --human

# Show merged config (secrets masked)
uv run lp-triage config
```

Output is NDJSON by default; `--human` prints readable lines. Each run writes
a timestamped NDJSON log (`run-<ts>.ndjson`) and a plain-text summary
(`run-<ts>-summary.txt`) to the current working directory.

### All CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--projects` | all | LP project names to triage |
| `--limit N` | all | Max bugs per project |
| `--provider` | from config | `openrouter` or `gemini` |
| `--model` | from config | Override model for this run |
| `--concurrency N` | 4 | Parallel bugs within a project |
| `--max-turns N` | 10 | Agent loop iterations per bug |
| `--refresh` | off | Bypass LP bug list cache |
| `--post-comment` | off | Post AI comment to each LP bug |
| `--dry-run` | off | Run full flow but skip LP writes |
| `--max-posts N` | 20 | Cap on comments posted per run |
| `--human` | off | Human-readable output |
| `--debug` | off | Include tool-call events in output |

## Web UI

```bash
uv run lp-triage serve          # http://localhost:8080
uv run lp-triage serve --open   # opens browser automatically
uv run lp-triage serve --port 9090
```

### Modes

**Auto** — runs the full triage and posts comments automatically when evidence
is found (subject to dry-run and max-posts limits).

**Review** — each classification is queued for human approval. Edit the draft
comment in-place if needed, then click **Approve & post** or **Skip**.

**Allow reposting** — by default bugs that already have an lp-triage comment
are skipped. Enable this checkbox to include them; they appear in the review
queue with a warning so you can decide whether a second post makes sense.
This option is web-UI-only; the CLI never reposts.

### Connecting Launchpad (for posting comments)

1. Open **Configuration → Connect Launchpad**.
2. Authorize `lp-triage` in the new tab that opens.
3. Return to the app and click **Complete authorization**.

Credentials are saved to `~/.config/lp-triage/lp-credentials` in launchpadlib
format and reused on subsequent runs.

## Testing

```bash
uv run pytest                        # all tests
uv run pytest tests/unit             # unit tests only
uv run pytest tests/integration      # integration tests (runs a real FastAPI server)
uv run pytest tests/ui               # Playwright browser tests (requires Chromium)
```

Tests must pass before any change is considered complete. See
[`architecture/`](architecture/) for the design rationale behind each module.
