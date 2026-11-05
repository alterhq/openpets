"""Real GTK atlas/drawing regression; isolated config, no account reads."""

import importlib.util
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("DISPLAY") and importlib.util.find_spec("gi"),
                     "Requires GTK and a display")
class AnimationDrawingTests(unittest.TestCase):
    def make_window(self):
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow
        temporary = tempfile.TemporaryDirectory(prefix="openpets-drag-gui-")
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

    def test_drag_direction_reversal_and_underlying_ttl(self):
        window = self.make_window()
        window.state.apply({"type": "playAnimation", "name": "review", "loop": False,
                            "ttlSeconds": 20})
        window.refresh(reset_animation=True)
        expiry = window.state._animation_expires_at
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        window.on_motion_notify(window, SimpleNamespace(x_root=140, y_root=100))
        self.assertEqual(window.current_animation, "running-right")
        source = window.frame_source
        window.frame = 2
        window.on_motion_notify(window, SimpleNamespace(x_root=125, y_root=100))
        self.assertEqual(window.current_animation, "running-left")
        self.assertEqual(window.frame_source, source)
        self.assertEqual(window.frame, 2)
        window.on_motion_notify(window, SimpleNamespace(x_root=125, y_root=75))
        # Vertical drag retains the last horizontal gait instead of jumping.
        self.assertEqual(window.current_animation, "running-left")
        self.assertEqual(window.state.animation, "review")
        self.assertEqual(window.state._animation_expires_at, expiry)
        self.assertFalse(window.state.loop)
        window.end_drag()
        self.assertEqual(window.current_animation, "review")
        self.assertEqual(window.state._animation_expires_at, expiry)
        self.assertFalse(window.state.loop)

    def test_v1_drag_still_moves_window(self):
        import gi
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
        window = self.make_window()
        moved = []
        window.move = lambda x, y: moved.append((x, y))
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        window.on_motion_notify(window, SimpleNamespace(
            x_root=120, y_root=110, state=Gdk.ModifierType.BUTTON1_MASK))
        self.assertTrue(moved)
        self.assertIsNone(window.look_direction)
        window.end_drag()

    def test_vertical_drag_falls_back_to_underlying_animation(self):
        window = self.make_window()
        window.state.apply({"type": "playAnimation", "name": "review", "loop": False,
                            "ttlSeconds": 20})
        window.refresh(reset_animation=True)
        expiry = window.state._animation_expires_at
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        # Vertical-only drag with no prior horizontal gait: no idle override,
        # no jumping; the underlying task animation stays visible.
        window.on_motion_notify(window, SimpleNamespace(x_root=100, y_root=60))
        self.assertEqual(window.current_animation, "review")
        self.assertFalse(window.state.loop)
        window.end_drag()
        self.assertEqual(window.current_animation, "review")
        self.assertEqual(window.state._animation_expires_at, expiry)
        self.assertFalse(window.state.loop)

    def test_v2_drag_selects_gaze_pose_without_moving_and_release_cleans_up(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GdkPixbuf
        from openpets_linux.ui import PetWindow

        with tempfile.TemporaryDirectory(prefix="openpets-v2-drag-gui-") as directory:
            pet_dir = os.path.join(directory, "pet")
            os.mkdir(pet_dir)
            sheet = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8 * 16, 11 * 16)
            sheet.fill(0xff0000ff)
            sheet.savev(os.path.join(pet_dir, "spritesheet.png"), "png", [], [])
            with open(os.path.join(pet_dir, "pet.json"), "w", encoding="utf-8") as manifest:
                json.dump({"id": "v2-test", "spritesheetPath": "spritesheet.png",
                           "spriteVersionNumber": 2}, manifest)
            with patch.dict(os.environ, {"HOME": directory,
                                         "XDG_CONFIG_HOME": directory + "/config",
                                         "XDG_DATA_HOME": directory + "/data",
                                         "DBUS_SESSION_BUS_ADDRESS": ""}):
                window = PetWindow(pet_dir, .42, directory + "/host.sock")
                try:
                    window.start()
                    moved = []
                    original_move = window.move
                    window.move = lambda x, y: moved.append((x, y))
                    window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
                    window.on_motion_notify(window, SimpleNamespace(
                        x_root=100, y_root=60, state=Gdk.ModifierType.BUTTON1_MASK))
                    self.assertEqual(window.look_direction, (9, 0))
                    self.assertEqual(moved, [])
                    window.on_motion_notify(window, SimpleNamespace(
                        x_root=140, y_root=100, state=Gdk.ModifierType.BUTTON1_MASK))
                    self.assertEqual(window.look_direction, (9, 4))
                    window.end_drag()
                    self.assertFalse(window.drag_active)
                    self.assertIsNone(window.look_direction)
                    window.move = original_move
                finally:
                    window.cleanup()

    def test_gtk_pointer_signals_are_wired(self):
        from gi.repository import Gdk
        window = self.make_window()
        rect = window.layout.pet_rect
        press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        press.button = 1
        press.x, press.y = rect.x + 8, rect.y + 8
        press.x_root, press.y_root = 100, 100
        window.emit("button-press-event", press)
        self.assertTrue(window.drag_active)
        motion = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
        motion.x_root, motion.y_root = 140, 100
        motion.state = Gdk.ModifierType.BUTTON1_MASK
        window.emit("motion-notify-event", motion)
        self.assertEqual(window.current_animation, "running-right")
        release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        release.button = 1
        window.emit("button-release-event", release)
        self.assertFalse(window.drag_active)
        self.assertEqual(window.current_animation, "idle")

    def test_notification_during_drag_wins_after_release(self):
        window = self.make_window()
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        window.on_motion_notify(window, SimpleNamespace(x_root=140, y_root=100))
        window.handle_command({"type": "notify", "notification": {
            "title": "New task", "status": "waiting", "threadId": "drag-test"}})
        window.refresh(reset_animation=True)
        self.assertEqual(window.current_animation, "running-right")
        window.end_drag()
        self.assertEqual(window.current_animation, "waiting")

    def test_slow_drag_accumulates_motion_and_cancels_idle_timer(self):
        window = self.make_window()
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        self.assertEqual(window.idle_source, 0)
        for x in range(101, 109):
            window.on_motion_notify(window, SimpleNamespace(x_root=x, y_root=100))
        self.assertEqual(window.current_animation, "running-right")
        for x in range(107, 96, -1):
            window.on_motion_notify(window, SimpleNamespace(x_root=x, y_root=100))
        self.assertEqual(window.current_animation, "running-left")
        window.end_drag()
        self.assertNotEqual(window.idle_source, 0)

    def test_idle_gesture_is_finite_and_does_not_interrupt_task(self):
        from gi.repository import GLib
        window = self.make_window()
        if window.idle_source:
            GLib.source_remove(window.idle_source)
            window.idle_source = 0
        with patch.object(window.idle_picker, "pick", return_value="waving"):
            window.on_idle_tick()
        self.assertEqual(window.current_animation, "waving")
        self.assertFalse(window.state.loop)
        window.state.apply({"type": "playAnimation", "name": "running"})
        window.refresh(reset_animation=True)
        self.assertFalse(window._idle_eligible())
        window.begin_drag(SimpleNamespace(x_root=100, y_root=100))
        self.assertFalse(window._idle_eligible())
        window.end_drag()

    def test_overlay_collapses_and_hit_targets_match(self):
        from openpets_linux.overlay import hit_test
        window = self.make_window()
        self.assertLess(window.layout.pet_top, 24)
        window.state.apply({"type": "notify", "notification": {
            "title": "Build", "text": "Working", "status": "running"}})
        window.refresh()
        rect = window.layout.message_rects[0].rect
        self.assertEqual(hit_test(window.layout, rect.x + 1, rect.y + 1).kind, "message")
        self.assertLessEqual(rect.y - (window.layout.pet_top + window.cell_height), 12)
        window.state.clear_messages()
        window.refresh()
        self.assertEqual(len(window.layout.message_rects), 0)
        self.assertLess(window.height, window.cell_height + 32)

    def test_every_animation_draws_and_advances(self):
        import cairo
        from gi.repository import GLib
        from openpets_linux.core import ANIMATIONS
        from openpets_linux.library import bundled_starcorn_path
        from openpets_linux.ui import PetWindow

        with tempfile.TemporaryDirectory(prefix="openpets-animation-gui-") as directory:
            with patch.dict(os.environ, {"HOME": directory,
                                        "XDG_CONFIG_HOME": directory + "/config",
                                        "XDG_DATA_HOME": directory + "/data",
                                        "DBUS_SESSION_BUS_ADDRESS": ""}):
                window = PetWindow(bundled_starcorn_path(), .42, directory + "/host.sock")
                try:
                    window.start()
                    for animation in ANIMATIONS:
                        with self.subTest(animation=animation):
                            result = window.handle_command({"type": "playAnimation", "name": animation})
                            self.assertTrue(result["ok"])
                            window.refresh(reset_animation=True)
                            self.assertEqual(window.current_animation, animation)
                            if window.frame_source:
                                GLib.source_remove(window.frame_source)
                                window.frame_source = 0
                            window.advance_frame()
                            self.assertGreater(window.frame, 0)
                            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                                         window.width, window.height)
                            window.on_draw(window, cairo.Context(surface))
                            self.assertTrue(any(surface.get_data()))
                finally:
                    window.cleanup()
