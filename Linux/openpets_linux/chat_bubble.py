"""Classification helpers for the optional Linux comic chat-bubble plugin."""

from __future__ import annotations

import math
import unicodedata
from collections import deque
from typing import Any


CHAT_BUBBLE_MIN_WIDTH = 96
CHAT_BUBBLE_MAX_WIDTH = 430
CHAT_BUBBLE_PAD_X = 12
CHAT_BUBBLE_PAD_Y = 12
CHAT_BUBBLE_MIN_HEIGHT = 0
CHAT_BUBBLE_MAX_HEIGHT = 10000
CHAT_BUBBLE_LINE_HEIGHT = 18
CHAT_BUBBLE_CHAR_WIDTH = 8
CHAT_BUBBLE_MAX_LINES = 13
MAX_CHAT_BUBBLE_QUEUE = 50
CHAT_BUBBLE_ASPECT_RATIO = 1.8
CHAT_BUBBLE_CORNER_RADIUS_PERCENT = 22.0
CHAT_BUBBLE_TEXT_SIZE = 10.5


# Lifecycle/status values used by OpenPets plus familiar aliases. Unknown
# status values remain conversational for compatibility with notify callers
# that use a short free-form status such as ``chat`` or ``thinking``.
STATUS_KINDS = frozenset({
    "running", "task", "working", "waiting", "queued", "pending",
    "review", "reviewing", "done", "success", "completed", "complete",
    "committed", "failed", "failure", "error", "attention", "reply",
    "message",
})


def is_chat_message(message: Any) -> bool:
    """Return whether a notification represents conversation, not task status.

    Explicit ``kind``/``messageType`` metadata takes precedence. For older
    callers without metadata, known lifecycle statuses are status messages;
    other status strings are treated as chat.
    """
    if not isinstance(message, dict):
        return False
    kind = message.get("kind", message.get("messageType"))
    if isinstance(kind, str) and kind.strip():
        return kind.strip().lower() in {"chat", "conversation"}
    status = message.get("status")
    if not isinstance(status, str):
        return False
    return status.strip().lower() not in STATUS_KINDS


def latest_chat_message(messages: Any) -> dict[str, Any] | None:
    """Pick the most recently updated chat notification from an iterable."""
    if isinstance(messages, dict):
        messages = messages.values()
    try:
        candidates = list(messages)
    except TypeError:
        return None
    chats = [message for message in candidates
             if is_chat_message(message)
             and isinstance(message.get("text"), str)
             and message["text"].strip()]
    if not chats:
        return None
    # Core notifications retain insertion order when the same thread updates;
    # use the monotonic update timestamp when available, with order as fallback.
    def ordering(pair):
        value = pair[1].get("updated_at")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            value = float("-inf")
        else:
            try:
                if not math.isfinite(value):
                    value = float("-inf")
            except OverflowError:
                value = float("-inf")
        return value, pair[0]

    return max(enumerate(chats), key=ordering)[1]


def estimate_bubble_size(
    text: Any, max_width: int = CHAT_BUBBLE_MAX_WIDTH
) -> tuple[int, int, int]:
    """Estimate ellipse width, height and wrapped lines from visible text.

    Width grows with the longest hard line, then height grows for remaining
    wrapped lines. The estimate intentionally adds generous font/spacing room.
    Both dimensions are capped so long messages stay bounded;
    the renderer applies Pango ellipsizing as a final safeguard.
    """
    if not isinstance(text, str):
        text = ""
    if isinstance(max_width, bool) or not isinstance(max_width, int):
        max_width = CHAT_BUBBLE_MAX_WIDTH
    max_width = max(1, min(CHAT_BUBBLE_MAX_WIDTH, max_width))
    hard_lines = text.splitlines() or [""]

    def units(line: str) -> float:
        total = 0.0
        for char in line:
            if char == "\t":
                total += 4
            elif char.isspace():
                total += 0.65
            elif unicodedata.east_asian_width(char) in ("W", "F"):
                total += 2
            else:
                total += 1
        return total

    line_units = [units(line) for line in hard_lines]
    longest = max(line_units, default=0.0)
    width = int(math.ceil(longest * CHAT_BUBBLE_CHAR_WIDTH + 2 * CHAT_BUBBLE_PAD_X))
    width = max(min(CHAT_BUBBLE_MIN_WIDTH, max_width), min(max_width, width))
    chars_per_line = max(
        1.0,
        (width - 2 * CHAT_BUBBLE_PAD_X) / CHAT_BUBBLE_CHAR_WIDTH,
    )
    wrapped_lines = sum(max(1, math.ceil(count / chars_per_line)) for count in line_units)
    wrapped_lines = max(1, min(CHAT_BUBBLE_MAX_LINES, wrapped_lines))
    height = 2 * CHAT_BUBBLE_PAD_Y + wrapped_lines * CHAT_BUBBLE_LINE_HEIGHT
    height = max(CHAT_BUBBLE_MIN_HEIGHT, min(CHAT_BUBBLE_MAX_HEIGHT, height))
    return width, height, wrapped_lines


class ChatBubblePlugin:
    """Small plugin facade keeping classification independent of GTK."""

    plugin_id = "openpets.plugin.chat-bubble"

    def __init__(self):
        self.current: dict[str, Any] | None = None
        self._pending: deque[dict[str, Any]] = deque()

    @staticmethod
    def latest(messages: Any) -> dict[str, Any] | None:
        return latest_chat_message(messages)

    def enqueue(self, message: Any) -> bool:
        """Queue a distinct chat event; return True when it becomes current."""
        if not is_chat_message(message):
            return False
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return False
        entry = dict(message)
        if self.current is None:
            self.current = entry
            return True
        if len(self._pending) >= MAX_CHAT_BUBBLE_QUEUE:
            self._pending.popleft()
        self._pending.append(entry)
        return False

    def advance(self) -> dict[str, Any] | None:
        """Move to the oldest queued chat, or clear the current item."""
        self.current = self._pending.popleft() if self._pending else None
        return self.current

    def clear(self) -> None:
        self.current = None
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)
