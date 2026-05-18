# Launchpad OAuth Flow

**File:** `lp_triage/engine/lp_auth.py`

## Background

Launchpad uses OAuth 1.0a. Unregistered consumer keys (like `lp-triage`) are
treated as desktop applications, so LP always uses the OOB (out-of-band)
desktop flow regardless of whether `oauth_callback` is sent. LP will never
redirect back to a callback URL for unregistered consumers.

## OOB flow

```
1. GET /auth/lp
      → server posts to LP +request-token with oauth_callback=oob
      → returns {auth_url, token_key} to browser

2. browser opens auth_url in new tab
      → user authorises "lp-triage" on Launchpad
      → LP shows "go back to the application" (no redirect)

3. user returns to app, clicks "Complete authorization"
      → browser posts {token_key} to POST /auth/lp/complete
      → server exchanges request token for access token (no verifier needed)
      → credentials saved to disk
```

## `get_request_token(lp_instance="production")`

Creates a `launchpadlib.credentials.Credentials` object and calls its
`get_request_token(web_root=...)` method. Returns
`(auth_url, token_key, creds)`. The `Credentials` object retains the request
token internally; the server stores it in `_pending_oauth[token_key]` until
the user completes the flow.

## `exchange_token(cfg, creds, lp_instance="production")`

Calls `creds.exchange_request_token_for_access_token(web_root=...)` then
`creds.save_to_path(creds_file)`. LP accepts the exchange without a verifier
because the request token was blessed by the user on LP's website.

## Credential storage

`Credentials.save_to_path()` writes the standard launchpadlib INI format to
`~/.config/lp-triage/lp-credentials`:

```ini
[1]
consumer_key = lp-triage
consumer_secret =
access_token = <token>
access_secret = <secret>
```

`Credentials.load_from_path()` reads this format directly, so `LPFetcher`
can authenticate without re-implementing the credential loading logic.
