"""Pure-stdlib animation helpers for the OpenPets Linux GTK host.

Adapt OpenPetsKit v0.2.1 PetAnimation.swift timing and directional drag
behavior without any GI/GTK dependency, so every helper is unit-testable
headless. The GTK host in :mod:`openpets_linux.ui` owns all GLib/GTK wiring;
this module only supplies data and deterministic helpers.

Public contract (for the UI/geometry reviewer):

* :data:`ANIMATION_DURATIONS` -- per-frame durations (ms) per animation name.
* :data:`IDLE_ACTIONS` -- spontaneous idle actions eligible when the pet is
  idle, not being dragged, and not running a status/manual animation.
* :data:`DIRECTION_ANIMATIONS` -- drag direction -> running animation name.
* :func:`classify_direction` -- classify a drag delta (left/right/up/down/none).
* :func:`drag_animation` -- animation name for a drag delta with sensible
  fallback for every direction.
* :class:`IdlePicker` -- deterministic idle-action + interval picker backed by
  an injected :class:`random.Random` (tests can pass a seeded instance).
"""

from __future__ import annotations

import random
from typing import Optional, Tuple

__all__ = [
    "ANIMATION_DURATIONS",
    "DIRECTION_ANIMATIONS",
    "IDLE_ACTIONS",
    "DIRECTION_THRESHOLD_PX",
    "IDLE_INTERVAL_MIN_MS",
    "IDLE_INTERVAL_MAX_MS",
    "classify_direction",
    "drag_animation",
    "IdlePicker",
]

# Per-frame durations (ms), based on OpenPetsKit PetAnimation.swift, shortened
# for responsive Linux gestures. Idle retains rests, without multi-second freezes.
# The final entry of each tuple is the hold/settle frame. Version 1 atlases are
# 8x9; version 2 atlases are 8x11 and keep these nine animation rows first,
# followed by two look-direction rows not currently played by this host.
ANIMATION_DURATIONS: dict[str, Tuple[int, ...]] = {
    "idle": (600, 180, 180, 180, 180, 900),
    "running-right": (80,) * 7 + (140,),
    "running-left": (80,) * 7 + (140,),
    "waving": (100, 100, 100, 180),
    "jumping": (100, 100, 100, 100, 180),
    "failed": (100,) * 7 + (180,),
    "waiting": (100,) * 5 + (180,),
    "running": (90,) * 5 + (160,),
    "review": (100,) * 5 + (180,),
}

# Animations eligible as spontaneous idle actions when there is no active
# status-driven or manual animation and the pet is not being dragged.
IDLE_ACTIONS: Tuple[str, ...] = ("waving", "jumping")

# Drag direction -> running animation. The atlas has no dedicated up/down
# sprite rows, so vertical drags do not invent a jumping row: they return
# ``"idle"`` and the UI keeps the last horizontal gait (or the underlying
# idle/task animation when none exists). Horizontal drags keep the
# directional gait.
DIRECTION_ANIMATIONS: dict[str, str] = {
    "right": "running-right",
    "left": "running-left",
}

# Minimum pixel delta on either axis before a direction is reported.
DIRECTION_THRESHOLD_PX = 6.0

# Idle scheduler bounds (ms). The next idle action fires after a random
# interval in this range once the pet returns to idle.
IDLE_INTERVAL_MIN_MS = 4000
IDLE_INTERVAL_MAX_MS = 11000


def classify_direction(
    dx: float, dy: float, threshold: float = DIRECTION_THRESHOLD_PX
) -> str:
    """Classify a drag delta into ``left``/``right``/``up``/``down``/``none``.

    The dominant axis wins. Sub-threshold deltas on both axes return ``"none"``.
    """
    adx, ady = abs(dx), abs(dy)
    if adx <= threshold and ady <= threshold:
        return "none"
    if adx >= ady:
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def drag_animation(
    dx: float, dy: float, threshold: float = DIRECTION_THRESHOLD_PX
) -> str:
    """Return the animation for a drag direction.

    Horizontal drags map to ``running-right``/``running-left``. Vertical and
    sub-threshold deltas return ``"idle"``: the atlas has no up/down sprite
    rows, so dragging never invents a jumping row. The UI keeps the last
    horizontal gait (or the underlying idle/task animation) for vertical drag.
    """
    direction = classify_direction(dx, dy, threshold)
    if direction in DIRECTION_ANIMATIONS:
        return DIRECTION_ANIMATIONS[direction]
    return "idle"


class IdlePicker:
    """Pick spontaneous idle actions and intervals from an injected RNG.

    Pure and dependency-free: pass a seeded :class:`random.Random` in tests to
    make idle action selection and scheduling fully deterministic.
    """

    def __init__(
        self,
        actions: Tuple[str, ...] = IDLE_ACTIONS,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.actions = tuple(actions)
        self._rng = rng or random.Random()

    def pick(self) -> str:
        """Return one eligible idle animation, or ``"idle"`` when none exist."""
        if not self.actions:
            return "idle"
        return self._rng.choice(self.actions)

    def next_interval_ms(
        self, minimum_ms: int = IDLE_INTERVAL_MIN_MS, maximum_ms: int = IDLE_INTERVAL_MAX_MS
    ) -> int:
        """Return a random idle-check interval in milliseconds."""
        return self._rng.randint(minimum_ms, maximum_ms)
