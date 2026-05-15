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

## `get_request_token(cfg)`

POSTs to `https://launchpad.net/+request-token` via httplib2 with:

```
oauth_consumer_key   = lp-triage
oauth_signature_method = PLAINTEXT
oauth_signature      = &
oauth_callback       = oob
```

Returns `(auth_url, token_key, token_secret)`. `token_secret` is stored in
`_pending_oauth[token_key]` in the server's memory until the user completes
the flow.

## `exchange_token(cfg, oauth_token, oauth_token_secret, oauth_verifier)`

POSTs to `https://launchpad.net/+access-token`. For the OOB flow,
`oauth_verifier` is passed as `""` and is omitted from the request. LP accepts
this because the request token was blessed by the user on LP's website.

## Credential storage

Access token and secret are written to
`~/.config/lp-triage/lp-credentials` in launchpadlib INI format:

```ini
[1]
consumer_key = lp-triage
consumer_secret =
access_token = <token>
access_secret = <secret>
```

`launchpadlib.credentials.Credentials.load_from_path()` reads this format
directly, so `LPFetcher` can authenticate without re-implementing the
credential loading logic.

## Why httplib2, not launchpadlib

launchpadlib's built-in `get_request_token()` does not send `oauth_callback`
at all, making it impossible to use the web flow even for registered
consumers. We call the LP REST endpoint directly with httplib2 to have full
control over the request parameters.
