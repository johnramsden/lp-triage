# Launchpad Data Layer

**File:** `lp_triage/engine/lp_fetcher.py`

## Responsibilities

- Fetch the list of active bugs for a project (anonymous LP REST API)
- Fetch full bug detail including messages
- Check whether an AI comment has already been posted (idempotency guard)
- Post a comment to a bug (authenticated, via launchpadlib)

## Two-level cache

| Level | Key | TTL |
|-------|-----|-----|
| Bug list | `{project}-bugs.json` | `bug_list_ttl` seconds (default 1 h) |
| Bug detail | `{bug_id}-{date_last_updated}.json` | Permanent (content-addressed) |

The detail cache is keyed on `(bug_id, date_last_updated)` so stale entries
are naturally invalidated when LP reports a newer modification timestamp.
`--refresh` bypasses the bug list cache but not the detail cache.

## Comment posting

`post_comment(bug_id, body, dry_run=False)`:

- `dry_run=True` — returns the bug URL without making any LP API call.
- `dry_run=False` — uses launchpadlib (`Credentials.load_from_path`) to post
  the comment and returns the message URL.

`has_existing_ai_comment(bug_id, lp_login)` fetches live messages and checks
for the disclaimer prefix, skipping the cache. This prevents duplicate
comments when a run is retried.

## Comment format

`build_comment_body(result, bug_id)` prefixes every comment with:

```
[lp-triage AI report — informational only; a human must decide final actions]
```

Followed by category, summary, evidence, and recommended action.
`has_existing_ai_comment` searches for this prefix to detect prior posts.
