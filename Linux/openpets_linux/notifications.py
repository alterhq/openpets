"""Pure-stdlib notification URL safety and session history for OpenPets Linux.

No GI, no disk storage, no personal config. All logic is deterministic and
safe to import in headless test environments.
"""

from __future__ import annotations

import re
import time
from typing import Any

__all__ = [
    "MAX_HISTORY_ITEMS",
    "safe_action_url",
    "NotificationHistory",
]

# Maximum number of notification entries retained in session history.
MAX_HISTORY_ITEMS = 100

# Unicode control characters (Cc) plus DEL (0x7F) and C1 controls (0x80-0x9F).
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def safe_action_url(value: Any) -> str | None:
    """Validate and return a safe absolute HTTP/HTTPS URL, or None.

    Only ``http`` and ``https`` schemes are accepted. The URL must be
    absolute (scheme + host). Rejects credentials, control characters,
    malformed ports, and non-web schemes (file, javascript, data, command,
    etc.).

    The returned string is safe to pass to a URL opener: it is never
    interpreted as markup and never passed to a shell. Only an explicit
    user click may trigger opening it.
    """
    if not isinstance(value, str):
        return None

    # Reject any control character (ASCII C0/C1, DEL, and Unicode Cc)
    # before trimming, so embedded or trailing control chars are rejected.
    if _CONTROL_CHARS.search(value):
        return None

    value = value.strip()
    if not value:
        return None

    from urllib.parse import urlparse

    try:
        parsed = urlparse(value)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None

    # Must be absolute: scheme and host both required.
    if not parsed.scheme or not parsed.netloc:
        return None
    hostname = parsed.hostname
    if not hostname or any(char.isspace() for char in hostname):
        return None

    # No embedded credentials (user:password@host or user@host).
    if parsed.username is not None or parsed.password is not None:
        return None

    # Port, if present, must be well-formed and in range 1-65535.
    if not _valid_port(parsed.netloc):
        return None

    return value


def _valid_port(netloc: str) -> bool:
    """Return True if netloc has no port or a valid TCP port (1-65535)."""
    host = netloc

    # Zone IDs (e.g. [fe80::1%eth0]) are not expected in action URLs.
    if "%" in host:
        return False

    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return False  # unclosed IPv6 bracket
        rest = host[end + 1:]
        if not rest:
            return True  # bare IPv6 address, no port
        if not rest.startswith(":"):
            return False  # junk after the closing bracket
        port_str = rest[1:]
    else:
        if ":" not in host:
            return True  # no port
        port_str = host.split(":", 1)[1]

    if not port_str:
        return False  # "host:" with an empty port
    if not port_str.isdigit():
        return False  # non-numeric port
    port = int(port_str)
    return 1 <= port <= 65535


class NotificationHistory:
    """Bounded in-memory session history of notification messages.

    No disk storage. Thread updates replace existing entries (no
    duplication). Dismissed and expired entries remain visible in session
    history until explicitly cleared or evicted by the retention bound.
    """

    def __init__(self, clock=None, max_items: int = MAX_HISTORY_ITEMS):
        self._clock = clock or time.monotonic
        self._max_items = max(1, int(max_items))
        self._entries: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []

    def sync(self, messages: dict) -> None:
        """Merge current messages into session history.

        ``messages`` maps thread_id -> message dict (as produced by
        ``core.State``). Existing threads are updated in place (no
        duplication). Threads no longer present are retained as dismissed;
        expired entries are retained and marked expired. The result is
        bounded to ``max_items``, keeping the most recently seen threads.
        """
        if not isinstance(messages, dict):
            messages = {}

        now = self._clock()
        for thread_id, message in messages.items():
            if not isinstance(thread_id, str) or not thread_id:
                continue
            if not isinstance(message, dict):
                continue
            entry = dict(message)
            entry["historyState"] = (
                "expired" if self._is_expired(entry, now) else "active"
            )
            self._entries[thread_id] = entry
            if thread_id in self._order:
                self._order.remove(thread_id)
            self._order.append(thread_id)

        # Threads which disappeared from core state were dismissed or expired.
        for thread_id in list(self._order):
            entry = self._entries[thread_id]
            if self._is_expired(entry, now):
                entry["historyState"] = "expired"
            elif entry.get("historyState") == "active" and thread_id not in messages:
                entry["historyState"] = "dismissed"

        # Enforce bound: drop oldest entries first.
        while len(self._order) > self._max_items:
            oldest = self._order.pop(0)
            del self._entries[oldest]

    def items(self) -> list[dict]:
        """Return retained entries, newest first, including dismissed/expired."""
        now = self._clock()
        for entry in self._entries.values():
            if self._is_expired(entry, now):
                entry["historyState"] = "expired"
        return [dict(self._entries[thread_id]) for thread_id in reversed(self._order)]

    def clear(self) -> None:
        """Remove all entries from the history."""
        self._entries.clear()
        self._order.clear()

    # -- internals -----------------------------------------------------

    @staticmethod
    def _is_expired(message: dict, now: float) -> bool:
        deadline = message.get("expires_at")
        if deadline is None:
            return False
        try:
            return now >= float(deadline)
        except (TypeError, ValueError):
            return False
