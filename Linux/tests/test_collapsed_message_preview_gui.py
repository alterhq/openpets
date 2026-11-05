"""Deterministic GTK tests for collapsed-message transient previews.

These tests exercise the preview lifecycle in :class:`openpets_linux.ui.PetWindow`:

* default 5-second TTL consumed from ``self.collapsed_message_preview_seconds``,
* previews only while collapsed,
* per-thread independent expiry,
* insertion order (older above, newer/updated at the bottom),
* same-thread update moves the preview to the bottom and resets its timer,
* expiry removes the preview,
* expanding clears all previews.

Timing is deterministic: :func:`time.monotonic` is replaced with a fake clock and
the expiry sweep is invoked directly, so no real sleeps are required.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import importlib.util

HAS_DISPLAY = bool(os.environ.get("DISPLAY")) and importlib.util.find_spec("gi") is not None

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)


def _notify(thread_id="t1", title="Build", status="running", text=None):
    return {
        "type": "notify",
        "notification": {
            "title": title,
            "text": text,
            "status": status,
            "threadId": thread_id,
        },
    }


class _FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value


@unittest.skipUnless(HAS_DISPLAY, "GTK tests require DISPLAY (use Xvfb)")
class TestCollapsedMessagePreviewGUI(unittest.TestCase):
    def _make_window(self, directory):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow

        environment = {"HOME": directory, "XDG_CONFIG_HOME": directory + "/config",
                       "XDG_DATA_HOME": directory + "/data",
                       "DBUS_SESSION_BUS_ADDRESS": ""}
        with patch.dict(os.environ, environment):
            window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
        # Isolate lifecycle tests from geometry/layout (overlay.py signature is
        # owned by work item C); the preview state itself is what we assert on.
        window.relayout = lambda *a, **k: None
        return window

    def _collapse(self, window):
        """Seed a message row then collapse so previews become active.

        The caller must have already called ``window.start()``.
        """
        window.handle_command(_notify("seed", "Seed message", "done"))
        window.refresh()
        self.assertTrue(window.message_rows)
        window.toggle_messages_collapsed(True)
        self.assertTrue(window.appearance.get("messagesCollapsed"))
        # Most lifecycle tests focus on incoming notifications after the
        # collapse transition, so clear the seed message's initial preview.
        window.preview_messages.clear()
        window._reschedule_preview_sweep()

    def test_collapsing_existing_messages_previews_them(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-collapse-") as directory:
            window = self._make_window(directory)
            window.start()
            try:
                window.handle_command(_notify("existing", "Already here", "done"))
                window.refresh()
                with patch("openpets_linux.ui.time.monotonic", return_value=1000.0):
                    window.toggle_messages_collapsed(True)
                self.assertEqual(list(window.preview_messages), ["existing"])
                self.assertEqual(window.preview_messages["existing"]["title"], "Already here")
                self.assertEqual(
                    window.preview_messages["existing"]["expires_at"], 1005.0,
                )
            finally:
                window.cleanup()

    def test_preview_ttl_defaults_to_five_seconds(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            window = self._make_window(directory)
            try:
                self.assertAlmostEqual(window.collapsed_message_preview_seconds, 5.0)
            finally:
                window.cleanup()

    def test_preview_only_registered_while_collapsed(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            window = self._make_window(directory)
            window.start()
            try:
                # Expanded: incoming notify must not create a preview.
                window.handle_command(_notify("t1", "Build", "running"))
                self.assertEqual(window.preview_messages, {})

                # Collapsed: same notify creates a preview.
                self._collapse(window)
                window.handle_command(_notify("t2", "Deploy", "running"))
                self.assertEqual(list(window.preview_messages.keys()), ["t2"])
                self.assertEqual(window.preview_messages["t2"]["title"], "Deploy")
            finally:
                window.cleanup()

    def test_collapsed_notify_reaches_overlay_layout(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-layout-") as directory:
            window = self._make_window(directory)
            window.start()
            try:
                self._collapse(window)
                # Lifecycle tests stub relayout; restore the real geometry pass
                # here to cover the actual incoming IPC/MCP -> overlay path.
                del window.relayout
                response = window.handle_command(
                    _notify("visible-preview", "Should appear", "running")
                )
                self.assertTrue(response["ok"])
                self.assertEqual(
                    [preview.thread_id for preview in window.layout.preview_rects],
                    ["visible-preview"],
                )
                toggle = window.layout.message_toggle_rect
                preview = window.layout.preview_rects[0].rect
                self.assertGreaterEqual(preview.y, toggle.y + toggle.height)
            finally:
                window.cleanup()

    def test_collapsed_notify_is_added_to_the_rendered_overlay_layout(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-layout-") as directory:
            window = self._make_window(directory)
            window.start()
            try:
                self._collapse(window)
                # Restore the real geometry pass: lifecycle-only tests above
                # stub relayout, but this assertion covers the UI-to-overlay
                # integration used by actual incoming IPC/MCP notifications.
                del window.relayout
                response = window.handle_command(
                    _notify("visible-preview", "Should appear", "running")
                )
                self.assertTrue(response["ok"])
                self.assertEqual(
                    [preview.thread_id for preview in window.layout.preview_rects],
                    ["visible-preview"],
                )
                toggle = window.layout.message_toggle_rect
                preview = window.layout.preview_rects[0].rect
                self.assertGreaterEqual(preview.y, toggle.y + toggle.height)
            finally:
                window.cleanup()

    def test_collapsed_notify_reveals_preview_after_toggle_auto_hidden(self):
        from openpets_linux.overlay import hit_test

        with tempfile.TemporaryDirectory(prefix="openpets-preview-autohide-") as directory:
            window = self._make_window(directory)
            window.start()
            try:
                self._collapse(window)
                del window.relayout
                window._pointer_in_pet = False
                window._hide_message_toggle()
                self.assertFalse(window.message_toggle_visible)

                response = window.handle_command(
                    _notify("arrived-while-hidden", "New result", "running")
                )
                window.refresh()
                self.assertTrue(response["ok"])
                self.assertTrue(window.message_toggle_visible)
                self.assertEqual(
                    [preview.thread_id for preview in window.layout.preview_rects],
                    ["arrived-while-hidden"],
                )
                preview = window.layout.preview_rects[0]
                self.assertEqual(
                    hit_test(window.layout, preview.rect.x + 2,
                             preview.rect.y + 2).kind,
                    "message",
                )
            finally:
                window.cleanup()

    def test_new_previews_appear_at_bottom_preserving_order(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            window = self._make_window(directory)
            window.start()
            try:
                self._collapse(window)
                clock = _FakeClock(1000.0)
                with patch("openpets_linux.ui.time.monotonic", clock):
                    window.handle_command(_notify("t1", "First", "running"))
                    window.handle_command(_notify("t2", "Second", "running"))
                    window.handle_command(_notify("t3", "Third", "running"))
                self.assertEqual(
                    list(window.preview_messages.keys()), ["t1", "t2", "t3"])
                # Independent expiries (all stamped at the same fake now).
                self.assertAlmostEqual(window.preview_messages["t1"]["expires_at"], 1005.0)
                self.assertAlmostEqual(window.preview_messages["t2"]["expires_at"], 1005.0)
                self.assertAlmostEqual(window.preview_messages["t3"]["expires_at"], 1005.0)
            finally:
                window.cleanup()

    def test_same_thread_update_moves_to_bottom_and_resets_timer(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            window = self._make_window(directory)
            try:
                self._collapse(window)
                clock = _FakeClock(1000.0)
                with patch("openpets_linux.ui.time.monotonic", clock):
                    window.handle_command(_notify("t1", "First", "running"))   # expires 1005
                    window.handle_command(_notify("t2", "Second", "running"))  # expires 1005
                    # Same thread updated (running -> completed), moves to bottom.
                    clock.value = 1002.0
                    window.handle_command(_notify("t1", "First", "completed"))  # expires 1007
                # t1 moved below t2.
                self.assertEqual(list(window.preview_messages.keys()), ["t2", "t1"])
                # t1's own timer reset to 1007; t2 untouched at 1005.
                self.assertAlmostEqual(window.preview_messages["t1"]["expires_at"], 1007.0)
                self.assertAlmostEqual(window.preview_messages["t2"]["expires_at"], 1005.0)
                self.assertEqual(window.preview_messages["t1"]["status"], "completed")
            finally:
                window.cleanup()

    def test_sweep_removes_expired_keeps_active(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            window = self._make_window(directory)
            window.start()
            try:
                self._collapse(window)
                clock = _FakeClock(1000.0)
                with patch("openpets_linux.ui.time.monotonic", clock):
                    window.handle_command(_notify("t1", "Expiring", "running"))   # expires 1005
                    clock.value = 1002.0
                    window.handle_command(_notify("t2", "Staying", "running"))    # expires 1007
                    # Advance past t1's expiry but not t2's; run sweep directly.
                    clock.value = 1006.0
                    window._preview_source = 0
                    window._preview_sweep()
                self.assertEqual(list(window.preview_messages.keys()), ["t2"])
                self.assertAlmostEqual(window.preview_messages["t2"]["expires_at"], 1007.0)
            finally:
                window.cleanup()

    def test_expanding_clears_all_previews(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            window = self._make_window(directory)
            try:
                self._collapse(window)
                clock = _FakeClock(1000.0)
                with patch("openpets_linux.ui.time.monotonic", clock):
                    window.handle_command(_notify("t1", "A", "running"))
                    window.handle_command(_notify("t2", "B", "running"))
                self.assertEqual(len(window.preview_messages), 2)
                window.toggle_messages_collapsed(False)
                self.assertEqual(window.preview_messages, {})
            finally:
                window.cleanup()

    def test_repeated_same_thread_update_keeps_single_entry(self):
        with tempfile.TemporaryDirectory(prefix="openpets-preview-") as directory:
            window = self._make_window(directory)
            try:
                self._collapse(window)
                clock = _FakeClock(1000.0)
                with patch("openpets_linux.ui.time.monotonic", clock):
                    for status in ("running", "running", "completed"):
                        window.handle_command(_notify("t1", "Build", status))
                # Only one entry, always at the bottom (only entry here).
                self.assertEqual(list(window.preview_messages.keys()), ["t1"])
                self.assertEqual(len(window.preview_messages), 1)
                self.assertEqual(window.preview_messages["t1"]["status"], "completed")
            finally:
                window.cleanup()


if __name__ == "__main__":
    unittest.main()
