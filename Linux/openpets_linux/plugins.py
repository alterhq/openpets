"""Pure-stdlib OpenPets battery and quota surface plugins for the Linux GTK3 port.

Wire behavior mirrors the Swift surface plugins
(OpenPetsBatterySurfacePlugin, OpenPetsClaudeCodeSurfacePlugin,
OpenPetsCodexUsageSurfacePlugin) but is an independent, standard-library-only
implementation: no GI, no third-party packages, no shell, no credential writes,
no token refresh, no personal-data scanning. Credentials are read only on an
explicit provider call, never at import or startup. Provider network is opt-in.

Public API:

    read_battery(sysfs_root=None) -> dict
    read_claude_usage(credentials_path=None, fetcher=None) -> dict
    read_codex_usage(auth_path=None, fetcher=None) -> dict

Each returns a normalized snapshot::

    {"id": str, "label": str, "value": str, "details": list[str], "state": str}

Quota snapshots may additionally include ``windows``: a list of normalized
window snapshots when the provider reports multiple windows.

Fetcher call signature (documented; tests supply a fake)::

    fetcher(url: str, headers: dict[str, str], timeout: float) -> dict

Returns {"status": int, "body": str}. Raises on transport/parse errors with a
message that contains no tokens, headers, or body text.
"""

from __future__ import annotations

import json
import base64
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib import request as _urllib_request
from urllib.error import HTTPError as _HTTPError
from urllib.error import URLError as _URLError

__all__ = [
    "read_battery",
    "read_claude_usage",
    "read_codex_usage",
    # Test hooks / constants
    "BATTERY_SYSFS_ROOT",
    "CLAUDE_CREDENTIALS_PATH",
    "CLAUDE_USAGE_URL",
    "CODEX_AUTH_PATH",
    "CODEX_USAGE_URL",
    # Error type
    "PluginError",
    # Snapshot helpers
    "snapshot",
    # Default fetcher (documented signature)
    "default_fetcher",
]

# --- constants -------------------------------------------------------------

BATTERY_SYSFS_ROOT = "/sys/class/power_supply"

CLAUDE_CREDENTIALS_PATH = os.path.expanduser("~/.claude/.credentials.json")
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

CODEX_AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

# Plugin IDs, mirroring the Swift pluginID values.
PLUGIN_ID_BATTERY = "openpets.plugin.battery"
PLUGIN_ID_CLAUDE = "openpets.plugin.claude-code"
PLUGIN_ID_CODEX = "openpets.plugin.codex-usage"

# Fetcher limits (hard caps enforced by the default fetcher).
FETCH_MAX_BODY = 1024 * 1024  # 1 MiB
FETCH_MAX_TIMEOUT = 5.0  # seconds

# Claude usage response window keys, matching the Swift reader.
CLAUDE_WINDOWS = (
    ("five_hour", "5h"),
    ("seven_day", "7d"),
)

# Codex usage bucket keys, matching the Swift reader.
CODEX_BUCKETS = (
    ("primary", "primary", "5h"),
    ("secondary", "secondary", "7d"),
    ("additional", "additional", "30d"),
)


# --- errors -----------------------------------------------------------------


class PluginError(Exception):
    """Raised for transport or parse failures; message is sanitized."""


# --- snapshot helpers --------------------------------------------------------


def snapshot(
    id: str,
    label: str,
    value: str,
    details: list[str] | None = None,
    state: str = "normal",
    windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a normalized plugin snapshot dict."""
    result: dict[str, Any] = {
        "id": id,
        "label": label,
        "value": value,
        "details": list(details or []),
        "state": state,
    }
    if windows is not None:
        result["windows"] = list(windows)
    return result


def _battery_state(percent: int | None, is_charging: bool) -> str:
    """Mirror Swift tone(): charging -> success, low -> critical/warning."""
    if is_charging:
        return "success"
    if percent is not None:
        if percent <= 10:
            return "critical"
        if percent <= 20:
            return "warning"
    return "normal"


def _quota_state(used: int | None) -> str:
    """Mirror Swift tone(): >=90 critical, >=70 warning, else normal."""
    if used is None:
        return "normal"
    if used >= 90:
        return "critical"
    if used >= 70:
        return "warning"
    return "normal"


def _missing_credentials_snapshot(
    plugin_id: str, label: str, credential_path: str, hint: str
) -> dict[str, Any]:
    """Actionable setup state when credentials are absent; never crashes."""
    return snapshot(
        plugin_id,
        label,
        "Not signed in",
        [
            f"Missing credentials at {credential_path}",
            hint,
        ],
        "setup",
    )


def _fetch_error_snapshot(plugin_id: str, label: str) -> dict[str, Any]:
    """Sanitized generic failure state; no token/header/body in message."""
    return snapshot(
        plugin_id,
        label,
        "Unavailable",
        ["Unable to reach usage service"],
        "failed",
    )


def _parse_error_snapshot(plugin_id: str, label: str) -> dict[str, Any]:
    """Sanitized generic parse failure state."""
    return snapshot(
        plugin_id,
        label,
        "Unavailable",
        ["Unable to parse usage response"],
        "failed",
    )


# --- small file readers -----------------------------------------------------


def _read_text_file(path: str) -> str | None:
    """Read one small text file from disk, returning None on any error.

    Never raises; intended for sysfs / credential files only.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(FETCH_MAX_BODY + 1)
            return text if len(text) <= FETCH_MAX_BODY else None
    except OSError:
        return None


def _read_int_file(path: str) -> int | None:
    """Read one integer from a sysfs-style file; None if missing or invalid."""
    text = _read_text_file(path)
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_first_line(path: str) -> str | None:
    text = _read_text_file(path)
    if text is None:
        return None
    return text.split("\n", 1)[0].strip()


# --- battery ----------------------------------------------------------------


def _is_power_supply_dir(root: str, name: str) -> bool:
    return os.path.isdir(os.path.join(root, name))


def _collect_battery_info(sysfs_root: str) -> tuple[list[dict[str, Any]], bool | None]:
    """Scan sysfs for battery and AC power supplies.

    Returns (batteries, ac_online). Each battery dict has name/capacity/status.
    ac_online is True/False when a Mains supply with online=1/0 is found,
    None when no AC info is available.
    """
    batteries: list[dict[str, Any]] = []
    ac_online: bool | None = None

    try:
        entries = sorted(os.listdir(sysfs_root))
    except OSError:
        return batteries, ac_online

    for name in entries:
        path = os.path.join(sysfs_root, name)
        if not _is_power_supply_dir(sysfs_root, name):
            continue
        ptype = _read_first_line(os.path.join(path, "type")) or ""
        ptype = ptype.strip()

        if ptype == "Battery":
            capacity = _read_int_file(os.path.join(path, "capacity"))
            status = _read_first_line(os.path.join(path, "status")) or "Unknown"
            batteries.append(
                {"name": name, "capacity": capacity, "status": status.strip()}
            )
        elif ptype == "Mains":
            online = _read_int_file(os.path.join(path, "online"))
            if online is not None:
                ac_online = online == 1

    return batteries, ac_online


def read_battery(sysfs_root: str | None = None) -> dict[str, Any]:
    """Read battery state from Linux sysfs.

    Reads ``capacity`` and ``status`` from each ``Battery`` entry under
    ``sysfs_root`` (default ``/sys/class/power_supply``), plus AC ``online``
    from any ``Mains`` entry. Multiple batteries are aggregated by averaging
    capacity and OR-ing charging/discharging status.

    Returns a normalized snapshot. When no battery is present the state is
    ``"failed"`` with an actionable detail; this never crashes.
    """
    root = sysfs_root or BATTERY_SYSFS_ROOT
    batteries, ac_online = _collect_battery_info(root)

    if not batteries:
        return snapshot(
            PLUGIN_ID_BATTERY,
            "Battery",
            "Unknown",
            [f"No battery found under {root}"],
            "failed",
        )

    capacities = [b["capacity"] for b in batteries if b["capacity"] is not None and 0 <= b["capacity"] <= 100]
    percent = sum(capacities) // len(capacities) if capacities else None

    statuses = [b["status"] for b in batteries]
    is_charging = any(s == "Charging" for s in statuses)
    is_full = any(s == "Full" for s in statuses)
    is_discharging = any(s == "Discharging" for s in statuses)

    state = _battery_state(percent, is_charging)

    if percent is not None:
        value = f"{percent}%"
    else:
        value = "Unknown"

    if is_charging:
        state_label = "Charging"
    elif is_full:
        state_label = "Full"
    elif is_discharging:
        state_label = "Discharging"
    else:
        state_label = statuses[0] if statuses else "Unknown"

    details = [f"Charge: {value}", f"State: {state_label}"]
    if ac_online is not None:
        details.append(f"AC: {'Online' if ac_online else 'Offline'}")

    return snapshot(PLUGIN_ID_BATTERY, "Battery", value, details, state)


# --- Claude (Anthropic) usage ----------------------------------------------


def _decode_json(text: str) -> dict[str, Any] | None:
    """Parse JSON object from text; None on failure. No secrets in errors."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _read_claude_credentials(credentials_path: str) -> str | None:
    """Read an access token from the Claude Code credentials file.

    Supported format (matches Swift ``OpenPetsClaudeCodeQuotaReader``)::

        {"claudeAiOauth": {"accessToken": "...", "expiresAt": <ms>}}

    Only the access token is returned. Expiry is intentionally not enforced:
    the plugin never refreshes credentials. Returns None when the file is
    missing, unreadable, or does not contain a usable token.
    """
    text = _read_text_file(credentials_path)
    if text is None:
        return None
    payload = _decode_json(text)
    if payload is None:
        return None
    oauth = payload.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    return token


def _parse_claude_usage(payload: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """Parse Claude usage payload into normalized window snapshots.

    Expected shape (matches Swift ``snapshot(fromOAuthUsageData:)``)::

        {
          "five_hour": {"utilization": <pct>, "resets_at": "<ISO8601>"},
          "seven_day": {"utilization": <pct>, "resets_at": "<ISO8601>"}
        }

    Windows whose reset is not in the future are skipped.
    """
    windows: list[dict[str, Any]] = []
    for key, label in CLAUDE_WINDOWS:
        window = payload.get(key)
        if not isinstance(window, dict):
            continue
        used = _to_int_percent(window.get("utilization"))
        if used is None:
            continue
        resets_at = _parse_iso8601(window.get("resets_at"))
        if resets_at is None or resets_at <= now:
            continue
        reset_seconds = max(0, resets_at - now)
        windows.append(
            snapshot(
                f"{PLUGIN_ID_CLAUDE}.{label.lower()}",
                f"Claude {label}",
                f"{used}%",
                [
                    f"Used: {used}%",
                    f"Reset: in {_format_seconds(reset_seconds)}",
                    f"Pace: {_pace_description(used, reset_seconds, _window_minutes(label))}",
                ],
                _quota_state(used),
            )
        )
    return windows


def _window_minutes(label: str) -> int:
    if label == "5h":
        return 5 * 60
    if label == "7d":
        return 7 * 24 * 60
    return 0


def _pace_description(used: int, reset_seconds: float, window_minutes: int) -> str:
    if window_minutes <= 0:
        return "Pace unknown"
    remaining_minutes = reset_seconds / 60.0
    if remaining_minutes > window_minutes:
        return "Pace unknown"
    elapsed_minutes = max(0, window_minutes - int(remaining_minutes))
    target = elapsed_minutes * 100 // window_minutes
    delta = used - target
    if delta > 0:
        return f"{delta}% over target"
    if delta < 0:
        return f"{abs(delta)}% under target"
    return "On track"


def _to_int_percent(value: Any) -> int | None:
    """Coerce a JSON number to an int percent clamped to 0..100."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            pct = int(value)
        elif isinstance(value, str):
            pct = int(float(value))
        else:
            return None
    except (ValueError, OverflowError):
        return None
    if 0 <= pct <= 100:
        return pct
    return None


def _parse_iso8601(value: Any) -> float | None:
    """Parse an ISO8601 timestamp to a Unix epoch float; None on failure."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Handle trailing 'Z'.
    candidate = text
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _build_claude_value(windows: list[dict[str, Any]]) -> str:
    parts = []
    for w in windows:
        label = w["label"]
        value = w["value"]
        parts.append(f"{label} {value}")
    if not parts:
        return "No quota data"
    return " / ".join(parts)


def read_claude_usage(
    credentials_path: str | None = None,
    fetcher: Callable[[str, dict[str, str], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read Claude Code usage from the Anthropic OAuth usage endpoint.

    Reads credentials only on this explicit call (never at import/startup) from
    ``credentials_path`` (default ``~/.claude/.credentials.json``). Supported
    format::

        {"claudeAiOauth": {"accessToken": "...", "expiresAt": <ms>}}

    Calls ``https://api.anthropic.com/api/oauth/usage`` with a bearer token and
    returns a normalized snapshot including a ``windows`` list (5h, 7d).

    Missing credentials yield an actionable ``"setup"`` state, never a crash.
    Network or parse failures yield a sanitized ``"failed"`` state with no
    token/header/body in the message. No token refresh or credential writes.
    """
    path = credentials_path or CLAUDE_CREDENTIALS_PATH

    token = _read_claude_credentials(path)
    if token is None:
        return _missing_credentials_snapshot(
            PLUGIN_ID_CLAUDE,
            "Claude Code",
            path,
            "Run claude auth login; environment-token overrides are not used by this plugin",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/1.0.0",
        "Accept": "application/json",
    }

    try:
        response = _fetch_with(fetcher, CLAUDE_USAGE_URL, headers, 5.0)
    except PluginError:
        return _fetch_error_snapshot(PLUGIN_ID_CLAUDE, "Claude Code")

    status = response.get("status")
    body = response.get("body", "")
    if not isinstance(status, int) or not (200 <= status < 300):
        if status == 401:
            return _missing_credentials_snapshot(
                PLUGIN_ID_CLAUDE,
                "Claude Code",
                path,
                "Run: claude auth login (token rejected by usage service)",
            )
        return _fetch_error_snapshot(PLUGIN_ID_CLAUDE, "Claude Code")

    payload = _decode_json(body)
    if payload is None:
        return _parse_error_snapshot(PLUGIN_ID_CLAUDE, "Claude Code")

    now = time.time()
    windows = _parse_claude_usage(payload, now)

    if not windows:
        return snapshot(
            PLUGIN_ID_CLAUDE,
            "Claude Code",
            "No quota data",
            ["Usage response contained no usable windows"],
            "failed",
        )

    return snapshot(
        PLUGIN_ID_CLAUDE,
        "Claude Code",
        _build_claude_value(windows),
        [f"Claude {w['label']}: {w['value']}" for w in windows],
        _worst_state([w["state"] for w in windows]),
        windows=windows,
    )


# --- Codex (OpenAI) usage ---------------------------------------------------


def _read_codex_credentials(auth_path: str) -> str | None:
    """Read an access token from the Codex auth file.

    Supported format (matches Swift ``OpenPetsCodexUsageReader``)::

        {"tokens": {"access_token": "..."}}

    Returns the access token string, or None when missing/unusable.
    """
    text = _read_text_file(auth_path)
    if text is None:
        return None
    payload = _decode_json(text)
    if payload is None:
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token:
        return None
    return token


def _extract_json_object(text: str) -> str | None:
    """Extract the first top-level JSON object substring from text.

    Mirrors the Swift reader's tolerance for leading/trailing non-JSON content.
    Returns None when no balanced object can be found.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaping = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escaping:
                escaping = False
            elif ch == "\\":
                escaping = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
    return None


def _parse_codex_usage(payload: dict[str, Any], now: float) -> list[dict[str, Any]]:
    """Parse Codex usage payload into normalized window snapshots.

    Expected shape (matches Swift ``snapshot(fromLiveUsageData:)``)::

        {"limits": {"primary": {...}, "secondary": {...}, ...}, "plan_type": "..."}

    Each bucket may use ``used_percent`` plus ``window_minutes`` /
    ``limit_window_seconds`` and ``resets_in_seconds`` / ``reset_after_seconds``
    / ``reset_at``.
    """
    rate_limits = payload.get("limits")
    if not isinstance(rate_limits, dict):
        rate_limits = payload.get("rate_limit")
    if not isinstance(rate_limits, dict):
        return []

    windows: list[dict[str, Any]] = []
    for key, kind, fallback_label in CODEX_BUCKETS:
        bucket = rate_limits.get(key) or rate_limits.get(key + "_window")
        if not isinstance(bucket, dict):
            continue
        window = _parse_codex_bucket(bucket, kind, fallback_label, now)
        if window is not None:
            windows.append(window)
    return windows


def _parse_codex_bucket(
    bucket: dict[str, Any], kind: str, fallback_label: str, now: float
) -> dict[str, Any] | None:
    used = _to_int_percent(bucket.get("used_percent"))
    if used is None:
        return None

    window_minutes = _to_number(bucket.get("window_minutes"))
    if window_minutes is None:
        raw = _to_number(bucket.get("limit_window_seconds"))
        if raw is not None:
            window_minutes = raw / 60.0

    reset_seconds = _to_number(bucket.get("resets_in_seconds"))
    if reset_seconds is None:
        reset_seconds = _to_number(bucket.get("reset_after_seconds"))
    if reset_seconds is None:
        reset_at = _to_number(bucket.get("reset_at"))
        if reset_at is not None:
            reset_seconds = max(0, reset_at - now)

    if reset_seconds is None:
        reset_label = "Unknown"
        reset_seconds_value = 0.0
    else:
        reset_seconds_value = max(0, reset_seconds)
        reset_label = f"in {_format_seconds(reset_seconds_value)}"

    label = fallback_label
    if window_minutes is not None:
        wm = int(round(window_minutes))
        if wm >= 24 * 60:
            label = f"{wm // (24 * 60)}d"
        elif wm >= 60:
            label = f"{wm // 60}h"
        else:
            label = f"{wm}m"

    remaining = max(0, 100 - used)
    detail_lines = [
        f"Remaining: {remaining}%",
        f"Reset: {reset_label}",
    ]
    pace = _codex_pace(used, reset_seconds_value, window_minutes)
    detail_lines.append(f"Pace: {pace}")

    return snapshot(
        f"{PLUGIN_ID_CODEX}.{kind}",
        f"Codex {label}",
        f"{remaining}%",
        detail_lines,
        _quota_state(used),
    )


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _codex_pace(used: int, reset_seconds: float, window_minutes: float | None) -> str:
    if window_minutes is None or window_minutes < 1:
        return "Pace unknown"
    remaining_minutes = reset_seconds / 60.0
    if remaining_minutes > window_minutes:
        return "Pace unknown"
    elapsed_minutes = max(0, int(window_minutes) - int(remaining_minutes))
    target = elapsed_minutes * 100 // int(window_minutes)
    delta = used - target
    if delta > 0:
        return f"{delta}% over target"
    if delta < 0:
        return f"{abs(delta)}% under target"
    return "On track"


def _build_codex_value(windows: list[dict[str, Any]]) -> str:
    parts = [f"{w['label']} {w['value']}" for w in windows]
    if not parts:
        return "No usage data"
    return " / ".join(parts)


def read_codex_usage(
    auth_path: str | None = None,
    fetcher: Callable[[str, dict[str, str], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read Codex usage from the OpenAI usage endpoint.

    Reads credentials only on this explicit call (never at import/startup) from
    ``auth_path`` (default ``~/.codex/auth.json``). Supported format::

        {"tokens": {"access_token": "..."}}

    Calls ``https://chatgpt.com/backend-api/wham/usage`` with a bearer token and
    returns a normalized snapshot including a ``windows`` list (primary,
    secondary, additional).

    Missing credentials yield an actionable ``"setup"`` state, never a crash.
    Network or parse failures yield a sanitized ``"failed"`` state with no
    token/header/body in the message. No token refresh or credential writes.
    """
    path = auth_path or CODEX_AUTH_PATH

    token = _read_codex_credentials(path)
    if token is None:
        return _missing_credentials_snapshot(
            PLUGIN_ID_CODEX,
            "Codex Usage",
            path,
            "Sign in to Codex (writing ~/.codex/auth.json)",
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "originator": "Codex Desktop",
        "User-Agent": "OpenPets/0.1.0",
    }
    # Same advisory account-header extraction as the Swift reader; never log it.
    try:
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        account = claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        if isinstance(account, str) and len(account) <= 256 and all(32 < ord(c) < 127 for c in account):
            headers["chatgpt-account-id"] = account
    except (ValueError, IndexError, AttributeError, TypeError, RecursionError):
        pass

    try:
        response = _fetch_with(fetcher, CODEX_USAGE_URL, headers, 5.0)
    except PluginError:
        return _fetch_error_snapshot(PLUGIN_ID_CODEX, "Codex Usage")

    status = response.get("status")
    body = response.get("body", "")
    if not isinstance(status, int) or not (200 <= status < 300):
        if status == 401:
            return _missing_credentials_snapshot(
                PLUGIN_ID_CODEX,
                "Codex Usage",
                path,
                "Sign in to Codex (token rejected by usage service)",
            )
        return _fetch_error_snapshot(PLUGIN_ID_CODEX, "Codex Usage")

    text_payload = _extract_json_object(body)
    if text_payload is None:
        return _parse_error_snapshot(PLUGIN_ID_CODEX, "Codex Usage")
    payload = _decode_json(text_payload)
    if payload is None:
        return _parse_error_snapshot(PLUGIN_ID_CODEX, "Codex Usage")

    now = time.time()
    windows = _parse_codex_usage(payload, now)

    if not windows:
        return snapshot(
            PLUGIN_ID_CODEX,
            "Codex Usage",
            "No usage data",
            ["Usage response contained no usable buckets"],
            "failed",
        )

    return snapshot(
        PLUGIN_ID_CODEX,
        "Codex Usage",
        _build_codex_value(windows),
        [f"Codex {w['label']}: {w['value']}" for w in windows],
        _worst_state([w["state"] for w in windows]),
        windows=windows,
    )


# --- worst-state helper -----------------------------------------------------


def _worst_state(states: list[str]) -> str:
    """Return the most severe state in the list."""
    order = {"critical": 3, "warning": 2, "failed": 2, "setup": 1, "normal": 0, "success": 0}
    best = "normal"
    best_rank = -1
    for s in states:
        rank = order.get(s, 0)
        if rank > best_rank:
            best_rank = rank
            best = s
    return best


# --- default fetcher --------------------------------------------------------


def default_fetcher(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Default HTTPS fetcher using only the standard library.

    Parameters
    ----------
    url:
        Fixed HTTPS provider URL. Only ``https://`` URLs are accepted.
    headers:
        HTTP request headers. May include ``Authorization`` bearer; the bearer
        is never forwarded on a cross-origin redirect and is never logged.
    timeout:
        Request timeout in seconds, hard-capped at :data:`FETCH_MAX_TIMEOUT`.

    Returns
    -------
    dict with ``"status"`` (int HTTP code) and ``"body"`` (str response text,
    capped at :data:`FETCH_MAX_BODY` bytes).

    Raises
    ------
    PluginError
        On transport, redirect, or read failures. The message is sanitized and
        contains no token, header, or body content.
    """
    if url not in (CLAUDE_USAGE_URL, CODEX_USAGE_URL):
        raise PluginError("unsupported URL scheme")

    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        raise PluginError("invalid timeout")
    if not (0 < timeout_value <= FETCH_MAX_TIMEOUT):
        raise PluginError("timeout out of bounds")

    # Fixed provider endpoints never follow redirects, including same-host
    # different-port destinations. Neither headers nor requests cross origins.
    req = _urllib_request.Request(url, headers=dict(headers), method="GET")

    opener = _urllib_request.build_opener(_SameHostRedirectHandler())

    try:
        with opener.open(req, timeout=timeout_value) as resp:
            status = resp.status
            raw = resp.read(FETCH_MAX_BODY + 1)
    except _HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(FETCH_MAX_BODY + 1)
        except OSError:
            raw = b""
    except _URLError:
        raise PluginError("network error")
    except OSError:
        raise PluginError("network error")

    if len(raw) > FETCH_MAX_BODY:
        raise PluginError("response too large")

    try:
        body = raw.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, ValueError):
        body = raw.decode("utf-8", errors="replace")

    return {"status": status, "body": body}


def _fetch_with(
    fetcher: Callable[[str, dict[str, str], float], dict[str, Any]] | None,
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Call the given fetcher or the default; PluginError propagates."""
    try:
        result = (fetcher or default_fetcher)(url, headers, timeout)
        if not isinstance(result, dict) or not isinstance(result.get("body"), str) or len(result["body"]) > FETCH_MAX_BODY:
            raise PluginError("Invalid usage response")
        return result
    except Exception:
        raise PluginError("Usage request failed") from None


class _SameHostRedirectHandler(_urllib_request.HTTPRedirectHandler):
    """Reject all redirects from the fixed authenticated usage endpoints."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
