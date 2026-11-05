# Linux animation and compact-status plan

## Acceptance criteria

- All nine atlas rows are accessible through MCP and actually advance/render.
- Active task states (`running`, `review`, `waiting`, `failed`) remain visible
  until superseded, explicitly stopped or expired. Completion is finite.
- Dragging selects left/right gait from pointer movement; vertical movement does
  not jump (the atlas has no up/down rows) and preserves the last horizontal gait
  or falls back to the underlying idle/task animation.
- Releasing the pointer restores the underlying task/manual animation.
- Idle occasionally performs a finite gesture without moving the pet around the
  desktop or interrupting an active task. No continuous high-FPS busy loop.
- Plugin chips are anchored immediately above the pet; at most three thread
  summaries sit immediately below it. Empty reserved rows are not clickable and
  do not leave a visible gap.
- Status text remains plain text, clipped/ellipsized; details/actions remain
  user-click only. No new credential/network access.

## Implementation sequence

1. Establish baseline, compare Swift contracts and existing atlas timing.
2. Run independent animation, MCP and overlay teammates with file ownership.
3. Integrate overlay rendering/input hit regions into the animation-owned window.
4. Run pure tests, full SDK tests, real GTK drawing and isolated IPC smoke.
5. Review protected macOS files and document restart/reload requirements.

The existing uncommitted Linux preview remains in place. No Git commit, push,
package installation, global configuration edit or macOS source change is part
of this work. See `linux-team-board.md` for owners and verification gates.
