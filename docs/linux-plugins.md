# Linux plugins — battery and quota surface readers

Pure-stdlib Python implementation of OpenPets surface plugins for the Linux
GTK3 port. Owned by the plugins teammate per
[linux-team-protocol.md](../linux-team-protocol.md).

## Owned files

- `Linux/openpets_linux/plugins.py` — implementation
- `Linux/tests/test_plugins.py` — unit tests (fixtures only, no network)
- `docs/linux-plugins.md` — this document

## Public API

All functions are pure standard library: no GI, no third-party packages, no
shell, no credential writes, no token refresh, no personal-data scanning.
Credentials are read only on an explicit provider call, never at import or
startup. Provider network is opt-in.

```python
read_battery(sysfs_root=None) -> dict
read_claude_usage(credentials_path=None, fetcher=None) -> dict
read_codex_usage(auth_path=None, fetcher=None) -> dict
```

Each returns a normalized snapshot::

    {"id": str, "label": str, "value": str, "details": list[str], "state": str}

Quota snapshots additionally include ``windows``: a list of normalized window
snapshots when the provider reports multiple windows. Each window snapshot has
the same shape (``id``, ``label``, ``value``, ``details``, ``state``).

States: ``normal``, ``warning``, ``critical``, ``success``, ``setup``,
``failed``.

### read_battery

Reads Linux sysfs battery state from `sysfs_root` (default
`/sys/class/power_supply`). For each `Battery` entry reads `capacity` and
`status`; for each `Mains` entry reads `online` (AC). Multiple batteries are
aggregated by averaging capacity and OR-ing charging/discharging status.

- Charging → state `success`
- ≤10% not charging → state `critical`
- ≤20% not charging → state `warning`
- Otherwise → state `normal`

No battery found → state `failed` with an actionable detail; never crashes.

### read_claude_usage

Reads Claude Code credentials from `credentials_path` (default
`~/.claude/.credentials.json`), format::

    {"claudeAiOauth": {"accessToken": "...", "expiresAt": <ms>}}

Only the access token is read. Expiry is intentionally not enforced — the
plugin never refreshes credentials.

Calls `https://api.anthropic.com/api/oauth/usage` with
`Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, and a
fixed User-Agent. Parses `five_hour` and `seven_day` windows
(`utilization` percent + `resets_at` ISO8601). Windows whose reset is in the
past are skipped.

- Missing credentials → state `setup` with hint `Run: claude auth login`
- HTTP 401 → state `setup` (token rejected)
- Other non-2xx / network / parse errors → state `failed`, sanitized message

### read_codex_usage

Reads Codex credentials from `auth_path` (default `~/.codex/auth.json`),
format::

    {"tokens": {"access_token": "..."}}

Calls `https://chatgpt.com/backend-api/wham/usage` with
`Authorization: Bearer <token>`. Parses `primary`, `secondary`, `additional`
buckets from `limits` (or `rate_limit`), tolerating leading/trailing
non-JSON content (matches the Swift reader). Each bucket uses `used_percent`
plus `window_minutes`/`limit_window_seconds` and
`resets_in_seconds`/`reset_after_seconds`/`reset_at`.

Value reports remaining percentage (100 − used).

- Missing credentials → state `setup` with hint `Sign in to Codex`
- HTTP 401 → state `setup` (token rejected)
- Other non-2xx / network / parse errors → state `failed`, sanitized message

### Fetcher signature

Network access is injected via an optional `fetcher`:

```python
fetcher(url: str, headers: dict[str, str], timeout: float) -> dict
```

Returns `{"status": int, "body": str}`. Raises on transport/parse errors; the
message contains no token, header, or body content. Tests supply a fake
fetcher; no real auth files or network are used.

The default fetcher (`default_fetcher`) uses only `urllib` and enforces:

- HTTPS only
- Timeout ≤ 5 seconds
- Response body ≤ 1 MiB
- All redirects are refused, including HTTPS same-host redirects to a different port
- No token, header, or body text in error messages

## Endpoint provenance

The Claude and Codex usage endpoints are **best-effort**: they are the
endpoints used by the official clients (Claude Code, Codex Desktop) and
observed in the Swift surface plugins. They are not part of a published,
versioned provider API contract and may change without notice. The plugin
treats any provider response shape it cannot parse as a `failed` state rather
than crashing.

## Security and privacy

- Credentials are read from disk only when the corresponding function is
  called; nothing is read at import time.
- No token is written, refreshed, or logged.
- Enabled quota providers are polled every 60 seconds. Use **Refresh enabled
  plugins now** in the Plugins menu to request an immediate read; this never
  enables a disabled provider. If a read is already in progress, one manual
  refresh is queued after it completes.
- Error messages are sanitized: no token, header, or body content is ever
  surfaced.
- The default fetcher never follows redirects or forwards bearer tokens to another request.
- No shell commands are executed. No personal data is scanned.

## Tests

Run from the repository root:

```sh
PYTHONPATH=Linux /usr/bin/python3 -m unittest Linux.tests.test_plugins -v
```

All tests use temp-file fixtures and fake fetchers. No real `~/.claude`,
`~/.codex`, or network access is used.

## Integration notes

These functions are called only after explicit user opt-in (parent owns the
menu; plugins default to OFF). The parent adds background refresh and menu
wiring. No GTK/UI/mainCLI edits are made here.

## Compatibility with Swift providers

Behavior mirrors the Swift surface plugins:

| Concern | Swift | Linux (`plugins.py`) |
|---|---|---|
| Battery sysfs | `IOPS*` via IOKit | `/sys/class/power_supply` |
| Claude creds | `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`) | same |
| Claude env token | `CLAUDE_CODE_OAUTH_TOKEN` | not read (file only); hint mentions it |
| Claude usage URL | `https://api.anthropic.com/api/oauth/usage` | same |
| Claude headers | `Authorization`, `anthropic-beta`, `User-Agent`, `Accept` | same |
| Codex creds | `~/.codex/auth.json` (`tokens.access_token`) | same |
| Codex usage URL | `https://chatgpt.com/backend-api/wham/usage` | same |
| Codex headers | `Authorization`, `Accept`, `Accept-Language`, `originator`, `User-Agent`, `chatgpt-account-id` | same minus `chatgpt-account-id` (JWT account-id decode not ported; best-effort) |
| Claude windows | `five_hour` (5h), `seven_day` (7d) | same |
| Codex buckets | `primary`/`secondary`/`additional` | same |
| Tone/state mapping | critical ≥90, warning ≥70 | same |
| Battery tone | charging → success, ≤10 → critical, ≤20 → warning | same |
