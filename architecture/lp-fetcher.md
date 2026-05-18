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
| Bug list | `{lp_project}_buglist.json` | `bug_list_ttl` seconds (default 1 h) |
| Bug detail | `bug_{bug_id}_{date_last_updated}.json` | Permanent (content-addressed) |

The `date_last_updated` string in the detail cache filename has `:` replaced
with `-` and `+` replaced with `p` so it is safe as a filesystem path.

The detail cache is keyed on `(bug_id, date_last_updated)` so stale entries
are naturally invalidated when LP reports a newer modification timestamp.
`--refresh` bypasses both the bug list cache and the bug detail cache.

## Constructor parameters

`LPFetcher(cache_dir, bug_list_ttl=3600, refresh=False, lp_credentials_file=None, lp_instance="production")`

`lp_instance` is passed to every launchpadlib call (`login_anonymously`,
`login_with`) and used to derive the bug URL base for dry runs. Valid values
are any name launchpadlib recognises: `production`, `qastaging`, `staging`.

## Comment posting

`post_comment(bug_id, body, dry_run=False)`:

- `dry_run=True` — returns the bug URL (derived from `lp_instance`) without making any LP API call.
- `dry_run=False` — uses launchpadlib (`Credentials.load_from_path`) to post
  the comment and returns the message URL.

`has_existing_ai_comment(bug_id)` fetches live messages anonymously and checks
for the disclaimer prefix, skipping the cache. This prevents duplicate
comments when a run is retried.

## Repost behaviour

By default, bugs that already have an lp-triage comment are skipped before
classification to avoid wasting AI tokens. Pass `allow_repost=True` to
`run_triage` (or enable "Allow reposting" in the web UI) to classify them
anyway — they are marked with `already_posted: true` in the result so the
reviewer sees a warning before approving a second post.

This flag is only available through the web UI; the CLI never reposts.

## Comment format

`build_comment_body(result, bug_id)` prefixes every comment with:

```
[lp-triage AI report — informational only; a human must decide final actions]
```

Followed by category, summary, evidence, and recommended action.
`has_existing_ai_comment` searches for this prefix to detect prior posts.
