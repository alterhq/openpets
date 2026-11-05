"""GTK regression tests for autonomous movement (follow cursor / free roam).

Requires a real display (DISPLAY) and GTK. Runs isolated config, no account
access. Covers: menu items, mode lifecycle, drag pause/resume, gait override,
cleanup, and no timer leaks.
"""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

class _FixedClock:
    """Deterministic clock advanced manually by tests."""

    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, dt):
        self.value += float(dt)


HAS_DISPLAY = bool(os.environ.get("DISPLAY")) and importlib.util.find_spec("gi") is not None

if HAS_DISPLAY:
    import gi
    gi.require_version("GLib", "2.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk


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

    def test_active_task_freezes_autonomous_motion_until_done(self):
        from openpets_linux.motion import MODE_FREE_ROAM

        window = self.make_window()
        window.motion.set_mode(MODE_FREE_ROAM)
        window.state.apply({
            "type": "notify",
            "notification": {"title": "Build", "status": "running", "threadId": "build"},
        })
        window.refresh()
        self.assertTrue(window.motion.is_paused())
        self.assertIsNone(window.motion.tick())

        # A second active task keeps motion frozen after the first completes.
        window.state.apply({
            "type": "notify",
            "notification": {"title": "Review", "status": "review", "threadId": "review"},
        })
        window.state.apply({
            "type": "notify",
            "notification": {"title": "Build", "status": "done", "threadId": "build"},
        })
        window.refresh()
        self.assertTrue(window.motion.is_paused())

        window.state.apply({
            "type": "notify",
            "notification": {"title": "Review", "status": "done", "threadId": "review"},
        })
        window.refresh()
        self.assertFalse(window.motion.is_paused())

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

    # -- v2 drag: look-direction gaze + pointer follow --------------------

    def _make_v2_pet_window(self):
        """Build a PetWindow for a synthetic v2 (8x11) cute-yaya pet bundle."""
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        temporary = tempfile.TemporaryDirectory(prefix="openpets-v2-drag-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        pet = directory / "cute-yaya"
        pet.mkdir()
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8 * 8, 11 * 8)
        sheet = pet / "spritesheet.png"
        pixbuf.savev(str(sheet), "png", [], [])
        (pet / "pet.json").write_text(json.dumps({
            "id": "cute-yaya", "displayName": "Cute Yaya",
            "spritesheetPath": "spritesheet.png", "spriteVersionNumber": 2}))
        environment = patch.dict(os.environ, {"HOME": temporary.name,
                                 "XDG_CONFIG_HOME": temporary.name + "/config",
                                 "XDG_DATA_HOME": temporary.name + "/data",
                                 "DBUS_SESSION_BUS_ADDRESS": ""})
        environment.start()
        self.addCleanup(environment.stop)
        from openpets_linux.ui import PetWindow
        window = PetWindow(str(pet), .42, temporary.name + "/host.sock")
        self.addCleanup(window.cleanup)
        window.start()
        return window

    def _button1_event(self, x_root, y_root):
        return SimpleNamespace(x_root=x_root, y_root=y_root,
                               state=Gdk.ModifierType.BUTTON1_MASK)

    def _flush(self):
        # Gtk.Window.move() is asynchronous; flush pending GTK/X11 events so
        # get_position() reports the moved position deterministically.
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def test_v2_pet_has_look_direction_enabled(self):
        window = self._make_v2_pet_window()
        self.assertTrue(window.look_direction_enabled)

    def test_v2_drag_follows_pointer_and_rotates_gaze(self):
        """V2 pets move with the pointer while rotating gaze by motion vector.

        Gaze follows the instantaneous pointer velocity (current root coords
        minus previous root coords), not displacement from the drag origin:
        dragging back toward the origin yields a leftward gaze even though the
        cursor is still at/right of the origin (origin-based math would report
        zero/up there).
        """
        window = self._make_v2_pet_window()
        self.assertTrue(window.look_direction_enabled)
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        ox, oy = window.drag_origin[2], window.drag_origin[3]
        # Drag right: velocity (120, 0) -> right gaze; window follows pointer.
        with patch.object(window, "move") as mock_move:
            window.on_motion_notify(window, self._button1_event(220, 100))
        mock_move.assert_called_once_with(ox + 120, oy)
        self.assertEqual(window.look_direction, (9, 4))  # right-facing pose
        # Drag back toward the origin: velocity (-120, 0) -> left gaze, even
        # though the cursor is back at the origin (displacement from origin is
        # zero here). Proves velocity, not origin displacement, drives gaze.
        with patch.object(window, "move") as mock_move:
            window.on_motion_notify(window, self._button1_event(100, 100))
        mock_move.assert_called_once_with(ox, oy)
        self.assertEqual(window.look_direction, (10, 4))  # left-facing pose
        window.end_drag()
        self.assertFalse(window.drag_active)
        self.assertIsNone(window.look_direction)

    def test_v2_drag_zero_movement_keeps_last_gaze(self):
        """A stationary cursor (zero velocity) mid-drag must not change gaze."""
        window = self._make_v2_pet_window()
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        ox, oy = window.drag_origin[2], window.drag_origin[3]
        with patch.object(window, "move"):
            window.on_motion_notify(window, self._button1_event(220, 100))
        self.assertEqual(window.look_direction, (9, 4))  # right
        # Zero velocity: gaze must be preserved, not re-derived from origin.
        with patch.object(window, "move") as mock_move:
            window.on_motion_notify(window, self._button1_event(220, 100))
        mock_move.assert_called_once_with(ox + 120, oy)
        self.assertEqual(window.look_direction, (9, 4))  # unchanged
        window.end_drag()
        self.assertFalse(window.drag_active)
        self.assertIsNone(window.look_direction)

    def test_v2_drag_gaze_uses_velocity_in_all_modes(self):
        """The same pointer velocity yields the same gaze in off/follow/free-roam.

        Direction is computed purely from the motion vector and is independent
        of the autonomous movement mode; only the move/re-anchor behavior is
        mode-specific (already covered by drag pause/re-anchor tests).
        """
        window = self._make_v2_pet_window()
        for mode_item in (
            window.motion_off_item,
            window.motion_follow_item,
            window.motion_free_item,
        ):
            mode_item.set_active(True)
            window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
            with patch.object(window, "move"):
                window.on_motion_notify(window, self._button1_event(220, 100))
            self.assertEqual(
                window.look_direction, (9, 4),
                f"mode {window.motion.mode}: gaze should follow velocity",
            )
            window.end_drag()
        self.assertFalse(window.drag_active)
        self.assertIsNone(window.look_direction)

    def test_v1_drag_still_follows_pointer_and_gaits(self):
        """Regression: legacy v1 pets keep move + running-gait drag behavior."""
        window = self.make_window()  # bundled Starcorn is v1 (8x9)
        self.assertFalse(window.look_direction_enabled)
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        ox, oy = window.drag_origin[2], window.drag_origin[3]
        with patch.object(window, "move") as mock_move:
            window.on_motion_notify(window, self._button1_event(220, 100))
        mock_move.assert_called_once_with(ox + 120, oy)
        self.assertEqual(window.current_animation, "running-right")
        window.end_drag()
        self.assertFalse(window.drag_active)
        self.assertIsNone(window.look_direction)

    def test_drag_cancels_autonomous_motion_gait(self):
        """Pressing/dragging cancels the running/turn-left/right gait override."""
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        x, y = 500, 400
        window.motion.set_position(x, y)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x + 600, y)):
            window.on_motion_tick(now=1000.0)
        self.assertFalse(window.motion.is_settled())
        self.assertIsNotNone(window.motion_animation)  # running-right gait active

        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        self.assertTrue(window.motion.is_paused())
        self.assertIsNone(window.motion_animation)  # gait cancelled on press
        window.end_drag()
        self.assertFalse(window.motion.is_paused())

    def test_release_stationary_mode_returns_to_idle_not_stale_gait(self):
        """Stationary mode returns to idle, not a stale turn-left/right gait."""
        window = self.make_window()  # mode: off
        self.assertEqual(window.motion.mode, "off")
        # Simulate a stale gait left over from autonomous movement.
        window.motion_animation = "running-right"
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        window.end_drag()
        self.assertEqual(window.current_animation, "idle")
        self.assertFalse(window.motion.is_paused())
        self.assertEqual(window.motion.mode, "off")
        # Off mode settles on the next tick after drag-resume.
        window.on_motion_tick()
        self.assertTrue(window.motion.is_settled())

    def test_release_autonomous_mode_reanchors_and_resumes(self):
        """Follow mode re-anchors at the drag-end position and resumes cleanly."""
        window = self.make_window()
        window.motion_follow_item.set_active(True)
        # Use a fixed clock so resume() anchors the movement clock to test
        # time instead of real monotonic time (avoiding a dt mismatch on the
        # resume tick).
        clock = _FixedClock(1000.0)
        window.motion._clock = clock
        x, y = 500, 400
        window.motion.set_position(x, y)
        with patch.object(window, "_query_cursor_position",
                          return_value=(x + 600, y)):
            window.on_motion_tick(now=1000.0)
        self.assertFalse(window.motion.is_settled())
        self.assertNotEqual(window.motion_source, 0)

        # Drag the pet to a new screen position while following is active.
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        window.on_motion_notify(window, self._button1_event(220, 100))
        self._flush()
        drag_end = window.get_position()
        window.end_drag()

        self.assertFalse(window.motion.is_paused())
        self.assertNotEqual(window.motion_source, 0)
        # Re-anchored to the drag-end position, not the drag start.
        self.assertEqual(window.motion.position, drag_end)
        # Resume anchors the movement clock to the fixed test time, so the
        # next tick advances normally and the pet resumes following.
        clock.advance(0.033)
        with patch.object(window, "_query_cursor_position",
                          return_value=(drag_end[0] + 600, drag_end[1])):
            window.on_motion_tick(now=clock())
        self.assertFalse(window.motion.is_settled())
        self.assertNotEqual(window.motion.position, drag_end)  # resumed moving

    def test_stationary_mode_allows_repositioning(self):
        """Stationary (off) mode can still be repositioned by dragging."""
        window = self.make_window()
        self.assertEqual(window.motion.mode, "off")
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        ox, oy = window.drag_origin[2], window.drag_origin[3]
        with patch.object(window, "move") as mock_move:
            window.on_motion_notify(window, self._button1_event(150, 130))
        mock_move.assert_called_once_with(ox + 50, oy + 30)
        window.end_drag()
        # Off mode: the next tick settles the controller back at the drop point.
        window.on_motion_tick()
        self.assertEqual(window.motion.mode, "off")
        self.assertTrue(window.motion.is_settled())
        self.assertFalse(window.motion.is_paused())

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
