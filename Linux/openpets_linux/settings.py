"""OpenPets user settings: bounded JSON config with atomic 0600 writes.

Pure standard library, no GI. Settings live under XDG config by default
(``$XDG_CONFIG_HOME/openpets/config.json`, falling back to
``~/.config/openpets/config.json``). An explicit path is honored as-is.

Writes are atomic (temp file + fsync + os.replace) and mode 0600. Writers are
serialized with an advisory file lock so concurrent read-modify-write cycles
cannot lose unrelated settings. An existing invalid config file is never
silently overwritten: reads fall back to defaults in memory only and never
write.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .motion import MODE_OFF

__all__ = [
    "Settings",
    "MAX_CONFIG_SIZE",
    "DEFAULT_CONFIG",
    "POSITIONS_FILE",
    "MODE_OFF",
]

MAX_CONFIG_SIZE = 64 * 1024
POSITIONS_FILE = "positions.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "socketPath": f"/tmp/openpets-{os.getuid()}.sock",
    "motionMode": MODE_OFF,
    "display": {
        "scale": 0.42,
        "messageAreaHeight": 56,
        "themeMode": "dark",
        "pluginOpacity": 0.94,
        "messageOpacity": 0.94,
        "blurBackground": False,
        "borderRadius": 14.0,
        "messagesCollapsed": False,
        "messageBubbleWidth": 240,
        "messageFontSize": 10.0,
    },
}

# Top-level keys that are themselves objects and should be deep-merged with
# defaults rather than wholesale replaced, preserving unrelated sub-keys.
_OBJECT_KEYS = frozenset({"display"})


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class Settings:
    """Typed, bounded, concurrency-safe settings store.

    ``path`` defaults to XDG config. ``read`` never writes to disk. ``update``
    merges changes, preserves unrelated fields, and writes atomically as 0600.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None):
        if path is None:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            if xdg:
                path = os.path.join(xdg, "openpets", "config.json")
            else:
                path = os.path.join(
                    os.path.expanduser("~"), ".config", "openpets", "config.json"
                )
        self.path = Path(path)
        self.directory = self.path.parent
        self.positions_path = self.directory / POSITIONS_FILE
        self._lock_path = self.directory / (self.path.name + ".lock")
        self._in_process_lock = threading.RLock()

    # -- low-level disk -----------------------------------------------------

    def _read_disk(self) -> dict[str, Any] | None:
        """Return parsed config dict, or None when missing/invalid (no write)."""
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                contents = handle.read(MAX_CONFIG_SIZE + 1)
                if len(contents) > MAX_CONFIG_SIZE:
                    return None
                data = json.loads(contents)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            # Invalid existing config: fall back to defaults in memory only;
            # never overwrite the offending file silently.
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _merge_with_defaults(self, data: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        for key, value in data.items():
            if key in _OBJECT_KEYS and isinstance(result.get(key), dict) and isinstance(value, dict):
                merged = dict(result[key])
                merged.update(value)
                result[key] = merged
            else:
                result[key] = value
        if not isinstance(result.get("display"), dict):
            result["display"] = copy.deepcopy(DEFAULT_CONFIG["display"])
        if not isinstance(result.get("socketPath"), str) or not result["socketPath"]:
            result["socketPath"] = DEFAULT_CONFIG["socketPath"]
        return result

    def _write_atomic(self, target: Path, data: dict[str, Any]) -> None:
        """Atomically write ``data`` as JSON to ``target`` with mode 0600."""
        contents = json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(contents.encode("utf-8")) > MAX_CONFIG_SIZE:
            raise ValueError("settings exceed 64 KiB")
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.directory, prefix=self.path.name + ".", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- locking ------------------------------------------------------------

    @contextlib.contextmanager
    def _exclusive_lock(self):
        """Advisory exclusive lock for writers; may create the lock file."""
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

    # -- public API ---------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Return current config without writing to disk.

        Invalid or missing config falls back to defaults in memory only; the
        offending file is never overwritten silently.
        """
        with self._in_process_lock:
            data = self._read_disk()
        if data is None:
            return copy.deepcopy(DEFAULT_CONFIG)
        return self._merge_with_defaults(data)

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Merge ``changes`` into config, preserve unrelated fields, write 0600.

        Returns the resulting config dict. Raises TypeError if ``changes`` is
        not a dict, ValueError if the result would exceed the bounded size.
        """
        if not isinstance(changes, dict):
            raise TypeError("changes must be a dict")
        with self._in_process_lock:
            with self._exclusive_lock():
                if self.path.is_symlink():
                    raise ValueError("Refusing to replace a symlinked config")
                current = self._read_disk()
                if current is None:
                    if os.path.lexists(self.path):
                        raise ValueError("Existing config is invalid or unreadable; repair it before saving")
                    current = copy.deepcopy(DEFAULT_CONFIG)
                else:
                    current = self._merge_with_defaults(current)
                for key, value in changes.items():
                    if isinstance(value, dict) and isinstance(current.get(key), dict):
                        current[key] = dict(current[key], **value)
                    else:
                        current[key] = value
                self._write_atomic(self.path, current)
                return dict(current)

    # -- positions (adjacent positions.json) --------------------------------

    def _read_positions_disk(self) -> dict[str, Any]:
        try:
            with self.positions_path.open("r", encoding="utf-8") as handle:
                contents = handle.read(MAX_CONFIG_SIZE + 1)
                if len(contents) > MAX_CONFIG_SIZE:
                    return {}
                data = json.loads(contents)
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def get_position(self, pet_id: str) -> tuple[int, int] | None:
        """Return (x, y) for ``pet_id`` from adjacent positions.json."""
        if not isinstance(pet_id, str) or not pet_id:
            return None
        with self._in_process_lock:
            data = self._read_positions_disk()
        entry = data.get(pet_id)
        if (
            isinstance(entry, list)
            and len(entry) == 2
            and all(_is_finite_number(v) and abs(v) <= 1000000 for v in entry)
        ):
            return (int(entry[0]), int(entry[1]))
        return None

    def set_position(self, pet_id: str, x: int, y: int) -> None:
        """Persist (x, y) for ``pet_id`` to adjacent positions.json (0600)."""
        if not isinstance(pet_id, str) or not pet_id:
            raise ValueError("pet_id must be a non-empty string")
        if len(pet_id) > 128:
            raise ValueError("pet_id is too long")
        for value in (x, y):
            if not _is_finite_number(value) or abs(value) > 1000000:
                raise ValueError("coordinates must be finite numbers")
        with self._in_process_lock:
            with self._exclusive_lock():
                if self.positions_path.is_symlink():
                    raise ValueError("Refusing to replace symlinked positions")
                if self.positions_path.exists():
                    with self.positions_path.open("rb") as handle:
                        raw = handle.read(MAX_CONFIG_SIZE + 1)
                    if len(raw) > MAX_CONFIG_SIZE:
                        raise ValueError("positions exceed 64 KiB")
                    try:
                        existing = json.loads(raw)
                        if not isinstance(existing, dict):
                            raise ValueError("Invalid existing positions")
                    except (ValueError, UnicodeError, RecursionError):
                        raise ValueError("Existing positions are invalid; repair before saving") from None
                data = self._read_positions_disk()
                data[pet_id] = [x, y]
                self._write_atomic(self.positions_path, data)
