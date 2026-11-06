# OpenPets Linux — compact status bubbles and message threads

## Goals

Replace the overly tall fixed header in `Linux/openpets_linux/ui.py`
(`HEADER_HEIGHT = 210`, `MESSAGE_TOP = 90`, window height `cell_height +
HEADER_HEIGHT`) with compact blocks anchored next to the pet sprite:

* **Plugin chips** — small colored chips stacked in rows directly **above**
  the pet sprite.
* **Message threads** — 1–2 line bounded-width bubbles stacked directly
  **below** the pet sprite.
* **Comic chat bubble** — when its opt-in plugin is enabled, conversational
  detail appears in a title-free rounded speech balloon with a connected tail
  aimed at the pet, on the side opposite the plugin stack.

Everything else clicks through to the desktop; the window grows only as much
as the visible content needs.

## Module

`Linux/openpets_linux/overlay.py` — pure layout geometry plus Cairo render
helpers. No Gtk/Gdk widget state, no event wiring, no display server needed
for import. Tested headlessly with in-memory `cairo.ImageSurface` surfaces
in `Linux/tests/test_overlay.py`.

Appearance is persisted in `display.themeMode`, `pluginOpacity`,
`messageOpacity`, and `borderRadius`. Dark and light change overlay surfaces
between black and white; opacity is applied independently to plugin and message
backgrounds. Rounded rectangles use a smooth cubic continuous-corner
approximation. The expanded thread stack has a collapse chevron; collapsed
threads become one compact bubble with an unread badge and expand on click.
Background blur remains compositor-owned: the GTK window is ARGB/transparent,
but the host cannot determine whether Picom (or another compositor) has blur
rules active.

## Palette

Minimal neutral, readable colors (alpha composited):

* Plugin chips by `surface["state"]`: critical/warning/success/failed, else
  a neutral slate (`STATE_COLORS` / `DEFAULT_STATE_COLOR`), alpha `0.94`,
  white `Sans 9` text.
* Thread bubbles: dark neutral `#1E242E`-ish (`BUBBLE_BG`, alpha `0.94`),
  white title (`Sans 10`), secondary white text (`Sans 9`, alpha `0.80`).

## Geometry

`compute_layout(width, cell_width, cell_height, surfaces, messages)` returns
an `OverlayLayout` with:

* `pet_rect`, `pet_top`, `header_height` (space above pet), `total_height`
  (full window height).
* `chip_rects` — one `ChipRect(index, rect, surface)` per plugin surface,
  rows of up to `CHIP_COLS` chips (`CHIP_HEIGHT = 30`) centered in each row,
  anchored directly above the pet.
* `message_rects` — one `MessageRect(index, thread_id, rect, lines, message)`
  per message; bubble width bounded by `MESSAGE_MAX_WIDTH (240)` and
  `width - 2 * MESSAGE_PAD_X`; 1 line title-only, 2 lines with text;
  stacked below the pet with `MESSAGE_BUBBLE_GAP` spacing.
* `chip_band_rect` / `message_band_rect` — layout bounds; `None` when
  empty.

Empty inputs collapse to a thin header (`header_height = CHIP_TOP_GAP +
CHIP_PET_GAP = 16 px`) and no bands.

## Input shape and hit testing

`input_rectangles(layout)` returns the `cairo.RectangleInt` union used for
`input_shape_combine_region`: pet rectangle + individual visible chips/bubbles.
Transparent gaps between chips or threads are excluded from the input region.
`hit_test(layout, x, y)` returns a `Hit(kind, index, thread_id, surface)`:

* `"chip"` → plugin was clicked
* `"message"` → message thread was clicked (carries `thread_id`)
* `"pet"` / `None` → transparent gap or pet body

No `__init__.py` changes are needed; `ui.py` already imports symbols
directly from submodules.

The conversational bubble is independently computed as `chat_bubble` in
`OverlayLayout`; its rounded body and pointer are included in the input shape
and render without a title. Its display duration has a separate setting from
collapsed message previews.

## Swift comparison

OpenPetsKit (SwiftUI) draws status surface chips above the pet and bounded
status banners below/next to it, with a neutral palette and click-through
around the pet. This module mirrors that structure: chips mirror the Swift
surface plugin chips (label:value, state-toned), and message threads mirror
Swift notification banners (bounded width, title + detail line). The Linux
port makes only visible rectangles input regions instead of full-window overlays, keeping
click-through to the desktop.

## Integrated behavior

`ui.py` now uses `relayout()` on content changes, preserves the sprite anchor
where possible, and clamps the expanded window to the monitor work area. Drag
events use `begin_drag()` with a visual-only override, not `begin_move_drag()`.
Thread bubbles have a small status-colored dot. The historical handoff below
describes the initial extraction, not code that still needs applying.

## Initial integration handoff (historical)

Do not edit `ui.py` here — the animation agent/parent owns it. Apply the
following contract to `Linux/openpets_linux/ui.py`:

```python
from .overlay import (
    compute_layout, draw_overlay, draw_plugin_chips,
    draw_message_threads, input_rectangles, hit_test,
)
```

Remove `HEADER_HEIGHT = 210` and `MESSAGE_TOP = 90`.

### 1. `__init__` — replace fixed window height

```python
self.layout = compute_layout(self.width, self.cell_width, self.cell_height, [], [])
self.height = self.layout.total_height
```

### 2. `select_pet` — recompute layout for the new cell size

Replace `self.width, self.height = max(360, width), height + HEADER_HEIGHT`
with a layout recompute before `self.resize()`:

```python
self.layout = compute_layout(self.width, self.cell_width, self.cell_height,
                             self.plugins.surfaces(), self.message_rows)
self.height = self.layout.total_height
self.resize(self.width, self.height)
```

### 3. `refresh` — recompute layout when messages/plugins change

After `self.message_rows = ...` (and before/after `update_input_shape()`):

```python
self.message_rows = list(self.state.messages.values())[-3:]
self.layout = compute_layout(self.width, self.cell_width, self.cell_height,
                             self.plugins.surfaces(), self.message_rows)
if self.layout.total_height != self.height:
    self.height = self.layout.total_height
    self.resize(self.width, self.height)
```

`resize` may follow immediately after `set_default_size`; both only set
size, so this is safe.

### 4. `update_input_shape` — union overlay bands

```python
region = cairo.Region()
for rect in input_rectangles(self.layout):
    region.union(rect)
self.input_shape_combine_region(region)
```

The pet rectangle is always present, so the region is never empty.

### 5. `on_draw` — paint pet at `layout.pet_top`, then overlay

Replace the pet translate target `HEADER_HEIGHT` with `self.layout.pet_top`,
and replace the plugin/message draw loops with:

```python
overlay.draw_plugin_chips(context, self.layout)
overlay.draw_message_threads(context, self.layout)
```

Remove the old per-state color dict and message `y = MESSAGE_TOP + index * 38`
loop.

### 6. `on_button` — dispatch via hit test

```python
if event.button == 3:
    self.menu.popup_at_pointer(event)
elif event.button == 1:
    hit = hit_test(self.layout, event.x, event.y)
    if hit is not None and hit.kind == "chip":
        self.plugins.show_details()
    elif hit is not None and hit.kind == "message":
        self.open_notifications()
    else:
        self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
return True
```

Right-click menu behavior is unchanged.

### Object shapes (unchanged)

* Plugin surfaces from `PluginController.surfaces()`: `{"id", "label",
  "value", "state", ...}` (max 5 entries).
* Messages from `core.State`: `{"title", "text", "status", "threadId",
  "url", ...}` (ui.py already slices to the last 3).

### Notes for the animation agent / parent

* `overlay.py` imports only `cairo`, `Pango`, `PangoCairo` — no Gtk/Gdk — so
  it can be unit tested headlessly (already covered by `test_overlay.py`).
* `compute_layout` is pure math; `Pango` text measurement happens only inside
  the draw functions, which already ran on every frame in the old `on_draw`.
* Window height is now dynamic (`total_height`); any code that caches
  `self.height` must refresh it via `compute_layout` before `resize()`.
* Tests: `PYTHONPATH=Linux python3 -m unittest discover -s Linux/tests`
  (CI unit job, no xvfb) and `... -p test_overlay.py -v` for this module.

## Tests

`Linux/tests/test_overlay.py` covers: empty-state collapse, chip row
geometry/centering/fit, message thread geometry (1 vs 2 lines, bounded
width, stacking), band `total_height < cell_height + 210`, hit targets
(chip/message/pet/gaps/outside), input rectangle coverage and window bounds,
Cairo render colors (chip state colors over black, bubble bg, text pixels,
ellipsized long text, unknown state fallback). Run headlessly:

```
PYTHONPATH=Linux python3 -m unittest discover -s Linux/tests -p test_overlay.py -v
```
