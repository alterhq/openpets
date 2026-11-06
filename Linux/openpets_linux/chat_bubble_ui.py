"""Input-transparent comic-bubble overlay with a cached Cairo raster surface."""

from __future__ import annotations

import logging
import time

import cairo
from gi.repository import Gdk, Gtk

from .chat_bubble import (
    CHAT_BUBBLE_ASPECT_RATIO,
    CHAT_BUBBLE_CORNER_RADIUS_PERCENT,
    CHAT_BUBBLE_MAX_WIDTH,
    CHAT_BUBBLE_TEXT_SIZE,
    estimate_bubble_size,
)
from .overlay import CHIP_PET_GAP, compute_layout, draw_chat_bubble
from .logging_setup import elapsed_ms

logger = logging.getLogger(__name__)


class ChatBubbleOverlay(Gtk.Window):
    """A top-level bubble that follows the pet without participating in its layout.

    The cached image is expressed relative to the pet, so ordinary movement and
    dragging only move this window. Cairo/Pango rendering is repeated only when
    visible bubble content or style changes, or when the bubble switches sides
    to stay on-screen.
    """

    def __init__(self, parent):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.parent = parent
        self.set_title("OpenPets comic bubble")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_app_paintable(True)
        self.set_transient_for(parent)
        visual = parent.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self.message = None
        self.layout = None
        self.surface = None
        self.cache_key = None
        self.render_count = 0
        self.plugin_side = "right"
        self.font_size = CHAT_BUBBLE_TEXT_SIZE
        self.aspect_ratio = CHAT_BUBBLE_ASPECT_RATIO
        self.corner_radius_percent = CHAT_BUBBLE_CORNER_RADIUS_PERCENT
        self.opacity = 0.94
        self.cell_width = 0
        self.cell_height = 0
        self.workarea = None
        self.pet_x = 0
        self.pet_y = 0
        self.chat_side = None

        self.connect("draw", self.on_draw)
        self.connect("realize", self._make_input_transparent)

    def set_bubble(
        self,
        message,
        *,
        pet_x,
        pet_y,
        cell_width,
        cell_height,
        workarea,
        plugin_side,
        font_size=CHAT_BUBBLE_TEXT_SIZE,
        aspect_ratio=CHAT_BUBBLE_ASPECT_RATIO,
        corner_radius_percent=CHAT_BUBBLE_CORNER_RADIUS_PERCENT,
        opacity=0.94,
    ):
        """Set visible content/style and anchor it to the pet's sprite rectangle."""
        if (not isinstance(message, dict)
                or not isinstance(message.get("text"), str)
                or not message["text"].strip()):
            self.message = None
            self.layout = None
            self.surface = None
            self.cache_key = None
            self.hide()
            return

        self.message = dict(message)
        self.pet_x, self.pet_y = int(pet_x), int(pet_y)
        self.cell_width, self.cell_height = int(cell_width), int(cell_height)
        self.workarea = tuple(workarea) if workarea is not None else None
        self.plugin_side = plugin_side if plugin_side in ("left", "right") else "right"
        self.font_size = float(font_size)
        self.aspect_ratio = float(aspect_ratio)
        self.corner_radius_percent = float(corner_radius_percent)
        self.opacity = max(0.0, min(1.0, float(opacity)))
        self._build_layout_and_cache()
        self._place_at_pet()
        if self.parent.get_visible():
            self.show()
            self._make_input_transparent()
        else:
            self.hide()

    def _available_widths(self):
        if self.workarea is None:
            return CHAT_BUBBLE_MAX_WIDTH, CHAT_BUBBLE_MAX_WIDTH
        area_x, _area_y, area_width, _area_height = self.workarea
        left = self.pet_x - area_x - CHIP_PET_GAP
        right = area_x + area_width - (self.pet_x + self.cell_width) - CHIP_PET_GAP
        return max(0, left), max(0, right)

    def _select_chat_side(self, required_width):
        preferred = "left" if self.plugin_side == "right" else "right"
        left_available, right_available = self._available_widths()
        available = {"left": left_available, "right": right_available}
        other = "right" if preferred == "left" else "left"
        if available[preferred] >= required_width:
            return preferred
        if available[other] >= required_width:
            return other
        return max(available, key=available.get)

    def _build_layout_and_cache(self):
        # Cap the bubble by monitor size, not current drag position. This keeps
        # the raster dimensions stable while the pet moves across that monitor.
        max_width = CHAT_BUBBLE_MAX_WIDTH
        if self.workarea is not None:
            max_width = min(
                max_width,
                max(96, (self.workarea[2] - self.cell_width) // 2 - CHIP_PET_GAP),
            )
        estimated_width, _, _ = estimate_bubble_size(
            self.message["text"], max_width=max_width
        )
        chat_side = self._select_chat_side(estimated_width)
        layout_plugin_side = "right" if chat_side == "left" else "left"
        canvas_width = max(
            360,
            self.cell_width + 2 * (estimated_width + CHIP_PET_GAP),
        )
        layout = compute_layout(
            canvas_width,
            self.cell_width,
            self.cell_height,
            plugin_side=layout_plugin_side,
            chat_message=self.message,
            chat_font_size=self.font_size,
            chat_aspect_ratio=self.aspect_ratio,
            chat_corner_radius_percent=self.corner_radius_percent,
            chat_bubble_opacity=self.opacity,
        )
        self.layout = layout
        self.chat_side = chat_side
        bubble = layout.chat_bubble
        if bubble is None:
            self.surface = None
            self.cache_key = None
            self.hide()
            return

        key = (
            self.message.get("text", ""),
            layout.width,
            layout.total_height,
            bubble.rect.width,
            bubble.rect.height,
            bubble.side,
            bubble.font_size,
            bubble.corner_radius_percent,
            layout.chat_bubble_opacity,
        )
        if key != self.cache_key:
            self._render_cache(key)
        # Gtk.Window.resize() issued before the first realization is not
        # reliably applied by GTK3; the toplevel can remain at its implicit
        # 200x200 size and clip the (larger) cached Cairo surface. Set the
        # initial size explicitly, and keep resize() for later content changes.
        self.set_default_size(layout.width, layout.total_height)
        self.resize(layout.width, layout.total_height)

    def _render_cache(self, key):
        started = time.perf_counter()
        self.surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, self.layout.width, self.layout.total_height
        )
        context = cairo.Context(self.surface)
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.set_source_rgba(0, 0, 0, 0)
        context.paint()
        context.set_operator(cairo.OPERATOR_OVER)
        draw_chat_bubble(context, self.layout)
        self.surface.flush()
        self.cache_key = key
        self.render_count += 1
        logger.info(
            "chat bubble raster cached width=%s height=%s bytes=%s renders=%s elapsed_ms=%.1f",
            self.surface.get_width(), self.surface.get_height(),
            self.surface.get_stride() * self.surface.get_height(),
            self.render_count, elapsed_ms(started),
        )

    def move_with_pet(self, pet_x, pet_y, workarea=None):
        """Move the overlay with the pet; do not repaint its cached contents."""
        if self.layout is None or self.layout.chat_bubble is None:
            return
        self.pet_x, self.pet_y = int(pet_x), int(pet_y)
        if workarea is not None and tuple(workarea) != self.workarea:
            self.workarea = tuple(workarea)
            self._build_layout_and_cache()
        else:
            required_width = self.layout.chat_bubble.rect.width
            if self._select_chat_side(required_width) != self.chat_side:
                self._build_layout_and_cache()
        self._place_at_pet()

    def _place_at_pet(self):
        if self.layout is None:
            return
        self.move(
            self.pet_x - self.layout.pet_x,
            self.pet_y - self.layout.pet_top,
        )

    def _make_input_transparent(self, *_args):
        if self.get_window() is not None:
            self.input_shape_combine_region(cairo.Region())

    def set_parent_visible(self, visible):
        if visible and self.message is not None and self.layout is not None:
            self.show()
            self._make_input_transparent()
            self._place_at_pet()
        else:
            self.hide()

    def on_draw(self, _widget, context):
        if self.surface is not None:
            context.set_source_surface(self.surface, 0, 0)
            context.paint()
        return False
