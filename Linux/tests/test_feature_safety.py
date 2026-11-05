import asyncio
import importlib.util
import json
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpets_linux import plugins
from openpets_linux.mcp_server import validate_server
from openpets_linux.settings import Settings
from openpets_linux.mcp_runtime import opencode_configuration


class FeatureSafety(unittest.TestCase):
    def test_invalid_settings_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{invalid user JSON")
            settings = Settings(path)
            with self.assertRaises(ValueError):
                settings.update({"activePetID": "starcorn"})
            self.assertEqual(path.read_text(), "{invalid user JSON")

    def test_settings_deep_merge_and_independent_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory) / "config.json")
            settings.read()["display"]["scale"] = 999
            self.assertEqual(settings.read()["display"]["scale"], .42)
            settings.update({"unrelated": "keep", "display": {"scale": .7, "other": 1}})
            settings.update({"display": {"scale": .5}})
            self.assertEqual(settings.read()["unrelated"], "keep")
            self.assertEqual(settings.read()["display"]["other"], 1)

    def test_lock_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory) / "config.json")
            victim = Path(directory) / "victim"
            victim.write_text("preserve")
            settings._lock_path.symlink_to(victim)
            with self.assertRaises(OSError):
                settings.update({"foo": 1})
            self.assertEqual(victim.read_text(), "preserve")

    def test_corrupt_positions_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory) / "config.json")
            settings.positions_path.write_text("broken positions")
            with self.assertRaises(ValueError):
                settings.set_position("starcorn", 20, 30)
            self.assertEqual(settings.positions_path.read_text(), "broken positions")

    def test_quota_redirects_never_followed(self):
        from urllib.request import Request
        handler = plugins._SameHostRedirectHandler()
        original = Request(plugins.CODEX_USAGE_URL, headers={"Authorization": "Bearer fixture"})
        for destination in ("https://chatgpt.com:8443/elsewhere", "https://evil.example/",
                            "http://chatgpt.com/", "https://chatgpt.com/other"):
            self.assertIsNone(handler.redirect_request(original, None, 302, "Found", {}, destination))

    def test_loopback_http_only(self):
        for host in ("0.0.0.0", "example.com", "192.168.1.1"):
            with self.assertRaises(ValueError):
                validate_server(host, 3001, "/mcp")
        validate_server("127.0.0.1", 3001, "/mcp")

    def test_gui_mcp_refuses_another_listeners_port(self):
        from openpets_linux.mcp_runtime import MCPProcess
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            owned = MCPProcess()
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, rf"port {port} on 127\.0\.0\.1 is already in use") as caught:
                owned.start("/unused.sock", {"mcpPort": port})
            self.assertIsNone(owned.process)
            self.assertIn(f"ss -ltnp 'sport = :{port}'", str(caught.exception))
            self.assertIn("update the MCP client URL", str(caught.exception))

    def test_gui_mcp_does_not_misreport_other_bind_errors_as_port_conflicts(self):
        from openpets_linux.mcp_runtime import MCPProcess
        with patch("openpets_linux.mcp_runtime.socket.socket") as make_socket:
            probe = make_socket.return_value.__enter__.return_value
            probe.bind.side_effect = OSError(13, "Permission denied")
            with self.assertRaisesRegex(RuntimeError, "Cannot check MCP port 3001") as caught:
                MCPProcess().start("/unused.sock", {})
        self.assertNotIn("already in use", str(caught.exception))
        self.assertIn("Permission denied", str(caught.exception))

    def test_opencode_v2_shape(self):
        config = opencode_configuration("/unused.sock")
        server = config["mcp"]["servers"]["openpets"]
        self.assertEqual(server["type"], "remote")
        self.assertNotIn("enabled", server)

    def test_bundled_id_cannot_be_shadowed(self):
        from openpets_linux.library import PetLibrary, bundled_starcorn_path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pet = root / "pets/starcorn"
            pet.mkdir(parents=True)
            (pet / "sprite.png").write_bytes(b"fixture")
            (pet / "pet.json").write_text(json.dumps({"id": "starcorn", "displayName": "Override",
                                                     "spritesheetPath": "sprite.png"}))
            library = PetLibrary(Settings(root / "config.json"))
            library._roots = (root / "pets",)
            self.assertEqual([entry["directory"] for entry in library.list_pets()
                              if entry["id"] == "starcorn"], [str(bundled_starcorn_path())])

    def test_codex_actual_window_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / "auth.json"
            auth.write_text(json.dumps({"tokens": {"access_token": "fixture-secret"}}))
            body = {"rate_limit": {"primary_window": {"used_percent": 25,
                     "limit_window_seconds": 18000, "reset_after_seconds": 2000}}}
            value = plugins.read_codex_usage(str(auth), lambda *_: {"status": 200, "body": json.dumps(body)})
            self.assertEqual(value["windows"][0]["value"], "75%")
            self.assertEqual(value["windows"][0]["label"], "Codex 5h")
            self.assertNotIn("fixture-secret", json.dumps(value))

    def test_plugin_nonfinite_fields_and_sanitized_fetch_errors(self):
        for value in (float("inf"), float("nan"), 10**400, "inf"):
            self.assertIsNone(plugins._to_int_percent(value))
            self.assertIsNone(plugins._to_number(value))
        with tempfile.TemporaryDirectory() as directory:
            auth = Path(directory) / "auth.json"
            auth.write_text(json.dumps({"tokens": {"access_token": "fixture-secret"}}))
            def fail(*_):
                raise RuntimeError("fixture-secret")
            value = plugins.read_codex_usage(str(auth), fail)
            self.assertNotIn("fixture-secret", json.dumps(value))
            self.assertEqual(value["value"], "Unavailable")
