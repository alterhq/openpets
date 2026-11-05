"""Unit tests for openpets_linux.animation (standard library only, no GI)."""

import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from openpets_linux.animation import (  # noqa: E402
    ANIMATION_DURATIONS,
    DIRECTION_ANIMATIONS,
    IDLE_ACTIONS,
    IDLE_INTERVAL_MAX_MS,
    IDLE_INTERVAL_MIN_MS,
    IdlePicker,
    classify_direction,
    drag_animation,
)

import random


class TestAnimationCatalog(unittest.TestCase):
    def test_durations_cover_every_animation(self):
        for name in (
            "idle",
            "running-right",
            "running-left",
            "waving",
            "jumping",
            "failed",
            "waiting",
            "running",
            "review",
        ):
            self.assertIn(name, ANIMATION_DURATIONS)
            durations = ANIMATION_DURATIONS[name]
            self.assertTrue(len(durations) >= 2)
            for frame_ms in durations:
                self.assertIsInstance(frame_ms, int)
                self.assertGreater(frame_ms, 0)

    def test_direction_animations(self):
        self.assertEqual(DIRECTION_ANIMATIONS["right"], "running-right")
        self.assertEqual(DIRECTION_ANIMATIONS["left"], "running-left")


class TestClassifyDirection(unittest.TestCase):
    def test_sub_threshold_is_none(self):
        self.assertEqual(classify_direction(0, 0), "none")
        self.assertEqual(classify_direction(3, -3), "none")
        self.assertEqual(classify_direction(5, 0), "none")

    def test_horizontal_dominates(self):
        self.assertEqual(classify_direction(100, 1), "right")
        self.assertEqual(classify_direction(-100, 1), "left")
        # tie on magnitude goes horizontal
        self.assertEqual(classify_direction(10, 10), "right")
        self.assertEqual(classify_direction(-10, 10), "left")

    def test_vertical_dominates(self):
        self.assertEqual(classify_direction(1, 100), "down")
        self.assertEqual(classify_direction(1, -100), "up")

    def test_threshold_respected(self):
        self.assertEqual(classify_direction(6, 0), "none")
        self.assertEqual(classify_direction(-6, 0), "none")
        self.assertEqual(classify_direction(0, 6), "none")
        self.assertEqual(classify_direction(0, -6), "none")
        self.assertEqual(classify_direction(7, 0), "right")
        self.assertEqual(classify_direction(-7, 0), "left")
        self.assertEqual(classify_direction(0, 7), "down")
        self.assertEqual(classify_direction(0, -7), "up")

    def test_custom_threshold(self):
        self.assertEqual(classify_direction(10, 0, threshold=20), "none")
        self.assertEqual(classify_direction(10, 0, threshold=5), "right")


class TestDragAnimation(unittest.TestCase):
    def test_right_drag_running_right(self):
        self.assertEqual(drag_animation(50, 0), "running-right")
        self.assertEqual(drag_animation(50, 10), "running-right")

    def test_left_drag_running_left(self):
        self.assertEqual(drag_animation(-50, 0), "running-left")
        self.assertEqual(drag_animation(-50, -5), "running-left")

    def test_vertical_drag_returns_idle_no_jumping(self):
        # The atlas has no up/down rows, so dragging never invents a jumping
        # row. Vertical drag returns "idle": the UI preserves the last
        # horizontal gait (or falls back to the underlying animation).
        self.assertEqual(drag_animation(0, 50), "idle")
        self.assertEqual(drag_animation(0, -50), "idle")
        self.assertEqual(drag_animation(3, 40), "idle")
        self.assertEqual(drag_animation(-3, -40), "idle")

    def test_no_movement_idle(self):
        self.assertEqual(drag_animation(0, 0), "idle")
        self.assertEqual(drag_animation(2, 1), "idle")


class TestIdlePicker(unittest.TestCase):
    def test_pick_returns_eligible_action(self):
        picker = IdlePicker(rng=random.Random(1))
        picked = picker.pick()
        self.assertIn(picked, IDLE_ACTIONS)

    def test_pick_deterministic_with_seed(self):
        a = IdlePicker(rng=random.Random(42)).pick()
        b = IdlePicker(rng=random.Random(42)).pick()
        self.assertEqual(a, b)

    def test_no_actions_returns_idle(self):
        picker = IdlePicker(actions=(), rng=random.Random(1))
        self.assertEqual(picker.pick(), "idle")

    def test_interval_in_bounds(self):
        picker = IdlePicker(rng=random.Random(7))
        for _ in range(100):
            interval = picker.next_interval_ms()
            self.assertGreaterEqual(interval, IDLE_INTERVAL_MIN_MS)
            self.assertLessEqual(interval, IDLE_INTERVAL_MAX_MS)

    def test_interval_custom_bounds(self):
        picker = IdlePicker(rng=random.Random(7))
        interval = picker.next_interval_ms(100, 200)
        self.assertGreaterEqual(interval, 100)
        self.assertLessEqual(interval, 200)


if __name__ == "__main__":
    unittest.main()
