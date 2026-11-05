"""Unit tests for openpets_linux.overlay.

Headless: layouts are pure geometry and rendering targets in-memory Cairo
image surfaces. No display server, Gtk widgets, or plugin provider access is
required anywhere.
"""

import os
import sys
import unittest

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_foreign("cairo")
import cairo  # noqa: E402  (requires the gi imports above)

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from openpets_linux.overlay import (  # noqa: E402
    BUBBLE_ALPHA,
    BUBBLE_BG,
    BUBBLE_RADIUS,
    CHIP_ALPHA,
    CHIP_COLS,
    CHIP_COL_GAP,
    CHIP_HEIGHT,
    CHIP_PAD_X,
    CHIP_PET_GAP,
    CHIP_TOP_GAP,
    DEFAULT_STATE_COLOR,
    MESSAGE_BOTTOM_GAP,
    MESSAGE_BUBBLE_GAP,
    MESSAGE_LINE_GAP,
    MESSAGE_MAX_WIDTH,
    MESSAGE_PAD_X,
    MESSAGE_PAD_Y,
    MESSAGE_TEXT_SIZE,
    MESSAGE_TITLE_SIZE,
    OverlayLayout,
    PET_BOTTOM_GAP,
    STATE_COLORS,
    TEXT_LINE_HEIGHT,
    TITLE_LINE_HEIGHT,
    compute_layout,
    draw_overlay,
    draw_plugin_chips,
    draw_message_threads,
    hit_test,
    input_rectangles,
)

WIDTH = 360
CELL_W = 140
CELL_H = 100


def _surface(name="battery", state="normal", label="Battery", value="80%"):
    return {
        "id": f"openpets.plugin.{name}",
        "label": label,
        "value": value,
        "state": state,
    }


def _message(thread_id="t1", title="Hello", text=None, status="done"):
    return {
        "title": title,
        "text": text,
        "status": status,
        "threadId": thread_id,
    }


def _blank_surface(width=WIDTH, height=WIDTH):
    surface = cairo.ImageSurface(cairo.Format.ARGB32, width, height)
    context = cairo.Context(surface)
    context.set_source_rgb(0, 0, 0)
    context.paint()
    return surface, context


def _pixel_rgba(data, stride, x, y):
    """Read one pixel as (r, g, b, a) regardless of byte order."""
    offset = y * stride + x * 4
    b0, b1, b2, b3 = data[offset], data[offset + 1], data[offset + 2], data[offset + 3]
    if sys.byteorder == "little":
        # CAIRO_FORMAT_ARGB32 stores words little-endian: B, G, R, A in memory.
        return b2, b1, b0, b3
    return b1, b2, b0, b3


def _blend_over_opaque_black(r, g, b, alpha):
    # Destination is opaque black (alpha 255): result alpha stays 255,
    # RGB is src * alpha + dst * (1 - alpha) = src * alpha over black.
    return tuple(round(c * alpha * 255) for c in (r, g, b)) + (255,)


class TestConstants(unittest.TestCase):
    def test_line_heights_are_positive_ints(self):
        self.assertIsInstance(TITLE_LINE_HEIGHT, int)
        self.assertIsInstance(TEXT_LINE_HEIGHT, int)
        self.assertGreater(TITLE_LINE_HEIGHT, 0)
        self.assertGreater(TEXT_LINE_HEIGHT, 0)

    def test_state_colors_cover_known_states(self):
        for state in ("critical", "warning", "success", "failed"):
            self.assertIn(state, STATE_COLORS)
            color = STATE_COLORS[state]
            self.assertEqual(len(color), 3)
            self.assertTrue(all(0 <= c <= 1 for c in color))
        self.assertEqual(len(DEFAULT_STATE_COLOR), 3)


class TestComputeLayoutEmpty(unittest.TestCase):
    def test_empty_state_collapses_to_thin_header(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        self.assertIsInstance(layout, OverlayLayout)
        self.assertEqual(layout.chip_rects, ())
        self.assertEqual(layout.message_rects, ())
        self.assertIsNone(layout.chip_band_rect)
        self.assertIsNone(layout.message_band_rect)
        self.assertEqual(layout.pet_top, CHIP_TOP_GAP + CHIP_PET_GAP)
        self.assertEqual(layout.header_height, layout.pet_top)
        self.assertEqual(
            layout.total_height, layout.pet_top + CELL_H + PET_BOTTOM_GAP
        )
        # Compact replacement for the old cell_height + HEADER_HEIGHT (210).
        self.assertLess(layout.total_height, CELL_H + 210)
        self.assertLess(layout.header_height, 210)

    def test_empty_state_pet_rect_is_inside_window(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        pr = layout.pet_rect
        self.assertEqual(pr.x, (WIDTH - CELL_W) // 2)
        self.assertEqual(pr.y, layout.pet_top)
        self.assertEqual(pr.width, CELL_W)
        self.assertEqual(pr.height, CELL_H)
        self.assertGreaterEqual(pr.y, 0)
        self.assertLessEqual(pr.y + pr.height, layout.total_height)

    def test_empty_input_rectangles_is_pet_only(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        rects = input_rectangles(layout)
        self.assertEqual(len(rects), 1)
        self.assertEqual(rects[0], layout.pet_rect)
        self.assertIsInstance(rects[0], cairo.RectangleInt)

    def test_layout_is_deterministic(self):
        a = compute_layout(WIDTH, CELL_W, CELL_H, (), ())
        b = compute_layout(WIDTH, CELL_W, CELL_H, (), ())
        self.assertEqual(a, b)


class TestComputeLayoutChips(unittest.TestCase):
    def test_chip_rows_above_pet(self):
        surfaces = [_surface(f"s{i}") for i in range(5)]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces)
        self.assertEqual(len(layout.chip_rects), 5)
        cols = min(CHIP_COLS, 5)  # 3 cols, 2 rows
        # Row 0: y = CHIP_TOP_GAP; row 1: one row height + gap below.
        expected = [
            (22, 8), (130, 8), (238, 8),
            (76, 44), (184, 44),
        ]
        self.assertEqual(len(expected), len(layout.chip_rects))
        for chip, (ex, ey) in zip(layout.chip_rects, expected):
            self.assertEqual(chip.rect.x, ex)
            self.assertEqual(chip.rect.y, ey)
            self.assertEqual(chip.rect.width, 100)
            self.assertEqual(chip.rect.height, CHIP_HEIGHT)
            self.assertEqual(chip.surface, surfaces[chip.index])
        # Rows anchored directly above the pet.
        self.assertEqual(layout.pet_top, 44 + CHIP_HEIGHT + CHIP_PET_GAP)  # 82
        self.assertEqual(layout.header_height, layout.pet_top)
        self.assertLess(layout.header_height, 210)

    def test_chip_rects_do_not_overlap_and_fit_width(self):
        surfaces = [_surface(f"s{i}") for i in range(5)]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces)
        for chip in layout.chip_rects:
            r = chip.rect
            self.assertGreaterEqual(r.x, 0)
            self.assertLessEqual(r.x + r.width, layout.width)
            self.assertGreaterEqual(r.y, CHIP_TOP_GAP)
            self.assertLess(r.y + r.height, layout.pet_top)
        for i in range(len(layout.chip_rects) - 1):
            a, b = layout.chip_rects[i], layout.chip_rects[i + 1]
            if a.rect.y == b.rect.y:
                self.assertLess(a.rect.x + a.rect.width, b.rect.x - CHIP_COL_GAP + 1)

    def test_single_chip_is_centered(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, [_surface()])
        self.assertEqual(len(layout.chip_rects), 1)
        chip = layout.chip_rects[0]
        self.assertEqual(chip.index, 0)
        self.assertGreater(chip.rect.x, 0)
        self.assertLess(chip.rect.x + chip.rect.width, layout.width)

    def test_chip_band_rect_covers_all_rows(self):
        surfaces = [_surface(f"s{i}") for i in range(5)]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces)
        band = layout.chip_band_rect
        self.assertIsNotNone(band)
        self.assertEqual(band.x, 0)
        self.assertEqual(band.y, CHIP_TOP_GAP)
        self.assertEqual(band.width, layout.width)
        self.assertEqual(band.y + band.height, layout.chip_rects[-1].rect.y + CHIP_HEIGHT)
        for chip in layout.chip_rects:
            r = chip.rect
            self.assertTrue(band.y <= r.y and r.y + r.height <= band.y + band.height)

    def test_no_surfaces_means_no_chip_band(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        self.assertIsNone(layout.chip_band_rect)


class TestComputeLayoutMessages(unittest.TestCase):
    def test_message_threads_below_pet(self):
        messages = [_message("t1", "First"), _message("t2", "Second", "with detail")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), messages)
        self.assertEqual(len(layout.message_rects), 2)
        # No chips: pet_top = 16, band starts at pet_top + cell_height + 10.
        self.assertEqual(layout.message_band_top, 16 + CELL_H + PET_BOTTOM_GAP)
        # Width bounded by MESSAGE_MAX_WIDTH and by width - 2 * pad.
        for msg in layout.message_rects:
            self.assertEqual(msg.rect.width, min(MESSAGE_MAX_WIDTH, WIDTH - 2 * MESSAGE_PAD_X))
            self.assertEqual(msg.rect.x, (WIDTH - msg.rect.width) // 2)
            self.assertGreaterEqual(msg.rect.y, layout.message_band_top)
        # Title-only -> 1 line; with text -> 2 lines.
        self.assertEqual(layout.message_rects[0].lines, 1)
        self.assertEqual(layout.message_rects[0].thread_id, "t1")
        self.assertEqual(layout.message_rects[1].lines, 2)
        self.assertEqual(layout.message_rects[1].thread_id, "t2")
        # Stacked with a gap, band ends with a bottom margin.
        a, b = layout.message_rects
        self.assertEqual(b.rect.y, a.rect.y + a.rect.height + MESSAGE_BUBBLE_GAP)
        self.assertEqual(
            layout.message_band_height,
            30 + a.rect.height + MESSAGE_BUBBLE_GAP + b.rect.height + MESSAGE_BOTTOM_GAP,
        )
        self.assertEqual(
            layout.total_height,
            layout.message_band_top + layout.message_band_height,
        )
        self.assertLess(layout.total_height, CELL_H + 210 + 120)  # still compact

    def test_bubble_width_scales_with_window(self):
        wide = compute_layout(900, CELL_W, CELL_H, (), [_message()])
        self.assertEqual(wide.message_rects[0].rect.width, MESSAGE_MAX_WIDTH)
        narrow = compute_layout(100, CELL_W, CELL_H, (), [_message()])
        self.assertEqual(narrow.message_rects[0].rect.width, 100 - 2 * MESSAGE_PAD_X)

    def test_no_messages_means_no_message_band(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        self.assertEqual(layout.message_band_height, 0)
        self.assertIsNone(layout.message_band_rect)
        self.assertEqual(layout.message_band_top, layout.pet_top + CELL_H + PET_BOTTOM_GAP)

    def test_collapsed_messages_use_summary_bubble_with_unread_badge(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                [_message("a"), _message("b")],
                                messages_collapsed=True, unread_count=2)
        self.assertEqual(layout.message_rects, ())
        self.assertIsNotNone(layout.collapsed_bubble_rect)
        self.assertIsNone(layout.message_toggle_rect)
        self.assertEqual(layout.unread_count, 2)
        rects = input_rectangles(layout)
        r = layout.collapsed_bubble_rect
        self.assertIn(r, rects)
        self.assertEqual(hit_test(layout, r.x + r.width // 2, r.y + r.height // 2).kind,
                         "message-expand")

    def test_expanded_messages_have_collapse_control(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), [_message()])
        self.assertIsNone(layout.collapsed_bubble_rect)
        rect = layout.message_toggle_rect
        self.assertIsNotNone(rect)
        self.assertEqual(hit_test(layout, rect.x + 2, rect.y + 2).kind, "message-collapse")

    def test_message_width_and_font_size_are_configurable(self):
        message = _message("thread", "A title", "Message body")
        default = compute_layout(WIDTH, CELL_W, CELL_H, (), [message])
        adjusted = compute_layout(500, CELL_W, CELL_H, (), [message], 420, 16)
        self.assertEqual(default.message_rects[0].rect.width, MESSAGE_MAX_WIDTH)
        self.assertEqual(adjusted.message_rects[0].rect.width, 420)
        self.assertEqual(adjusted.message_font_size, 16)
        self.assertGreater(adjusted.message_rects[0].rect.height,
                           default.message_rects[0].rect.height)


class TestHitTest(unittest.TestCase):
    def setUp(self):
        surfaces = [_surface("battery", "success"), _surface("claude", "warning")]
        messages = [_message("t1", "First"), _message("t2", "Second", "text")]
        self.layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces, messages)

    def test_hit_chip_returns_index_and_surface(self):
        chip = self.layout.chip_rects[0]
        r = chip.rect
        hit = hit_test(self.layout, r.x + r.width // 2, r.y + r.height // 2)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, "chip")
        self.assertEqual(hit.index, 0)
        self.assertEqual(hit.surface, chip.surface)
        self.assertIsNone(hit.thread_id)

    def test_hit_pet_returns_pet(self):
        pr = self.layout.pet_rect
        hit = hit_test(self.layout, pr.x + pr.width // 2, pr.y + pr.height // 2)
        self.assertEqual(hit.kind, "pet")

    def test_hit_message_returns_index_and_thread_id(self):
        msg = self.layout.message_rects[0]
        r = msg.rect
        hit = hit_test(self.layout, r.x + r.width // 2, r.y + r.height // 2)
        self.assertEqual(hit.kind, "message")
        self.assertEqual(hit.index, 0)
        self.assertEqual(hit.thread_id, msg.thread_id)
        self.assertIsNone(hit.surface)

    def test_gaps_miss_everything(self):
        # Between chip band and pet top.
        hit = hit_test(self.layout, WIDTH // 2, self.layout.chip_rects[-1].rect.y + CHIP_HEIGHT + 2)
        self.assertIsNone(hit)
        # Top edge gap above chips.
        self.assertIsNone(hit_test(self.layout, WIDTH // 2, 3))
        # Below the message band.
        self.assertIsNone(
            hit_test(self.layout, WIDTH // 2, self.layout.total_height + 5)
        )
        # Outside the window.
        self.assertIsNone(hit_test(self.layout, -1, 50))
        self.assertIsNone(hit_test(self.layout, WIDTH + 10, 50))
        self.assertIsNone(hit_test(self.layout, 50, -10))

    def test_hit_test_coordinates_accept_floats(self):
        pr = self.layout.pet_rect
        hit = hit_test(self.layout, float(pr.x + pr.width // 2) + 0.5, float(pr.y + 10.7))
        self.assertEqual(hit.kind, "pet")


class TestInputRectangles(unittest.TestCase):
    def test_rects_cover_visible_blocks_and_fit_window(self):
        surfaces = [_surface(f"s{i}") for i in range(4)]
        messages = [_message("t1", "T"), _message("t2", "T", "x")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces, messages)
        rects = input_rectangles(layout)
        # Pet + individual chips/messages; transparent gaps click through.
        self.assertEqual(len(rects), 2 + len(surfaces) + len(messages))
        for rect in rects:
            self.assertIsInstance(rect, cairo.RectangleInt)
            self.assertGreaterEqual(rect.x, 0)
            self.assertGreaterEqual(rect.y, 0)
            self.assertLessEqual(rect.x + rect.width, layout.width)
            self.assertLessEqual(rect.y + rect.height, layout.total_height)
        # Every visible block center lies inside some input rectangle.
        points = [ch.rect for ch in layout.chip_rects]
        points.append(layout.pet_rect)
        points.extend(m.rect for m in layout.message_rects)
        for r in points:
            cx, cy = r.x + r.width // 2, r.y + r.height // 2
            self.assertTrue(
                any(
                    rr.x <= cx < rr.x + rr.width and rr.y <= cy < rr.y + rr.height
                    for rr in rects
                ),
                f"point {cx},{cy} not covered",
            )


class TestRendering(unittest.TestCase):
    def _render(self, layout):
        surface, context = _blank_surface(layout.width, layout.total_height)
        draw_overlay(context, layout)
        return surface, context

    def test_empty_state_renders_without_error(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        surface, _ = self._render(layout)
        data = surface.get_data()
        stride = surface.get_stride()
        # Black background untouched everywhere (opaque black pixels).
        for yy in range(0, layout.total_height, 17):
            for xx in range(0, layout.width, 17):
                self.assertEqual(_pixel_rgba(data, stride, xx, yy), (0, 0, 0, 255))

    def test_chip_render_color(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, [_surface(state="success")])
        surface, _ = self._render(layout)
        chip = layout.chip_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        r, g, b, a = _pixel_rgba(data, stride, chip.rect.x + chip.rect.width // 2,
                                 chip.rect.y + chip.rect.height // 2)
        # The chip surface is black in the default dark theme; status remains
        # available through its colored status dot.
        expected = (0, 0, 0, 255)
        self.assertAlmostEqual(r, expected[0], delta=6)
        self.assertAlmostEqual(g, expected[1], delta=6)
        self.assertAlmostEqual(b, expected[2], delta=6)
        self.assertAlmostEqual(a, expected[3], delta=4)
        # Gap below chips stays black.
        gap_y = chip.rect.y + chip.rect.height + 2
        self.assertEqual(_pixel_rgba(data, stride, chip.rect.x + 10, gap_y)[:3], (0, 0, 0))

    def test_message_bubble_render_color(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), [_message("t1", "Title")])
        surface, _ = self._render(layout)
        msg = layout.message_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        cx, cy = msg.rect.x + msg.rect.width // 2, msg.rect.y + msg.rect.height // 2
        r, g, b, a = _pixel_rgba(data, stride, cx, cy)
        expected = (0, 0, 0, 255)
        self.assertAlmostEqual(r, expected[0], delta=6)
        self.assertAlmostEqual(g, expected[1], delta=6)
        self.assertAlmostEqual(b, expected[2], delta=6)
        # Band gap below pet stays black until bubbles start.
        self.assertEqual(_pixel_rgba(data, stride, cx, layout.message_band_top - 1)[:3], (0, 0, 0))

    def test_text_renders_inside_bubble(self):
        layout = compute_layout(
            WIDTH, CELL_W, CELL_H, (),
            [_message("t1", "A somewhat long title", "and some detail text")]
        )
        surface, _ = self._render(layout)
        msg = layout.message_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        # Somewhere inside the text area there must be near-white text pixels
        # on top of the dark bubble.
        found_text = False
        for yy in range(msg.rect.y + MESSAGE_PAD_Y, msg.rect.y + msg.rect.height - MESSAGE_PAD_Y, 4):
            for xx in range(msg.rect.x + MESSAGE_PAD_X, msg.rect.x + msg.rect.width - MESSAGE_PAD_X, 4):
                r, g, b, _ = _pixel_rgba(data, stride, xx, yy)
                if r > 200 and g > 200 and b > 200:
                    found_text = True
                    break
            if found_text:
                break
        self.assertTrue(found_text, "no white text pixels found inside bubble")

    def test_long_text_ellipsizes_without_error(self):
        layout = compute_layout(
            WIDTH, CELL_W, CELL_H, (),
            [_message("t1", "x" * 300, "y" * 300)]
        )
        surface, _ = self._render(layout)
        data = surface.get_data()
        self.assertEqual(len(data), surface.get_stride() * layout.total_height)

    def test_draw_plugin_chips_and_messages_separately(self):
        surfaces = [_surface(state="critical")]
        messages = [_message("t1", "T", "x")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces, messages)
        surface, context = _blank_surface(layout.width, layout.total_height)
        draw_plugin_chips(context, layout)
        draw_message_threads(context, layout)
        # Both bands present: chip color plus bubble color.
        chip = layout.chip_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        cr, cg, cb, ca = _pixel_rgba(
            data, stride, chip.rect.x + chip.rect.width // 2,
            chip.rect.y + chip.rect.height // 2,
        )
        expected = (0, 0, 0, 255)
        self.assertAlmostEqual(cr, expected[0], delta=6)

    def test_unknown_plugin_state_uses_default_color(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, [_surface(state="weird-state")])
        surface, _ = self._render(layout)
        chip = layout.chip_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        r, g, b, _ = _pixel_rgba(
            data, stride, chip.rect.x + chip.rect.width // 2,
            chip.rect.y + chip.rect.height // 2,
        )
        expected = (0, 0, 0, 255)
        self.assertAlmostEqual(r, expected[0], delta=6)
        self.assertAlmostEqual(g, expected[1], delta=6)
        self.assertAlmostEqual(b, expected[2], delta=6)

    def test_light_theme_uses_white_background_and_configured_opacity(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), [_message()],
                                theme_mode="light", message_opacity=.5)
        surface = cairo.ImageSurface(cairo.Format.ARGB32, layout.width, layout.total_height)
        context = cairo.Context(surface)
        context.set_source_rgb(0, 0, 0)
        context.paint()
        draw_overlay(context, layout)
        data = surface.get_data()
        rect = layout.message_rects[0].rect
        rgb = _pixel_rgba(data, surface.get_stride(), rect.x + rect.width // 2,
                          rect.y + rect.height - 3)[:3]
        self.assertTrue(all(110 <= channel <= 145 for channel in rgb), rgb)


if __name__ == "__main__":
    unittest.main()
