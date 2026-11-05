"""Pure-stdlib OpenPets core for the Linux GTK3 port.

Wire field names follow OpenPetsKit v0.2.1. This is an independent preview,
not a complete reproduction of the macOS host's scheduling and UI behavior:

    * one JSON object per line, newline (0x0a) terminated, UTF-8
    * client -> server: PetCommand  (notify, playAnimation, stopAnimation,
      clearMessage, clearMessages, ping, shutdown)
    * server -> client: PetResponse  (ok, message?, threadId?)

This module has no third-party dependencies: standard library only, no GI.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable

__all__ = [
    "ANIMATIONS",
    "ANIMATION_ALIASES",
    "STATUS_ANIMATION",
    "FINITE_STATUS_ANIMATIONS",
    "has_active_task",
    "DEFAULT_SCALE",
    "DEFAULT_MESSAGE_AREA_HEIGHT",
    "MAX_COMMAND_SIZE",
    "CoreError",
    "State",
    "load_config",
    "resolve_animation",
    "list_pet_animations",
]

# Canonical animation raw values, matching PetAnimation raw values in
# openpetskit-linux-survey v0.2.1 (PetAnimation.swift).
ANIMATIONS: tuple[str, ...] = (
    "idle",
    "running-right",
    "running-left",
    "waving",
    "jumping",
    "failed",
    "waiting",
    "running",
    "review",
)

# CLI aliases accepted alongside raw values (PetAnimation.init?(cliValue:)).
# runright/runleft are lowercase no-hyphen variants users commonly type.
# "falled" is a misspelling of "failed" and is deliberately not aliased.
ANIMATION_ALIASES: dict[str, str] = {
    "runningRight": "running-right",
    "right": "running-right",
    "runright": "running-right",
    "runningLeft": "running-left",
    "left": "running-left",
    "runleft": "running-left",
}

# notification.status (case-insensitive) -> animation, mirroring the
# animation(forStatusKind:) / finiteAnimation(forStatusKind:) mappings in
# OpenPetsHost.swift.
STATUS_ANIMATION: dict[str, str] = {
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "review": "review",
    "reviewing": "review",
    "waiting": "waiting",
    "queued": "waiting",
    "pending": "waiting",
    "running": "running",
    "task": "running",
    "working": "running",
    "done": "jumping",
    "success": "jumping",
    "completed": "jumping",
    "complete": "jumping",
    "committed": "jumping",
    "attention": "waving",
    "reply": "waving",
    "message": "waving",
}

# Statuses whose animation plays a finite cycle (loopCount 3 in Swift) and
# then returns to idle, with no animation TTL of its own. On Linux, only the
# completion/success family is finite: done, success, completed, complete,
# committed. running/review/waiting/waving/failed/message remain animated
# (looping) while active instead of stopping after three cycles -- a
# deliberate Linux UX divergence from the macOS host, where running/task/
# working/review/reviewing were also finite.
FINITE_STATUS_ANIMATIONS: frozenset[str] = frozenset({
    "done",
    "success",
    "completed",
    "complete",
    "committed",
})

# Notifications in these states mean the task is still underway. The pet host
# uses the latest status for each thread to freeze autonomous movement until
# every active task reaches a terminal status (for example, done or failed).
ACTIVE_TASK_STATUSES: frozenset[str] = frozenset({
    "running", "task", "working", "waiting", "queued", "pending",
    "review", "reviewing",
})


def has_active_task(messages: Any) -> bool:
    """Return whether any thread in a message mapping is still in progress."""
    items = messages.values() if isinstance(messages, dict) else messages
    return any(
        isinstance(message, dict)
        and str(message.get("status", "")).strip().lower() in ACTIVE_TASK_STATUSES
        for message in items
    )

DEFAULT_SCALE = 0.42
DEFAULT_MESSAGE_AREA_HEIGHT = 56

# IPC frame cap shared with openpets_linux.ipc (64 KiB).
MAX_COMMAND_SIZE = 64 * 1024

# Validation limits for notification/command payload fields.
MAX_TITLE_LENGTH = 256
MAX_TEXT_LENGTH = 4096
MAX_STATUS_LENGTH = 64
MAX_THREAD_ID_LENGTH = 128
MAX_URL_LENGTH = 2048
MAX_BUTTON_LABEL_LENGTH = 128
MAX_MESSAGES = 100


class CoreError(Exception):
    """Raised for invalid commands or configuration."""


def default_socket_path() -> str:
    """Default Unix socket path: /tmp/openpets-{UID}.sock (OpenPetsPaths)."""
    return f"/tmp/openpets-{os.getuid()}.sock"


def resolve_animation(cli_value: Any) -> str | None:
    """Map a CLI animation value to a canonical ANIMATIONS raw value.

    Mirrors PetAnimation.init?(cliValue:): raw values match first, then the
    Swift-side aliases runningRight/right and runningLeft/left. Returns None
    for unknown or non-string values.
    """
    if isinstance(cli_value, str):
        if cli_value in ANIMATIONS:
            return cli_value
        return ANIMATION_ALIASES.get(cli_value)
    return None


def list_pet_animations() -> list[dict[str, Any]]:
    """Return discoverable animation metadata: canonical name, aliases, purpose, atlas row.

    Pure standard library: no GI, no GTK, no socket required. The ``atlas_row``
    is the zero-based index into ``ANIMATIONS`` and matches the spritesheet row
    used by the GTK host (``ui.py`` resolves ``list(ANIMATIONS).index(name)``).
    """
    aliases_by_name: dict[str, list[str]] = {name: [] for name in ANIMATIONS}
    for alias, target in ANIMATION_ALIASES.items():
        if target in aliases_by_name:
            aliases_by_name[target].append(alias)

    purposes = {
        "idle": "Default resting pose; the pet returns here when stopped.",
        "running-right": "Pet runs toward the right side of the screen.",
        "running-left": "Pet runs toward the left side of the screen.",
        "waving": "Pet waves, typically for attention or a message.",
        "jumping": "Completion or success celebration.",
        "failed": "Task failed or error state.",
        "waiting": "Pet waits, typically for a queued or pending task.",
        "running": "Generic running while a task is in progress.",
        "review": "Review requested of the user.",
    }

    catalog: list[dict[str, Any]] = []
    for row, name in enumerate(ANIMATIONS):
        catalog.append({
            "name": name,
            "aliases": list(aliases_by_name[name]),
            "purpose": purposes[name],
            "atlas_row": row,
        })
    return catalog


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class State:
    """In-memory OpenPets pet state.

    Pure logic: no UI, no sockets, no GI. A monotonic clock is injected so
    TTL expiry is deterministic in tests and in production.
    """

    def __init__(self, clock: Callable[[], float] | None = None):
        self._clock: Callable[[], float] = clock or time.monotonic
        self.animation: str = "idle"
        self.messages: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.stopping: bool = False
        self.loop: bool = True
        self._animation_expires_at: float | None = None

    # -- command dispatch ---------------------------------------------

    def apply(self, command: Any) -> dict[str, Any]:
        """Apply one decoded command dict; return a PetResponse dict."""
        if not isinstance(command, dict):
            return {"ok": False, "message": "command must be a JSON object"}

        command_type = command.get("type")

        if command_type == "ping":
            return {"ok": True, "message": "pong"}

        if command_type == "shutdown":
            self.stopping = True
            return {"ok": True, "message": "shutting down"}

        if self.stopping:
            return {"ok": False, "message": "pet is not running"}

        if command_type == "notify":
            return self._apply_notify(command)
        if command_type == "playAnimation":
            return self._apply_play_animation(command)
        if command_type == "stopAnimation":
            self.stop_animation()
            return {"ok": True}
        if command_type == "clearMessage":
            return self._apply_clear_message(command)
        if command_type == "clearMessages":
            self.clear_messages()
            return {"ok": True}
        return {"ok": False, "message": f"unknown command type: {command_type!r}"}

    def stop_animation(self) -> None:
        self.animation = "idle"
        self.loop = True
        self._animation_expires_at = None

    def clear_messages(self) -> None:
        self.messages.clear()

    # -- command handlers ---------------------------------------------

    def _apply_notify(self, command: dict[str, Any]) -> dict[str, Any]:
        notification = command.get("notification")
        if not isinstance(notification, dict):
            return {"ok": False, "message": "notification must be a JSON object"}

        title = notification.get("title")
        if not isinstance(title, str) or not title:
            return {"ok": False, "message": "notification.title is required"}
        if len(title) > MAX_TITLE_LENGTH:
            return {"ok": False, "message": "notification.title is too long"}

        text = notification.get("text")
        if text is not None and (
            not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH
        ):
            return {"ok": False, "message": "notification.text is too long"}

        status = notification.get("status")
        if not isinstance(status, str) or not status:
            return {"ok": False, "message": "notification.status is required"}
        if len(status) > MAX_STATUS_LENGTH:
            return {"ok": False, "message": "notification.status is too long"}

        thread_id = notification.get("threadId")
        if thread_id is not None and not isinstance(thread_id, str):
            return {"ok": False, "message": "notification.threadId must be a string"}
        if not thread_id:
            thread_id = uuid.uuid4().hex
        elif len(thread_id) > MAX_THREAD_ID_LENGTH:
            return {"ok": False, "message": "notification.threadId is too long"}

        url = notification.get("url")
        if url is not None and (
            not isinstance(url, str) or len(url) > MAX_URL_LENGTH
        ):
            return {"ok": False, "message": "notification.url is too long"}
        if url is not None:
            from .notifications import safe_action_url
            url = safe_action_url(url)
            if url is None:
                return {"ok": False, "message": "Only valid HTTP/HTTPS notification action URLs are allowed"}

        button_label = notification.get("buttonLabel")
        if button_label is not None and (
            not isinstance(button_label, str)
            or len(button_label) > MAX_BUTTON_LABEL_LENGTH
        ):
            return {"ok": False, "message": "notification.buttonLabel is too long"}

        ttl_seconds = notification.get("ttlSeconds")
        if ttl_seconds is not None and (
            not _is_finite_number(ttl_seconds) or ttl_seconds < 0
        ):
            return {"ok": False, "message": "notification.ttlSeconds must be a non-negative number"}

        now = self._clock()
        self.expire(now)
        if thread_id not in self.messages and len(self.messages) >= MAX_MESSAGES:
            return {"ok": False, "message": "message limit reached; clear an existing thread first"}
        expires_at = now + ttl_seconds if (ttl_seconds is not None and ttl_seconds > 0) else None
        self.messages[thread_id] = {
            "title": title,
            "text": text,
            "status": status,
            "threadId": thread_id,
            "url": url,
            "buttonLabel": button_label,
            "ttlSeconds": ttl_seconds,
            "expires_at": expires_at,
        }

        status_key = status.lower()
        self.loop = True
        if status_key in FINITE_STATUS_ANIMATIONS:
            self.animation = STATUS_ANIMATION[status_key]
            self._animation_expires_at = None
        else:
            self.animation = STATUS_ANIMATION.get(status_key, "idle")
            if ttl_seconds is not None and ttl_seconds > 0:
                self._animation_expires_at = now + ttl_seconds
            else:
                self._animation_expires_at = None

        return {"ok": True, "threadId": thread_id}

    def _apply_play_animation(self, command: dict[str, Any]) -> dict[str, Any]:
        name = command.get("name")
        resolved = resolve_animation(name)
        if resolved is None:
            return {"ok": False, "message": f"unknown animation: {name!r}"}

        loop = command.get("loop")
        if loop is None:
            loop = True
        if loop is None:
            loop = True
        if not isinstance(loop, bool):
            return {"ok": False, "message": "playAnimation.loop must be a boolean"}

        ttl_seconds = command.get("ttlSeconds")
        if ttl_seconds is not None and (
            not _is_finite_number(ttl_seconds) or ttl_seconds < 0
        ):
            return {"ok": False, "message": "playAnimation.ttlSeconds must be a non-negative number"}

        now = self._clock()
        self.animation = resolved
        self.loop = bool(loop)
        if ttl_seconds is not None and ttl_seconds > 0:
            self._animation_expires_at = now + ttl_seconds
        else:
            self._animation_expires_at = None
        return {"ok": True}

    def _apply_clear_message(self, command: dict[str, Any]) -> dict[str, Any]:
        thread_id = command.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            return {"ok": False, "message": "clearMessage.threadId is required"}
        self.messages.pop(thread_id, None)
        return {"ok": True}

    # -- TTL ----------------------------------------------------------

    def expire(self, now: float | None = None) -> list[str]:
        """Expire messages (and animation) whose deadline is <= now.

        Returns the thread ids of messages removed, in order. `now` defaults
        to the injected monotonic clock.
        """
        if now is None:
            now = self._clock()

        expired: list[str] = []
        for thread_id in list(self.messages.keys()):
            entry = self.messages[thread_id]
            deadline = entry.get("expires_at")
            if deadline is not None and now >= deadline:
                expired.append(thread_id)
                del self.messages[thread_id]

        if (
            self._animation_expires_at is not None
            and now >= self._animation_expires_at
        ):
            self._animation_expires_at = None
            if self.animation != "idle":
                self.animation = "idle"

        return expired


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load OpenPets configuration without writing or clobbering anything.

    Resolution mirrors OpenPetsPaths:

    * explicit ``path``
    * else ``$XDG_CONFIG_HOME/openpets/config.json``
    * else ``~/.config/openpets/config.json``

    Missing or unreadable config falls back to defaults:

        socketPath /tmp/openpets-{UID}.sock
        display {scale: 0.42, messageAreaHeight: 56,
                 messageBubbleWidth: 240, messageFontSize: 10}
    """
    result: dict[str, Any] = {
        "socketPath": default_socket_path(),
        "display": {
            "scale": DEFAULT_SCALE,
            "messageAreaHeight": DEFAULT_MESSAGE_AREA_HEIGHT,
            "messageBubbleWidth": 240,
            "messageFontSize": 10.0,
        },
    }

    if path is None:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config_home:
            path = os.path.join(xdg_config_home, "openpets", "config.json")
        else:
            path = os.path.join(os.path.expanduser("~"), ".config", "openpets", "config.json")

    if not os.path.exists(path):
        return result

    try:
        with open(path, "r", encoding="utf-8") as handle:
            contents = handle.read(MAX_COMMAND_SIZE + 1)
            if len(contents) > MAX_COMMAND_SIZE:
                raise ValueError("configuration exceeds 64 KiB")
            data = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return result

    if not isinstance(data, dict):
        return result

    socket_path = data.get("socketPath")
    if isinstance(socket_path, str) and socket_path:
        result["socketPath"] = socket_path

    active_pet = data.get("activePetID")
    if isinstance(active_pet, str) and active_pet not in (".", "..") and "/" not in active_pet and len(active_pet) <= 128:
        result["activePetID"] = active_pet

    display = data.get("display")
    if isinstance(display, dict):
        scale = display.get("scale")
        if _is_finite_number(scale):
            result["display"]["scale"] = float(scale)
        message_area_height = display.get("messageAreaHeight")
        if _is_finite_number(message_area_height):
            result["display"]["messageAreaHeight"] = message_area_height
        message_bubble_width = display.get("messageBubbleWidth")
        if _is_finite_number(message_bubble_width) and 120 <= message_bubble_width <= 600:
            result["display"]["messageBubbleWidth"] = int(message_bubble_width)
        message_font_size = display.get("messageFontSize")
        if _is_finite_number(message_font_size) and 6 <= message_font_size <= 24:
            result["display"]["messageFontSize"] = float(message_font_size)

    return result
