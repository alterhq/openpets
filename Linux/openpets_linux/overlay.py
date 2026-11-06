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
import unicodedata
from collections import deque
from functools import lru_cache
from typing import NamedTuple

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_foreign("cairo")

import cairo  # noqa: E402  (requires the gi imports above)
from gi.repository import Pango, PangoCairo  # noqa: E402
from .chat_bubble import (
    CHAT_BUBBLE_ASPECT_RATIO,
    CHAT_BUBBLE_CORNER_RADIUS_PERCENT,
    CHAT_BUBBLE_MAX_WIDTH,
    CHAT_BUBBLE_PAD_X,
    CHAT_BUBBLE_PAD_Y,
    CHAT_BUBBLE_TEXT_SIZE,
)

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
    "CHAT_BUBBLE_MAX_WIDTH",
    "CHAT_BUBBLE_HEIGHT",
    "COLLAPSED_BUBBLE_WIDTH",
    "COLLAPSED_BUBBLE_HEIGHT",
    "TITLE_LINE_HEIGHT",
    "TEXT_LINE_HEIGHT",
    # Geometry types
    "ChipRect",
    "MessageRect",
    "ChatBubbleRect",
    "PreviewRect",
    "Hit",
    "OverlayLayout",
    # Pure layout
    "compute_layout",
    "estimate_chat_bubble_size",
    "input_rectangles",
    "hit_test",
    # Cairo rendering
    "rounded_rect",
    "draw_plugin_chips",
    "draw_message_threads",
    "draw_chat_bubble",
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
CHAT_BUBBLE_HEIGHT = 0  # compatibility constant; dimensions follow text and ratio

# Conservative Pango line-height estimates (points * 1.45, rounded up) used by
# the layout so ellipsized text always fits inside its computed bubble.
TITLE_LINE_HEIGHT = int(math.ceil(MESSAGE_TITLE_SIZE * 1.45))   # 15
TEXT_LINE_HEIGHT = int(math.ceil(MESSAGE_TEXT_SIZE * 1.45))     # 14


def _bounded_number(value, default, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    try:
        if not math.isfinite(value):
            return float(default)
    except OverflowError:
        return float(default)
    return max(minimum, min(maximum, float(value)))


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


class ChatBubbleRect(NamedTuple):
    """Comic speech balloon geometry and notification metadata."""

    thread_id: str
    rect: cairo.RectangleInt
    tail: tuple[cairo.RectangleInt, ...]  # hit area around the speech pointer
    side: str
    message: dict
    lines: int
    font_size: float
    corner_radius_percent: float


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
    chat_bubble_opacity: float
    border_radius: float
    unread_count: int
    messages_collapsed: bool
    collapsed_bubble_rect: cairo.RectangleInt | None
    message_toggle_rect: cairo.RectangleInt | None
    plugin_side: str        # "left" or "right": side of the vertical chip stack
    chat_bubble: ChatBubbleRect | None


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
    chat_bubble_opacity: float = BUBBLE_ALPHA,
    border_radius: float = 14.0,
    messages_collapsed: bool = False,
    unread_count: int = 0,
    plugin_side: str = "right",
    chat_message: dict | None = None,
    chat_font_size: float = CHAT_BUBBLE_TEXT_SIZE,
    chat_aspect_ratio: float = CHAT_BUBBLE_ASPECT_RATIO,
    chat_corner_radius_percent: float = CHAT_BUBBLE_CORNER_RADIUS_PERCENT,
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
    chat_bubble_opacity = _bounded_opacity(chat_bubble_opacity, BUBBLE_ALPHA)
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
    chat_font_size = _bounded_number(chat_font_size, CHAT_BUBBLE_TEXT_SIZE, 8, 28)
    chat_aspect_ratio = _bounded_number(chat_aspect_ratio, CHAT_BUBBLE_ASPECT_RATIO, 1, 3.5)
    chat_corner_radius_percent = _bounded_number(
        chat_corner_radius_percent, CHAT_BUBBLE_CORNER_RADIUS_PERCENT, 0, 50
    )

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

    # Comic chat lives on the side opposite plugin chips. Its single speech
    # pointer reaches toward the pet, with the hit target spanning the pointer.
    chat_bubble = None
    if isinstance(chat_message, dict) and isinstance(chat_message.get("text"), str) and chat_message["text"].strip():
        chat_side = "left" if plugin_side == "right" else "right"
        side_width = max(1, (width - cell_width) // 2 - CHIP_PET_GAP)
        chat_width, chat_height, chat_lines = estimate_chat_bubble_size(
            chat_message["text"],
            max_width=min(CHAT_BUBBLE_MAX_WIDTH, side_width),
            font_size=chat_font_size,
            aspect_ratio=chat_aspect_ratio,
            corner_radius_percent=chat_corner_radius_percent,
        )
        chat_x = pet_x - CHIP_PET_GAP - chat_width if chat_side == "left" else pet_x + cell_width + CHIP_PET_GAP
        chat_x = max(0, min(width - chat_width, chat_x))
        chat_y = int(pet_top + max(0, min(cell_height - chat_height, cell_height * 0.08)))
        chat_rect = cairo.RectangleInt(int(chat_x), chat_y, int(chat_width), chat_height)
        tip_x = pet_x + int(cell_width * (0.95 if chat_side == "right" else 0.05))
        tip_y = int(pet_top + cell_height * 0.30)
        tail_thickness = min(16, max(8, int(chat_height * 0.12)))
        tail_y = chat_rect.y + chat_height // 2
        base_x = chat_rect.x + chat_rect.width if chat_side == "left" else chat_rect.x
        tail = (cairo.RectangleInt(
            min(base_x, tip_x),
            min(tail_y - tail_thickness // 2, tip_y),
            max(1, abs(tip_x - base_x)),
            abs(tip_y - tail_y) + tail_thickness,
        ),)
        chat_bubble = ChatBubbleRect(
            str(chat_message.get("threadId") or ""), chat_rect, tuple(tail),
            chat_side, chat_message, chat_lines, chat_font_size,
            chat_corner_radius_percent,
        )

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
        max([pet_bottom]
            + [dot.y + dot.height for dot in (chat_bubble.tail if chat_bubble else ())]
            + ([chat_bubble.rect.y + chat_bubble.rect.height] if chat_bubble else [])),
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
        chat_bubble_opacity=chat_bubble_opacity,
        border_radius=border_radius,
        unread_count=unread_count,
        messages_collapsed=is_collapsed,
        collapsed_bubble_rect=collapsed_bubble_rect,
        message_toggle_rect=message_toggle_rect,
        plugin_side=plugin_side,
        chat_bubble=chat_bubble,
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
    if layout.chat_bubble is not None:
        rects.append(layout.chat_bubble.rect)
        rects.extend(layout.chat_bubble.tail)
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

    if layout.chat_bubble is not None:
        bubble = layout.chat_bubble
        r = bubble.rect
        if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
            return Hit("chat", None, bubble.thread_id, None)
        if any(dot.x <= x < dot.x + dot.width and dot.y <= y < dot.y + dot.height
               for dot in bubble.tail):
            return Hit("chat", None, bubble.thread_id, None)

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


def _shape_text_width(width, height, top, bottom, radius_percent, pad_x):
    """Usable line width at a vertical band inside the rounded rectangle."""
    rx = min(width * radius_percent / 100.0, width / 2.0)
    ry = min(height * radius_percent / 100.0, height / 2.0)

    def inset(y):
        if rx <= 0 or ry <= 0:
            return 0.0
        if y < ry:
            normalized = (ry - y) / ry
        elif y > height - ry:
            normalized = (y - (height - ry)) / ry
        else:
            return 0.0
        normalized = min(1.0, max(0.0, normalized))
        return rx * (1 - math.sqrt(max(0.0, 1 - normalized * normalized)))

    inset_x = max(inset(top), inset(bottom))
    return max(1, int(width - 2 * pad_x - 2 * inset_x))


def _text_graphemes(text):
    """Yield simple Unicode grapheme clusters so wrapping keeps combining marks."""
    cluster = ""
    join_next = False
    for char in text:
        codepoint = ord(char)
        extender = (
            unicodedata.combining(char) != 0
            or char == "\u200d"
            or join_next
            or 0xFE00 <= codepoint <= 0xFE0F
            or 0x1F3FB <= codepoint <= 0x1F3FF
        )
        if cluster and not extender:
            yield cluster
            cluster = ""
        cluster += char
        join_next = char == "\u200d"
    if cluster:
        yield cluster


def wrap_chat_text_to_shape(
    context, text, width, height, font_size=CHAT_BUBBLE_TEXT_SIZE,
    radius_percent=CHAT_BUBBLE_CORNER_RADIUS_PERCENT, width_cache=None,
):
    """Shape-aware Unicode wrapping, centered vertically and line-by-line."""
    _, line_height = _text_layout(context, "Mg", font_size, 1000).get_pixel_size()
    line_height = max(1, line_height)
    initial = _text_layout(context, text, font_size, max(1, int(width - 2 * CHAT_BUBBLE_PAD_X)))
    initial.set_single_paragraph_mode(False)
    initial.set_wrap(Pango.WrapMode.WORD_CHAR)
    guess = max(1, initial.get_line_count())

    width_cache = width_cache if width_cache is not None else {}
    measure_layout = PangoCairo.create_layout(context)
    measure_layout.set_font_description(Pango.FontDescription(f"Sans {font_size}"))
    measure_layout.set_width(-1)
    measure_layout.set_single_paragraph_mode(True)

    def measure_width(value):
        if value not in width_cache:
            measure_layout.set_text(value, -1)
            width_cache[value] = measure_layout.get_pixel_size()[0]
        return width_cache[value]

    def wrap_once(start_y):
        lines = []
        space_width = measure_width(" ")
        for paragraph in text.split("\n"):
            remaining = deque(paragraph.split())
            if not remaining:
                lines.append("")
                continue
            while remaining:
                top = start_y + len(lines) * line_height
                available = _shape_text_width(
                    width, height, top, top + line_height,
                    radius_percent, CHAT_BUBBLE_PAD_X,
                )
                current_words = []
                current_width = 0
                while remaining:
                    word = remaining[0]
                    word_width = measure_width(word)
                    candidate_width = current_width + (space_width if current_words else 0) + word_width
                    if candidate_width > available:
                        break
                    current_words.append(remaining.popleft())
                    current_width = candidate_width
                if not current_words:
                    # Split an oversized token using cached grapheme widths
                    # rather than shaping every possible string prefix.
                    word = remaining.popleft()
                    fragments = []
                    fragment_width = 0
                    used = 0
                    for grapheme in _text_graphemes(word):
                        grapheme_width = measure_width(grapheme)
                        if fragments and fragment_width + grapheme_width > available:
                            break
                        fragments.append(grapheme)
                        fragment_width += grapheme_width
                        used += len(grapheme)
                    current = "".join(fragments) or word[:1]
                    while len(fragments) > 1 and measure_width(current) > available:
                        used -= len(fragments.pop())
                        current = "".join(fragments)
                    if word[used:]:
                        remaining.appendleft(word[used:])
                    lines.append(current)
                    continue
                current = " ".join(current_words)
                # Word widths are accumulated for speed; verify the shaped
                # line and carry trailing words forward if kerning exceeds it.
                while current_words and measure_width(current) > available:
                    remaining.appendleft(current_words.pop())
                    current = " ".join(current_words)
                if current_words:
                    lines.append(current)
        return lines or [""]

    lines = [""]
    for _ in range(16):
        start_y = max(CHAT_BUBBLE_PAD_Y, (height - guess * line_height) / 2.0)
        lines = wrap_once(start_y)
        if len(lines) <= guess:
            break
        guess = len(lines)
    return lines, line_height


def estimate_chat_bubble_size(
    text, max_width=CHAT_BUBBLE_MAX_WIDTH,
    font_size=CHAT_BUBBLE_TEXT_SIZE,
    aspect_ratio=CHAT_BUBBLE_ASPECT_RATIO,
    corner_radius_percent=CHAT_BUBBLE_CORNER_RADIUS_PERCENT,
):
    """Measure a bubble by actual Pango UTF-8 wrapping and its corner shape."""
    text = text if isinstance(text, str) else ""
    try:
        max_width = max(1, min(CHAT_BUBBLE_MAX_WIDTH, int(max_width)))
    except (TypeError, ValueError, OverflowError):
        max_width = CHAT_BUBBLE_MAX_WIDTH
    font_size = _bounded_number(font_size, CHAT_BUBBLE_TEXT_SIZE, 8, 28)
    aspect_ratio = _bounded_number(aspect_ratio, CHAT_BUBBLE_ASPECT_RATIO, 1, 3.5)
    corner_radius_percent = _bounded_number(
        corner_radius_percent, CHAT_BUBBLE_CORNER_RADIUS_PERCENT, 0, 50
    )
    return _estimate_chat_bubble_size_cached(
        text, max_width, font_size, aspect_ratio, corner_radius_percent
    )


@lru_cache(maxsize=128)
def _estimate_chat_bubble_size_cached(
    text, max_width, font_size, aspect_ratio, corner_radius_percent
):
    """Cache stable Pango measurements across repeated overlay relayouts."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    context = cairo.Context(surface)
    low = min(max_width, max(96, 2 * CHAT_BUBBLE_PAD_X + 1))
    width_cache = {}

    def measure(width):
        height = width / aspect_ratio
        lines, line_height = wrap_chat_text_to_shape(
            context, text, width, height, font_size, corner_radius_percent,
            width_cache,
        )
        return 2 * CHAT_BUBBLE_PAD_Y + len(lines) * line_height, len(lines)

    high = max_width
    high_height, high_lines = measure(high)
    if high_height > high / aspect_ratio:
        return high, high_height, high_lines
    for _ in range(18):
        middle = (low + high) / 2.0
        required_height, _ = measure(middle)
        if required_height <= middle / aspect_ratio:
            high = middle
        else:
            low = middle
    width = int(math.ceil(high))
    while width < max_width:
        content_height, lines = measure(width)
        if content_height <= width / aspect_ratio:
            break
        width += 1
    content_height, lines = measure(width)
    if content_height > width / aspect_ratio:
        return width, int(content_height), int(lines)
    return width, int(round(width / aspect_ratio)), int(lines)


@lru_cache(maxsize=256)
def _cached_chat_lines(text, width, height, font_size, radius_percent):
    """Memoize per-frame bubble line shaping; drawing can run many times/sec."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    context = cairo.Context(surface)
    lines, line_height = wrap_chat_text_to_shape(
        context, text, width, height, font_size, radius_percent
    )
    return tuple(lines), line_height


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
        text_layout.set_alignment(Pango.Alignment.CENTER)
        _, text_h = text_layout.get_size()
        text_h = text_h / Pango.SCALE
        context.set_source_rgb(*ink)
        # Pango's layout has a fixed inner width for ellipsizing; center its
        # text within that width rather than centering the layout box itself.
        context.move_to(r.x + CHIP_PAD_X, r.y + (r.height - text_h) / 2.0)
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


def draw_chat_bubble(context: cairo.Context, layout: OverlayLayout) -> None:
    """Draw a white, black-ink speech balloon with shape-aware centered text."""
    bubble = layout.chat_bubble
    if bubble is None:
        return
    if layout.chat_bubble_opacity <= 0:
        return
    r = bubble.rect
    context.push_group()
    radius_x = r.width * bubble.corner_radius_percent / 100.0
    radius_y = r.height * bubble.corner_radius_percent / 100.0
    half_contact = min(8.0, r.height * 0.12)
    top_y = r.height / 2.0 - half_contact
    bottom_y = r.height / 2.0 + half_contact
    if bubble.side == "right":
        top_x = r.x + _chat_side_inset(r.height, radius_y, radius_x, top_y)
        bottom_x = r.x + _chat_side_inset(r.height, radius_y, radius_x, bottom_y)
        tip_x = layout.pet_rect.x + layout.cell_width * 0.76
    else:
        top_x = r.x + r.width - _chat_side_inset(r.height, radius_y, radius_x, top_y)
        bottom_x = r.x + r.width - _chat_side_inset(r.height, radius_y, radius_x, bottom_y)
        tip_x = layout.pet_rect.x + layout.cell_width * 0.24
    tip_y = layout.pet_rect.y + layout.cell_height * 0.30

    # Fill the pointed tail first, then the balloon body over its root.
    context.set_source_rgb(1, 1, 1)
    context.move_to(top_x, r.y + top_y)
    context.line_to(tip_x, tip_y)
    context.line_to(bottom_x, r.y + bottom_y)
    context.close_path()
    context.fill()
    _rounded_rect_elliptical(context, r, radius_x, radius_y)
    context.fill()

    # Stroke the body, remove exactly the perimeter segment between the
    # computed tail contacts, then ink the two edges into that opening.
    context.set_source_rgb(0, 0, 0)
    context.set_line_width(2.5)
    _rounded_rect_elliptical(context, r, radius_x, radius_y)
    context.stroke()
    context.set_source_rgb(1, 1, 1)
    context.set_line_width(4.0)
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.move_to(top_x, r.y + top_y)
    context.line_to(bottom_x, r.y + bottom_y)
    context.stroke()
    context.set_source_rgb(0, 0, 0)
    context.set_line_width(2.5)
    context.move_to(tip_x, tip_y)
    context.line_to(top_x, r.y + top_y)
    context.move_to(tip_x, tip_y)
    context.line_to(bottom_x, r.y + bottom_y)
    context.stroke()

    text = bubble.message.get("text", "").strip()
    lines, line_height = _cached_chat_lines(
        text, r.width, r.height, bubble.font_size, bubble.corner_radius_percent,
    )
    text_y = max(CHAT_BUBBLE_PAD_Y, (r.height - len(lines) * line_height) / 2.0)
    context.save()
    _rounded_rect_elliptical(context, r, radius_x, radius_y)
    context.clip()
    context.set_source_rgb(0, 0, 0)
    for index, line in enumerate(lines):
        line_layout = _text_layout(context, line, bubble.font_size, 10000)
        line_width, _ = line_layout.get_pixel_size()
        context.move_to(r.x + (r.width - line_width) / 2.0,
                        r.y + text_y + index * line_height)
        PangoCairo.show_layout(context, line_layout)
    context.restore()
    context.pop_group_to_source()
    context.paint_with_alpha(layout.chat_bubble_opacity)


def _chat_side_inset(height, radius_y, radius_x, y):
    if radius_x <= 0 or radius_y <= 0:
        return 0.0
    if y < radius_y:
        normalized = (radius_y - y) / radius_y
    elif y > height - radius_y:
        normalized = (y - (height - radius_y)) / radius_y
    else:
        return 0.0
    normalized = min(1.0, max(0.0, normalized))
    return radius_x * (1 - math.sqrt(max(0.0, 1 - normalized * normalized)))


def _rounded_rect_elliptical(context, rect, radius_x, radius_y):
    x, y, width, height = rect.x, rect.y, rect.width, rect.height
    rx, ry = min(radius_x, width / 2.0), min(radius_y, height / 2.0)
    k = 0.5522847498
    context.new_sub_path()
    context.move_to(x + rx, y)
    context.line_to(x + width - rx, y)
    context.curve_to(x + width - rx * (1-k), y, x + width, y + ry * (1-k), x + width, y + ry)
    context.line_to(x + width, y + height - ry)
    context.curve_to(x + width, y + height - ry * (1-k), x + width - rx * (1-k), y + height, x + width - rx, y + height)
    context.line_to(x + rx, y + height)
    context.curve_to(x + rx * (1-k), y + height, x, y + height - ry * (1-k), x, y + height - ry)
    context.line_to(x, y + ry)
    context.curve_to(x, y + ry * (1-k), x + rx * (1-k), y, x + rx, y)
    context.close_path()


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


def draw_overlay(
    context: cairo.Context, layout: OverlayLayout, show_toggle: bool = True,
    draw_chat: bool = True,
) -> None:
    """Draw plugin chips and message thread bubbles for one frame."""
    draw_plugin_chips(context, layout)
    draw_message_threads(context, layout)
    if draw_chat:
        draw_chat_bubble(context, layout)
    draw_message_previews(context, layout)
    draw_message_controls(context, layout, show_toggle=show_toggle)
