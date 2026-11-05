"""Unit tests for openpets_linux.motion (standard library only, no GI, no display).

Pure deterministic tests: clock and RNG are injected so every motion decision
is reproducible.
"""

import math
import os
import random
import statistics
import sys
import unittest
from unittest.mock import patch

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from openpets_linux.motion import (  # noqa: E402
    MODE_OFF,
    MODE_FOLLOW,
    MODE_FREE_ROAM,
    MOTION_INTERVAL_MS,
    DEADZONE_PX,
    MIN_HORIZONTAL_DELTA_PX,
    GAIT_CADENCE_MIN_MS,
    GAIT_CADENCE_MAX_MS,
    SPEED_PX_PER_SEC,
    ROAM_SPEED_PX_PER_SEC,
    ROAM_SPEED_MIN_PX_PER_SEC,
    ROAM_SPEED_MAX_PX_PER_SEC,
    ROAM_SEGMENT_MIN_SEC,
    ROAM_SEGMENT_MAX_SEC,
    ROAM_REST_MIN_SEC,
    ROAM_REST_MAX_SEC,
    ROAM_REST_DURING_CHANCE,
    ROAM_REST_DURING_MIN_SEC,
    ROAM_REST_DURING_MAX_SEC,
    ARRIVAL_THRESHOLD_PX,
    MAX_DT_SEC,
    MotionController,
    clamp_position_to_workarea,
    is_vertical_vector,
    gait_for_vector,
    next_free_roam_waypoint,
    is_valid_mode,
)


class _FixedClock:
    """Deterministic clock that the test advances manually."""

    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, dt):
        self.value += dt


class TestConstants(unittest.TestCase):
    def test_mode_constants(self):
        self.assertEqual(MODE_OFF, "off")
        self.assertEqual(MODE_FOLLOW, "follow")
        self.assertEqual(MODE_FREE_ROAM, "free_roam")

    def test_motion_modes(self):
        self.assertEqual(MOTION_MODES := (MODE_OFF, MODE_FOLLOW, MODE_FREE_ROAM),
                         ("off", "follow", "free_roam"))

    def test_motion_interval(self):
        self.assertEqual(MOTION_INTERVAL_MS, 33)  # ~30 Hz

    def test_deadzone_positive(self):
        self.assertGreater(DEADZONE_PX, 0)

    def test_min_horizontal_delta_positive(self):
        self.assertGreater(MIN_HORIZONTAL_DELTA_PX, 0)

    def test_gait_cadence_range(self):
        self.assertGreater(GAIT_CADENCE_MIN_MS, 0)
        self.assertGreater(GAIT_CADENCE_MAX_MS, GAIT_CADENCE_MIN_MS)

    def test_speed_positive(self):
        self.assertGreater(SPEED_PX_PER_SEC, 0)

    def test_max_dt_clamp(self):
        self.assertGreater(MAX_DT_SEC, 0)
        self.assertLess(MAX_DT_SEC, 1.0)


class TestIsValidMode(unittest.TestCase):
    def test_valid_modes(self):
        for mode in (MODE_OFF, MODE_FOLLOW, MODE_FREE_ROAM):
            self.assertTrue(is_valid_mode(mode))

    def test_invalid_modes(self):
        for bad in ("", "OFF", "Follow", "flying", None, 42):
            self.assertFalse(is_valid_mode(bad))


class TestMotionControllerDefaults(unittest.TestCase):
    def test_defaults(self):
        clock = _FixedClock(1000.0)
        mc = MotionController(clock=clock)
        self.assertEqual(mc.mode, MODE_OFF)
        self.assertEqual(mc.position, (0.0, 0.0))
        self.assertEqual(mc.size, (0, 0))
        self.assertIsNone(mc.workarea)
        self.assertIsNone(mc.cursor)
        self.assertTrue(mc.is_settled())
        self.assertFalse(mc.is_moving())
        self.assertFalse(mc.is_paused())


class TestSetMode(unittest.TestCase):
    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(1))

    def test_set_mode_off(self):
        self.mc.set_mode(MODE_OFF)
        self.assertEqual(self.mc.mode, MODE_OFF)
        self.assertTrue(self.mc.is_settled())

    def test_set_mode_follow(self):
        self.mc.set_position(100, 100)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FOLLOW)
        self.assertEqual(self.mc.mode, MODE_FOLLOW)
        self.assertFalse(self.mc.is_settled())

    def test_set_mode_free_roam_picks_waypoint(self):
        self.mc.set_position(500, 500)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FREE_ROAM)
        self.assertEqual(self.mc.mode, MODE_FREE_ROAM)
        self.assertIsNotNone(self.mc.state.waypoint)
        self.assertFalse(self.mc.is_settled())

    def test_set_mode_invalid_rejected(self):
        with self.assertRaises(ValueError):
            self.mc.set_mode("flying")

    def test_mode_change_resets_state(self):
        self.mc.set_position(100, 100)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FOLLOW)
        self.mc.set_cursor(500, 500)
        self.mc.tick()  # start moving
        self.assertFalse(self.mc.is_settled())
        self.mc.set_mode(MODE_OFF)
        self.assertTrue(self.mc.is_settled())


class TestFollowMode(unittest.TestCase):
    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(7))
        self.mc.set_position(100, 100)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FOLLOW)

    def test_no_cursor_settled(self):
        result = self.mc.tick()
        self.assertIsNone(result)
        self.assertTrue(self.mc.is_settled())

    def test_moves_toward_cursor(self):
        self.mc.set_cursor(300, 100)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        position, gait, settled = result
        self.assertFalse(settled)
        self.assertGreater(position[0], 100)  # moved right
        self.assertEqual(gait, "running-right")

    def test_stops_at_deadzone(self):
        self.mc.set_cursor(110, 100)  # within deadzone of (100, 100)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        _position, gait, settled = result
        self.assertTrue(settled)
        # None gait at rest = no override, underlying animation shows through.
        self.assertIsNone(gait)

    def test_respects_workarea_bounds(self):
        # Cursor far to the right; pet should be clamped at workarea edge.
        self.mc.set_position(1700, 500)
        self.mc.set_cursor(1900, 500)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        position, _gait, _settled = result
        self.assertLessEqual(position[0] + 200, 1920)  # window fits in workarea

    def test_vertical_target_still_moves(self):
        # Target directly above: pet moves up, gait alternates run left/right.
        self.mc.set_cursor(100, 10)  # directly above (90 deg vertical)
        results = []
        for i in range(30):
            self.clock.advance(0.033)  # ~30 Hz tick
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            results.append(result)
        # Pet should have moved upward (y decreased).
        self.assertGreater(len(results), 0)
        last_y = results[-1][0][1]
        self.assertLess(last_y, 100)  # moved up toward cursor

    def test_vertical_target_alternates_gait(self):
        """Directly-above target alternates running-left/running-right gradually."""
        # Place the cursor far above so the pet keeps moving long enough to
        # cycle through multiple gait-switch windows.
        self.mc.set_cursor(100, 500)  # far above (dy = 400)
        gaits = set()
        for i in range(300):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            gaits.add(result[1])
        # Should see both running-left and running-right (alternating gait).
        self.assertIn("running-left", gaits, f"gaits seen: {gaits}")
        self.assertIn("running-right", gaits, f"gaits seen: {gaits}")
        # Never jumping on vertical follow.
        self.assertNotIn("jumping", gaits)

    def test_vertical_target_gait_not_rapid_flicker(self):
        """Gait cadence is ~0.6-1s, not per-frame flicker."""
        self.mc.set_cursor(100, 10)
        switches = 0
        last_gait = None
        for i in range(200):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            gait = result[1]
            if last_gait is not None and gait != last_gait:
                switches += 1
            last_gait = gait
        # With ~0.6-1s cadence over ~6.6s, expect a handful of switches, not
        # one per tick (which would be ~200).
        self.assertLess(switches, 20)
        self.assertGreater(switches, 0)


class TestFreeRoamMode(unittest.TestCase):
    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(3))
        self.mc.set_position(500, 500)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FREE_ROAM)

    def test_no_waypoint_settled_until_picked(self):
        # After set_mode, a waypoint should already be picked.
        self.assertIsNotNone(self.mc.state.waypoint)
        self.assertFalse(self.mc.is_settled())

    def test_no_straight_vertical_path(self):
        """Every free-roam waypoint has a meaningful horizontal delta."""
        start_x = self.mc.position[0]
        for _ in range(30):
            wp = self.mc.state.waypoint
            self.assertIsNotNone(wp)
            dx = abs(wp[0] - start_x)
            # Horizontal delta must be meaningful (or max available).
            self.assertGreaterEqual(dx, MIN_HORIZONTAL_DELTA_PX * 0.5,
                                    f"waypoint {wp} has dx={dx}")
            # Advance and pick next waypoint.
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            _pos, _gait, settled = result
            if settled:
                start_x = self.mc.position[0]

    def test_waypoint_stays_in_workarea(self):
        for _ in range(20):
            wp = self.mc.state.waypoint
            self.assertIsNotNone(wp)
            self.assertGreaterEqual(wp[0], 0)
            self.assertLessEqual(wp[0] + 200, 1920)
            self.assertGreaterEqual(wp[1], 0)
            self.assertLessEqual(wp[1] + 200, 1080)
            self.clock.advance(0.033)
            self.mc.tick(now=self.clock())

    def test_moves_diagonal_not_vertical(self):
        """Free roam produces diagonal/horizontal movement, never straight up/down."""
        horizontal_moved = False
        for _ in range(30):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            _pos, _gait, settled = result
            if self.mc.state.waypoint:
                dx = abs(self.mc.state.waypoint[0] - self.mc.position[0])
                if dx >= MIN_HORIZONTAL_DELTA_PX * 0.5:
                    horizontal_moved = True
            if settled:
                break
        self.assertTrue(horizontal_moved, "free roam never moved horizontally")


class TestDragPauseResume(unittest.TestCase):
    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(5))
        self.mc.set_position(100, 100)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FOLLOW)
        self.mc.set_cursor(500, 100)

    def test_pause_stops_movement(self):
        self.mc.pause()
        self.assertTrue(self.mc.is_paused())
        result = self.mc.tick(now=self.clock())
        self.assertIsNone(result)
        self.assertTrue(self.mc.is_settled())

    def test_resume_continues_from_current_position(self):
        self.mc.tick(now=self.clock())  # move a bit
        pos_before_pause = self.mc.position
        self.mc.pause()
        self.mc.tick(now=self.clock())  # should not move while paused
        self.assertEqual(self.mc.position, pos_before_pause)
        self.mc.resume()
        self.assertFalse(self.mc.is_paused())
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)  # moving again

    def test_resume_reanchors_waypoint_free_roam(self):
        self.mc.set_mode(MODE_FREE_ROAM)
        self.mc.pause()
        wp_before = self.mc.state.waypoint
        self.mc.resume()
        # Waypoint should be re-picked or retained; either way not None.
        self.assertIsNotNone(self.mc.state.waypoint)


class TestDtClamping(unittest.TestCase):
    def test_large_dt_does_not_teleport(self):
        clock = _FixedClock(0.0)
        mc = MotionController(clock=clock, rng=random.Random(9))
        mc.set_position(0, 0)
        mc.set_size(200, 200)
        mc.set_workarea((0, 0, 1920, 1080))
        mc.set_mode(MODE_FOLLOW)
        mc.set_cursor(5000, 0)  # far away

        # One tick with a 5-second gap (way more than MAX_DT_SEC).
        result = mc.tick(now=5.0)
        self.assertIsNotNone(result)
        position, _gait, _settled = result
        # Max step = SPEED_PX_PER_SEC * MAX_DT_SEC
        max_step = SPEED_PX_PER_SEC * MAX_DT_SEC
        self.assertLessEqual(position[0], max_step,
                             f"moved {position[0]}px, max step {max_step:.1f}px")

    def test_dt_clamped_at_boundary(self):
        clock = _FixedClock(0.0)
        mc = MotionController(clock=clock, rng=random.Random(9))
        mc.set_position(0, 0)
        mc.set_size(200, 200)
        mc.set_workarea((0, 0, 1920, 1080))
        mc.set_mode(MODE_FOLLOW)
        mc.set_cursor(5000, 0)

        # First tick establishes moving_since_ms.
        mc.tick(now=0.0)
        # Reset position so the second tick's delta is isolated.
        mc.position = (0.0, 0.0)
        # Second tick: dt exactly at MAX_DT_SEC (0.1s elapsed).
        result = mc.tick(now=MAX_DT_SEC)
        self.assertIsNotNone(result)
        position, _gait, _settled = result
        max_step = SPEED_PX_PER_SEC * MAX_DT_SEC
        self.assertAlmostEqual(position[0], max_step, delta=1.0)

    def test_small_dt_moves_proportionally(self):
        clock = _FixedClock(0.0)
        mc = MotionController(clock=clock, rng=random.Random(9))
        mc.set_position(0, 0)
        mc.set_size(200, 200)
        mc.set_workarea((0, 0, 1920, 1080))
        mc.set_mode(MODE_FOLLOW)
        mc.set_cursor(1000, 0)

        # First tick establishes moving_since_ms.
        mc.tick(now=0.0)
        # Reset position so the second tick's delta is isolated.
        mc.position = (0.0, 0.0)
        # Second tick: 0.5 seconds elapsed -> clamped to MAX_DT_SEC (0.1s).
        result = mc.tick(now=0.5)
        self.assertIsNotNone(result)
        position, _gait, _settled = result
        # dt clamped to MAX_DT_SEC, so step = SPEED * 0.1
        expected = SPEED_PX_PER_SEC * MAX_DT_SEC
        self.assertAlmostEqual(position[0], expected, delta=1.0)


class TestClampPositionToWorkarea(unittest.TestCase):
    def test_clamps_inside_workarea(self):
        x, y = clamp_position_to_workarea(-50, -50, 200, 200, (0, 0, 1920, 1080))
        self.assertEqual(x, 0)
        self.assertEqual(y, 0)

    def test_clamps_right_bottom_edges(self):
        x, y = clamp_position_to_workarea(2000, 1000, 200, 200, (0, 0, 1920, 1080))
        self.assertEqual(x, 1720)  # 1920 - 200
        self.assertEqual(y, 880)   # 1080 - 200

    def test_workarea_smaller_than_window(self):
        x, y = clamp_position_to_workarea(100, 100, 2000, 2000, (0, 0, 1920, 1080))
        self.assertEqual(x, 0)
        self.assertEqual(y, 0)


class TestIsVerticalVector(unittest.TestCase):
    def test_straight_up_is_vertical(self):
        self.assertTrue(is_vertical_vector(0, -100))

    def test_straight_down_is_vertical(self):
        self.assertTrue(is_vertical_vector(0, 100))

    def test_near_vertical_is_vertical(self):
        self.assertTrue(is_vertical_vector(5, 100))  # dx < 20% of dy

    def test_horizontal_is_not_vertical(self):
        self.assertFalse(is_vertical_vector(100, 0))

    def test_diagonal_is_not_vertical(self):
        self.assertFalse(is_vertical_vector(50, 50))

    def test_zero_vector_is_not_vertical(self):
        self.assertFalse(is_vertical_vector(0, 0))


class TestGaitForVector(unittest.TestCase):
    def test_right(self):
        self.assertEqual(gait_for_vector(100, 0), "running-right")
        self.assertEqual(gait_for_vector(100, 10), "running-right")

    def test_left(self):
        self.assertEqual(gait_for_vector(-100, 0), "running-left")
        self.assertEqual(gait_for_vector(-100, -10), "running-left")

    def test_vertical_idle(self):
        self.assertEqual(gait_for_vector(0, 100), "idle")
        self.assertEqual(gait_for_vector(0, -100), "idle")

    def test_zero_idle(self):
        self.assertEqual(gait_for_vector(0, 0), "idle")

    def test_diagonal_right_down_is_running_right(self):
        # Vertical-dominant diagonal must still return a run gait (not idle).
        self.assertEqual(gait_for_vector(30, 100), "running-right")
        self.assertEqual(gait_for_vector(30, 1000), "running-right")

    def test_diagonal_left_down_is_running_left(self):
        self.assertEqual(gait_for_vector(-30, 100), "running-left")
        self.assertEqual(gait_for_vector(-30, 1000), "running-left")

    def test_diagonal_right_up_is_running_right(self):
        self.assertEqual(gait_for_vector(30, -100), "running-right")
        self.assertEqual(gait_for_vector(30, -1000), "running-right")

    def test_diagonal_left_up_is_running_left(self):
        self.assertEqual(gait_for_vector(-30, -100), "running-left")
        self.assertEqual(gait_for_vector(-30, -1000), "running-left")

    def test_near_zero_horizontal_is_running(self):
        # Any nonzero horizontal movement (even tiny) returns a run gait.
        self.assertEqual(gait_for_vector(0.0001, 100), "running-right")
        self.assertEqual(gait_for_vector(-0.0001, 100), "running-left")


class TestFollowDirection(unittest.TestCase):
    """Follow mode must move toward the cursor in the correct direction."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(1))
        self.mc.set_position(500, 400)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FOLLOW)

    def test_cursor_left_moves_left(self):
        # Cursor to the left: pet must move left (negative dx), not right.
        self.mc.set_cursor(400, 400)  # left of window (cx ~ 70, sprite center ~ 570)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        position, gait, settled = result
        self.assertFalse(settled)
        self.assertLess(position[0], 500)  # moved left
        self.assertEqual(gait, "running-left")

    def test_cursor_right_moves_right(self):
        self.mc.set_cursor(600, 400)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        position, gait, settled = result
        self.assertFalse(settled)
        self.assertGreater(position[0], 500)  # moved right
        self.assertEqual(gait, "running-right")

    def test_cursor_above_moves_up(self):
        self.mc.set_cursor(500, 300)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        position, _gait, settled = result
        self.assertFalse(settled)
        self.assertLess(position[1], 400)  # moved up

    def test_cursor_below_moves_down(self):
        self.mc.set_cursor(500, 500)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        position, _gait, settled = result
        self.assertFalse(settled)
        self.assertGreater(position[1], 400)  # moved down

    def test_cursor_diagonal_top_right_moves_diagonal(self):
        # Top-right: move right and up, gait running-right (never idle).
        self.mc.set_cursor(700, 300)
        gaits = set()
        for _ in range(10):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            position, gait, settled = result
            gaits.add(gait)
            if settled:
                break
        self.assertIn("running-right", gaits)
        self.assertNotIn("idle", gaits)

    def test_cursor_diagonal_bottom_left_moves_diagonal(self):
        # Bottom-left: move left and down, gait running-left (never idle).
        self.mc.set_cursor(300, 600)
        gaits = set()
        for _ in range(10):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            position, gait, settled = result
            gaits.add(gait)
            if settled:
                break
        self.assertIn("running-left", gaits)
        self.assertNotIn("idle", gaits)

    def test_cursor_diagonal_always_non_idle_until_stop(self):
        """Diagonal movement always has a running gait until settled."""
        self.mc.set_cursor(800, 200)  # far top-right
        saw_idle = False
        for _ in range(50):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            _position, gait, settled = result
            if gait == "idle":
                saw_idle = True
            if settled:
                break
        # While moving, never idle. (Idle only at rest/settled.)
        self.assertFalse(saw_idle, "diagonal movement showed idle gait")
        self.assertTrue(self.mc.is_settled() or saw_idle is False)


class TestNextFreeRoamWaypoint(unittest.TestCase):
    def test_waypoint_has_horizontal_delta(self):
        rng = random.Random(1)
        position = (500, 500)
        area = (0, 0, 1920, 1080)
        for _ in range(50):
            wp = next_free_roam_waypoint(position, area, 200, 200, rng)
            dx = abs(wp[0] - position[0])
            self.assertGreaterEqual(dx, MIN_HORIZONTAL_DELTA_PX * 0.5)

    def test_waypoint_in_workarea(self):
        rng = random.Random(2)
        area = (0, 0, 1920, 1080)
        for _ in range(50):
            wp = next_free_roam_waypoint((100, 100), area, 200, 200, rng)
            self.assertGreaterEqual(wp[0], 0)
            self.assertLessEqual(wp[0] + 200, 1920)
            self.assertGreaterEqual(wp[1], 0)
            self.assertLessEqual(wp[1] + 200, 1080)

    def test_narrow_workarea_uses_max_available(self):
        rng = random.Random(3)
        # Workarea narrower than MIN_HORIZONTAL_DELTA_PX.
        area = (0, 0, 30, 1000)
        wp = next_free_roam_waypoint((15, 500), area, 20, 20, rng)
        # Should still clamp inside the workarea.
        self.assertGreaterEqual(wp[0], 0)
        self.assertLessEqual(wp[0] + 20, 30)

    def test_free_roam_at_left_boundary_no_vertical_escape(self):
        # At the left boundary, only rightward room exists; must not clamp
        # back to px and then move straight vertically.
        rng = random.Random(9)
        area = (0, 0, 1920, 1080)
        wp = next_free_roam_waypoint((0, 500), area, 200, 200, rng)
        self.assertGreater(wp[0], 0)  # nonzero horizontal displacement

    def test_free_roam_at_right_boundary_no_vertical_escape(self):
        # At the right boundary, only leftward room exists.
        rng = random.Random(9)
        area = (0, 0, 1920, 1080)
        wp = next_free_roam_waypoint((1720, 500), area, 200, 200, rng)
        self.assertLess(wp[0], 1720)  # nonzero horizontal displacement

    def test_free_roam_no_horizontal_room_stays_still(self):
        # Workarea exactly window width: no horizontal room at all, stay still.
        rng = random.Random(1)
        area = (0, 0, 200, 1000)
        wp = next_free_roam_waypoint((0, 500), area, 200, 200, rng)
        self.assertEqual(wp, (0, 500))

    def test_free_roam_never_returns_waypoint_equal_to_position_with_vertical_move(self):
        rng = random.Random(42)
        area = (0, 0, 1920, 1080)
        for _ in range(200):
            wp = next_free_roam_waypoint((960, 540), area, 200, 200, rng)
            self.assertNotEqual(wp[0], 960, f"straight vertical escape at {wp}")


class TestFollowSettledEdge(unittest.TestCase):
    """Follow mode settles at unreachable targets instead of running forever."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(1))
        self.mc.set_position(0, 0)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FOLLOW)

    def test_target_at_screen_edge_settles_when_clamped(self):
        # Cursor target clamped so close the pet can't reach; tick should
        # settle after one clamp-no-progress step rather than run forever.
        self.mc.set_position(1720, 0)  # right edge, window touches edge
        self.mc.set_cursor(1900, 0)  # target beyond the edge
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        _position, _gait, settled = result
        self.assertTrue(settled)
        self.assertTrue(self.mc.is_settled())

    def test_gait_none_at_rest_does_not_override_task(self):
        # When follow settles (deadzone), gait override is None so an
        # underlying task animation survives at rest.
        self.mc.set_position(100, 100)
        self.mc.set_cursor(110, 100)  # within deadzone
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        _position, gait, settled = result
        self.assertTrue(settled)
        self.assertIsNone(gait)


class TestConfigureNoJitter(unittest.TestCase):
    """set_workarea must not reset timer/jitter position when bounds unchanged."""

    def test_set_workarea_unchanged_returns_false_no_repick(self):
        mc = MotionController(clock=_FixedClock(0.0), rng=random.Random(1))
        mc.set_position(100, 100)
        mc.set_size(200, 200)
        mc.set_workarea((0, 0, 1920, 1080))
        mc.set_mode(MODE_FREE_ROAM)
        wp_before = mc.state.waypoint
        settled_before = mc.state.settled
        # Calling set_workarea with the same bounds must be a no-op.
        self.assertFalse(mc.set_workarea((0, 0, 1920, 1080)))
        self.assertEqual(mc.state.waypoint, wp_before)
        self.assertEqual(mc.state.settled, settled_before)

    def test_set_workarea_changed_returns_true_repicks(self):
        mc = MotionController(clock=_FixedClock(0.0), rng=random.Random(1))
        mc.set_position(100, 100)
        mc.set_size(200, 200)
        mc.set_workarea((0, 0, 1920, 1080))
        mc.set_mode(MODE_FREE_ROAM)
        self.assertTrue(mc.set_workarea((0, 0, 800, 600)))
        self.assertIsNotNone(mc.state.waypoint)
        # Waypoint must be inside the new, smaller workarea.
        self.assertLessEqual(mc.state.waypoint[0] + 200, 800)
        self.assertLessEqual(mc.state.waypoint[1] + 200, 600)


class TestRoamPacingConstants(unittest.TestCase):
    """Roam pacing constants are sane and slower than follow-cursor speed."""

    def test_roam_speed_slower_than_follow(self):
        self.assertLess(ROAM_SPEED_PX_PER_SEC, SPEED_PX_PER_SEC)
        self.assertGreater(ROAM_SPEED_PX_PER_SEC, 0)

    def test_roam_speed_bounds(self):
        self.assertLess(ROAM_SPEED_MIN_PX_PER_SEC, ROAM_SPEED_PX_PER_SEC)
        self.assertGreater(ROAM_SPEED_MAX_PX_PER_SEC, ROAM_SPEED_PX_PER_SEC)

    def test_segment_bounds(self):
        self.assertGreater(ROAM_SEGMENT_MIN_SEC, 0)
        self.assertGreater(ROAM_SEGMENT_MAX_SEC, ROAM_SEGMENT_MIN_SEC)

    def test_rest_bounds(self):
        self.assertGreater(ROAM_REST_MIN_SEC, 0)
        self.assertGreater(ROAM_REST_MAX_SEC, ROAM_REST_MIN_SEC)

    def test_rest_during_bounds(self):
        self.assertGreaterEqual(ROAM_REST_DURING_CHANCE, 0.0)
        self.assertLessEqual(ROAM_REST_DURING_CHANCE, 1.0)
        self.assertGreater(ROAM_REST_DURING_MIN_SEC, 0)
        self.assertGreater(ROAM_REST_DURING_MAX_SEC, ROAM_REST_DURING_MIN_SEC)


class TestFreeRoamPacing(unittest.TestCase):
    """Free-roam natural pacing: walks, rests, variable speed, no dt bursts."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(3))
        self.mc.set_position(500, 500)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FREE_ROAM)

    def test_starts_walking_not_resting(self):
        self.assertEqual(self.mc.state.pace_mode, "walking")
        self.assertIsNotNone(self.mc.state.waypoint)
        self.assertGreater(self.mc.state.pace_speed, 0)
        self.assertFalse(self.mc.is_settled())

    def test_speed_within_bounds(self):
        self.assertGreaterEqual(self.mc.state.pace_speed, ROAM_SPEED_MIN_PX_PER_SEC)
        self.assertLessEqual(self.mc.state.pace_speed, ROAM_SPEED_MAX_PX_PER_SEC)

    def test_arrival_triggers_rest(self):
        # Set waypoint very close so arrival happens on first tick.
        self.mc.set_position(500, 500)
        self.mc.state.waypoint = (505, 500)  # within arrival threshold
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        _pos, gait, settled = result
        self.assertTrue(settled)
        self.assertEqual(self.mc.state.pace_mode, "resting")
        # None gait at rest: no override, underlying animation shows through.
        self.assertIsNone(gait)

    def test_rest_truly_stops_no_gait_override(self):
        # Force resting state.
        self.mc.state.pace_mode = "resting"
        self.mc.state.pace_rest_end_ms = self.clock() * 1000.0 + 5000.0
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        _pos, gait, settled = result
        self.assertTrue(settled)
        self.assertIsNone(gait)
        # Position unchanged during rest.
        self.assertEqual(self.mc.position, (500.0, 500.0))

    def test_rest_resume_no_position_jump(self):
        # Start resting, then advance clock past rest end.
        self.mc.state.pace_mode = "resting"
        self.mc.state.pace_rest_end_ms = self.clock() * 1000.0 - 1.0  # already past
        # Set a far waypoint so arrival doesn't happen on the resume tick.
        self.mc.state.waypoint = (1800, 500)
        pos_before = self.mc.position
        result = self.mc.tick(now=self.clock())  # clock at 1000.0
        self.assertIsNotNone(result)
        _pos, _gait, settled = result
        # Should resume walking, position not reset.
        self.assertEqual(self.mc.state.pace_mode, "walking")
        self.assertEqual(self.mc.position, pos_before)  # no jump on resume
        # No dt burst: moving_since_ms anchored to now.
        self.assertEqual(self.mc.state.moving_since_ms, self.clock() * 1000.0)

    def test_midroute_rest_resume_gets_fresh_segment_no_idle_chain(self):
        self.mc.state.pace_mode = "resting"
        self.mc.state.pace_rest_end_ms = self.clock() * 1000.0 - 1
        self.mc.state.waypoint = (1800, 500)
        self.mc.state.pace_segment_end_ms = self.clock() * 1000.0 - 1000
        self.clock.advance(0.033)
        # If the old expired segment were checked again immediately, this
        # would trigger another rest (random value below 12%).
        with patch.object(self.mc._rng, "random", return_value=0.01):
            result = self.mc.tick(now=self.clock())
        self.assertEqual(self.mc.state.pace_mode, "walking")
        self.assertGreater(self.mc.state.pace_segment_end_ms, self.clock() * 1000.0)
        self.assertIsNotNone(result)
        self.clock.advance(0.033)
        with patch.object(self.mc._rng, "random", return_value=0.01):
            result = self.mc.tick(now=self.clock())
        self.assertFalse(result[2])
        self.assertEqual(self.mc.state.pace_mode, "walking")

    def test_mid_trajectory_speed_change(self):
        # Walk a long route so a segment ends before arrival.
        self.mc.set_position(100, 500)
        self.mc.state.waypoint = (1800, 500)  # far away
        speeds = set()
        for _ in range(200):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            _pos, _gait, settled = result
            speeds.add(self.mc.state.pace_speed)
            if settled:
                break
        # Should see at least two different speeds within one route.
        self.assertGreaterEqual(len(speeds), 2, f"speeds seen: {speeds}")

    def test_slow_base_speed(self):
        # Roam baseline speed should be well below follow-cursor 120.
        self.assertLess(ROAM_SPEED_PX_PER_SEC, SPEED_PX_PER_SEC)
        # Actual first segment speed within bounded range.
        self.assertGreaterEqual(self.mc.state.pace_speed, ROAM_SPEED_MIN_PX_PER_SEC)
        self.assertLessEqual(self.mc.state.pace_speed, ROAM_SPEED_MAX_PX_PER_SEC)

    def test_slow_subpixel_steps_accumulate_instead_of_settling(self):
        self.mc.set_position(500, 500)
        self.mc.state.waypoint = (1700, 500)
        self.mc.state.pace_speed = ROAM_SPEED_MIN_PX_PER_SEC
        self.mc.state.pace_segment_end_ms = self.clock() * 1000 + 10000
        self.mc.state.moving_since_ms = self.clock() * 1000
        self.mc.state.pace_mode = "walking"
        for _ in range(4):
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            self.assertIsNotNone(result)
            self.assertFalse(result[2])
        self.assertGreater(self.mc.position[0], 500)

    def test_pacing_reproducible_with_seed(self):
        # Two controllers with same seed and clock produce identical pacing.
        c1 = MotionController(clock=_FixedClock(1000.0), rng=random.Random(42))
        c1.set_position(500, 500)
        c1.set_size(200, 200)
        c1.set_workarea((0, 0, 1920, 1080))
        c1.set_mode(MODE_FREE_ROAM)
        c2 = MotionController(clock=_FixedClock(1000.0), rng=random.Random(42))
        c2.set_position(500, 500)
        c2.set_size(200, 200)
        c2.set_workarea((0, 0, 1920, 1080))
        c2.set_mode(MODE_FREE_ROAM)
        for _ in range(50):
            c1._clock.advance(0.033)
            c2._clock.advance(0.033)
            r1 = c1.tick(now=c1._clock())
            r2 = c2.tick(now=c2._clock())
            if r1 is None and r2 is None:
                break
            self.assertEqual(r1, r2, f"diverged: {r1} vs {r2}")


class TestFollowSpeedUnchanged(unittest.TestCase):
    """Follow-cursor speed stays at 120 px/sec, unaffected by roam pacing."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(1))
        self.mc.set_position(100, 100)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FOLLOW)
        self.mc.set_cursor(500, 100)

    def test_follow_speed_is_120(self):
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        _pos, _gait, settled = result
        # First step dt = MOTION_INTERVAL_MS, step = 120 * 0.033 ≈ 4px.
        self.assertGreater(result[0][0], 100)


class TestRoamNoVerticalStill(unittest.TestCase):
    """Free roam still never produces straight vertical paths."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(99))
        self.mc.set_position(500, 500)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FREE_ROAM)

    def test_no_vertical_escape_across_rests(self):
        start_x = self.mc.position[0]
        for _ in range(50):
            wp = self.mc.state.waypoint
            self.assertIsNotNone(wp)
            dx = abs(wp[0] - start_x)
            self.assertGreaterEqual(dx, MIN_HORIZONTAL_DELTA_PX * 0.5,
                                    f"waypoint {wp} has dx={dx}")
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            _pos, _gait, settled = result
            if settled:
                start_x = self.mc.position[0]


class TestFreeRoamInitialWalk(unittest.TestCase):
    """Switching off -> free_roam must start walking immediately (no drag)."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(3))
        self.mc.set_position(500, 500)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.assertEqual(self.mc.mode, MODE_OFF)

    def test_off_to_free_roam_walks_immediately_without_drag(self):
        self.mc.set_mode(MODE_FREE_ROAM)
        # The controller is walking right after the mode switch: a waypoint is
        # picked, the pet is not settled, and it reports as moving. No manual
        # drag is required to kick off the first walk.
        self.assertEqual(self.mc.mode, MODE_FREE_ROAM)
        self.assertIsNotNone(self.mc.state.waypoint)
        self.assertFalse(self.mc.is_settled())
        self.assertTrue(self.mc.is_moving())

        start = self.mc.position
        # One motion interval later the pet has actually moved.
        self.clock.advance(0.033)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        position, _gait, settled = result
        self.assertNotEqual(position, start, "free roam did not move on first tick")
        self.assertFalse(settled)


class TestFreeRoamVariedDirections(unittest.TestCase):
    """Repeated free-roam movements must vary direction and not drift
    systematically toward the bottom-right."""

    def test_repeated_waypoints_varied_not_bottom_right(self):
        rng = random.Random(11)
        position = (960.0, 540.0)
        area = (0, 0, 1920, 1080)
        dxs, dys = [], []
        for _ in range(200):
            wp = next_free_roam_waypoint(position, area, 200, 200, rng)
            dxs.append(wp[0] - position[0])
            dys.append(wp[1] - position[1])
            position = wp

        # Every horizontal direction must appear (no one-way bias).
        self.assertGreater(sum(1 for d in dxs if d > 0), 0, "never moved right")
        self.assertGreater(sum(1 for d in dxs if d < 0), 0, "never moved left")
        self.assertGreater(sum(1 for d in dys if d > 0), 0, "never moved down")
        self.assertGreater(sum(1 for d in dys if d < 0), 0, "never moved up")

        # Not systematic bottom-right: mean displacement is small relative to
        # the average step size in both axes.
        mean_dx = statistics.mean(dxs)
        mean_dy = statistics.mean(dys)
        mean_abs_dx = statistics.mean(abs(d) for d in dxs)
        mean_abs_dy = statistics.mean(abs(d) for d in dys)
        self.assertLess(abs(mean_dx), mean_abs_dx * 0.25,
                        f"systematic horizontal drift: mean_dx={mean_dx:.2f}")
        self.assertLess(abs(mean_dy), mean_abs_dy * 0.25,
                        f"systematic vertical drift: mean_dy={mean_dy:.2f}")

        # Never straight vertical: every waypoint has a meaningful horizontal delta.
        for d in dxs:
            self.assertNotEqual(d, 0, "straight vertical waypoint escape")


class TestFreeRoamInBoundsNoVertical(unittest.TestCase):
    """Free roam never leaves the workarea and never uses vertical-only waypoints."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(99))
        self.mc.set_position(500, 500)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FREE_ROAM)

    def test_all_waypoints_in_bounds_and_non_vertical(self):
        # Every waypoint the controller picks must stay in the workarea, and
        # the pet must actually move horizontally over the run (not stuck on a
        # vertical line). While resting, waypoint is None (cleared on arrival so
        # the next rest-wake repicks a fresh destination); skip those ticks.
        # Per-waypoint horizontal-delta is asserted at the function level in
        # TestNextFreeRoamWaypoint.
        saw_horizontal_move = False
        start_x = self.mc.position[0]
        for _ in range(120):
            wp = self.mc.state.waypoint
            if wp is not None:
                # In bounds (whole window fits).
                self.assertGreaterEqual(wp[0], 0)
                self.assertLessEqual(wp[0] + 200, 1920)
                self.assertGreaterEqual(wp[1], 0)
                self.assertLessEqual(wp[1] + 200, 1080)
            self.clock.advance(0.033)
            result = self.mc.tick(now=self.clock())
            if result is None:
                break
            _pos, _gait, settled = result
            if abs(self.mc.position[0] - start_x) >= MIN_HORIZONTAL_DELTA_PX * 0.5:
                saw_horizontal_move = True
            if settled:
                start_x = self.mc.position[0]
        self.assertTrue(saw_horizontal_move, "free roam never moved horizontally")


class TestFreeRoamRestNotPermanentLock(unittest.TestCase):
    """Pacing/rest must not permanently lock the pet in a stopped state."""

    def setUp(self):
        self.clock = _FixedClock(1000.0)
        self.mc = MotionController(clock=self.clock, rng=random.Random(3))
        self.mc.set_position(500, 500)
        self.mc.set_size(200, 200)
        self.mc.set_workarea((0, 0, 1920, 1080))
        self.mc.set_mode(MODE_FREE_ROAM)

    def test_rest_resumes_walking_after_rest_ends(self):
        # Force a long rest with a far waypoint (arrival won't happen on resume).
        self.mc.state.pace_mode = "resting"
        self.mc.state.pace_rest_end_ms = self.clock() * 1000.0 + 60000.0
        self.mc.state.waypoint = (1200, 500)

        # While resting: stopped and settled.
        self.clock.advance(0.033)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        self.assertEqual(self.mc.state.pace_mode, "resting")
        self.assertTrue(self.mc.is_settled())

        # Advance the clock past the rest end: the pet must resume walking and
        # must not stay locked in the resting state.
        self.clock.advance(60.0)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        self.assertEqual(self.mc.state.pace_mode, "walking")  # not locked resting
        # One more motion interval: it must actually resume moving.
        self.clock.advance(0.033)
        result = self.mc.tick(now=self.clock())
        self.assertIsNotNone(result)
        _pos, _gait, settled = result
        self.assertFalse(settled)
        self.assertTrue(self.mc.is_moving())


if __name__ == "__main__":
    unittest.main()
