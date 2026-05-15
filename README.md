# lp-triage

Triages active Launchpad bugs for ceph-charms using AI classification.

## Setup

```bash
uv sync
```

Add your OpenRouter key to `~/.config/lp-triage/config.toml`:

```toml
[auth]
openrouter_api_key = "sk-or-..."
```

## Run

```bash
# Classify bugs, print results as human-readable text
uv run lp-triage run --human

# Limit to specific projects or a handful of bugs
uv run lp-triage run --projects charm-ceph-mon charm-ceph-osd --limit 5 --human

# Dry-run with comment posting enabled (generates comments, doesn't post)
uv run lp-triage run --post-comment --dry-run --human

# Actually post comments (capped at 20 by default)
uv run lp-triage run --post-comment --human
```

Output is NDJSON by default; `--human` makes it readable. Each run writes a full log and summary to `~/lp-triage-reports/`.

## Web UI

```bash
uv run lp-triage serve --open
```

Opens at `http://localhost:8080`. Supports auto mode (runs and posts) and review mode (approve/edit/skip each classification before posting).

## Tests

```bash
uv run pytest
```

Requires Chromium for Playwright tests — install once with:

```bash
uv run playwright install-deps chromium
```

## Key options

| Flag | Default | Description |
|------|---------|-------------|
| `--projects` | all | LP project names to triage |
| `--limit N` | all | Max bugs per project |
| `--provider` | openrouter | `openrouter` or `gemini` |
| `--model` | from config | Override model per run |
| `--concurrency N` | 4 | Parallel bugs within a project |
| `--refresh` | off | Bust the LP bug list cache |
| `--post-comment` | off | Post AI comments to LP |
| `--dry-run` | off | Generate comments without posting |
| `--max-posts N` | 20 | Cap on comments posted per run |
| `--human` | off | Human-readable output instead of NDJSON |
