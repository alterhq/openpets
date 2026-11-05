"""Unit tests for openpets_linux.overlay.

Headless: layouts are pure geometry and rendering targets in-memory Cairo
image surfaces. No display server, Gtk widgets, or plugin provider access is
required anywhere.

Layout contract (work item C):

* Plugin chips form a single vertical stack to the pet's left or right
  (``plugin_side``, default ``"right"``), centered vertically against the pet
  regardless of plugin count. The leading colored status dot is removed and
  chip text is balanced/centered.
* Message threads appear above the pet, with a small circular expand/collapse
  toggle above the pet at its upper-right, same size collapsed/expanded.
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
    CHIP_HEIGHT,
    CHIP_PAD_X,
    CHIP_PET_GAP,
    CHIP_STACK_GAP,
    CHIP_TOP_GAP,
    MESSAGE_TOP_GAP,
    DEFAULT_STATE_COLOR,
    MESSAGE_BOTTOM_GAP,
    MESSAGE_BUBBLE_GAP,
    MESSAGE_LINE_GAP,
    MESSAGE_MAX_WIDTH,
    MESSAGE_PAD_X,
    MESSAGE_PAD_Y,
    MESSAGE_TEXT_SIZE,
    MESSAGE_TITLE_SIZE,
    MESSAGE_TOGGLE_SIZE,
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


def _preview(thread_id="p1", title="Preview", status="waiting"):
    return {
        "title": title,
        "text": None,
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
        self.assertIsNotNone(layout.message_toggle_rect)
        self.assertIsNotNone(layout.message_band_rect)
        self.assertEqual(
            hit_test(layout, layout.message_toggle_rect.x + 2,
                     layout.message_toggle_rect.y + 2).kind,
            "message-collapse",
        )
        # The always-visible control reserves its own row above the pet.
        self.assertGreater(layout.pet_top, MESSAGE_TOP_GAP)
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

    def test_empty_input_rectangles_include_toggle_and_pet(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        rects = input_rectangles(layout)
        self.assertEqual(len(rects), 2)
        self.assertIn(layout.pet_rect, rects)
        self.assertIn(layout.message_toggle_rect, rects)
        self.assertTrue(all(isinstance(rect, cairo.RectangleInt) for rect in rects))

    def test_hidden_toggle_is_removed_from_hit_target_and_input_region(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        rect = layout.message_toggle_rect
        x, y = rect.x + rect.width // 2, rect.y + rect.height // 2
        self.assertEqual(hit_test(layout, x, y).kind, "message-collapse")
        self.assertIsNone(hit_test(layout, x, y, include_toggle=False))
        self.assertNotIn(rect, input_rectangles(layout, include_toggle=False))

    def test_layout_is_deterministic(self):
        a = compute_layout(WIDTH, CELL_W, CELL_H, (), ())
        b = compute_layout(WIDTH, CELL_W, CELL_H, (), ())
        self.assertEqual(a, b)


class TestComputeLayoutChips(unittest.TestCase):
    def test_chips_form_single_vertical_stack_to_right_of_pet(self):
        surfaces = [_surface(f"s{i}") for i in range(3)]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces)
        self.assertEqual(len(layout.chip_rects), 3)
        pet = layout.pet_rect
        # Stack vertically centered against the pet. pet_top may rise by half
        # the stack-height excess when the stack is taller than the pet cell.
        stack_height = 3 * CHIP_HEIGHT + 2 * CHIP_STACK_GAP
        excess_over_cell = max(0, stack_height - CELL_H) / 2.0
        self.assertEqual(layout.pet_top, MESSAGE_TOP_GAP + MESSAGE_TOGGLE_SIZE + PET_BOTTOM_GAP + excess_over_cell)
        pet_center_y = pet.y + pet.height / 2.0
        stack_center_y = (
            layout.chip_rects[0].rect.y + stack_height / 2.0
        )
        self.assertAlmostEqual(stack_center_y, pet_center_y, delta=2)
        # Stack sits to the right of the pet.
        for chip in layout.chip_rects:
            r = chip.rect
            self.assertEqual(r.height, CHIP_HEIGHT)
            self.assertGreaterEqual(r.x, pet.x + pet.width)
            self.assertLessEqual(r.x + r.width, layout.width)
            self.assertEqual(chip.surface, surfaces[chip.index])
        # Chip band covers the whole stack.
        band = layout.chip_band_rect
        self.assertIsNotNone(band)
        self.assertEqual(band.x, layout.chip_rects[0].rect.x)
        self.assertEqual(band.y, layout.chip_rects[0].rect.y)
        self.assertEqual(
            band.y + band.height,
            layout.chip_rects[-1].rect.y + CHIP_HEIGHT,
        )

    def test_chip_stack_is_vertically_centered_on_pet(self):
        # One chip: its center must equal the pet's center.
        layout = compute_layout(WIDTH, CELL_W, CELL_H, [_surface()])
        pet = layout.pet_rect
        chip = layout.chip_rects[0]
        pet_center_y = pet.y + pet.height / 2.0
        chip_center_y = chip.rect.y + chip.rect.height / 2.0
        self.assertAlmostEqual(chip_center_y, pet_center_y, delta=1)
        # To the right of the pet.
        self.assertEqual(chip.rect.x, pet.x + pet.width + CHIP_PET_GAP)

    def test_plugin_side_left_puts_stack_on_left(self):
        surfaces = [_surface("s0"), _surface("s1")]
        right = compute_layout(WIDTH, CELL_W, CELL_H, surfaces, plugin_side="right")
        left = compute_layout(WIDTH, CELL_W, CELL_H, surfaces, plugin_side="left")
        self.assertEqual(left.chip_rects[0].rect.x + left.chip_rects[0].rect.width,
                         right.pet_rect.x - CHIP_PET_GAP)
        self.assertEqual(right.chip_rects[0].rect.x,
                         right.pet_rect.x + right.pet_rect.width + CHIP_PET_GAP)
        # Invalid side defaults to right.
        default = compute_layout(WIDTH, CELL_W, CELL_H, surfaces, plugin_side="bogus")
        self.assertEqual(default.chip_rects[0].rect.x, right.chip_rects[0].rect.x)

    def test_chip_stack_taller_than_pet_stays_centered_and_in_window(self):
        # 5 chips (stack 174) vs short pet cell (40): stack is taller than the
        # pet, so pet moves down by half the excess; stack stays centered and
        # its top edge never goes negative.
        short_cell_h = 40
        surfaces = [_surface(f"s{i}") for i in range(5)]
        layout = compute_layout(WIDTH, CELL_W, short_cell_h, surfaces)
        self.assertEqual(len(layout.chip_rects), 5)
        stack_height = 5 * CHIP_HEIGHT + 4 * CHIP_STACK_GAP  # 174
        half_excess = (stack_height - short_cell_h) / 2.0  # 67
        # Stack top is clamped up to the window top edge (>= 0), never negative.
        self.assertGreaterEqual(layout.chip_rects[0].rect.y, 0)
        self.assertAlmostEqual(layout.chip_rects[0].rect.y, CHIP_TOP_GAP + MESSAGE_TOGGLE_SIZE + PET_BOTTOM_GAP, delta=1)
        # Stack center equals pet center.
        pet_center_y = layout.pet_rect.y + layout.pet_rect.height / 2.0
        stack_top = layout.chip_rects[0].rect.y
        stack_center_y = stack_top + stack_height / 2.0
        self.assertAlmostEqual(stack_center_y, pet_center_y, delta=1)
        # All chips within window and non-overlapping.
        for chip in layout.chip_rects:
            r = chip.rect
            self.assertGreaterEqual(r.y, 0)
            self.assertLessEqual(r.y + r.height, layout.total_height)
            self.assertGreaterEqual(r.x, 0)
            self.assertLessEqual(r.x + r.width, layout.width)
        for i in range(len(layout.chip_rects) - 1):
            a, b = layout.chip_rects[i], layout.chip_rects[i + 1]
            self.assertLess(a.rect.y + a.rect.height, b.rect.y)
        # Window is tall enough for the stack.
        self.assertGreaterEqual(layout.total_height, stack_top + stack_height)

    def test_chip_rects_fit_window_and_do_not_overlap(self):
        surfaces = [_surface(f"s{i}") for i in range(4)]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces)
        for chip in layout.chip_rects:
            r = chip.rect
            self.assertGreaterEqual(r.x, 0)
            self.assertLessEqual(r.x + r.width, layout.width)
            self.assertGreaterEqual(r.y, CHIP_TOP_GAP)
            self.assertLessEqual(r.y + r.height, layout.total_height)
        for i in range(len(layout.chip_rects) - 1):
            a, b = layout.chip_rects[i], layout.chip_rects[i + 1]
            self.assertLess(a.rect.y + a.rect.height, b.rect.y)

    def test_no_surfaces_means_no_chip_band(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        self.assertIsNone(layout.chip_band_rect)


class TestComputeLayoutMessages(unittest.TestCase):
    def test_message_threads_above_pet(self):
        messages = [_message("t1", "First"), _message("t2", "Second", "with detail")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), messages)
        self.assertEqual(len(layout.message_rects), 2)
        # Message band starts at the window top edge.
        self.assertEqual(layout.message_band_top, MESSAGE_TOP_GAP)
        # Pet sits below the message band.
        self.assertEqual(
            layout.pet_top, layout.message_rects[-1].rect.y + layout.message_rects[-1].rect.height + PET_BOTTOM_GAP
        )
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
        # Stacked with a gap.
        a, b = layout.message_rects
        self.assertEqual(b.rect.y, a.rect.y + a.rect.height + MESSAGE_BUBBLE_GAP)

    def test_toggle_is_above_pet_at_upper_right(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), [_message()])
        rect = layout.message_toggle_rect
        self.assertIsNotNone(rect)
        # Square circular toggle, same size constant.
        self.assertEqual(rect.width, MESSAGE_TOGGLE_SIZE)
        self.assertEqual(rect.height, MESSAGE_TOGGLE_SIZE)
        # Anchored to the pet's upper-right edge, not the far edge of the window.
        self.assertEqual(rect.y, MESSAGE_TOP_GAP)
        self.assertEqual(rect.x, layout.pet_rect.x + CELL_W - MESSAGE_TOGGLE_SIZE)
        self.assertLess(rect.y + rect.height, layout.pet_top)

    def test_toggle_never_overlaps_message_bubbles_at_any_configured_width(self):
        for window_width in (360, 500, 760):
            for bubble_width in (120, 240, 600):
                with self.subTest(window=window_width, bubble=bubble_width):
                    layout = compute_layout(
                        window_width, CELL_W, CELL_H, (), [_message("t1")],
                        message_max_width=bubble_width,
                    )
                    toggle = layout.message_toggle_rect
                    bubble = layout.message_rects[0].rect
                    self.assertGreaterEqual(
                        bubble.y,
                        toggle.y + toggle.height + MESSAGE_BUBBLE_GAP,
                    )
                    self.assertEqual(
                        toggle.x,
                        layout.pet_rect.x + CELL_W - MESSAGE_TOGGLE_SIZE,
                    )

    def test_bubble_width_scales_with_window(self):
        wide = compute_layout(900, CELL_W, CELL_H, (), [_message()])
        self.assertEqual(wide.message_rects[0].rect.width, MESSAGE_MAX_WIDTH)
        narrow = compute_layout(100, CELL_W, CELL_H, (), [_message()])
        self.assertEqual(narrow.message_rects[0].rect.width, 100 - 2 * MESSAGE_PAD_X)

    def test_no_messages_still_has_toggle_band(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H)
        self.assertEqual(layout.message_band_height, MESSAGE_TOGGLE_SIZE + MESSAGE_BOTTOM_GAP)
        self.assertIsNotNone(layout.message_band_rect)
        self.assertIsNotNone(layout.message_toggle_rect)
        self.assertEqual(layout.pet_top, MESSAGE_TOP_GAP + MESSAGE_TOGGLE_SIZE + PET_BOTTOM_GAP)

    def test_collapsed_messages_use_large_toggle_without_empty_summary(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                [_message("a"), _message("b")],
                                messages_collapsed=True, unread_count=2)
        self.assertEqual(layout.message_rects, ())
        self.assertIsNone(layout.collapsed_bubble_rect)
        self.assertIsNotNone(layout.message_toggle_rect)
        self.assertEqual(layout.unread_count, 2)
        self.assertTrue(layout.messages_collapsed)
        rects = input_rectangles(layout)
        tr = layout.message_toggle_rect
        self.assertIsNotNone(tr)
        self.assertIn(tr, rects)
        self.assertEqual(hit_test(layout, tr.x + tr.width // 2, tr.y + tr.height // 2).kind,
                         "message-expand")
        # The enlarged target remains a circle and is easy to hit throughout.
        for x, y in ((tr.x + 2, tr.y + 2), (tr.x + tr.width - 3, tr.y + tr.height - 3)):
            self.assertEqual(hit_test(layout, x, y).kind, "message-expand")
        # Toggle same size as expanded state.
        expanded = compute_layout(WIDTH, CELL_W, CELL_H, (), [_message()])
        self.assertEqual(layout.message_toggle_rect.width, expanded.message_toggle_rect.width)
        self.assertEqual(layout.message_toggle_rect.height, expanded.message_toggle_rect.height)
        self.assertGreaterEqual(tr.width, 40)

    def test_collapsed_previews_shown_in_place_of_static_summary(self):
        previews = [_preview("p1", "Older"), _preview("p2", "Newer", "running")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                [], preview_messages=previews,
                                messages_collapsed=True)
        # Static summary replaced by preview cards.
        self.assertIsNone(layout.collapsed_bubble_rect)
        self.assertEqual(len(layout.preview_rects), 2)
        self.assertEqual(layout.message_rects, ())
        # Toggle retained as sole control, same fixed size.
        self.assertIsNotNone(layout.message_toggle_rect)
        tr = layout.message_toggle_rect
        self.assertEqual(tr.width, MESSAGE_TOGGLE_SIZE)
        self.assertEqual(tr.height, MESSAGE_TOGGLE_SIZE)
        # Stacked old-at-top / newest-at-bottom above the pet.
        self.assertLess(layout.preview_rects[0].rect.y, layout.preview_rects[1].rect.y)
        self.assertEqual(layout.preview_rects[0].thread_id, "p1")
        self.assertEqual(layout.preview_rects[1].thread_id, "p2")
        # Pet sits below the preview band.
        self.assertEqual(layout.pet_top,
                         layout.preview_rects[-1].rect.y + layout.preview_rects[-1].rect.height + PET_BOTTOM_GAP)
        # Toggle is sole control; previews are not control targets.
        self.assertEqual(hit_test(layout, tr.x + tr.width // 2, tr.y + tr.height // 2).kind,
                         "message-expand")
        self.assertGreaterEqual(
            layout.preview_rects[0].rect.y,
            tr.y + tr.height + MESSAGE_BUBBLE_GAP,
        )

    def test_collapsed_without_previews_shows_only_toggle(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                [], messages_collapsed=True)
        self.assertIsNone(layout.collapsed_bubble_rect)
        self.assertEqual(layout.preview_rects, ())
        self.assertIsNotNone(layout.message_toggle_rect)
        self.assertTrue(layout.messages_collapsed)

    def test_expanded_state_ignores_previews(self):
        previews = [_preview("p1", "Older")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                [_message("t1", "Expanded")],
                                preview_messages=previews)
        self.assertEqual(len(layout.message_rects), 1)
        self.assertEqual(layout.preview_rects, ())
        self.assertIsNone(layout.collapsed_bubble_rect)

    def test_preview_hit_returns_message(self):
        previews = [_preview("p1", "Older")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                [], preview_messages=previews,
                                messages_collapsed=True)
        pr = layout.preview_rects[0]
        hit = hit_test(layout, pr.rect.x + pr.rect.width // 2, pr.rect.y + pr.rect.height // 2)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, "message")
        self.assertEqual(hit.thread_id, "p1")

    def test_expanded_messages_have_collapse_control(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), [_message()])
        self.assertIsNone(layout.collapsed_bubble_rect)
        rect = layout.message_toggle_rect
        self.assertIsNotNone(rect)
        self.assertEqual(hit_test(layout, rect.x + 2, rect.y + 2).kind, "message-collapse")


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

    def test_hit_toggle_returns_control(self):
        rect = self.layout.message_toggle_rect
        self.assertIsNotNone(rect)
        hit = hit_test(self.layout, rect.x + rect.width // 2, rect.y + rect.height // 2)
        self.assertIsNotNone(hit)
        self.assertIn(hit.kind, ("message-expand", "message-collapse"))

    def test_gaps_miss_everything(self):
        # Above the message band / window top edge.
        self.assertIsNone(hit_test(self.layout, WIDTH // 2, 3))
        # Between message band and pet.
        gap_y = self.layout.message_rects[-1].rect.y + self.layout.message_rects[-1].rect.height + 2
        self.assertIsNone(hit_test(self.layout, WIDTH // 2, gap_y))
        # Below the window.
        self.assertIsNone(hit_test(self.layout, WIDTH // 2, self.layout.total_height + 5))
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
        # Pet + individual chips + messages; transparent gaps click through.
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

    def test_collapsed_unread_dot_is_integrated_on_toggle(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), [],
                                messages_collapsed=True, unread_count=1)
        surface, _ = self._render(layout)
        toggle = layout.message_toggle_rect
        self.assertIsNotNone(toggle)
        # Badge is a small red dot at the upper-right of the button itself,
        # not a separate summary bubble or hit target.
        self.assertIsNone(layout.collapsed_bubble_rect)
        data, stride = surface.get_data(), surface.get_stride()
        x, y = toggle.x + toggle.width - 4, toggle.y + 4
        r, g, b, _ = _pixel_rgba(data, stride, x, y)
        self.assertGreater(r, 170)
        self.assertLess(g, 100)
        self.assertLess(b, 100)

    def test_collapsed_toggle_without_unread_has_no_red_dot(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (), [],
                                messages_collapsed=True, unread_count=0)
        surface, _ = self._render(layout)
        toggle = layout.message_toggle_rect
        data, stride = surface.get_data(), surface.get_stride()
        x, y = toggle.x + toggle.width - 4, toggle.y + 4
        r, g, b, _ = _pixel_rgba(data, stride, x, y)
        self.assertFalse(r > 170 and g < 100 and b < 100)

    def test_chip_render_without_status_dot(self):
        # The leading colored status dot must be gone: where it used to be
        # drawn (a 3px arc at pad_x/2) there must now be chip background only.
        layout = compute_layout(WIDTH, CELL_W, CELL_H,
                                [_surface(state="success", label="Battery", value="80%")])
        surface, _ = self._render(layout)
        chip = layout.chip_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        cy = chip.rect.y + chip.rect.height // 2
        dot_x = chip.rect.x + CHIP_PAD_X // 2
        dr, dg, db, _ = _pixel_rgba(data, stride, dot_x, cy)
        # Dark theme chip background is black; must not be the success green dot.
        self.assertLess(dr + dg + db, 60,
                        "leading status dot was not removed from chip")
        # Chip fill covers its interior (sample below the vertically centered
        # text so we do not hit a text glyph).
        fill_x = chip.rect.x + chip.rect.width // 2
        fill_y = chip.rect.y + chip.rect.height - 6
        cr, cg, cb, ca = _pixel_rgba(data, stride, fill_x, fill_y)
        self.assertAlmostEqual(cr, 0, delta=6)
        self.assertAlmostEqual(cg, 0, delta=6)
        self.assertAlmostEqual(cb, 0, delta=6)
        self.assertAlmostEqual(ca, 255, delta=4)

    def test_chip_text_is_horizontally_centered(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H,
                                [_surface(state="warning", label="A", value="1")])
        surface, context = _blank_surface(layout.width, layout.total_height)
        draw_plugin_chips(context, layout)
        chip = layout.chip_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        cy = chip.rect.y + chip.rect.height // 2
        ink_xs = [xx for xx in range(chip.rect.x, chip.rect.x + chip.rect.width)
                  if _pixel_rgba(data, stride, xx, cy)[0] > 150]
        self.assertTrue(ink_xs, "no text ink found inside chip")
        text_center = (min(ink_xs) + max(ink_xs)) / 2.0
        chip_center = chip.rect.x + chip.rect.width / 2.0
        self.assertAlmostEqual(
            text_center, chip_center, delta=8,
            msg=f"chip text center {text_center} not near chip center {chip_center}",
        )

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

    def test_text_renders_inside_bubble(self):
        layout = compute_layout(
            WIDTH, CELL_W, CELL_H, (),
            [_message("t1", "A somewhat long title", "and some detail text")]
        )
        surface, _ = self._render(layout)
        msg = layout.message_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
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

    def test_unknown_plugin_state_uses_default_color(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, [_surface(state="weird-state")])
        surface, _ = self._render(layout)
        chip = layout.chip_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        # Sample below the vertically centered text so we hit chip fill, not a glyph.
        fill_x = chip.rect.x + chip.rect.width // 2
        fill_y = chip.rect.y + chip.rect.height - 6
        r, g, b, _ = _pixel_rgba(data, stride, fill_x, fill_y)
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

    def test_status_marks_are_drawn_beyond_color_alone(self):
        # Each known status renders a visible status mark (status color, not
        # the dark bubble background) somewhere beside the title. Marks are
        # thin strokes, so scan a small region around the mark anchor.
        mark_x = MESSAGE_PAD_X + 6
        mark_y = MESSAGE_PAD_Y + 8
        expected = {
            "running": (97, 184, 232),    # .38,.72,.91 ring
            "completed": (107, 204, 150), # .42,.80,.59 checkmark
            "done": (107, 204, 150),      # production synonym for completed
            "failed": (242, 117, 115),    # .95,.46,.45 cross
            "waiting": (233, 187, 92),    # .91,.73,.36 filled dot
            "review": (181, 161, 240),    # .71,.63,.94 partial arc
        }
        for status, (er, eg, eb) in expected.items():
            with self.subTest(status=status):
                layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                        [_message("t1", "Title", status=status)])
                surface, _ = self._render(layout)
                msg = layout.message_rects[0]
                data = surface.get_data()
                stride = surface.get_stride()
                found = False
                for yy in range(msg.rect.y + mark_y - 5, msg.rect.y + mark_y + 6, 2):
                    for xx in range(msg.rect.x + mark_x - 5, msg.rect.x + mark_x + 6, 2):
                        r, g, b, _ = _pixel_rgba(data, stride, xx, yy)
                        if (abs(r - er) <= 40 and abs(g - eg) <= 40 and abs(b - eb) <= 40):
                            found = True
                            break
                    if found:
                        break
                self.assertTrue(found, f"no {status} status mark drawn")

    def test_preview_cards_draw_status_marks(self):
        layout = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                [], preview_messages=[_preview("p1", "P", "completed")],
                                messages_collapsed=True)
        surface, _ = self._render(layout)
        preview = layout.preview_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        # Completed checkmark mark in status color over dark bg (scan region).
        found = False
        for yy in range(preview.rect.y + MESSAGE_PAD_Y + 3,
                        preview.rect.y + MESSAGE_PAD_Y + 13, 2):
            for xx in range(preview.rect.x + MESSAGE_PAD_X + 1,
                            preview.rect.x + MESSAGE_PAD_X + 11, 2):
                r, g, b, _ = _pixel_rgba(data, stride, xx, yy)
                if abs(r - 107) <= 40 and abs(g - 204) <= 40 and abs(b - 150) <= 40:
                    found = True
                    break
            if found:
                break
        self.assertTrue(found, "no completed status mark drawn on preview card")

    def test_message_width_and_font_size_are_configurable(self):
        message = _message("thread", "A title", "Message body")
        default = compute_layout(WIDTH, CELL_W, CELL_H, (), [message])
        adjusted = compute_layout(500, CELL_W, CELL_H, (), [message],
                                  message_max_width=420, message_font_size=16)
        self.assertEqual(default.message_rects[0].rect.width, MESSAGE_MAX_WIDTH)
        self.assertEqual(adjusted.message_rects[0].rect.width, 420)
        self.assertEqual(adjusted.message_font_size, 16)
        self.assertGreater(adjusted.message_rects[0].rect.height,
                           default.message_rects[0].rect.height)

    def test_message_bubble_uses_balanced_top_and_bottom_padding(self):
        title_only = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                    [_message("title", text=None)])
        title_and_body = compute_layout(WIDTH, CELL_W, CELL_H, (),
                                        [_message("body", text="Body")])
        expected_title = 2 * MESSAGE_PAD_Y + TITLE_LINE_HEIGHT
        expected_body = (expected_title + MESSAGE_LINE_GAP + TEXT_LINE_HEIGHT)
        self.assertEqual(title_only.message_rects[0].rect.height, expected_title)
        self.assertEqual(title_and_body.message_rects[0].rect.height, expected_body)

    def test_draw_plugin_chips_and_messages_separately(self):
        surfaces = [_surface(state="critical")]
        messages = [_message("t1", "T", "x")]
        layout = compute_layout(WIDTH, CELL_W, CELL_H, surfaces, messages)
        surface, context = _blank_surface(layout.width, layout.total_height)
        draw_plugin_chips(context, layout)
        draw_message_threads(context, layout)
        # Both bands present: chip fill plus bubble fill.
        chip = layout.chip_rects[0]
        data = surface.get_data()
        stride = surface.get_stride()
        fill_x = chip.rect.x + chip.rect.width // 2
        fill_y = chip.rect.y + chip.rect.height - 6
        cr, cg, cb, _ = _pixel_rgba(data, stride, fill_x, fill_y)
        self.assertAlmostEqual(cr, 0, delta=6)
        self.assertAlmostEqual(cg, 0, delta=6)
        self.assertAlmostEqual(cb, 0, delta=6)
        msg = layout.message_rects[0]
        cx, cy = msg.rect.x + msg.rect.width // 2, msg.rect.y + msg.rect.height // 2
        mr, mg, mb, _ = _pixel_rgba(data, stride, cx, cy)
        self.assertAlmostEqual(mr, 0, delta=6)
        self.assertAlmostEqual(mg, 0, delta=6)
        self.assertAlmostEqual(mb, 0, delta=6)


if __name__ == "__main__":
    unittest.main()
