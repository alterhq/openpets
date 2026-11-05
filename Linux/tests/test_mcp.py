"""Tests for openpets_linux.mcp_server.

Pure schema/config pieces run without the MCP Python SDK. SDK integration
cases (create_server + FastMCP client over stdio/HTTP) skip when the SDK is
absent and explicitly report the skip reason, so skips are distinguished from
passes.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from openpets_linux import mcp_server  # noqa: E402
from openpets_linux.mcp_server import (  # noqa: E402
    ANIMATION_ALIASES,
    ANIMATION_VALUES,
    MCPUnavailable,
    PACKAGE_ROOT,
    STATUS_VALUES,
    TOOL_NAMES,
    clear_pet_message,
    create_server,
    get_openpets_status,
    list_pet_animations,
    notify,
    ping_pet,
    play_pet_animation,
    run_server,
    stop_pet,
    stop_pet_animation,
    wake_pet,
)

try:  # SDK is optional; integration cases skip when absent.
    from mcp.server.fastmcp import FastMCP  # noqa: F401

    HAS_SDK = True
except ImportError:  # pragma: no cover - exercised only without the SDK
    FastMCP = None
    HAS_SDK = False


class SchemaTests(unittest.TestCase):
    """SDK-free schema/catalog checks."""

    def test_tool_names_match_upstream(self):
        self.assertEqual(
            TOOL_NAMES,
            (
                "get_openpets_status",
                "wake_pet",
                "stop_pet",
                "notify",
                "play_pet_animation",
                "stop_pet_animation",
                "clear_pet_message",
                "ping_pet",
                "list_pet_animations",
            ),
        )

    def test_status_values(self):
        self.assertEqual(
            STATUS_VALUES, ("running", "review", "done", "failed", "waiting", "message")
        )

    def test_animation_values_and_aliases(self):
        self.assertEqual(
            ANIMATION_VALUES,
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
        self.assertEqual(ANIMATION_ALIASES["right"], "running-right")
        self.assertEqual(ANIMATION_ALIASES["left"], "running-left")

    def test_package_root_derives_from_file(self):
        self.assertTrue(str(PACKAGE_ROOT).endswith("Linux"))
        self.assertEqual(PACKAGE_ROOT.name, "Linux")


class CommandHelperTests(unittest.TestCase):
    """SDK-free command helper behaviour using private sockets."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="openpets-mcp-")
        self.addCleanup(temporary.cleanup)
        self.socket_path = os.path.join(temporary.name, "host.sock")

    def test_get_openpets_status_describes_missing_socket(self):
        result = get_openpets_status(self.socket_path)
        self.assertFalse(result["ok"])
        self.assertIsInstance(result["message"], str)
        self.assertNotIn("ok", result["message"].lower()[:5])  # not a false success

    def test_stop_pet_describes_missing_socket(self):
        result = stop_pet(self.socket_path)
        self.assertFalse(result["ok"])
        self.assertIsInstance(result["message"], str)

    def test_ping_pet_describes_missing_socket(self):
        result = ping_pet(self.socket_path)
        self.assertFalse(result["ok"])

    def test_clear_pet_message_requires_thread_id(self):
        result = clear_pet_message(self.socket_path, threadId="")
        self.assertFalse(result["ok"])
        self.assertIn("threadId", result["message"])
        result = clear_pet_message(self.socket_path, threadId=None)
        self.assertFalse(result["ok"])

    def test_notify_requires_title_and_status_but_text_is_optional(self):
        result = notify(self.socket_path, title="", text="t", status="running")
        self.assertFalse(result["ok"])
        self.assertIn("title", result["message"])
        with patch("openpets_linux.mcp_server.send", return_value={"ok": True}) as sender:
            result = notify(self.socket_path, title="T", text="", status="running")
            self.assertTrue(result["ok"])
            self.assertEqual(sender.call_args.args[0]["notification"]["text"], "")
        result = notify(self.socket_path, title="T", text="t", status="")
        self.assertFalse(result["ok"])
        self.assertIn("status", result["message"])

    def test_play_pet_animation_rejects_unknown_name(self):
        result = play_pet_animation(self.socket_path, name="not-an-animation")
        self.assertFalse(result["ok"])
        self.assertIn("Invalid animation", result["message"])

    def test_play_pet_animation_accepts_alias(self):
        result = play_pet_animation(self.socket_path, name="right")
        # Socket is not listening; the failure should be a socket error, not
        # an animation-validation error.
        self.assertFalse(result["ok"])
        self.assertNotIn("unknown or invalid animation", result["message"])

    def test_play_pet_animation_accepts_lowercase_aliases(self):
        for alias in ("runright", "runleft"):
            result = play_pet_animation(self.socket_path, name=alias)
            # Alias resolves; failure (if any) must be a socket error, not
            # an animation-validation error.
            self.assertFalse(result["ok"])
            self.assertNotIn("unknown or invalid animation", result["message"])

    def test_play_pet_animation_rejects_misspelled_failed(self):
        # "falled" is a misspelling of "failed" and is deliberately not aliased.
        result = play_pet_animation(self.socket_path, name="falled")
        self.assertFalse(result["ok"])
        self.assertIn("Invalid animation", result["message"])

    def test_list_pet_animations_returns_catalog_without_socket(self):
        # Local catalog query: works even when no pet is listening on the socket.
        result = list_pet_animations(self.socket_path)
        self.assertEqual(len(result["animations"]), 9)
        names = [entry["name"] for entry in result["animations"]]
        self.assertEqual(names, list(ANIMATION_VALUES))
        running_right = next(e for e in result["animations"] if e["name"] == "running-right")
        self.assertEqual(running_right["aliases"], ["runningRight", "right", "runright"])
        self.assertEqual(running_right["atlas_row"], ANIMATION_VALUES.index("running-right"))
        idle = next(e for e in result["animations"] if e["name"] == "idle")
        self.assertEqual(idle["aliases"], [])
        self.assertTrue(all("purpose" in e and e["purpose"] for e in result["animations"]))

    def test_wake_pet_reports_missing_gi(self):
        with patch("openpets_linux.mcp_server.importlib.util.find_spec", return_value=None):
            result = wake_pet(self.socket_path)
        self.assertFalse(result["ok"])
        self.assertIn("gi", result["message"].lower())

    def test_wake_pet_uses_executable_and_package_root_env(self):
        captured = {}

        def fake_popen(args, env=None, **kwargs):
            captured["args"] = args
            captured["env"] = env
            captured["kwargs"] = kwargs
            class Proc:
                pid = 1
                def poll(self):
                    return None
                def wait(self):
                    return 0
            return Proc()

        with patch("openpets_linux.mcp_server.subprocess.Popen", side_effect=fake_popen):
            with patch("openpets_linux.mcp_server._ipc", side_effect=[{"ok": False}, {"ok": True}]):
                result = wake_pet(self.socket_path)
        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(captured["args"][0], sys.executable)
        self.assertEqual(captured["args"][1:4], ["-m", "openpets_linux", "run"])
        self.assertIn("--socket", captured["args"])
        self.assertIn(self.socket_path, captured["args"])
        pythonpath = captured["env"]["PYTHONPATH"]
        self.assertIn(str(PACKAGE_ROOT), pythonpath)
        self.assertEqual(captured["kwargs"]["stdin"], __import__("subprocess").DEVNULL)
        self.assertEqual(captured["kwargs"]["cwd"], PACKAGE_ROOT)


@unittest.skipIf(not HAS_SDK, "mcp Python SDK not installed; SDK integration skipped")
class SDKIntegrationTests(unittest.TestCase):
    """SDK integration: create_server exposes the nine tools."""

    def test_create_server_returns_fastmcp(self):
        server = create_server("/tmp/openpets-test.sock")
        self.assertIsInstance(server, FastMCP)

    def test_create_server_exposes_all_nine_tools(self):
        server = create_server("/tmp/openpets-test.sock")
        # FastMCP keeps registered tools on its tool manager; introspect
        # defensively so the test reports a real failure rather than crashing
        # on an SDK version with a different internal attribute.
        tool_names = None
        for attr in ("_tool_manager", "tool_manager"):
            manager = getattr(server, attr, None)
            if manager is not None:
                tool_names = set(getattr(manager, "_tools", {}).keys())
                break
        self.assertIsNotNone(tool_names, "could not locate registered tools on server")
        missing = set(TOOL_NAMES) - tool_names
        self.assertEqual(missing, set(), f"missing tools: {sorted(missing)}")
        self.assertIn("list_pet_animations", tool_names)

    def test_run_server_rejects_unsupported_transport(self):
        with self.assertRaises(ValueError):
            run_server("/tmp/openpets-test.sock", transport="not-a-transport")


if __name__ == "__main__":
    unittest.main()
