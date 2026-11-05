"""Unit tests for openpets_linux.settings (standard library only, no GI, no display).

All tests run in isolated temporary directories and restore the process
environment, so the root user's config is never read or written.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys_path_inserted = False

# Ensure `openpets_linux` is importable when run via unittest discover.
sys_path_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path_here not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path_here)

from openpets_linux.settings import (  # noqa: E402
    DEFAULT_CONFIG,
    MAX_CONFIG_SIZE,
    POSITIONS_FILE,
    Settings,
)


class _EnvRestore:
    """Context manager that restores XDG/HOME env after the test."""

    def __init__(self):
        self._saved = {key: os.environ.get(key) for key in (
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HOME",
        )}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _make_settings(temporary=None):
    """Build a Settings rooted in an isolated temp dir via XDG_CONFIG_HOME."""
    if temporary is None:
        temporary = tempfile.mkdtemp(prefix="openpets-settings-")
    config_home = Path(temporary) / "config"
    env = {"XDG_CONFIG_HOME": str(config_home), "HOME": str(temporary)}
    return Settings(), env, temporary


class DefaultPathTests(unittest.TestCase):
    def test_defaults_to_xdg_config_home(self):
        with _EnvRestore(), tempfile.TemporaryDirectory(prefix="openpets-") as tmp:
            config_home = Path(tmp) / "config"
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home),
                                         "HOME": str(tmp)}):
                settings = Settings()
                expected = config_home / "openpets" / "config.json"
                self.assertEqual(settings.path, expected)
                self.assertEqual(settings.directory, config_home / "openpets")
                self.assertEqual(
                    settings.positions_path, config_home / "openpets" / POSITIONS_FILE
                )

    def test_falls_back_to_dot_config_when_no_xdg(self):
        with _EnvRestore(), tempfile.TemporaryDirectory(prefix="openpets-") as tmp:
            config_home = Path(tmp) / "config"
            env = {"HOME": str(tmp)}
            env.pop("XDG_CONFIG_HOME", None)
            with patch.dict(os.environ, env, clear=False):
                # Remove XDG_CONFIG_HOME if present in inherited env.
                os.environ.pop("XDG_CONFIG_HOME", None)
                settings = Settings()
                expected = Path(tmp) / ".config" / "openpets" / "config.json"
                self.assertEqual(settings.path, expected)

    def test_explicit_path_honored_as_is(self):
        with tempfile.TemporaryDirectory(prefix="openpets-") as tmp:
            explicit = Path(tmp) / "custom" / "config.json"
            settings = Settings(explicit)
            self.assertEqual(settings.path, explicit)


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp(prefix="openpets-read-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.temporary, ignore_errors=True))
        self._env = _EnvRestore()
        self._env.__enter__()
        self.addCleanup(self._env.__exit__, None, None, None)
        os.environ["XDG_CONFIG_HOME"] = str(Path(self.temporary) / "config")
        os.environ["HOME"] = self.temporary
        self.settings = Settings()

    def test_read_missing_returns_defaults_and_writes_nothing(self):
        config = self.settings.read()
        self.assertEqual(config, DEFAULT_CONFIG)
        self.assertFalse(self.settings.path.exists())

    def test_read_invalid_json_returns_defaults_without_overwriting(self):
        self.settings.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.path.write_text("{ not valid json", encoding="utf-8")
        before = self.settings.path.read_text(encoding="utf-8")
        config = self.settings.read()
        self.assertEqual(config, DEFAULT_CONFIG)
        # Invalid file must not be silently overwritten.
        self.assertEqual(self.settings.path.read_text(encoding="utf-8"), before)

    def test_read_non_object_json_returns_defaults(self):
        self.settings.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(self.settings.read(), DEFAULT_CONFIG)

    def test_read_merges_file_over_defaults(self):
        self.settings.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.path.write_text(
            json.dumps({"socketPath": "/tmp/custom.sock"}), encoding="utf-8"
        )
        config = self.settings.read()
        self.assertEqual(config["socketPath"], "/tmp/custom.sock")
        # Unrelated defaults preserved.
        self.assertEqual(config["display"], DEFAULT_CONFIG["display"])


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp(prefix="openpets-update-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.temporary, ignore_errors=True))
        self._env = _EnvRestore()
        self._env.__enter__()
        self.addCleanup(self._env.__exit__, None, None, None)
        os.environ["XDG_CONFIG_HOME"] = str(Path(self.temporary) / "config")
        os.environ["HOME"] = self.temporary
        self.settings = Settings()

    def test_update_creates_file_and_preserves_unrelated(self):
        self.settings.update({"socketPath": "/tmp/custom.sock"})
        self.assertTrue(self.settings.path.exists())
        config = self.settings.read()
        self.assertEqual(config["socketPath"], "/tmp/custom.sock")
        self.assertEqual(config["display"], DEFAULT_CONFIG["display"])

    def test_update_preserves_unrelated_fields(self):
        self.settings.update({"socketPath": "/tmp/a.sock"})
        self.settings.update({"display": {"scale": 1.5}})
        config = self.settings.read()
        self.assertEqual(config["socketPath"], "/tmp/a.sock")
        self.assertEqual(config["display"]["scale"], 1.5)
        self.assertEqual(config["display"]["messageAreaHeight"], DEFAULT_CONFIG["display"]["messageAreaHeight"])

    def test_update_atomic_mode_0600(self):
        self.settings.update({"socketPath": "/tmp/x.sock"})
        mode = stat.S_IMODE(self.settings.path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_update_bounded_size_rejects_oversized(self):
        huge = {"x": "y" * (MAX_CONFIG_SIZE + 1)}
        with self.assertRaises(ValueError):
            self.settings.update(huge)
        # Nothing written.
        self.assertFalse(self.settings.path.exists())

    def test_update_rejects_non_dict(self):
        for bad in ("nope", 42, ["k"], None):
            with self.assertRaises(TypeError):
                self.settings.update(bad)

    def test_concurrent_updates_do_not_lose_fields(self):
        self.settings.update({"socketPath": "/tmp/a.sock", "display": {"scale": 0.5}})
        errors = []

        def worker(suffix):
            try:
                for index in range(20):
                    self.settings.update({"socketPath": f"/tmp/worker-{suffix}-{index}.sock"})
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors, errors)
        config = self.settings.read()
        # Every field written by any thread must still be retrievable; the
        # last socketPath wins but display.scale must be preserved.
        self.assertEqual(config["display"]["scale"], 0.5)
        self.assertIsInstance(config["socketPath"], str)


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp(prefix="openpets-pos-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.temporary, ignore_errors=True))
        self._env = _EnvRestore()
        self._env.__enter__()
        self.addCleanup(self._env.__exit__, None, None, None)
        os.environ["XDG_CONFIG_HOME"] = str(Path(self.temporary) / "config")
        os.environ["HOME"] = self.temporary
        self.settings = Settings()

    def test_get_position_missing_returns_none(self):
        self.assertIsNone(self.settings.get_position("starcorn"))

    def test_set_and_get_position_round_trip(self):
        self.settings.set_position("starcorn", 100, 200)
        self.assertEqual(self.settings.get_position("starcorn"), (100, 200))
        # Stored adjacent to config directory as positions.json.
        self.assertTrue(self.settings.positions_path.exists())

    def test_position_independent_pets(self):
        self.settings.set_position("a", 1, 2)
        self.settings.set_position("b", 3, 4)
        self.assertEqual(self.settings.get_position("a"), (1, 2))
        self.assertEqual(self.settings.get_position("b"), (3, 4))

    def test_position_mode_0600(self):
        self.settings.set_position("starcorn", 1, 2)
        mode = stat.S_IMODE(self.settings.positions_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_set_position_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            self.settings.set_position("", 1, 2)
        with self.assertRaises(ValueError):
            self.settings.set_position("x", float("nan"), 2)
        with self.assertRaises(ValueError):
            self.settings.set_position("x", True, 2)
        with self.assertRaises(ValueError):
            self.settings.set_position("x", 1, float("inf"))

    def test_positions_survive_config_rewrite(self):
        self.settings.set_position("starcorn", 5, 6)
        self.settings.update({"socketPath": "/tmp/z.sock"})
        self.assertEqual(self.settings.get_position("starcorn"), (5, 6))


if __name__ == "__main__":
    unittest.main()
