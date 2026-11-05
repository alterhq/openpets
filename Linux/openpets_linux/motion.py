"""Pure deterministic motion logic for autonomous pet movement.

No GI/GTK dependency. The GTK host in :mod:`openpets_linux.ui` owns all
GLib/GTK wiring; this module only supplies deterministic motion helpers and
a small clock/RNG-injected :class:`MotionController`.

Public contract:

* :data:`MODE_OFF` / :data:`MODE_FOLLOW` / :data:`MODE_FREE_ROAM` -- movement
  modes, mutually exclusive.
* :data:`MOTION_MODES` -- all valid modes.
* :class:`MotionState` -- plain-data snapshot of motion state.
* :class:`MotionController` -- clock/RNG-injected motion logic.
* :func:`clamp_position_to_workarea` -- keep a window inside a monitor workarea.
* :func:`is_vertical_vector` -- is a movement vector (nearly) straight up/down?
* :func:`gait_for_vector` -- running-left/running-right/idle for a vector.
* :func:`next_free_roam_waypoint` -- random waypoint with min horizontal delta.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

__all__ = [
    "MODE_OFF",
    "MODE_FOLLOW",
    "MODE_FREE_ROAM",
    "MOTION_MODES",
    "MOTION_HZ",
    "MOTION_INTERVAL_MS",
    "DEADZONE_PX",
    "MIN_HORIZONTAL_DELTA_PX",
    "GAIT_CADENCE_MIN_MS",
    "GAIT_CADENCE_MAX_MS",
    "SPEED_PX_PER_SEC",
    "ROAM_SPEED_PX_PER_SEC",
    "ROAM_SPEED_MIN_PX_PER_SEC",
    "ROAM_SPEED_MAX_PX_PER_SEC",
    "ROAM_SEGMENT_MIN_SEC",
    "ROAM_SEGMENT_MAX_SEC",
    "ROAM_REST_MIN_SEC",
    "ROAM_REST_MAX_SEC",
    "ROAM_REST_DURING_CHANCE",
    "ROAM_REST_DURING_MIN_SEC",
    "ROAM_REST_DURING_MAX_SEC",
    "ROAM_PATH_MAX_ANGLE_RAD",
    "ARRIVAL_THRESHOLD_PX",
    "MAX_DT_SEC",
    "MotionState",
    "MotionController",
    "clamp_position_to_workarea",
    "is_vertical_vector",
    "gait_for_vector",
    "next_free_roam_waypoint",
    "is_valid_mode",
]

MODE_OFF = "off"
MODE_FOLLOW = "follow"
MODE_FREE_ROAM = "free_roam"
MOTION_MODES = (MODE_OFF, MODE_FOLLOW, MODE_FREE_ROAM)

# Motion tick cadence (~30 Hz = ~33 ms). Actual atlas/frame timing is
# independent and handled by the animation loop in ui.py.
MOTION_HZ = 30
MOTION_INTERVAL_MS = int(1000.0 / MOTION_HZ)

# Stop near the cursor without obstructing it.
DEADZONE_PX = 24

# Free-roam waypoints must have a meaningful horizontal delta so the pet
# never travels straight vertically (up/down only).
MIN_HORIZONTAL_DELTA_PX = 40

# Gradual gait alternation cadence for vertical targets in follow mode.
GAIT_CADENCE_MIN_MS = 600
GAIT_CADENCE_MAX_MS = 1000

# Autonomous movement speed (pixels per second) used by follow-cursor mode.
SPEED_PX_PER_SEC = 120.0

# --- Free-roam pacing (roam only; follow-cursor is unchanged) -------------

# Slower baseline movement speed for free roam vs follow-cursor (120 px/sec).
ROAM_SPEED_PX_PER_SEC = 70.0

# Bounded variable-speed segment range (pixels per second). A segment picks a
# speed in this range and holds it for a segment duration; mid-trajectory
# changes can suddenly slow down then speed up.
ROAM_SPEED_MIN_PX_PER_SEC = 30.0   # slow stretch
ROAM_SPEED_MAX_PX_PER_SEC = 140.0  # burst

# How long a speed segment lasts (seconds). Independent of waypoint arrival.
ROAM_SEGMENT_MIN_SEC = 1.0
ROAM_SEGMENT_MAX_SEC = 4.0

# Rest between walks (seconds). Truly stops: no gait override, no movement.
ROAM_REST_MIN_SEC = 1.5
ROAM_REST_MAX_SEC = 5.0

# Rest *during* a walk (mid-trajectory): chance per segment-end to briefly
# pause before continuing to the same waypoint.
ROAM_REST_DURING_CHANCE = 0.12
ROAM_REST_DURING_MIN_SEC = 0.5
ROAM_REST_DURING_MAX_SEC = 2.0

# Maximum angle (radians) of a free-roam path away from horizontal. Paths are
# diagonal at a random shallow angle so movement is never straight vertical and
# never an extreme full-screen swing that camps at the top/bottom edges.
# Vertical displacement is proportional to the horizontal step (|dy| <= |dx|
# at 45 deg), which yields varied directions without a systematic sink.
ROAM_PATH_MAX_ANGLE_RAD = math.radians(45.0)

# Consider a waypoint reached within this many pixels.
ARRIVAL_THRESHOLD_PX = 6.0

# Clamp dt so a long frame doesn't teleport the pet.
MAX_DT_SEC = 0.1


def is_valid_mode(mode: str) -> bool:
    """Return True when ``mode`` is one of the valid motion modes."""
    return mode in MOTION_MODES


@dataclass
class MotionState:
    """Plain-data snapshot of autonomous motion state."""

    mode: str = MODE_OFF
    target: Optional[Tuple[float, float]] = None
    waypoint: Optional[Tuple[float, float]] = None
    gait: str = "idle"
    gait_last_switch_ms: float = 0.0
    paused: bool = False
    last_position: Optional[Tuple[float, float]] = None
    settled: bool = True
    moving_since_ms: Optional[float] = None
    # Free-roam pacing state.
    pace_mode: str = "walking"      # "walking" | "resting"
    pace_speed: float = 0.0         # current segment speed (px/sec)
    pace_segment_end_ms: float = 0.0  # when current speed segment ends
    pace_rest_end_ms: float = 0.0   # when current rest ends


def clamp_position_to_workarea(
    x: float,
    y: float,
    width: int,
    height: int,
    area: Tuple[int, int, int, int],
) -> Tuple[int, int]:
    """Clamp a top-left window position so the window stays inside ``area``.

    ``area`` is ``(x, y, width, height)`` of the monitor workarea. Returns
    integer clamped coordinates.
    """
    ax, ay, aw, ah = area
    max_x = ax + max(0, aw - int(width))
    max_y = ay + max(0, ah - int(height))
    return (
        int(max(ax, min(x, max_x))),
        int(max(ay, min(y, max_y))),
    )


def _clamp_float_position(x, y, width, height, area):
    """Clamp a subpixel motion position without quantizing its internal state."""
    ax, ay, aw, ah = area
    max_x = ax + max(0, aw - int(width))
    max_y = ay + max(0, ah - int(height))
    return (max(float(ax), min(float(x), float(max_x))),
            max(float(ay), min(float(y), float(max_y))))


def is_vertical_vector(dx: float, dy: float) -> bool:
    """True when the movement vector is (nearly) straight vertical.

    A vector is "vertical" when its horizontal magnitude is less than 20% of
    its vertical magnitude (roughly within ~11 degrees of straight up/down).
    Used by follow mode to decide whether to alternate running gait instead
    of showing a single directional run.
    """
    if abs(dx) <= 1e-6 and abs(dy) <= 1e-6:
        return False
    return abs(dx) < abs(dy) * 0.2


def gait_for_vector(dx: float, dy: float) -> str:
    """Return ``running-left``/``running-right``/``idle`` for a movement vector.

    Any meaningful horizontal movement (including diagonal) returns the
    corresponding running gait. Only pure vertical or zero vectors return
    ``"idle"`` so the caller can apply gradual gait alternation for vertical
    follow targets.
    """
    adx, ady = abs(dx), abs(dy)
    if adx <= 1e-6 and ady <= 1e-6:
        return "idle"
    if adx > 1e-6:
        # Horizontal component (pure or diagonal): always a directional run.
        return "running-right" if dx > 0 else "running-left"
    return "idle"  # pure vertical (no horizontal component)


def next_free_roam_waypoint(
    position: Tuple[float, float],
    area: Tuple[int, int, int, int],
    width: int,
    height: int,
    rng: random.Random,
) -> Tuple[float, float]:
    """Pick a random free-roam waypoint inside ``area``.

    The waypoint always has a nonzero horizontal displacement so the pet never
    travels straight vertically. At a left/right boundary only a direction with
    real room is chosen (the wrong sign would clamp ``wx == px``, a forbidden
    straight-vertical escape). When no direction offers a meaningful delta
    (>= :data:`MIN_HORIZONTAL_DELTA_PX`), whatever horizontal room exists is
    used; if there is no horizontal room at all, the pet stays still rather
    than moving straight up/down. Returns ``(x, y)`` clamped so the whole
    window fits.
    """
    ax, ay, aw, ah = area
    max_x = ax + max(0, aw - int(width))
    max_y = ay + max(0, ah - int(height))
    px, py = position

    # Clamp the source position into the valid range so out-of-bounds inputs
    # cannot produce out-of-bounds waypoints.
    px = max(ax, min(px, max_x))
    py = max(ay, min(py, max_y))

    # Available horizontal room on each side of px where the whole window
    # still fits inside the workarea.
    left_room = px - ax
    right_room = max_x - px

    # Meaningful horizontal displacement (>= MIN_HORIZONTAL_DELTA_PX).
    candidates = []
    if right_room >= MIN_HORIZONTAL_DELTA_PX:
        candidates.append((1.0, right_room))
    if left_room >= MIN_HORIZONTAL_DELTA_PX:
        candidates.append((-1.0, left_room))

    if candidates:
        sign, room = rng.choice(candidates)
        h_delta = sign * rng.uniform(MIN_HORIZONTAL_DELTA_PX, room)
    else:
        # No direction offers a meaningful delta. Use whatever horizontal
        # room exists (max available) so displacement is still nonzero; if
        # there is no horizontal room at all, stay still.
        best = max(left_room, right_room)
        if best > 0:
            h_delta = best if right_room >= left_room else -best
        else:
            h_delta = 0.0

    # If no horizontal displacement at all, stay completely still (no straight
    # vertical escape).
    if h_delta == 0.0:
        return (px, py)

    # Vertical delta: a diagonal path at a random shallow angle. Movement is
    # never straight vertical (horizontal delta is always meaningful) and never
    # an extreme full-screen swing that camps at the top/bottom edges. Vertical
    # magnitude is proportional to the horizontal step (|dy| <= |dx| at 45 deg),
    # giving varied directions without a systematic bottom/top-edge sink.
    angle = rng.uniform(-ROAM_PATH_MAX_ANGLE_RAD, ROAM_PATH_MAX_ANGLE_RAD)
    v_delta = h_delta * math.tan(angle)

    wx = max(ax, min(px + h_delta, max_x))
    wy = max(ay, min(py + v_delta, max_y))
    return (wx, wy)




class MotionController:
    """Deterministic autonomous motion controller.

    Injected clock and RNG make every decision testable headlessly. No GI,
    no GTK, no sockets.

    The controller owns mode, target/waypoint, gait, and pause state. The
    GTK host calls :meth:`tick` on a ~30 Hz timer and applies the returned
    position/gait to the window.
    """

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._clock: Callable[[], float] = clock or time.monotonic
        self._rng: random.Random = rng or random.Random()
        self.mode: str = MODE_OFF
        self.position: Tuple[float, float] = (0.0, 0.0)
        self.size: Tuple[int, int] = (0, 0)
        self.workarea: Optional[Tuple[int, int, int, int]] = None
        self.cursor: Optional[Tuple[float, float]] = None
        self.state: MotionState = MotionState(mode=MODE_OFF)
        self._paused_by_drag: bool = False

    # -- configuration -------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Switch movement mode. Resets motion state when the mode changes."""
        if mode not in MOTION_MODES:
            raise ValueError(f"unknown motion mode: {mode!r}")
        if mode != self.mode:
            self.mode = mode
            self.state = MotionState(mode=mode)
            self.state.last_position = self.position
            if mode == MODE_FOLLOW:
                # Start moving toward the cursor on the next tick.
                self.state.settled = False
            elif mode == MODE_FREE_ROAM and self.workarea is not None:
                self._start_roam_walk()

    def set_position(self, x: float, y: float) -> None:
        self.position = (float(x), float(y))
        if self.state.last_position is None:
            self.state.last_position = self.position

    def set_size(self, width: int, height: int) -> None:
        self.size = (int(width), int(height))

    def set_workarea(self, area: Optional[Tuple[int, int, int, int]]) -> bool:
        """Set the monitor workarea.

        Only repicks the free-roam waypoint when the bounds actually change,
        so repeated calls (e.g. from ``configure-event`` during owned motion)
        do not reset the timer or jitter the position. Returns True when the
        workarea changed.
        """
        if area is not None:
            area = (int(area[0]), int(area[1]), int(area[2]), int(area[3]))
        if self.workarea == area:
            return False
        self.workarea = area
        if self.mode == MODE_FREE_ROAM and area is not None:
            self._start_roam_walk()
        return True

    def set_cursor(self, x: float, y: float) -> None:
        """Set the mouse cursor position (follow-mode target)."""
        self.cursor = (float(x), float(y))

    # -- drag pause/resume ---------------------------------------------

    def pause(self) -> None:
        """Pause autonomous movement (manual drag). Retains mode/state."""
        self._paused_by_drag = True
        self.state.paused = True

    def resume(self) -> None:
        """Resume autonomous movement after a manual drag.

        Re-anchors to the current position so the pet does not snap back.
        No dt burst on resume: moving_since_ms is set to now so the first
        step uses a normal single-tick delta.
        """
        self._paused_by_drag = False
        self.state.paused = False
        self.state.last_position = self.position
        if self.mode == MODE_FREE_ROAM and self.state.waypoint is None:
            self._start_roam_walk()
        self.state.settled = False
        self.state.moving_since_ms = self._now_ms()

    # -- tick ----------------------------------------------------------

    def tick(
        self, now: Optional[float] = None
    ) -> Optional[Tuple[Tuple[int, int], str, bool]]:
        """Advance one motion step.

        Returns ``((x, y), gait, settled)`` or ``None`` when no movement
        occurs (off mode, paused, hidden, or already settled).

        Follow-cursor mode moves at :data:`SPEED_PX_PER_SEC` toward the
        cursor with no resting. Free-roam mode uses natural pacing:
        bounded variable-speed segments with mid-trajectory changes, random
        rests between and during walks, and a slower baseline speed
        (:data:`ROAM_SPEED_PX_PER_SEC`).
        """
        if now is None:
            now = self._clock()
        now_ms = now * 1000.0

        if (
            self.mode == MODE_OFF
            or self._paused_by_drag
            or self.workarea is None
            or self.size == (0, 0)
        ):
            self.state.settled = True
            return None

        # ---- follow-cursor mode (unchanged pacing, no resting) ------------
        if self.mode == MODE_FOLLOW:
            if self.cursor is None:
                self.state.settled = True
                return None
            target = self.cursor
            speed = SPEED_PX_PER_SEC
        # ---- free-roam mode (natural pacing) -------------------------------
        else:
            # Resting: truly stopped, no gait override, no movement.
            if self.state.pace_mode == "resting":
                if now_ms < self.state.pace_rest_end_ms:
                    self.state.settled = True
                    clamped = clamp_position_to_workarea(
                        self.position[0], self.position[1],
                        self.size[0], self.size[1], self.workarea
                    )
                    return (clamped, None, True)
                # Rest over: resume walking to the current waypoint.
                self.state.pace_mode = "walking"
                if self.state.waypoint is None:
                    self._start_roam_walk()
                else:
                    # A mid-route rest consumes the expired speed segment.
                    # Start a fresh walking segment so an expired deadline
                    # cannot trigger a chain of immediate rests/flicker.
                    self._pick_roam_speed(now_ms)
                # No dt burst on rest resume: anchor to now.
                self.state.moving_since_ms = now_ms

            # Walking: handle mid-trajectory segment end (speed change or
            # brief rest during walk, independent of waypoint arrival).
            if now_ms >= self.state.pace_segment_end_ms:
                if self._rng.random() < ROAM_REST_DURING_CHANCE:
                    self._start_roam_rest(now_ms)
                    self.state.settled = True
                    clamped = clamp_position_to_workarea(
                        self.position[0], self.position[1],
                        self.size[0], self.size[1], self.workarea
                    )
                    return (clamped, None, True)
                self._pick_roam_speed(now_ms)

            target = self.state.waypoint
            speed = self.state.pace_speed

        tx, ty = target
        px, py = self.position
        dx = tx - px
        dy = ty - py
        dist = math.hypot(dx, dy)

        # Settled checks.
        if self.mode == MODE_FOLLOW:
            if dist <= DEADZONE_PX:
                self.state.settled = True
                self.state.gait = "idle"
                clamped = clamp_position_to_workarea(
                    px, py, self.size[0], self.size[1], self.workarea
                )
                # None = no animation override; lets the underlying
                # task/idle animation show through at rest.
                return (clamped, None, True)
        else:
            if dist <= ARRIVAL_THRESHOLD_PX:
                # Arrived at waypoint: rest before picking the next one.
                self.state.pace_mode = "resting"
                # Clear the waypoint so the rest-wake path repicks a fresh one;
                # otherwise the pet would "resume walking to the current
                # waypoint" it is already at and stop forever.
                self.state.waypoint = None
                rest_dur = self._rng.uniform(ROAM_REST_MIN_SEC, ROAM_REST_MAX_SEC)
                self.state.pace_rest_end_ms = now_ms + rest_dur * 1000.0
                self.state.settled = True
                self.state.gait = "idle"
                clamped = clamp_position_to_workarea(
                    px, py, self.size[0], self.size[1], self.workarea
                )
                return (clamped, None, True)

        if dist <= 1e-6:
            self.state.settled = True
            return None

        # Gait for this movement vector.
        gait = self._update_gait(dx, dy, now_ms)

        # Move one step, clamping dt so a lag spike can't teleport.
        base_ms = self.state.moving_since_ms
        if base_ms is None:
            # First movement step: use one motion interval so the pet starts
            # moving immediately rather than waiting a tick.
            dt = MOTION_INTERVAL_MS / 1000.0
        else:
            dt = min(now_ms - base_ms, MAX_DT_SEC * 1000.0) / 1000.0
        step = speed * dt
        if step > dist:
            step = dist
        ratio = step / dist
        nx = px + dx * ratio
        ny = py + dy * ratio
        nx, ny = _clamp_float_position(
            nx, ny, self.size[0], self.size[1], self.workarea
        )
        # Bounds-clamped and unable to make progress toward the target: settle
        # instead of running forever at a screen edge the target can't reach.
        if (nx, ny) == (px, py):
            self.state.settled = True
            self.state.gait = "idle"
            return ((int(nx), int(ny)), None, True)
        self.position = (nx, ny)
        self.state.moving_since_ms = now_ms
        self.state.settled = False
        return ((int(nx), int(ny)), gait, False)

    def _now_ms(self) -> float:
        return self._clock() * 1000.0

    def _start_roam_walk(self) -> None:
        """Begin a free-roam walk: pick waypoint, speed, and segment."""
        self._pick_waypoint()
        self._pick_roam_speed(self._now_ms())
        self.state.pace_mode = "walking"
        self.state.settled = False
        # No dt burst: anchor movement clock to now.
        self.state.moving_since_ms = self._now_ms()

    def _pick_roam_speed(self, now_ms: float) -> None:
        """Pick a new bounded speed and segment duration for free roam.

        Speed can be a slow stretch, baseline, or burst. Segment duration is
        independent of waypoint arrival, so speed changes mid-trajectory.
        """
        r = self._rng.random()
        if r < 0.30:
            # Slow stretch.
            self.state.pace_speed = self._rng.uniform(
                ROAM_SPEED_MIN_PX_PER_SEC, ROAM_SPEED_PX_PER_SEC * 0.7
            )
        elif r < 0.60:
            # Baseline.
            self.state.pace_speed = ROAM_SPEED_PX_PER_SEC
        else:
            # Burst.
            self.state.pace_speed = self._rng.uniform(
                ROAM_SPEED_PX_PER_SEC * 1.3, ROAM_SPEED_MAX_PX_PER_SEC
            )
        segment_dur = self._rng.uniform(ROAM_SEGMENT_MIN_SEC, ROAM_SEGMENT_MAX_SEC)
        self.state.pace_segment_end_ms = now_ms + segment_dur * 1000.0

    def _start_roam_rest(self, now_ms: float) -> None:
        """Begin a free-roam rest: truly stopped, no gait override."""
        self.state.pace_mode = "resting"
        rest_dur = self._rng.uniform(ROAM_REST_MIN_SEC, ROAM_REST_MAX_SEC)
        self.state.pace_rest_end_ms = now_ms + rest_dur * 1000.0

    def _pick_waypoint(self) -> None:
        if self.workarea is None:
            return
        self.state.waypoint = next_free_roam_waypoint(
            self.position, self.workarea, self.size[0], self.size[1], self._rng
        )

    def _update_gait(self, dx: float, dy: float, now_ms: float) -> str:
        """Determine the gait for the current movement vector.

        Near-vertical targets in follow mode slowly alternate running-left /
        running-right (~0.6-1 s cadence) instead of showing a single frame.
        """
        vertical = is_vertical_vector(dx, dy)
        base_gait = gait_for_vector(dx, dy)

        if not vertical:
            self.state.gait = base_gait
            self.state.gait_last_switch_ms = now_ms
            return base_gait

        # Vertical target: alternate running-left/running-right gradually.
        cadence = self._rng.uniform(GAIT_CADENCE_MIN_MS, GAIT_CADENCE_MAX_MS)
        if now_ms - self.state.gait_last_switch_ms >= cadence:
            self.state.gait = (
                "running-left"
                if self.state.gait == "running-right"
                else "running-right"
            )
            self.state.gait_last_switch_ms = now_ms
        if self.state.gait == "idle":
            self.state.gait = "running-right"
        return self.state.gait

    # -- queries -------------------------------------------------------

    def is_settled(self) -> bool:
        return self.state.settled

    def is_moving(self) -> bool:
        return not self.state.settled and not self._paused_by_drag

    def is_paused(self) -> bool:
        return self._paused_by_drag
