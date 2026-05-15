# Agent instructions for lp-triage

## Commits

Use `Assisted-by` (not `Co-Authored-By`) in every commit message:

```
Assisted-by: Claude Sonnet 4.6 <noreply@anthropic.com>
```

## Documentation — keep it current

After any non-trivial change, update the relevant docs:

- **`README.md`** — if CLI flags, config keys, setup steps, or behaviour change
- **`architecture/`** — if a module's design, data flow, or API surface changes

The architecture docs are the authoritative description of how the code works.
They are not optional. A change that isn't reflected in the architecture docs
is incomplete.

## Tests

Run the full suite before considering any task done:

```bash
uv run pytest
```

Subset runs for faster iteration:

```bash
uv run pytest tests/unit          # pure unit tests, no I/O
uv run pytest tests/integration   # FastAPI server tests
uv run pytest tests/ui            # Playwright browser tests (requires Chromium)
```

Tests are the verification step, not a formality. If tests fail, the task is
not done. Fix the failure before moving on — do not skip or comment out tests
to make them pass.

Playwright requires Chromium. Install once with:

```bash
uv run playwright install chromium
```

## Code style

- Python 3.12, formatted with the project's existing conventions
- No comments unless the *why* is non-obvious
- No speculative abstractions — implement only what the current task requires
- Prefer editing existing files over creating new ones
