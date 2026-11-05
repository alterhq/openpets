"""Unit tests for openpets_linux.core (standard library only, no GI)."""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from collections import OrderedDict

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from openpets_linux.core import (  # noqa: E402
    ANIMATIONS,
    ANIMATION_ALIASES,
    DEFAULT_MESSAGE_AREA_HEIGHT,
    DEFAULT_SCALE,
    FINITE_STATUS_ANIMATIONS,
    MAX_COMMAND_SIZE,
    STATUS_ANIMATION,
    State,
    load_config,
    list_pet_animations,
    resolve_animation,
)


class _FixedClock:
    """Deterministic monotonic clock for TTL tests."""

    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class TestAnimationCatalog(unittest.TestCase):
    def test_animations_match_swift_raw_values(self):
        self.assertEqual(
            ANIMATIONS,
            (
                "idle",
                "running-right",
                "running-left",
                "waving",
                "jumping",
                "failed",
                "waiting",
                "running",
                "review",
            ),
        )

    def test_animations_are_unique_strings(self):
        self.assertEqual(len(ANIMATIONS), len(set(ANIMATIONS)))
        for animation in ANIMATIONS:
            self.assertIsInstance(animation, str)

    def test_cli_mapping_accepts_raw_values(self):
        for animation in ANIMATIONS:
            self.assertEqual(resolve_animation(animation), animation)

    def test_cli_mapping_accepts_swift_aliases(self):
        self.assertEqual(resolve_animation("runningRight"), "running-right")
        self.assertEqual(resolve_animation("right"), "running-right")
        self.assertEqual(resolve_animation("runningLeft"), "running-left")
        self.assertEqual(resolve_animation("left"), "running-left")

    def test_cli_mapping_accepts_lowercase_aliases(self):
        self.assertEqual(resolve_animation("runright"), "running-right")
        self.assertEqual(resolve_animation("runleft"), "running-left")

    def test_cli_mapping_rejects_misspelled_failed(self):
        # "falled" is a misspelling of "failed" and is deliberately not aliased.
        self.assertIsNone(resolve_animation("falled"))
        self.assertIsNone(resolve_animation("Falled"))

    def test_cli_mapping_rejects_unknown_values(self):
        self.assertIsNone(resolve_animation("flying"))
        self.assertIsNone(resolve_animation(""))
        self.assertIsNone(resolve_animation(None))
        self.assertIsNone(resolve_animation(7))


class TestStateDefaults(unittest.TestCase):
    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.state = State(clock=self.clock)

    def test_default_state(self):
        self.assertEqual(self.state.animation, "idle")
        self.assertEqual(self.state.messages, OrderedDict())
        self.assertIsInstance(self.state.messages, OrderedDict)
        self.assertFalse(self.state.stopping)


class TestPingShutdown(unittest.TestCase):
    def setUp(self):
        self.state = State()

    def test_ping_responds_pong(self):
        self.assertEqual(
            self.state.apply({"type": "ping"}), {"ok": True, "message": "pong"}
        )

    def test_shutdown_sets_stopping_flag(self):
        response = self.state.apply({"type": "shutdown"})
        self.assertEqual(response, {"ok": True, "message": "shutting down"})
        self.assertTrue(self.state.stopping)

    def test_ping_still_responds_after_shutdown(self):
        self.state.apply({"type": "shutdown"})
        self.assertEqual(
            self.state.apply({"type": "ping"}), {"ok": True, "message": "pong"}
        )

    def test_other_commands_rejected_after_shutdown(self):
        self.state.apply({"type": "shutdown"})
        commands = [
            {"type": "notify", "notification": {"title": "t", "status": "s"}},
            {"type": "playAnimation", "name": "idle"},
            {"type": "stopAnimation"},
            {"type": "clearMessage", "threadId": "x"},
            {"type": "clearMessages"},
        ]
        for command in commands:
            response = self.state.apply(command)
            self.assertFalse(response["ok"])
            self.assertEqual(response["message"], "pet is not running")


class TestNotify(unittest.TestCase):
    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.state = State(clock=self.clock)

    def _notify(self, notification):
        return self.state.apply({"type": "notify", "notification": notification})

    def test_notify_ok_with_generated_thread_id(self):
        response = self._notify({"title": "Hello", "status": "message"})
        self.assertTrue(response["ok"])
        self.assertIsInstance(response["threadId"], str)
        self.assertNotEqual(response["threadId"], "")
        self.assertEqual(set(response.keys()), {"ok", "threadId"})

    def test_notify_preserves_given_thread_id(self):
        response = self._notify({"title": "Hello", "status": "message", "threadId": "thread-1"})
        self.assertEqual(response["threadId"], "thread-1")
        self.assertEqual(list(self.state.messages.keys()), ["thread-1"])

    def test_notify_empty_thread_id_is_generated(self):
        response = self._notify({"title": "Hello", "status": "message", "threadId": ""})
        self.assertIsInstance(response["threadId"], str)
        self.assertNotEqual(response["threadId"], "")

    def test_notify_stores_message_fields(self):
        self._notify({
            "title": "Title",
            "text": "Body",
            "status": "attention",
            "threadId": "t1",
            "url": "https://example.com",
            "buttonLabel": "Open",
            "ttlSeconds": 30,
        })
        entry = self.state.messages["t1"]
        self.assertEqual(entry["title"], "Title")
        self.assertEqual(entry["text"], "Body")
        self.assertEqual(entry["status"], "attention")
        self.assertEqual(entry["threadId"], "t1")
        self.assertEqual(entry["url"], "https://example.com")
        self.assertEqual(entry["buttonLabel"], "Open")
        self.assertEqual(entry["ttlSeconds"], 30)
        self.assertEqual(entry["expires_at"], 1030.0)

    def test_notify_ttl_zero_means_no_expiry(self):
        self._notify({"title": "T", "status": "s", "threadId": "t1", "ttlSeconds": 0})
        self.assertIsNone(self.state.messages["t1"]["expires_at"])

    def test_notify_missing_title_rejected(self):
        response = self._notify({"status": "s"})
        self.assertFalse(response["ok"])
        self.assertIn("title", response["message"])

    def test_notify_missing_status_rejected(self):
        response = self._notify({"title": "T"})
        self.assertFalse(response["ok"])
        self.assertIn("status", response["message"])

    def test_notify_notification_not_object_rejected(self):
        response = self.state.apply({"type": "notify", "notification": "nope"})
        self.assertFalse(response["ok"])

    def test_notify_title_too_long_rejected(self):
        response = self._notify({"title": "x" * 257, "status": "s"})
        self.assertFalse(response["ok"])

    def test_notify_status_too_long_rejected(self):
        response = self._notify({"title": "T", "status": "x" * 65})
        self.assertFalse(response["ok"])

    def test_notify_text_too_long_rejected(self):
        response = self._notify({"title": "T", "status": "s", "text": "x" * 4097})
        self.assertFalse(response["ok"])

    def test_notify_ttl_negative_rejected(self):
        response = self._notify({"title": "T", "status": "s", "ttlSeconds": -1})
        self.assertFalse(response["ok"])

    def test_notify_ttl_non_number_rejected(self):
        response = self._notify({"title": "T", "status": "s", "ttlSeconds": "fast"})
        self.assertFalse(response["ok"])

    def test_notify_ttl_bool_rejected(self):
        response = self._notify({"title": "T", "status": "s", "ttlSeconds": True})
        self.assertFalse(response["ok"])

    def test_notify_thread_id_too_long_rejected(self):
        response = self._notify({"title": "T", "status": "s", "threadId": "x" * 129})
        self.assertFalse(response["ok"])

    def test_notify_status_animation_mapping(self):
        cases = {
            "error": "failed",
            "failed": "failed",
            "failure": "failed",
            "review": "review",
            "reviewing": "review",
            "waiting": "waiting",
            "queued": "waiting",
            "pending": "waiting",
            "running": "running",
            "working": "running",
            "task": "running",
            "success": "jumping",
            "done": "jumping",
            "completed": "jumping",
            "complete": "jumping",
            "committed": "jumping",
            "attention": "waving",
            "reply": "waving",
            "message": "waving",
            "weird-status": "idle",
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.state = State(clock=self.clock)
                self._notify({"title": "T", "status": status})
                self.assertEqual(self.state.animation, expected)

    def test_notify_case_insensitive_status(self):
        self._notify({"title": "T", "status": "ERROR"})
        self.assertEqual(self.state.animation, "failed")

    def test_finite_animation_keeps_playing(self):
        self._notify({"title": "T", "status": "success", "threadId": "t1", "ttlSeconds": 5})
        self.assertEqual(self.state.animation, "jumping")
        self.state.expire(1010.0)
        self.assertEqual(self.state.animation, "jumping")

    def test_notify_animation_ttl_expires_to_idle(self):
        self._notify({"title": "T", "status": "attention", "threadId": "t1", "ttlSeconds": 5})
        self.assertEqual(self.state.animation, "waving")
        self.state.expire(1004.9)
        self.assertEqual(self.state.animation, "waving")
        self.state.expire(1005.0)
        self.assertEqual(self.state.animation, "idle")

    def test_expire_removes_expired_messages_in_order(self):
        self._notify({"title": "A", "status": "s", "threadId": "a", "ttlSeconds": 3})
        self._notify({"title": "B", "status": "s", "threadId": "b"})
        self._notify({"title": "C", "status": "s", "threadId": "c", "ttlSeconds": 0})
        expired = self.state.expire(1003.0)
        self.assertEqual(expired, ["a"])
        self.assertEqual(list(self.state.messages.keys()), ["b", "c"])
        self.state.expire(1004.0)
        self.assertEqual(list(self.state.messages.keys()), ["b", "c"])

    def test_expire_default_clock(self):
        clock = _FixedClock()
        state = State(clock=clock)
        state.apply({"type": "notify", "notification": {"title": "T", "status": "s", "ttlSeconds": 0.01}})
        self.assertEqual(state.expire(), [])
        clock.value += 0.02
        thread_id = next(iter(state.messages))
        self.assertEqual(state.expire(), [thread_id])
        self.assertEqual(state.messages, OrderedDict())

    def test_notify_same_thread_id_replaces_in_place(self):
        self._notify({"title": "A", "status": "s", "threadId": "t1"})
        self._notify({"title": "B", "status": "s", "threadId": "t2"})
        self._notify({"title": "C", "status": "s", "threadId": "t1"})
        self.assertEqual(list(self.state.messages.keys()), ["t1", "t2"])
        self.assertEqual(self.state.messages["t1"]["title"], "C")


class TestFiniteAnimation(unittest.TestCase):
    """Linux UX divergence: only the completion/success family is finite;
    running/review/waiting/waving/failed/message remain animated while active."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.state = State(clock=self.clock)

    def _notify(self, notification):
        return self.state.apply({"type": "notify", "notification": notification})

    def test_finite_set_is_completion_family_only(self):
        self.assertEqual(
            FINITE_STATUS_ANIMATIONS,
            frozenset({"done", "success", "completed", "complete", "committed"}),
        )
        for status in ("running", "review", "reviewing", "waiting", "waving",
                       "failed", "message", "task", "working"):
            self.assertNotIn(status, FINITE_STATUS_ANIMATIONS)

    def test_running_remains_animated_no_animation_ttl(self):
        self._notify({"title": "T", "status": "running"})
        self.assertEqual(self.state.animation, "running")
        self.assertTrue(self.state.loop)
        self.assertIsNone(self.state._animation_expires_at)

    def test_review_remains_animated_no_animation_ttl(self):
        self._notify({"title": "T", "status": "review"})
        self.assertEqual(self.state.animation, "review")
        self.assertTrue(self.state.loop)
        self.assertIsNone(self.state._animation_expires_at)

    def test_waiting_remains_animated(self):
        self._notify({"title": "T", "status": "waiting"})
        self.assertEqual(self.state.animation, "waiting")
        self.assertIsNone(self.state._animation_expires_at)

    def test_done_finite_ignores_ttl_for_animation_expiry(self):
        self._notify({"title": "T", "status": "done", "threadId": "t1", "ttlSeconds": 5})
        self.assertEqual(self.state.animation, "jumping")
        self.assertTrue(self.state.loop)
        self.assertIsNone(self.state._animation_expires_at)
        # Expiry does not return the finite completion animation to idle.
        self.state.expire(1005.0)
        self.assertEqual(self.state.animation, "jumping")

    def test_running_with_ttl_expires_to_idle(self):
        self._notify({"title": "T", "status": "running", "threadId": "t1", "ttlSeconds": 5})
        self.assertEqual(self.state.animation, "running")
        self.state.expire(1004.9)
        self.assertEqual(self.state.animation, "running")
        self.state.expire(1005.0)
        self.assertEqual(self.state.animation, "idle")


class TestListAnimations(unittest.TestCase):
    """Discoverable animation catalog: nine canonical names, aliases, purpose, atlas row."""

    def test_list_pet_animations_returns_nine_entries(self):
        catalog = list_pet_animations()
        self.assertEqual(len(catalog), len(ANIMATIONS))
        names = [entry["name"] for entry in catalog]
        self.assertEqual(names, list(ANIMATIONS))

    def test_list_pet_animations_entry_shape(self):
        catalog = list_pet_animations()
        for entry in catalog:
            self.assertEqual(set(entry), {"name", "aliases", "purpose", "atlas_row"})
            self.assertIsInstance(entry["name"], str)
            self.assertIsInstance(entry["aliases"], list)
            self.assertTrue(all(isinstance(a, str) for a in entry["aliases"]))
            self.assertIsInstance(entry["purpose"], str)
            self.assertTrue(entry["purpose"])
            self.assertIsInstance(entry["atlas_row"], int)

    def test_list_pet_animations_atlas_row_matches_index(self):
        catalog = list_pet_animations()
        for row, name in enumerate(ANIMATIONS):
            self.assertEqual(catalog[row]["name"], name)
            self.assertEqual(catalog[row]["atlas_row"], row)

    def test_list_pet_animations_aliases(self):
        by_name = {entry["name"]: entry["aliases"] for entry in list_pet_animations()}
        self.assertEqual(by_name["running-right"], ["runningRight", "right", "runright"])
        self.assertEqual(by_name["running-left"], ["runningLeft", "left", "runleft"])
        # idle has no aliases.
        self.assertEqual(by_name["idle"], [])
        self.assertEqual(by_name["failed"], [])

    def test_list_pet_animations_no_gtk_dependency(self):
        # Importable and callable without GI/Gtk.
        import openpets_linux.core as core
        self.assertTrue(hasattr(core, "list_pet_animations"))
        catalog = core.list_pet_animations()
        self.assertEqual(len(catalog), 9)


class TestPlayAnimation(unittest.TestCase):
    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.state = State(clock=self.clock)

    def test_play_animation_ok(self):
        response = self.state.apply({"type": "playAnimation", "name": "running-right"})
        self.assertEqual(response, {"ok": True})
        self.assertEqual(self.state.animation, "running-right")

    def test_play_animation_unknown_name_rejected(self):
        response = self.state.apply({"type": "playAnimation", "name": "flying"})
        self.assertFalse(response["ok"])
        self.assertIn("animation", response["message"])

    def test_play_animation_name_not_string_rejected(self):
        response = self.state.apply({"type": "playAnimation", "name": 3})
        self.assertFalse(response["ok"])

    def test_play_animation_loop_must_be_bool(self):
        response = self.state.apply({"type": "playAnimation", "name": "idle", "loop": "yes"})
        self.assertFalse(response["ok"])

    def test_play_animation_ttl_negative_rejected(self):
        response = self.state.apply({"type": "playAnimation", "name": "idle", "ttlSeconds": -2})
        self.assertFalse(response["ok"])

    def test_play_animation_ttl_bool_rejected(self):
        response = self.state.apply({"type": "playAnimation", "name": "idle", "ttlSeconds": False})
        self.assertFalse(response["ok"])

    def test_play_animation_ttl_expires_to_idle(self):
        self.state.apply({"type": "playAnimation", "name": "jumping", "ttlSeconds": 7})
        self.assertEqual(self.state.animation, "jumping")
        self.state.expire(1006.9)
        self.assertEqual(self.state.animation, "jumping")
        self.state.expire(1007.0)
        self.assertEqual(self.state.animation, "idle")

    def test_play_animation_sets_loop_attribute(self):
        self.state.apply({"type": "playAnimation", "name": "waving", "loop": False})
        self.assertFalse(self.state.loop)
        self.state.apply({"type": "playAnimation", "name": "idle"})
        self.assertTrue(self.state.loop)

    def test_stop_animation_clears_animation_ttl(self):
        self.state.apply({"type": "playAnimation", "name": "jumping", "ttlSeconds": 7})
        self.state.apply({"type": "stopAnimation"})
        self.assertEqual(self.state.animation, "idle")
        self.assertIsNone(self.state._animation_expires_at)


class TestStopAnimation(unittest.TestCase):
    def setUp(self):
        self.state = State()

    def test_stop_animation_returns_idle(self):
        self.state.apply({"type": "playAnimation", "name": "waving"})
        self.assertEqual(self.state.animation, "waving")
        response = self.state.apply({"type": "stopAnimation"})
        self.assertEqual(response, {"ok": True})
        self.assertEqual(self.state.animation, "idle")


class TestClearMessages(unittest.TestCase):
    def setUp(self):
        self.state = State()

    def _add(self, thread_id, title):
        return self.state.apply({
            "type": "notify",
            "notification": {"title": title, "status": "s", "threadId": thread_id},
        })

    def test_clear_message_removes_one(self):
        self._add("a", "A")
        self._add("b", "B")
        self.assertEqual(self.state.apply({"type": "clearMessage", "threadId": "a"}), {"ok": True})
        self.assertEqual(list(self.state.messages.keys()), ["b"])

    def test_clear_message_unknown_thread_id_ok(self):
        self.assertEqual(self.state.apply({"type": "clearMessage", "threadId": "nope"}), {"ok": True})

    def test_clear_message_missing_thread_id_rejected(self):
        response = self.state.apply({"type": "clearMessage"})
        self.assertFalse(response["ok"])
        self.assertIn("threadId", response["message"])

    def test_clear_messages_removes_all(self):
        self._add("a", "A")
        self._add("b", "B")
        self.assertEqual(self.state.apply({"type": "clearMessages"}), {"ok": True})
        self.assertEqual(self.state.messages, OrderedDict())


class TestUnknownAndMalformedCommands(unittest.TestCase):
    def setUp(self):
        self.state = State()

    def test_unknown_type_rejected(self):
        response = self.state.apply({"type": "flutterby"})
        self.assertFalse(response["ok"])
        self.assertIn("unknown command type", response["message"])

    def test_missing_type_rejected(self):
        response = self.state.apply({})
        self.assertFalse(response["ok"])

    def test_non_dict_command_rejected(self):
        for bad in ("ping", 42, ["type", "ping"], None, 3.14):
            response = self.state.apply(bad)
            self.assertFalse(response["ok"])
            self.assertIn("message", response)


class TestLoadConfig(unittest.TestCase):
    def test_defaults_when_no_file(self):
        config = load_config("/nonexistent-dir-12345/openpets/config.json")
        self.assertEqual(
            config["socketPath"], f"/tmp/openpets-{os.getuid()}.sock"
        )
        self.assertEqual(
            config["display"],
            {"scale": DEFAULT_SCALE, "messageAreaHeight": DEFAULT_MESSAGE_AREA_HEIGHT},
        )

    def test_defaults_do_not_write_file(self):
        path = "/nonexistent-dir-54321/openpets/config.json"
        load_config(path)
        self.assertFalse(os.path.exists(path))

    def _write_config(self, content):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        return path

    def test_config_file_overrides_defaults(self):
        path = self._write_config(
            json.dumps({
                "socketPath": "/tmp/custom.sock",
                "display": {"scale": 0.84, "messageAreaHeight": 112},
            })
        )
        try:
            config = load_config(path)
            self.assertEqual(config["socketPath"], "/tmp/custom.sock")
            self.assertEqual(
                config["display"], {"scale": 0.84, "messageAreaHeight": 112}
            )
        finally:
            os.remove(path)

    def test_partial_display_keeps_defaults(self):
        path = self._write_config(json.dumps({"display": {"scale": 1.0}}))
        try:
            config = load_config(path)
            self.assertEqual(config["display"]["scale"], 1.0)
            self.assertEqual(config["display"]["messageAreaHeight"], DEFAULT_MESSAGE_AREA_HEIGHT)
        finally:
            os.remove(path)

    def test_invalid_json_falls_back_to_defaults(self):
        path = self._write_config("{ not valid json")
        try:
            config = load_config(path)
            self.assertEqual(config["socketPath"], f"/tmp/openpets-{os.getuid()}.sock")
        finally:
            os.remove(path)

    def test_non_object_json_falls_back_to_defaults(self):
        path = self._write_config("[1, 2, 3]")
        try:
            config = load_config(path)
            self.assertEqual(config["socketPath"], f"/tmp/openpets-{os.getuid()}.sock")
        finally:
            os.remove(path)

    def test_xdg_config_home_used_without_explicit_path(self):
        home = tempfile.mkdtemp(prefix="openpets-xdg-")
        try:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": home}):
                config = load_config()
                expected = os.path.join(home, "openpets", "config.json")
                self.assertFalse(os.path.exists(expected))
                self.assertEqual(
                    config["socketPath"], f"/tmp/openpets-{os.getuid()}.sock"
                )
        finally:
            os.rmdir(home)

    def test_max_command_size_constant(self):
        self.assertEqual(MAX_COMMAND_SIZE, 64 * 1024)


if __name__ == "__main__":
    unittest.main()
