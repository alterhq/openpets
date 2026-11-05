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
    def test_settings_persist_and_apply_together(self):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow

        with tempfile.TemporaryDirectory(prefix="openpets-settings-") as directory:
            environment = {"HOME": directory, "XDG_CONFIG_HOME": directory + "/config",
                           "XDG_DATA_HOME": directory + "/data",
                           "DBUS_SESSION_BUS_ADDRESS": ""}
            with patch.dict(os.environ, environment):
                window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
                try:
                    window.start()
                    # One Settings entry applies display + appearance together.
                    window.apply_settings({
                        "themeMode": "light", "pluginOpacity": .6,
                        "messageOpacity": .7, "borderRadius": 20,
                        "blurBackground": False, "scale": .6,
                        "messageBubbleWidth": 360, "messageFontSize": 14,
                    })
                    window.state.apply({"type": "notify", "notification": {
                        "title": "Build", "status": "running"}})
                    window.refresh()
                    window.toggle_messages_collapsed(True)
                    self.assertEqual(window.layout.theme_mode, "light")
                    self.assertEqual(window.layout.plugin_opacity, .6)
                    self.assertEqual(window.layout.message_opacity, .7)
                    self.assertEqual(window.layout.border_radius, 20)
                    self.assertTrue(window.layout.messages_collapsed)
                    self.assertIsNotNone(window.layout.message_toggle_rect)
                    self.assertEqual(window.layout.unread_count, 1)
                    saved = window.settings.read()["display"]
                    self.assertEqual(saved["themeMode"], "light")
                    self.assertEqual(saved["pluginOpacity"], .6)
                    self.assertEqual(saved["messageOpacity"], .7)
                    self.assertEqual(saved["borderRadius"], 20)
                    self.assertEqual(saved["scale"], .6)
                    self.assertEqual(saved["messageBubbleWidth"], 360)
                    self.assertEqual(saved["messageFontSize"], 14)
                    self.assertTrue(saved["messagesCollapsed"])
                    window.toggle_messages_collapsed(False)
                    self.assertIsNotNone(window.layout.message_toggle_rect)
                    self.assertEqual(window.unread_message_count, 0)
                    self.assertEqual(window.message_bubble_width, 360)
                    self.assertEqual(window.layout.message_font_size, 14)
                    self.assertGreater(window.cell_width, 80)
                finally:
                    window.cleanup()

    def test_settings_fractional_opacity_zero_to_one(self):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow

        with tempfile.TemporaryDirectory(prefix="openpets-opacity-") as directory:
            environment = {"HOME": directory, "XDG_CONFIG_HOME": directory + "/config",
                           "XDG_DATA_HOME": directory + "/data",
                           "DBUS_SESSION_BUS_ADDRESS": ""}
            with patch.dict(os.environ, environment):
                window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
                try:
                    window.start()
                    # Fractional opacity across the full 0.0..1.0 range
                    # validates, persists and renders continuously.
                    window.apply_settings({
                        "themeMode": "dark", "pluginOpacity": 0.37,
                        "messageOpacity": 0.05, "borderRadius": 14,
                        "blurBackground": False, "scale": 0.42,
                        "messageBubbleWidth": 240, "messageFontSize": 10.0,
                    })
                    self.assertEqual(window.layout.plugin_opacity, 0.37)
                    self.assertEqual(window.layout.message_opacity, 0.05)
                    saved = window.settings.read()["display"]
                    self.assertEqual(saved["pluginOpacity"], 0.37)
                    self.assertEqual(saved["messageOpacity"], 0.05)
                    # Values outside 0.0..1.0 clamp at the boundary instead of
                    # quantizing to 0 or 1.
                    window.apply_settings({
                        "themeMode": "dark", "pluginOpacity": 1.5,
                        "messageOpacity": -0.25, "borderRadius": 14,
                        "blurBackground": False, "scale": 0.42,
                        "messageBubbleWidth": 240, "messageFontSize": 10.0,
                    })
                    self.assertEqual(window.layout.plugin_opacity, 1.0)
                    self.assertEqual(window.layout.message_opacity, 0.0)
                    saved = window.settings.read()["display"]
                    self.assertEqual(saved["pluginOpacity"], 1.0)
                    self.assertEqual(saved["messageOpacity"], 0.0)
                finally:
                    window.cleanup()

    def test_settings_plugin_side_left_and_right(self):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow

        with tempfile.TemporaryDirectory(prefix="openpets-side-") as directory:
            environment = {"HOME": directory, "XDG_CONFIG_HOME": directory + "/config",
                           "XDG_DATA_HOME": directory + "/data",
                           "DBUS_SESSION_BUS_ADDRESS": ""}
            with patch.dict(os.environ, environment):
                window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
                try:
                    window.start()
                    self.assertEqual(window.plugin_side, "right")
                    self.assertEqual(window.layout.plugin_side, "right")
                    window.apply_settings({
                        "themeMode": "dark", "pluginOpacity": .94,
                        "messageOpacity": .94, "borderRadius": 14,
                        "blurBackground": False, "scale": .42,
                        "messageBubbleWidth": 240, "messageFontSize": 10.0,
                        "pluginSide": "left",
                    })
                    self.assertEqual(window.plugin_side, "left")
                    self.assertEqual(window.layout.plugin_side, "left")
                    saved = window.settings.read()["display"]
                    self.assertEqual(saved["pluginSide"], "left")
                    window.apply_settings({
                        "themeMode": "dark", "pluginOpacity": .94,
                        "messageOpacity": .94, "borderRadius": 14,
                        "blurBackground": False, "scale": .42,
                        "messageBubbleWidth": 240, "messageFontSize": 10.0,
                        "pluginSide": "bogus",
                    })
                    # Invalid side falls back to right.
                    self.assertEqual(window.plugin_side, "right")
                    self.assertEqual(window.layout.plugin_side, "right")
                    saved = window.settings.read()["display"]
                    self.assertEqual(saved["pluginSide"], "right")
                finally:
                    window.cleanup()

    def test_settings_collapsed_message_preview_seconds(self):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow

        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            environment = {"HOME": directory, "XDG_CONFIG_HOME": directory + "/config",
                           "XDG_DATA_HOME": directory + "/data",
                           "DBUS_SESSION_BUS_ADDRESS": ""}
            with patch.dict(os.environ, environment):
                window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
                try:
                    window.start()
                    self.assertEqual(window.collapsed_message_preview_seconds, 5)
                    window.apply_settings({
                        "themeMode": "dark", "pluginOpacity": .94,
                        "messageOpacity": .94, "borderRadius": 14,
                        "blurBackground": False, "scale": .42,
                        "messageBubbleWidth": 240, "messageFontSize": 10.0,
                        "pluginSide": "right",
                        "collapsedMessagePreviewSeconds": 30,
                    })
                    self.assertEqual(window.collapsed_message_preview_seconds, 30)
                    saved = window.settings.read()["display"]
                    self.assertEqual(saved["collapsedMessagePreviewSeconds"], 30)
                    # Clamped to the 1..60 range at the apply layer.
                    window.apply_settings({
                        "themeMode": "dark", "pluginOpacity": .94,
                        "messageOpacity": .94, "borderRadius": 14,
                        "blurBackground": False, "scale": .42,
                        "messageBubbleWidth": 240, "messageFontSize": 10.0,
                        "pluginSide": "right",
                        "collapsedMessagePreviewSeconds": 0,
                    })
                    self.assertEqual(window.collapsed_message_preview_seconds, 1)
                    self.assertEqual(window.settings.read()["display"]["collapsedMessagePreviewSeconds"], 1)
                finally:
                    window.cleanup()

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
