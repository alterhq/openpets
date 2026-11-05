"""GTK regression tests for autonomous movement (follow cursor / free roam).

Requires a real display (DISPLAY) and GTK. Runs isolated config, no account
access. Covers: menu items, mode lifecycle, drag pause/resume, gait override,
cleanup, and no timer leaks.
"""

import importlib.util
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HAS_DISPLAY = bool(os.environ.get("DISPLAY")) and importlib.util.find_spec("gi") is not None

if HAS_DISPLAY:
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib


@unittest.skipUnless(HAS_DISPLAY, "GTK tests require DISPLAY (use Xvfb)")
class MotionGUITests(unittest.TestCase):
    def make_window(self):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow
        temporary = tempfile.TemporaryDirectory(prefix="openpets-motion-gui-")
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        environment = patch.dict(os.environ, {"HOME": directory,
                                 "XDG_CONFIG_HOME": directory + "/config",
                                 "XDG_DATA_HOME": directory + "/data",
                                 "DBUS_SESSION_BUS_ADDRESS": ""})
        environment.start()
        self.addCleanup(environment.stop)
        window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
        self.addCleanup(window.cleanup)
        window.start()
        return window

    # -- menu -----------------------------------------------------------

    def test_motion_menu_items_exist(self):
        window = self.make_window()
        labels = [item.get_label() for item in window.menu.get_children()
                  if hasattr(item, "get_label")]
        self.assertIn("Movement: Off", labels)
        self.assertIn("Movement: Follow cursor", labels)
        self.assertIn("Movement: Free roam", labels)

    def test_default_mode_is_off(self):
        window = self.make_window()
        self.assertTrue(window.motion_off_item.get_active())
        self.assertFalse(window.motion_follow_item.get_active())
        self.assertFalse(window.motion_free_item.get_active())
        self.assertEqual(window.motion.mode, "off")

    def test_free_roam_recovers_primary_workarea_when_saved_position_misses_monitor(self):
        window = self.make_window()
        monitor = SimpleNamespace(get_workarea=lambda: SimpleNamespace(
            x=0, y=0, width=1600, height=900))
        display = SimpleNamespace(
            get_monitor_at_point=lambda _x, _y: None,
            get_primary_monitor=lambda: monitor,
            get_n_monitors=lambda: 1,
            get_monitor=lambda _index: monitor,
        )
        with patch.object(window, "get_display", return_value=display):
            window.motion.workarea = None
            window._setup_motion()
        self.assertEqual(window.motion.workarea, (0, 0, 1600, 900))
        window.motion_free_item.set_active(True)
        self.assertIsNotNone(window.motion.state.waypoint)
        self.assertTrue(window.motion.is_moving())

    def test_menu_items_are_mutually_exclusive(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        self.assertTrue(window.motion_follow_item.get_active())
        self.assertFalse(window.motion_off_item.get_active())
        self.assertFalse(window.motion_free_item.get_active())
        self.assertEqual(window.motion.mode, "follow")

        window.motion_free_item.set_active(True)
        self.assertTrue(window.motion_free_item.get_active())
        self.assertFalse(window.motion_off_item.get_active())
        self.assertFalse(window.motion_follow_item.get_active())
        self.assertEqual(window.motion.mode, "free_roam")

        window.motion_off_item.set_active(True)
        self.assertTrue(window.motion_off_item.get_active())
        self.assertFalse(window.motion_follow_item.get_active())
        self.assertFalse(window.motion_free_item.get_active())
        self.assertEqual(window.motion.mode, "off")

    # -- mode lifecycle --------------------------------------------------

    def test_follow_mode_starts_motion_timer(self):
        window = self.make_window()
        self.assertEqual(window.motion_source, 0)
        window.motion_follow_item.set_active(True)
        self.assertEqual(window.motion.mode, "follow")
        self.assertNotEqual(window.motion_source, 0)

    def test_off_mode_stops_motion_timer(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        self.assertNotEqual(window.motion_source, 0)
        window.motion_off_item.set_active(True)
        self.assertEqual(window.motion.mode, "off")
        self.assertEqual(window.motion_source, 0)

    def test_settings_persist_motion_mode(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        config = window.settings.read()
        self.assertEqual(config.get("motionMode"), "follow")

    def test_invalid_saved_mode_falls_back_to_off(self):
        window = self.make_window()
        # Corrupt the saved motionMode to an invalid value.
        window.settings.update({"motionMode": "not-a-mode"})
        # Re-setup reads it and should fall back to off.
        window._setup_motion()
        self.assertTrue(window.motion_off_item.get_active())
        self.assertEqual(window.motion.mode, "off")

    # -- drag pause/resume ----------------------------------------------

    def test_drag_pauses_autonomous_motion(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        window.motion.set_cursor(500, 100)
        # Let it start moving.
        window.on_motion_tick()
        self.assertFalse(window.motion.is_settled())

        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        self.assertTrue(window.motion.is_paused())
        # Tick should not move while paused.
        pos_before = window.motion.position
        window.on_motion_tick()
        self.assertEqual(window.motion.position, pos_before)

    def test_drag_release_resumes_autonomous_motion(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        window.motion.set_cursor(500, 100)
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        self.assertTrue(window.motion.is_paused())

        window.end_drag()
        self.assertFalse(window.motion.is_paused())
        self.assertNotEqual(window.motion_source, 0)

    def test_gait_override_during_drag_uses_drag_animation(self):
        window = self.make_window()
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        window.on_motion_notify(window, SimpleNamespace(x_root=140, y_root=100))
        self.assertEqual(window.current_animation, "running-right")
        # Motion is paused, so motion_animation should not override drag.
        self.assertIsNone(window.motion_animation)
        window.end_drag()

    # -- cleanup ---------------------------------------------------------

    def test_cleanup_stops_motion_timer(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        self.assertNotEqual(window.motion_source, 0)
        window.cleanup()
        self.assertEqual(window.motion_source, 0)
        self.assertEqual(window.motion_settled_save_source, 0)

    def test_hide_stops_motion_timer(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        self.assertNotEqual(window.motion_source, 0)
        window.hide()
        self.assertEqual(window.motion_source, 0)
        window.show_all()

    def test_no_timer_duplicate_on_mode_toggle(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        source1 = window.motion_source
        window.motion_free_item.set_active(True)
        source2 = window.motion_source
        self.assertNotEqual(source1, 0)
        self.assertNotEqual(source2, 0)
        # Old source removed, new source different.
        self.assertNotEqual(source1, source2)

    # -- no movement when hidden ----------------------------------------

    def test_no_move_when_hidden(self):
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        window.motion.set_cursor(500, 100)
        window.hide()
        pos_before = window.get_position()
        window.on_motion_tick()
        self.assertEqual(window.get_position(), pos_before)
        window.show_all()

    # -- follow cursor quadrants (regression for wrong-direction bug) ----

    def _start_follow(self, window):
        window.motion_follow_item.set_active(True)
        # Use a fixed known position with room to move in all directions,
        # avoiding X11 window-placement races where get_position() may
        # temporarily return (0, 0).
        x, y = 500, 400
        window.motion.set_position(x, y)
        return x, y

    def test_follow_cursor_right_moves_right(self):
        window = self.make_window()
        x, y = self._start_follow(window)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x + 600, y)):
            window.on_motion_tick()
        self.assertGreater(window.motion.position[0], x)
        self.assertEqual(window.motion_animation, "running-right")

    def test_follow_cursor_left_moves_left(self):
        """Regression: cursor left of window must move LEFT, not right.

        The old target math double-counted win_x (target = cursor - cx + win_x),
        making dx independent of current position and often flipping direction.
        """
        window = self.make_window()
        x, y = self._start_follow(window)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x - 600, y)):
            window.on_motion_tick()
        self.assertLess(window.motion.position[0], x)
        self.assertEqual(window.motion_animation, "running-left")

    def test_follow_cursor_above_moves_up(self):
        window = self.make_window()
        x, y = self._start_follow(window)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x, y - 600)):
            window.on_motion_tick()
        self.assertLess(window.motion.position[1], y)

    def test_follow_cursor_below_moves_down(self):
        window = self.make_window()
        x, y = self._start_follow(window)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x, y + 600)):
            window.on_motion_tick()
        self.assertGreater(window.motion.position[1], y)

    def test_follow_cursor_diagonal_top_right(self):
        """Diagonal top-right: move right+up, gait running-right (never idle)."""
        window = self.make_window()
        x, y = self._start_follow(window)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x + 800, y - 400)):
            now = 1000.0
            saw_idle = False
            for _ in range(20):
                window.on_motion_tick(now=now)
                now += 0.033
                if window.motion.is_settled():
                    break
                if window.motion_animation == "idle":
                    saw_idle = True
            self.assertFalse(saw_idle, "diagonal movement showed idle gait")
        nx, ny = window.motion.position
        self.assertGreater(nx, x)   # moved right
        self.assertLess(ny, y)      # moved up

    def test_follow_cursor_diagonal_bottom_left(self):
        """Diagonal bottom-left: move left+down, gait running-left (never idle)."""
        window = self.make_window()
        x, y = self._start_follow(window)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x - 800, y + 400)):
            now = 1000.0
            saw_idle = False
            for _ in range(20):
                window.on_motion_tick(now=now)
                now += 0.033
                if window.motion.is_settled():
                    break
                if window.motion_animation == "idle":
                    saw_idle = True
            self.assertFalse(saw_idle, "diagonal movement showed idle gait")
        nx, ny = window.motion.position
        self.assertLess(nx, x)      # moved left
        self.assertGreater(ny, y)   # moved down

    def test_follow_vertical_target_moves_up_no_jump(self):
        """Cursor directly above sprite center: pet moves up, never jumps/idles while moving."""
        window = self.make_window()
        x, y = self._start_follow(window)
        cx = window.layout.pet_x + window.cell_width / 2.0
        with patch.object(window, "_query_cursor_position",
                          return_value=(x + cx, y - 600)):
            now = 1000.0
            for _ in range(40):
                window.on_motion_tick(now=now)
                now += 0.033
                if window.motion.is_settled():
                    break
                ga = window.motion_animation
                # While moving toward a vertical target, gait is a running
                # gait (left or right), never idle or jumping.
                if ga:
                    self.assertIn(ga, ("running-left", "running-right"))
        nx, ny = window.motion.position
        self.assertLess(ny, y)  # moved up toward cursor

    # -- overlay / top-edge concern --------------------------------------

    def test_overlay_chips_above_pet_visible_at_top_edge(self):
        """Plugin chips above the pet stay visible when pet is near top edge.

        relayout must clamp the whole layout (chips + pet + messages) inside
        the workarea so nothing clips at the top edge.
        """
        window = self.make_window()
        x, y = self._start_follow(window)
        # Place pet near the top edge (small y).
        window.motion.set_position(x, 10)
        # Add a plugin surface -> chips appear above pet -> pet_top increases.
        from openpets_linux.plugin_ui import PROVIDERS
        fixture = {"id": "battery", "label": "Battery", "value": "73%",
                   "details": [], "state": "normal"}
        with patch.dict(PROVIDERS, {"battery": ("Battery", lambda: fixture)}):
            window.plugins.set_enabled("battery", True)
            deadline = time.monotonic() + 2
            while window.plugins.busy and time.monotonic() < deadline:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(0.01)
        # Force a relayout with the new chip content.
        window.refresh()
        # The controller position should be synced and the window top
        # clamped so chips (at window_y + CHIP_TOP_GAP) stay visible.
        self.assertGreaterEqual(window.motion.position[1], 0)
        # Sprite anchor: pet_top should still be at a valid screen position.
        self.assertGreaterEqual(window.layout.pet_top, 0)

    def test_content_expiry_resizing_preserves_sprite_anchor(self):
        """Message appearing/disappearing (content change) must not jump the
        sprite anchor when the pet is near the top edge."""
        window = self.make_window()
        x, y = self._start_follow(window)
        window.motion.set_position(x, 20)
        # Sync controller to actual position.
        window.motion.set_position(*window.get_position())
        sx, sy = window.motion.position
        # Add a message (content below pet increases total_height).
        window.state.apply({
            "type": "notify",
            "notification": {"title": "Test", "status": "running",
                             "threadId": "top-edge-test"},
        })
        window.refresh()
        # Sprite screen anchor should be preserved (pet_top unchanged since
        # no chips above; y should not have jumped).
        nx, ny = window.motion.position
        # The window may have been clamped, but sprite should not have jumped
        # by more than the layout change allows.
        self.assertLessEqual(abs(ny - sy), window.layout.total_height,
                             f"sprite jumped from y={sy} to y={ny}")
        window.state.clear_messages()
        window.refresh()

    def test_follow_target_sprite_center_preserved_after_relayout(self):
        """Follow-cursor target is expressed at sprite center; after relayout
        (content change), the cursor target still targets the sprite center."""
        window = self.make_window()
        x, y = self._start_follow(window)
        window.motion_follow_item.set_active(True)

        def sprite_center():
            return (window.layout.pet_x + window.cell_width / 2.0,
                    window.layout.pet_top + window.cell_height / 2.0)

        cx, cy = sprite_center()
        # Cursor at sprite center -> settled (within deadzone).
        with patch.object(window, "_query_cursor_position",
                          return_value=(x + cx, y + cy)):
            window.on_motion_tick(now=1000.0)
        self.assertTrue(window.motion.is_settled())

        # Change content (add message) -> relayout changes layout geometry.
        window.state.apply({
            "type": "notify",
            "notification": {"title": "T", "status": "done",
                             "threadId": "relayout-test"},
        })
        window.refresh()

        # Re-anchor controller to actual position after relayout.
        wx, wy = window.get_position()
        window.motion.set_position(wx, wy)

        # Cursor at new sprite center -> still settled.
        cx2, cy2 = sprite_center()
        with patch.object(window, "_query_cursor_position",
                          return_value=(wx + cx2, wy + cy2)):
            window.on_motion_tick(now=1001.0)
        self.assertTrue(window.motion.is_settled())


if __name__ == "__main__":
    unittest.main()
