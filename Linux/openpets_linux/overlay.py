"""Compact GTK/Cairo status bubbles and message threads for OpenPets Linux.

Pure layout geometry plus Cairo render helpers. Replaces the fixed
``HEADER_HEIGHT = 210`` / ``MESSAGE_TOP = 90`` header in ``ui.py`` with
compact blocks anchored next to the pet sprite:

* plugin chips -- compact colored chips directly above the pet sprite,
* message threads -- 1-2 line bounded-width bubbles directly below the pet.

No widget state and no Gtk/Gdk event wiring. Rendering functions take a
:class:`cairo.Context` and geometry is computed by pure functions, so the
module is testable headlessly with an in-memory image surface.

Object shapes consumed (read-only):

* plugin surface from :meth:`PluginController.surfaces`::

      {"id": str, "label": str, "value": str, "state": str, ...}

* message from :class:`core.State` notifications::

      {"title": str, "text": str | None, "status": str, "threadId": str, ...}

Design notes: docs/linux-overlay.md
"""

from __future__ import annotations

import math
from typing import NamedTuple

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_foreign("cairo")

import cairo  # noqa: E402  (requires the gi imports above)
from gi.repository import Pango, PangoCairo  # noqa: E402

__all__ = [
    # Palette
    "CHIP_ALPHA",
    "BUBBLE_ALPHA",
    "TEXT_SECONDARY_ALPHA",
    "STATE_COLORS",
    "DEFAULT_STATE_COLOR",
    "BUBBLE_BG",
    "TEXT_PRIMARY",
    # Geometry constants
    "CHIP_TOP_GAP",
    "CHIP_HEIGHT",
    "CHIP_ROW_GAP",
    "CHIP_PET_GAP",
    "CHIP_COLS",
    "CHIP_COL_GAP",
    "CHIP_PAD_X",
    "CHIP_RADIUS",
    "CHIP_FONT_SIZE",
    "PET_BOTTOM_GAP",
    "MESSAGE_BOTTOM_GAP",
    "MESSAGE_MAX_WIDTH",
    "MESSAGE_PAD_X",
    "MESSAGE_PAD_Y",
    "MESSAGE_LINE_GAP",
    "MESSAGE_BUBBLE_GAP",
    "MESSAGE_MAX_LINES",
    "BUBBLE_RADIUS",
    "MESSAGE_TITLE_SIZE",
    "MESSAGE_TEXT_SIZE",
    "TITLE_LINE_HEIGHT",
    "TEXT_LINE_HEIGHT",
    # Geometry types
    "ChipRect",
    "MessageRect",
    "Hit",
    "OverlayLayout",
    # Pure layout
    "compute_layout",
    "input_rectangles",
    "hit_test",
    # Cairo rendering
    "rounded_rect",
    "draw_plugin_chips",
    "draw_message_threads",
    "draw_overlay",
]

# ---------------------------------------------------------------------------
# Palette -- minimal, neutral, readable
# ---------------------------------------------------------------------------

CHIP_ALPHA = 0.94
BUBBLE_ALPHA = 0.94
TEXT_SECONDARY_ALPHA = 0.80

# Plugin chip colors by surface state, mirroring the existing ui.py tones.
STATE_COLORS = {
    "critical": (0.50, 0.14, 0.17),
    "warning": (0.42, 0.30, 0.09),
    "success": (0.14, 0.34, 0.25),
    "failed": (0.30, 0.27, 0.27),
}
DEFAULT_STATE_COLOR = (0.19, 0.27, 0.32)

BUBBLE_BG = (0.12, 0.14, 0.18)  # dark neutral thread bubble background
TEXT_PRIMARY = (1.0, 1.0, 1.0)

# ---------------------------------------------------------------------------
# Geometry constants
# ---------------------------------------------------------------------------

CHIP_TOP_GAP = 8          # window top edge -> first plugin chip row
CHIP_HEIGHT = 30          # plugin chip height
CHIP_ROW_GAP = 6          # gap between plugin chip rows
CHIP_PET_GAP = 8          # last chip row -> pet sprite top
CHIP_COLS = 3             # max chips per row
CHIP_COL_GAP = 8          # gap between chips in a row
CHIP_PAD_X = 10           # chip text horizontal padding
CHIP_RADIUS = 7.0         # chip corner radius
CHIP_FONT_SIZE = 9        # chip label font size

PET_BOTTOM_GAP = 10       # pet sprite bottom -> message band
MESSAGE_BOTTOM_GAP = 8    # message band -> window bottom edge

MESSAGE_MAX_WIDTH = 240   # bounded bubble width for thread text
MESSAGE_PAD_X = 12
MESSAGE_PAD_Y = 10
MESSAGE_LINE_GAP = 4      # gap between title line and text line
MESSAGE_BUBBLE_GAP = 6    # gap between consecutive thread bubbles
MESSAGE_MAX_LINES = 2
BUBBLE_RADIUS = 9         # bubble corner radius

MESSAGE_TITLE_SIZE = 10.0
MESSAGE_TEXT_SIZE = 9.0

# Conservative Pango line-height estimates (points * 1.45, rounded up) used by
# the layout so ellipsized text always fits inside its computed bubble.
TITLE_LINE_HEIGHT = int(math.ceil(MESSAGE_TITLE_SIZE * 1.45))   # 15
TEXT_LINE_HEIGHT = int(math.ceil(MESSAGE_TEXT_SIZE * 1.45))     # 14


def _chip_cell_width(width: int, cols: int) -> float:
    return (width - 2 * CHIP_PAD_X - (cols - 1) * CHIP_COL_GAP) / cols


# ---------------------------------------------------------------------------
# Geometry types
# ---------------------------------------------------------------------------


class ChipRect(NamedTuple):
    """One plugin chip: its index, screen rectangle, and source surface."""

    index: int
    rect: cairo.RectangleInt
    surface: dict


class MessageRect(NamedTuple):
    """One message thread bubble: index, thread id, rectangle, and line count."""

    index: int
    thread_id: str
    rect: cairo.RectangleInt
    lines: int
    message: dict


class Hit(NamedTuple):
    """Result of hit testing a point against the overlay geometry."""

    kind: str          # "chip" | "message" | "pet" | None
    index: int | None
    thread_id: str | None
    surface: dict | None


class OverlayLayout(NamedTuple):
    """Complete compact geometry for one frame."""

    width: int
    cell_width: int
    cell_height: int
    message_font_size: float
    pet_x: int
    pet_top: int
    header_height: int        # vertical space above the pet sprite
    message_band_top: int     # y where the message band starts (below pet)
    message_band_height: int  # 0 when there are no messages
    total_height: int         # full window height = cell_height + bands
    chip_rects: tuple[ChipRect, ...]
    message_rects: tuple[MessageRect, ...]
    chip_band_rect: cairo.RectangleInt | None   # input-shape band above pet
    message_band_rect: cairo.RectangleInt | None  # input-shape band below pet
    pet_rect: cairo.RectangleInt                 # pet sprite (always present)


# ---------------------------------------------------------------------------
# Pure layout
# ---------------------------------------------------------------------------


def compute_layout(
    width: int,
    cell_width: int,
    cell_height: int,
    surfaces: tuple = (),
    messages: tuple = (),
    message_max_width: int = MESSAGE_MAX_WIDTH,
    message_font_size: float = MESSAGE_TITLE_SIZE,
) -> OverlayLayout:
    """Compute compact overlay geometry for one frame.

    Replaces the fixed ``HEADER_HEIGHT / MESSAGE_TOP`` header: plugin chips
    are stacked in rows directly above the pet and message threads are stacked
    in bounded-width bubbles directly below it. Everything is anchored next to
    the pet sprite; empty inputs collapse to a thin header and no bands.
    """
    width = int(width)
    cell_width = int(cell_width)
    cell_height = int(cell_height)
    if isinstance(message_max_width, bool) or not isinstance(message_max_width, (int, float)):
        message_max_width = MESSAGE_MAX_WIDTH
    message_max_width = max(120, min(600, int(message_max_width)))
    try:
        font_size_is_finite = math.isfinite(message_font_size)
    except (TypeError, OverflowError):
        font_size_is_finite = False
    if isinstance(message_font_size, bool) or not isinstance(message_font_size, (int, float)) or not font_size_is_finite:
        message_font_size = MESSAGE_TITLE_SIZE
    message_font_size = max(6.0, min(24.0, float(message_font_size)))
    message_body_font_size = max(5.0, message_font_size - 1.0)
    title_line_height = int(math.ceil(message_font_size * 1.45))
    text_line_height = int(math.ceil(message_body_font_size * 1.45))
    surfaces = list(surfaces)
    messages = list(messages)

    # --- plugin chip rows (above the pet) -------------------------------
    chip_rects: list[ChipRect] = []
    chip_top = CHIP_TOP_GAP
    if surfaces:
        cols = min(CHIP_COLS, len(surfaces))
        cell_w = _chip_cell_width(width, cols)
        chip_w = int(math.floor(cell_w - CHIP_COL_GAP))
        if chip_w < 1:
            chip_w = 1
        rows = math.ceil(len(surfaces) / cols)
        y = chip_top
        index = 0
        for _row in range(rows):
            row_count = min(cols, len(surfaces) - index)
            row_w = row_count * chip_w + (row_count - 1) * CHIP_COL_GAP
            x = int(math.floor((width - row_w) / 2))
            for _col in range(row_count):
                surface = surfaces[index]
                rect = cairo.RectangleInt(x, y, chip_w, CHIP_HEIGHT)
                chip_rects.append(ChipRect(index, rect, surface))
                x += chip_w + CHIP_COL_GAP
                index += 1
            y += CHIP_HEIGHT + CHIP_ROW_GAP

    chips_bottom = (
        chip_rects[-1].rect.y + chip_rects[-1].rect.height
        if chip_rects
        else CHIP_TOP_GAP
    )
    pet_top = chips_bottom + CHIP_PET_GAP

    pet_x = (width - cell_width) // 2
    pet_rect = cairo.RectangleInt(pet_x, pet_top, cell_width, cell_height)

    # --- message thread band (below the pet) ----------------------------
    message_rects: list[MessageRect] = []
    band_top = pet_top + cell_height + PET_BOTTOM_GAP
    bubble_w = min(message_max_width, max(0, width - 2 * MESSAGE_PAD_X))
    bubble_x = (width - bubble_w) // 2
    y = band_top
    for index, message in enumerate(messages):
        thread_id = message.get("threadId") or ""
        has_text = bool(message.get("text"))
        lines = 2 if has_text else 1
        bubble_h = 2 * MESSAGE_PAD_Y + title_line_height
        if has_text:
            bubble_h += MESSAGE_LINE_GAP + text_line_height
        rect = cairo.RectangleInt(bubble_x, y, bubble_w, bubble_h)
        message_rects.append(
            MessageRect(index, str(thread_id), rect, lines, message)
        )
        y += bubble_h + MESSAGE_BUBBLE_GAP

    bubble_area = sum(m.rect.height for m in message_rects)
    bubble_area += MESSAGE_BUBBLE_GAP * max(0, len(message_rects) - 1)
    if message_rects:
        message_band_height = bubble_area + MESSAGE_BOTTOM_GAP
        message_band_rect = cairo.RectangleInt(
            0, band_top, width, message_band_height
        )
        total_height = band_top + message_band_height
    else:
        message_band_height = 0
        message_band_rect = None
        total_height = pet_top + cell_height + PET_BOTTOM_GAP

    if chip_rects:
        chip_band_rect = cairo.RectangleInt(
            0, CHIP_TOP_GAP, width, chips_bottom - CHIP_TOP_GAP
        )
    else:
        chip_band_rect = None

    return OverlayLayout(
        width=width,
        cell_width=cell_width,
        cell_height=cell_height,
        message_font_size=message_font_size,
        pet_x=pet_x,
        pet_top=pet_top,
        header_height=pet_top,
        message_band_top=band_top,
        message_band_height=message_band_height,
        total_height=total_height,
        chip_rects=tuple(chip_rects),
        message_rects=tuple(message_rects),
        chip_band_rect=chip_band_rect,
        message_band_rect=message_band_rect,
        pet_rect=pet_rect,
    )


def input_rectangles(layout: OverlayLayout) -> tuple[cairo.RectangleInt, ...]:
    """Rectangles that must receive input (pet + visible bands).

    Everything outside these rectangles clicks through to the desktop.
    The pet rectangle is always present, so the result is never empty.
    """
    rects = [layout.pet_rect]
    rects.extend(chip.rect for chip in layout.chip_rects)
    rects.extend(message.rect for message in layout.message_rects)
    return tuple(rects)


def hit_test(layout: OverlayLayout, x: float, y: float) -> Hit | None:
    """Hit test a window coordinate against the overlay.

    Returns a :class:`Hit` describing what is under the point, or ``None``
    when the point falls in a transparent gap (clicks through, or drags the
    window when on the pet sprite / background).
    """
    x = int(x)
    y = int(y)

    for chip in layout.chip_rects:
        r = chip.rect
        if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
            return Hit("chip", chip.index, None, chip.surface)

    if layout.message_band_rect is not None:
        band = layout.message_band_rect
        if band.x <= x < band.x + band.width and band.y <= y < band.y + band.height:
            for msg in layout.message_rects:
                r = msg.rect
                if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                    return Hit("message", msg.index, msg.thread_id, None)

    pr = layout.pet_rect
    if pr.x <= x < pr.x + pr.width and pr.y <= y < pr.y + pr.height:
        return Hit("pet", None, None, None)

    return None


# ---------------------------------------------------------------------------
# Cairo rendering helpers
# ---------------------------------------------------------------------------


def rounded_rect(
    context: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    radius: float,
) -> None:
    """Append a rounded rectangle to the current path."""
    r = min(radius, w / 2.0, h / 2.0)
    if r <= 0:
        context.rectangle(x, y, w, h)
        return
    context.new_sub_path()
    context.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    context.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    context.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    context.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    context.close_path()


def _text_layout(
    context: cairo.Context,
    text: str,
    size: float,
    width_px: int,
) -> Pango.Layout:
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(Pango.FontDescription(f"Sans {size}"))
    layout.set_text(text, -1)  # Never interpret notification text as markup.
    layout.set_width(max(1, int(width_px)) * Pango.SCALE)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_single_paragraph_mode(True)
    return layout


def draw_plugin_chips(context: cairo.Context, layout: OverlayLayout) -> None:
    """Draw plugin status chips from ``layout.chip_rects``."""
    for chip in layout.chip_rects:
        r = chip.rect
        color = STATE_COLORS.get(chip.surface.get("state"), DEFAULT_STATE_COLOR)
        context.set_source_rgba(color[0], color[1], color[2], CHIP_ALPHA)
        rounded_rect(context, r.x, r.y, r.width, r.height, CHIP_RADIUS)
        context.fill()
        text = "{}: {}".format(
            chip.surface.get("label", ""), chip.surface.get("value", "")
        )
        text_layout = _text_layout(
            context, text, CHIP_FONT_SIZE, r.width - 2 * CHIP_PAD_X
        )
        _, text_h = text_layout.get_size()
        text_h = text_h / Pango.SCALE
        context.set_source_rgb(1, 1, 1)
        context.move_to(r.x + CHIP_PAD_X, r.y + (r.height - text_h) / 2.0)
        PangoCairo.show_layout(context, text_layout)


def draw_message_threads(context: cairo.Context, layout: OverlayLayout) -> None:
    """Draw message thread bubbles from ``layout.message_rects``.

    Each bubble shows its title line, plus a second secondary line when the
    message carries text, both ellipsized inside the bounded bubble width.
    """
    for msg in layout.message_rects:
        r = msg.rect
        context.set_source_rgba(
            BUBBLE_BG[0], BUBBLE_BG[1], BUBBLE_BG[2], BUBBLE_ALPHA
        )
        rounded_rect(context, r.x, r.y, r.width, r.height, BUBBLE_RADIUS)
        context.fill()
        status_color = {"running": (.38, .72, .91), "review": (.71, .63, .94),
                        "waiting": (.91, .73, .36), "done": (.42, .80, .59),
                        "failed": (.95, .46, .45)}.get(msg.message.get("status"), (.68, .74, .79))
        context.set_source_rgb(*status_color)
        context.arc(r.x + MESSAGE_PAD_X + 3, r.y + MESSAGE_PAD_Y + 8, 3, 0, 2 * math.pi)
        context.fill()
        inner_w = r.width - 2 * MESSAGE_PAD_X - 12
        title = msg.message.get("title", "")
        title_layout = _text_layout(context, title, layout.message_font_size, inner_w)
        _, title_h = title_layout.get_size()
        title_h = title_h / Pango.SCALE
        context.set_source_rgb(*TEXT_PRIMARY)
        context.move_to(r.x + MESSAGE_PAD_X + 12, r.y + MESSAGE_PAD_Y)
        PangoCairo.show_layout(context, title_layout)
        text = msg.message.get("text")
        if text:
            text_layout = _text_layout(context, text, max(5.0, layout.message_font_size - 1.0), inner_w)
            context.set_source_rgba(
                1, 1, 1, TEXT_SECONDARY_ALPHA
            )
            context.move_to(
                r.x + MESSAGE_PAD_X + 12,
                r.y + MESSAGE_PAD_Y + title_h + MESSAGE_LINE_GAP,
            )
            PangoCairo.show_layout(context, text_layout)


def draw_overlay(context: cairo.Context, layout: OverlayLayout) -> None:
    """Draw plugin chips and message thread bubbles for one frame."""
    draw_plugin_chips(context, layout)
    draw_message_threads(context, layout)
