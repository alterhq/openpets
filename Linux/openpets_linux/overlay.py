"""Compact GTK/Cairo status bubbles and message threads for OpenPets Linux.

Pure layout geometry plus Cairo render helpers. Replaces the fixed
``HEADER_HEIGHT = 210`` / ``MESSAGE_TOP = 90`` header in ``ui.py`` with
compact blocks anchored next to the pet sprite:

* plugin chips -- compact colored chips directly above the pet sprite,
* message threads -- 1-2 line bounded-width bubbles directly below the pet,
* transient collapsed notification previews (when collapsed and
  ``preview_messages`` are provided) -- compact cards stacked above the pet,
  old-at-top / newest-at-bottom, shown in place of the static collapsed
  summary while the fixed-size circular toggle is retained.

No widget state and no Gtk/Gdk event wiring. Rendering functions take a
:class:`cairo.Context` and geometry is computed by pure functions, so the
module is testable headlessly with an in-memory image surface.

Status is conveyed by a shape/mark in addition to color so it remains
distinguishable without relying on hue alone:

* running   -- ring (clock / circular progress mark)
* completed -- checkmark
* failed    -- cross (X)
* waiting   -- filled dot
* review    -- partial arc
* default   -- filled dot

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
    "COLLAPSED_BUBBLE_WIDTH",
    "COLLAPSED_BUBBLE_HEIGHT",
    "TITLE_LINE_HEIGHT",
    "TEXT_LINE_HEIGHT",
    # Geometry types
    "ChipRect",
    "MessageRect",
    "PreviewRect",
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
    "draw_message_controls",
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

CHIP_TOP_GAP = 8          # window top edge -> first vertical stack element (messages/chips)
CHIP_HEIGHT = 30          # plugin chip height
CHIP_ROW_GAP = 6          # gap between plugin chip rows (kept for API compat)
CHIP_PET_GAP = 8          # gap between pet sprite and the plugin chip stack
CHIP_COLS = 3             # max chips per row (legacy; chips now use a single side stack)
CHIP_COL_GAP = 8          # gap between chips in a row (legacy)
CHIP_PAD_X = 10           # chip text horizontal padding
CHIP_STACK_GAP = 6        # gap between chips in the vertical side stack
CHIP_MAX_WIDTH = 200      # max width of a chip in the side stack
CHIP_RADIUS = 7.0         # chip corner radius
CHIP_FONT_SIZE = 9        # chip label font size

MESSAGE_TOP_GAP = 8       # window top edge -> message band (above the pet)
PET_BOTTOM_GAP = 10       # pet sprite bottom -> window bottom edge (no chips below)
MESSAGE_BOTTOM_GAP = 8    # message band -> window bottom edge

MESSAGE_MAX_WIDTH = 240   # bounded bubble width for thread text
MESSAGE_PAD_X = 12
MESSAGE_PAD_Y = 12
MESSAGE_LINE_GAP = 5      # gap between title line and text line
MESSAGE_BUBBLE_GAP = 6    # gap between consecutive thread bubbles
MESSAGE_MAX_LINES = 2
BUBBLE_RADIUS = 9         # bubble corner radius

MESSAGE_TITLE_SIZE = 10.0
MESSAGE_TEXT_SIZE = 9.0
COLLAPSED_BUBBLE_WIDTH = 56
COLLAPSED_BUBBLE_HEIGHT = 36
MESSAGE_TOGGLE_SIZE = 40
MESSAGE_STATUS_SIZE = 10
MESSAGE_STATUS_TEXT_GAP = 10

# Conservative Pango line-height estimates (points * 1.45, rounded up) used by
# the layout so ellipsized text always fits inside its computed bubble.
TITLE_LINE_HEIGHT = int(math.ceil(MESSAGE_TITLE_SIZE * 1.45))   # 15
TEXT_LINE_HEIGHT = int(math.ceil(MESSAGE_TEXT_SIZE * 1.45))     # 14


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


class PreviewRect(NamedTuple):
    """One transient collapsed notification preview card: index, thread id, rectangle, and source message.

    Previews are stacked above the pet in input order (old-at-top, newest-at-bottom)
    and are shown in place of the static collapsed summary when collapsed previews
    are non-empty. The fixed-size circular toggle remains the sole control.
    """

    index: int
    thread_id: str
    rect: cairo.RectangleInt
    message: dict


class Hit(NamedTuple):
    """Result of hit testing a point against the overlay geometry."""

    kind: str          # "chip" | "message" | "pet" | message controls
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
    preview_rects: tuple[PreviewRect, ...]   # transient collapsed notification previews
    chip_band_rect: cairo.RectangleInt | None   # input-shape band above pet
    message_band_rect: cairo.RectangleInt | None  # input-shape band below pet
    pet_rect: cairo.RectangleInt                 # pet sprite (always present)
    theme_mode: str
    plugin_opacity: float
    message_opacity: float
    border_radius: float
    unread_count: int
    messages_collapsed: bool
    collapsed_bubble_rect: cairo.RectangleInt | None
    message_toggle_rect: cairo.RectangleInt | None
    plugin_side: str        # "left" or "right": side of the vertical chip stack


# ---------------------------------------------------------------------------
# Pure layout
# ---------------------------------------------------------------------------


def compute_layout(
    width: int,
    cell_width: int,
    cell_height: int,
    surfaces: tuple = (),
    messages: tuple = (),
    preview_messages: tuple = (),
    message_max_width: int = MESSAGE_MAX_WIDTH,
    message_font_size: float = MESSAGE_TITLE_SIZE,
    theme_mode: str = "dark",
    plugin_opacity: float = CHIP_ALPHA,
    message_opacity: float = BUBBLE_ALPHA,
    border_radius: float = 14.0,
    messages_collapsed: bool = False,
    unread_count: int = 0,
    plugin_side: str = "right",
) -> OverlayLayout:
    """Compute compact overlay geometry for one frame.

    Plugin chips form a single vertical stack to the pet's left or right
    (``plugin_side``, default ``"right"``), centered vertically against the pet
    regardless of plugin count. Message threads appear above the pet, with the
    expand/collapse toggle above the pet at its upper-right. When collapsed and
    ``preview_messages`` are non-empty, transient preview cards are stacked
    above the pet in input order (old-at-top, newest-at-bottom) in place of the
    static collapsed summary; the fixed-size circular toggle is retained.
    Empty inputs collapse to a thin header and no bands.
    """
    width = int(width)
    cell_width = int(cell_width)
    cell_height = int(cell_height)
    plugin_side = plugin_side if plugin_side in ("left", "right") else "right"
    theme_mode = theme_mode if theme_mode in ("dark", "light") else "dark"
    plugin_opacity = _bounded_opacity(plugin_opacity, CHIP_ALPHA)
    message_opacity = _bounded_opacity(message_opacity, BUBBLE_ALPHA)
    if isinstance(border_radius, bool) or not isinstance(border_radius, (int, float)):
        border_radius = 14.0
    try:
        if not math.isfinite(border_radius):
            border_radius = 14.0
    except OverflowError:
        border_radius = 14.0
    border_radius = max(0.0, min(32.0, float(border_radius)))
    if isinstance(unread_count, bool) or not isinstance(unread_count, int):
        unread_count = 0
    unread_count = max(0, min(999, unread_count))
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
    preview_messages = list(preview_messages)

    # --- message band (above the pet) -------------------------------------
    message_rects: list[MessageRect] = []
    preview_rects: list[PreviewRect] = []
    collapsed_bubble_rect = None
    message_toggle_rect = None
    band_top = MESSAGE_TOP_GAP
    toggle_size = MESSAGE_TOGGLE_SIZE

    has_messages = bool(messages)
    has_previews = bool(preview_messages)
    is_collapsed = bool(messages_collapsed)

    pet_x = (width - cell_width) // 2

    # Fixed-size circular toggle: align to the pet's right edge rather than
    # the window edge. Its own header row keeps it clear of all message cards,
    # regardless of the configured bubble width.
    # Keep an always-available affordance even when there are no messages.
    message_toggle_rect = cairo.RectangleInt(
        max(0, min(width - toggle_size, pet_x + cell_width - toggle_size)), band_top,
        toggle_size, toggle_size,
    )

    bubble_w = min(message_max_width, max(0, width - 2 * MESSAGE_PAD_X))
    bubble_x = (width - bubble_w) // 2
    # Reserve a full control row above message cards so neither wide nor
    # narrow bubbles can collide with the toggle.
    y = band_top + toggle_size + MESSAGE_BUBBLE_GAP if message_toggle_rect else band_top

    if is_collapsed and has_previews:
        # Transient collapsed previews: old-at-top / newest-at-bottom, each a
        # compact title card with a status mark. Shown in place of the static
        # collapsed summary; the toggle remains the sole control.
        for index, message in enumerate(preview_messages):
            thread_id = message.get("threadId") or ""
            preview_h = 2 * MESSAGE_PAD_Y + title_line_height
            rect = cairo.RectangleInt(bubble_x, y, bubble_w, preview_h)
            preview_rects.append(
                PreviewRect(index, str(thread_id), rect, message)
            )
            y += preview_h
            if index < len(preview_messages) - 1:
                y += MESSAGE_BUBBLE_GAP
        message_band_bottom = y
    elif is_collapsed:
        # No empty summary pill: the larger circular toggle is the collapsed
        # affordance and carries the unread indicator itself.
        message_band_bottom = band_top + toggle_size
    elif has_messages:
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
            y += bubble_h
            if index < len(messages) - 1:
                y += MESSAGE_BUBBLE_GAP
        message_band_bottom = y
    else:
        # The toggle row exists even with an empty message stack.
        message_band_bottom = band_top + toggle_size

    # --- pet sprite (centered horizontally) --------------------------------
    pet_top = message_band_bottom + PET_BOTTOM_GAP

    pet_x = (width - cell_width) // 2

    # --- plugin chip stack (left or right of pet, vertically centered) -----
    chip_rects: list[ChipRect] = []
    chip_stack_top = 0.0
    chip_stack_height = 0.0
    if surfaces:
        side_space = (width - cell_width) // 2 - CHIP_PET_GAP
        chip_w = min(CHIP_MAX_WIDTH, max(1, side_space))
        n = len(surfaces)
        chip_stack_height = n * CHIP_HEIGHT + max(0, n - 1) * CHIP_STACK_GAP
        # Tentative center-aligned stack top so stack center == pet center.
        chip_stack_top = pet_top + (cell_height - chip_stack_height) / 2.0
        # When the stack is taller than the pet cell, move the pet down by half
        # the excess so the stack stays vertically centered against the pet and
        # its top edge never clamps to the window top edge (keeps y >= 0).
        if chip_stack_height > cell_height:
            pet_top += (chip_stack_height - cell_height) / 2.0
            chip_stack_top = pet_top + (cell_height - chip_stack_height) / 2.0
    pet_rect = cairo.RectangleInt(pet_x, int(pet_top), cell_width, cell_height)

    if surfaces:
        if plugin_side == "right":
            stack_x = pet_x + cell_width + CHIP_PET_GAP
        else:
            stack_x = pet_x - chip_w - CHIP_PET_GAP
        # Clamp the stack horizontally to the window.
        if stack_x < 0:
            stack_x = 0
        if stack_x + chip_w > width:
            stack_x = width - chip_w
        stack_x = int(math.floor(stack_x))
        chip_w = int(math.floor(chip_w))
        y = chip_stack_top
        for index, surface in enumerate(surfaces):
            rect = cairo.RectangleInt(stack_x, int(math.floor(y)), chip_w, CHIP_HEIGHT)
            chip_rects.append(ChipRect(index, rect, surface))
            y += CHIP_HEIGHT + CHIP_STACK_GAP

    chip_stack_bottom = chip_stack_top + chip_stack_height
    pet_bottom = pet_top + cell_height

    if chip_rects:
        chip_band_rect = cairo.RectangleInt(
            min(chip.rect.x for chip in chip_rects),
            int(math.floor(chip_stack_top)),
            max(chip.rect.x + chip.rect.width for chip in chip_rects)
            - min(chip.rect.x for chip in chip_rects),
            int(math.ceil(chip_stack_bottom - chip_stack_top)),
        )
    else:
        chip_band_rect = None

    bubble_area = sum(m.rect.height for m in message_rects)
    bubble_area += MESSAGE_BUBBLE_GAP * max(0, len(message_rects) - 1)
    # The window must be tall enough for the pet, the message band, and any
    # vertically-centered chip stack that extends beyond the pet.
    content_bottom = max(
        pet_bottom,
        chip_stack_bottom if chip_rects else pet_bottom,
    )
    if is_collapsed and not preview_rects:
        message_band_height = toggle_size + MESSAGE_BOTTOM_GAP
        message_band_rect = cairo.RectangleInt(0, band_top, width, message_band_height)
        total_height = max(content_bottom, band_top + message_band_height) + PET_BOTTOM_GAP
    elif preview_rects:
        message_band_height = message_band_bottom - band_top + MESSAGE_BOTTOM_GAP
        message_band_rect = cairo.RectangleInt(0, band_top, width, message_band_height)
        total_height = max(content_bottom, band_top + message_band_height) + PET_BOTTOM_GAP
    elif message_rects:
        message_band_height = message_band_bottom - band_top + MESSAGE_BOTTOM_GAP
        message_band_rect = cairo.RectangleInt(
            0, band_top, width, message_band_height
        )
        total_height = max(content_bottom, band_top + message_band_height) + PET_BOTTOM_GAP
    else:
        message_band_height = toggle_size + MESSAGE_BOTTOM_GAP
        message_band_rect = cairo.RectangleInt(0, band_top, width, message_band_height)
        total_height = max(content_bottom, band_top + message_band_height) + PET_BOTTOM_GAP

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
        preview_rects=tuple(preview_rects),
        chip_band_rect=chip_band_rect,
        message_band_rect=message_band_rect,
        pet_rect=pet_rect,
        theme_mode=theme_mode,
        plugin_opacity=plugin_opacity,
        message_opacity=message_opacity,
        border_radius=border_radius,
        unread_count=unread_count,
        messages_collapsed=is_collapsed,
        collapsed_bubble_rect=collapsed_bubble_rect,
        message_toggle_rect=message_toggle_rect,
        plugin_side=plugin_side,
    )


def _bounded_opacity(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        if not math.isfinite(value):
            return default
    except OverflowError:
        return default
    return max(0.0, min(1.0, float(value)))


def input_rectangles(layout: OverlayLayout, include_toggle: bool = True) -> tuple[cairo.RectangleInt, ...]:
    """Rectangles that must receive input (pet + visible bands).

    Everything outside these rectangles clicks through to the desktop.
    The pet rectangle is always present, so the result is never empty.
    """
    rects = [layout.pet_rect]
    rects.extend(chip.rect for chip in layout.chip_rects)
    rects.extend(message.rect for message in layout.message_rects)
    rects.extend(preview.rect for preview in layout.preview_rects)
    if layout.collapsed_bubble_rect is not None:
        rects.append(layout.collapsed_bubble_rect)
    if include_toggle and layout.message_toggle_rect is not None:
        rects.append(layout.message_toggle_rect)
    return tuple(rects)


def hit_test(layout: OverlayLayout, x: float, y: float, include_toggle: bool = True) -> Hit | None:
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

    # Sole expand/collapse control: the circular message_toggle_rect.
    rect = layout.message_toggle_rect
    if include_toggle and rect is not None and rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height:
        kind = "message-expand" if layout.messages_collapsed else "message-collapse"
        return Hit(kind, None, None, None)

    if layout.message_band_rect is not None:
        band = layout.message_band_rect
        if band.x <= x < band.x + band.width and band.y <= y < band.y + band.height:
            for msg in layout.message_rects:
                r = msg.rect
                if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                    return Hit("message", msg.index, msg.thread_id, None)
            for preview in layout.preview_rects:
                r = preview.rect
                if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                    return Hit("message", preview.index, preview.thread_id, None)

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
    # Smooth continuous-corner approximation, rather than four circular arcs.
    k = 0.4477
    context.new_sub_path()
    context.move_to(x + r, y)
    context.line_to(x + w - r, y)
    context.curve_to(x + w - r * (1-k), y, x+w, y+r*(1-k), x+w, y+r)
    context.line_to(x+w, y+h-r)
    context.curve_to(x+w, y+h-r*(1-k), x+w-r*(1-k), y+h, x+w-r, y+h)
    context.line_to(x+r, y+h)
    context.curve_to(x+r*(1-k), y+h, x, y+h-r*(1-k), x, y+h-r)
    context.line_to(x, y+r)
    context.curve_to(x, y+r*(1-k), x+r*(1-k), y, x+r, y)
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
        background = (0, 0, 0) if layout.theme_mode == "dark" else (1, 1, 1)
        ink = (1, 1, 1) if layout.theme_mode == "dark" else (0, 0, 0)
        context.set_source_rgba(*background, layout.plugin_opacity)
        rounded_rect(context, r.x, r.y, r.width, r.height, layout.border_radius)
        context.fill()
        # No leading status dot/bullet: the chip background carries the state
        # color through its opacity and border, so text is balanced/centered
        # instead of left-shifted next to an adornment dot.
        text = "{}: {}".format(
            chip.surface.get("label", ""), chip.surface.get("value", "")
        )
        text_layout = _text_layout(
            context, text, CHIP_FONT_SIZE, r.width - 2 * CHIP_PAD_X
        )
        text_w, text_h = text_layout.get_size()
        text_w = text_w / Pango.SCALE
        text_h = text_h / Pango.SCALE
        context.set_source_rgb(*ink)
        # Center the label/value horizontally within the chip so the text is
        # not left-shifted after the adornment dot was removed.
        text_x = r.x + max(CHIP_PAD_X, (r.width - text_w) / 2.0)
        context.move_to(text_x, r.y + (r.height - text_h) / 2.0)
        PangoCairo.show_layout(context, text_layout)


def _draw_status_mark(context: cairo.Context, cx: float, cy: float, status: str, color, size: int = 10) -> None:
    """Draw a minimal status mark beyond color alone so status is distinguishable
    without relying on hue.

    * running   -- ring (clock / circular progress mark)
    * completed -- checkmark
    * failed    -- cross (X)
    * review    -- partial arc
    * default   -- filled dot (waiting / unknown)
    """
    status = str(status or "").lower()
    context.set_source_rgb(*color)
    context.set_line_width(2)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    r = size / 2.0
    if status == "running":
        context.arc(cx, cy, r, 0, 2 * math.pi)
        context.stroke()
    elif status in ("done", "completed"):
        context.move_to(cx - r, cy)
        context.line_to(cx - r / 2, cy + r)
        context.line_to(cx + r, cy - r)
        context.stroke()
    elif status == "failed":
        context.move_to(cx - r, cy - r)
        context.line_to(cx + r, cy + r)
        context.move_to(cx + r, cy - r)
        context.line_to(cx - r, cy + r)
        context.stroke()
    elif status == "review":
        context.arc(cx, cy, r, 0, 1.5 * math.pi)
        context.stroke()
    else:
        # waiting / default: filled dot.
        context.arc(cx, cy, r, 0, 2 * math.pi)
        context.fill()


def draw_message_threads(context: cairo.Context, layout: OverlayLayout) -> None:
    """Draw message thread bubbles from ``layout.message_rects``.

    Each bubble shows its title line, plus a second secondary line when the
    message carries text, both ellipsized inside the bounded bubble width.
    """
    for msg in layout.message_rects:
        r = msg.rect
        background = (0, 0, 0) if layout.theme_mode == "dark" else (1, 1, 1)
        ink = (1, 1, 1) if layout.theme_mode == "dark" else (0, 0, 0)
        context.set_source_rgba(*background, layout.message_opacity)
        rounded_rect(context, r.x, r.y, r.width, r.height, layout.border_radius)
        context.fill()
        status_color = {"running": (.38, .72, .91), "review": (.71, .63, .94),
                        "waiting": (.91, .73, .36), "done": (.42, .80, .59),
                        "completed": (.42, .80, .59),
                        "failed": (.95, .46, .45)}.get(msg.message.get("status"), (.68, .74, .79))
        status_size = MESSAGE_STATUS_SIZE
        status_title_height = math.ceil(layout.message_font_size * 1.45)
        _draw_status_mark(
            context,
            r.x + MESSAGE_PAD_X + status_size / 2.0,
            r.y + MESSAGE_PAD_Y + status_title_height / 2.0,
            msg.message.get("status"),
            status_color,
            size=status_size,
        )
        text_offset = status_size + MESSAGE_STATUS_TEXT_GAP
        inner_w = r.width - 2 * MESSAGE_PAD_X - text_offset
        title = msg.message.get("title", "")
        title_layout = _text_layout(context, title, layout.message_font_size, inner_w)
        _, title_h = title_layout.get_size()
        title_h = title_h / Pango.SCALE
        context.set_source_rgb(*ink)
        context.move_to(r.x + MESSAGE_PAD_X + text_offset, r.y + MESSAGE_PAD_Y)
        PangoCairo.show_layout(context, title_layout)
        text = msg.message.get("text")
        if text:
            text_layout = _text_layout(context, text, max(5.0, layout.message_font_size - 1.0), inner_w)
            context.set_source_rgba(*ink, TEXT_SECONDARY_ALPHA)
            context.move_to(
                r.x + MESSAGE_PAD_X + text_offset,
                r.y + MESSAGE_PAD_Y + title_h + MESSAGE_LINE_GAP,
            )
            PangoCairo.show_layout(context, text_layout)


def draw_message_previews(context: cairo.Context, layout: OverlayLayout) -> None:
    """Draw transient collapsed notification preview cards from ``layout.preview_rects``.

    Each card shows its title and a status mark, ellipsized inside the bounded
    card width. Stacked old-at-top / newest-at-bottom by the layout.
    """
    for preview in layout.preview_rects:
        r = preview.rect
        background = (0, 0, 0) if layout.theme_mode == "dark" else (1, 1, 1)
        ink = (1, 1, 1) if layout.theme_mode == "dark" else (0, 0, 0)
        context.set_source_rgba(*background, layout.message_opacity)
        rounded_rect(context, r.x, r.y, r.width, r.height, layout.border_radius)
        context.fill()
        status_color = {"running": (.38, .72, .91), "review": (.71, .63, .94),
                        "waiting": (.91, .73, .36), "done": (.42, .80, .59),
                        "completed": (.42, .80, .59),
                        "failed": (.95, .46, .45)}.get(preview.message.get("status"), (.68, .74, .79))
        status_size = MESSAGE_STATUS_SIZE
        status_title_height = math.ceil(layout.message_font_size * 1.45)
        _draw_status_mark(
            context,
            r.x + MESSAGE_PAD_X + status_size / 2.0,
            r.y + MESSAGE_PAD_Y + status_title_height / 2.0,
            preview.message.get("status"),
            status_color,
            size=status_size,
        )
        text_offset = status_size + MESSAGE_STATUS_TEXT_GAP
        inner_w = r.width - 2 * MESSAGE_PAD_X - text_offset
        title = preview.message.get("title", "")
        title_layout = _text_layout(context, title, layout.message_font_size, inner_w)
        _, title_h = title_layout.get_size()
        title_h = title_h / Pango.SCALE
        context.set_source_rgb(*ink)
        context.move_to(r.x + MESSAGE_PAD_X + text_offset, r.y + MESSAGE_PAD_Y)
        PangoCairo.show_layout(context, title_layout)


def _draw_chevron(context, rect, upward, ink):
    cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
    context.set_source_rgb(*ink)
    context.set_line_width(2)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.set_line_join(cairo.LINE_JOIN_ROUND)
    if upward:
        context.move_to(cx - 5, cy + 2); context.line_to(cx, cy - 3); context.line_to(cx + 5, cy + 2)
    else:
        context.move_to(cx - 5, cy - 2); context.line_to(cx, cy + 3); context.line_to(cx + 5, cy - 2)
    context.stroke()


def draw_message_controls(context: cairo.Context, layout: OverlayLayout, show_toggle: bool = True) -> None:
    background = (0, 0, 0) if layout.theme_mode == "dark" else (1, 1, 1)
    ink = (1, 1, 1) if layout.theme_mode == "dark" else (0, 0, 0)
    if show_toggle and layout.message_toggle_rect is not None:
        # Circular toggle: sole expand/collapse control, same size collapsed
        # and expanded. Chevron shows what clicking does.
        trect = layout.message_toggle_rect
        context.set_source_rgba(*background, layout.message_opacity)
        radius = trect.width / 2.0 - 2.0
        context.arc(trect.x + trect.width / 2.0, trect.y + trect.height / 2.0,
                    radius, 0, 2 * math.pi)
        context.fill()
        _draw_chevron(context, trect, not layout.messages_collapsed, ink)
        if layout.messages_collapsed and layout.unread_count:
            # Minimal unread cue integrated into the actual click target.
            # A fine light outline keeps the red dot visible on both themes.
            dot_x = trect.x + trect.width - 4.0
            dot_y = trect.y + 4.0
            context.set_source_rgba(*background, 1.0)
            context.arc(dot_x, dot_y, 7.0, 0, 2 * math.pi)
            context.fill()
            context.set_source_rgb(0.88, 0.16, 0.19)
            context.arc(dot_x, dot_y, 5.2, 0, 2 * math.pi)
            context.fill()


def draw_overlay(context: cairo.Context, layout: OverlayLayout, show_toggle: bool = True) -> None:
    """Draw plugin chips and message thread bubbles for one frame."""
    draw_plugin_chips(context, layout)
    draw_message_threads(context, layout)
    draw_message_previews(context, layout)
    draw_message_controls(context, layout, show_toggle=show_toggle)
