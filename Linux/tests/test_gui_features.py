"""GTK menu/action integration without real account access."""

import importlib.util
import os
import socket
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

HAS_DISPLAY = bool(os.environ.get("DISPLAY")) and importlib.util.find_spec("gi") is not None


@unittest.skipUnless(HAS_DISPLAY, "GTK tests require DISPLAY (use Xvfb)")
class GUIFeatures(unittest.TestCase):
    def test_plugin_refresh_now_only_refreshes_enabled_providers(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.plugin_ui import PROVIDERS
        from openpets_linux.ui import PetWindow

        with tempfile.TemporaryDirectory(prefix="openpets-plugin-refresh-") as directory:
            environment = {"HOME": directory, "XDG_CONFIG_HOME": directory + "/config",
                           "XDG_DATA_HOME": directory + "/data",
                           "DBUS_SESSION_BUS_ADDRESS": ""}
            calls = []
            fixture = {"id": "battery", "label": "Battery", "value": "73%",
                       "details": [], "state": "normal"}
            def provider():
                calls.append(True)
                return fixture
            with patch.dict(os.environ, environment):
                window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
                try:
                    window.start()
                    with patch.dict(PROVIDERS, {"battery": ("Battery", provider)}):
                        window.plugins.set_enabled("battery", True)
                        deadline = time.monotonic() + 2
                        while window.plugins.busy and time.monotonic() < deadline:
                            while GLib.MainContext.default().pending():
                                GLib.MainContext.default().iteration(False)
                            time.sleep(.01)
                        initial = len(calls)
                        self.assertEqual(initial, 1)
                        self.assertFalse(window.plugins.enabled["codex"])
                        window.plugins.refresh_enabled()
                        deadline = time.monotonic() + 2
                        while window.plugins.busy and time.monotonic() < deadline:
                            while GLib.MainContext.default().pending():
                                GLib.MainContext.default().iteration(False)
                            time.sleep(.01)
                        self.assertEqual(len(calls), initial + 1)
                        self.assertNotIn("codex", window.plugins.snapshots)
                        self.assertEqual(window.plugins.surfaces()[0]["value"], "73%")
                finally:
                    window.cleanup()

    def test_chooser_notifications_actions_and_plugin_consent(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.notification_ui import NotificationCenter
        from openpets_linux.ui import PetWindow
        from openpets_linux.onboarding_ui import OpenCodeSetup
        from openpets_linux.plugin_ui import PROVIDERS
        with tempfile.TemporaryDirectory(prefix="openpets-gui-") as directory:
            environment = {"HOME": directory, "XDG_CONFIG_HOME": directory + "/config",
                           "XDG_DATA_HOME": directory + "/data"}
            with patch.dict(os.environ, environment):
                window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
                try:
                    window.start()
                    window.open_chooser()
                    self.assertGreater(len(window.chooser.entries), 0)
                    window.chooser.on_select(None)
                    window.chooser.destroy()
                    onboarding = OpenCodeSetup(window, directory + "/host.sock", {})
                    self.assertIn('"servers"', onboarding.fragment)
                    onboarding.destroy()  # Do not touch the real clipboard/config.
                    result = window.state.apply({"type": "notify", "notification": {
                        "title": "<b>plain text</b>", "text": "Details", "status": "review",
                        "url": "https://example.com/review", "buttonLabel": "Review"
                    }})
                    window.refresh()
                    opened = []
                    center = NotificationCenter(window, window.history, window.dismiss_message, opened.append)
                    self.assertEqual(opened, [])
                    center._open_action(None, "file:///etc/passwd")
                    self.assertEqual(opened, [])
                    center._open_action(None, "https://example.com/review")
                    self.assertEqual(opened, ["https://example.com/review"])
                    center._dismiss(None, result["threadId"])
                    self.assertEqual(len(window.state.messages), 0)
                    center.destroy()
                    self.assertTrue(center.closed)
                    window.open_notifications()
                    window.notification_center.destroy()
                    window.open_notifications()  # Reopening a destroyed center works.
                    window.notification_center.destroy()
                    menu = window.plugins.menu()
                    claude = menu.get_children()[1]
                    with patch.object(Gtk.MessageDialog, "run", return_value=Gtk.ResponseType.NO):
                        with patch.dict(PROVIDERS, {"claude": ("Claude quota", lambda: self.fail("No consent"))}):
                            claude.set_active(True)
                    self.assertFalse(claude.get_active())
                    self.assertFalse(window.plugins.enabled["claude"])
                    fixture = {"id": "battery", "label": "Battery", "value": "73%", "details": [], "state": "normal"}
                    with patch.dict(PROVIDERS, {"battery": ("Battery", lambda: fixture)}):
                        window.plugins.set_enabled("battery", True)
                        deadline = time.monotonic() + 2
                        while window.plugins.busy and time.monotonic() < deadline:
                            while GLib.MainContext.default().pending():
                                GLib.MainContext.default().iteration(False)
                            time.sleep(.01)
                        self.assertEqual(window.plugins.surfaces()[0]["value"], "73%")
                        window.plugins.show_details()
                        window.plugins.dialog.destroy()
                        window.plugins.set_enabled("battery", False)
                    if importlib.util.find_spec("mcp") is not None:
                        with socket.socket() as probe:
                            probe.bind(("127.0.0.1", 0))
                            port = probe.getsockname()[1]
                        window.settings.update({"mcpPort": port})
                        window.mcp_item.set_active(True)
                        self.assertTrue(window.mcp_process.running())
                        deadline = time.monotonic() + 8
                        ready = False
                        while time.monotonic() < deadline:
                            with socket.socket() as probe:
                                if probe.connect_ex(("127.0.0.1", port)) == 0:
                                    ready = True
                                    break
                            time.sleep(.05)
                        self.assertTrue(ready)
                        process = window.mcp_process.process
                        window.mcp_item.set_active(False)
                        self.assertFalse(window.mcp_process.running())
                        self.assertIsNotNone(process.poll())
                    menu.destroy()
                finally:
                    window.cleanup()
                self.assertFalse(Path(directory + "/host.sock").exists())
