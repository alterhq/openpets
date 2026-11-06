"""Real GLib-source regressions for collapsed previews held by queued bubbles.

Run under Xvfb. These tests use the actual IPC listener, layout and timer
callbacks; only the next source deadline is advanced to avoid waiting seconds.
"""

import importlib.util
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAS_DISPLAY = bool(os.environ.get("DISPLAY")) and importlib.util.find_spec("gi") is not None


@unittest.skipUnless(HAS_DISPLAY, "GTK tests require DISPLAY (use Xvfb)")
class PreviewTimerGUITests(unittest.TestCase):
    def setUp(self):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.settings import Settings
        from openpets_linux.ui import GLib, PetWindow

        temporary = tempfile.TemporaryDirectory(prefix="openpets-preview-timer-")
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        environment = patch.dict(os.environ, {
            "HOME": directory,
            "XDG_CONFIG_HOME": directory + "/config",
            "XDG_DATA_HOME": directory + "/data",
            "XDG_STATE_HOME": directory + "/state",
            "DBUS_SESSION_BUS_ADDRESS": "",
            "SESSION_MANAGER": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.socket_path = directory + "/isolated.sock"
        Settings().update({
            "socketPath": self.socket_path,
            "linuxMCPEnabled": False,
            "linuxPlugins": {"chatBubble": True},
            "motionMode": "off",
            "display": {"messagesCollapsed": True,
                        "chatBubbleDurationSeconds": 8,
                        "collapsedMessagePreviewSeconds": 10},
        })
        self.window = PetWindow(bundled_starcorn_path(), .37, self.socket_path)
        self.addCleanup(self.window.cleanup)
        self.context = GLib.MainContext.default()
        self.preview_sources = []
        original_add = GLib.timeout_add_seconds

        def track_source(interval, callback, *args, **kwargs):
            tag = original_add(interval, callback, *args, **kwargs)
            if getattr(callback, "__name__", "") == "_preview_sweep":
                self.preview_sources.append(tag)
            return tag

        tracking = patch.object(GLib, "timeout_add_seconds", side_effect=track_source)
        tracking.start()
        self.addCleanup(tracking.stop)
        self.addCleanup(self._remove_preview_sources)
        self.window.start()
        self._send_burst()

    def _remove_preview_sources(self):
        from openpets_linux.ui import GLib

        for tag in self.preview_sources:
            if self.context.find_source_by_id(tag) is not None:
                GLib.source_remove(tag)
        self.window.preview_source = 0

    def _pump_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.context.iteration(False)
            time.sleep(.001)
        self.assertTrue(predicate(), "GTK loop did not reach the expected state")

    def _send_burst(self):
        from openpets_linux.ipc import send

        replies, errors = [], []

        def client():
            try:
                for index, status in enumerate(("running", "review", "done")):
                    replies.append(send({
                        "type": "notify",
                        "notification": {"title": "Synthetic timer regression",
                                         "text": "Synthetic update " + str(index),
                                         "status": status, "kind": "chat",
                                         "threadId": "timer-regression"},
                    }, self.socket_path))
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=client, daemon=True)
        worker.start()
        self._pump_until(lambda: not worker.is_alive())
        worker.join(timeout=1)
        self.assertEqual(errors, [])
        self.assertEqual(len(replies), 3)
        self.assertTrue(all(reply.get("ok") for reply in replies))
        self.assertEqual(self.window.plugins.chat_bubble.pending_count, 2)
        self.assertEqual(len(self.window.preview_messages), 1)

    def _dispatch_preview_once(self):
        previous = self.window.preview_source
        source = self.context.find_source_by_id(previous)
        self.assertIsNotNone(source)
        source.set_ready_time(0)
        self._pump_until(lambda: self.window.preview_source != previous)
        return previous

    def test_held_preview_replaces_rather_than_duplicates_its_timer(self):
        for _ in range(4):
            previous = self._dispatch_preview_once()
            self.assertIsNone(
                self.context.find_source_by_id(previous),
                "Old repeating preview source survived explicit rescheduling",
            )
            live = [tag for tag in self.preview_sources
                    if self.context.find_source_by_id(tag) is not None]
            self.assertEqual(live, [self.window.preview_source])
            self.assertEqual(len(self.window.preview_messages), 1)

    def test_cancelling_latest_preview_timer_leaves_no_orphan_sources(self):
        self._dispatch_preview_once()
        self.window.preview_messages.clear()
        self.window._reschedule_preview_sweep()
        self.assertEqual(self.window.preview_source, 0)
        live = [tag for tag in self.preview_sources
                if self.context.find_source_by_id(tag) is not None]
        self.assertEqual(live, [], "An untracked preview source is still alive")

    def test_expired_preview_is_removed_after_final_queued_bubble(self):
        self._dispatch_preview_once()
        for preview in self.window.preview_messages.values():
            preview["expires_at"] = time.monotonic() - 1
        self._dispatch_preview_once()
        self.assertTrue(self.window._chat_bubble_queue_active())
        self.assertEqual(len(self.window.preview_messages), 1,
                         "Expired preview must stay while queued bubbles are visible")
        for _ in range(3):
            source = self.context.find_source_by_id(self.window.chat_bubble_source)
            self.assertIsNotNone(source)
            previous = self.window.chat_bubble_source
            source.set_ready_time(0)
            self._pump_until(lambda: self.window.chat_bubble_source != previous)
        self.assertFalse(self.window._chat_bubble_queue_active())
        self.assertEqual(len(self.window.preview_messages), 0)
        previous = self.window.preview_source
        self.context.find_source_by_id(previous).set_ready_time(0)
        self._pump_until(lambda: self.window.preview_source == 0)
        live = [tag for tag in self.preview_sources
                if self.context.find_source_by_id(tag) is not None]
        self.assertEqual(live, [])


if __name__ == "__main__":
    unittest.main()
