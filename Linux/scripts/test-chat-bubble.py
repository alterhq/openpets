#!/usr/bin/env python3
"""Standalone visual tuning window for the OpenPets comic chat bubble.

Run from the checkout with:
    /usr/bin/python3 Linux/scripts/test-chat-bubble.py

This is deliberately not connected to the app, IPC, settings, or MCP. It uses
the app's layout and text flow, with a test-only rounded speech bubble and a
placeholder pet anchor.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_foreign("cairo")
from gi.repository import Gtk  # noqa: E402


LINUX_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LINUX_ROOT))

from openpets_linux import chat_bubble, overlay  # noqa: E402


CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 520
CELL_WIDTH = 200
CELL_HEIGHT = 200
# Layout leaves room for a bubble at the side of the pet. The actual bubble
# width is content- and aspect-ratio-driven, up to this available canvas space.
TEST_MAX_WIDTH = 430
TEST_PAD_X = 12
TEST_PAD_Y = 12


class BubbleTester:
    def __init__(self, initial_text: str):
        self.window = Gtk.Window(title="OpenPets — Comic Chat Bubble Test")
        self.window.set_default_size(1120, 820)
        self.window.connect("destroy", Gtk.main_quit)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_border_width(12)
        self.window.add(root)

        title = Gtk.Label()
        title.set_markup(
            "<b>Comic chat bubble — cửa sổ thử độc lập</b>\n"
            "Khung bo tròn, đuôi thoại hướng vào nhân vật; không gửi notification "
            "và không sửa cấu hình."
        )
        title.set_xalign(0)
        root.pack_start(title, False, False, 0)

        text_label = Gtk.Label(label="Nội dung tin nhắn")
        text_label.set_xalign(0)
        root.pack_start(text_label, False, False, 0)

        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.get_buffer().set_text(initial_text)
        self.text_view.get_buffer().connect("changed", self._changed)
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        text_scroll.set_min_content_height(76)
        text_scroll.add(self.text_view)
        root.pack_start(text_scroll, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        root.pack_start(controls, False, False, 0)
        self.font_scale = self._add_scale(
            controls, "Cỡ chữ", 8.0, 28.0, overlay.CHAT_BUBBLE_TEXT_SIZE, 0.5
        )
        self.ratio_scale = self._add_scale(
            controls, "Tỷ lệ rộng:cao", 1.0, 3.5, 1.8, 0.1
        )
        self.radius_scale = self._add_scale(
            controls, "Corner radius (% mỗi chiều)", 0, 50, 22, 1
        )
        padding_note = Gtk.Label(label="Đệm ngang / dọc cố định: 12 px")
        padding_note.set_xalign(0)
        root.pack_start(padding_note, False, False, 0)

        self.info = Gtk.Label()
        self.info.set_xalign(0)
        root.pack_start(self.info, False, False, 0)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(CANVAS_WIDTH, CANVAS_HEIGHT)
        self.canvas.connect("draw", self._draw)
        canvas_scroll = Gtk.ScrolledWindow()
        canvas_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        canvas_scroll.add(self.canvas)
        root.pack_start(canvas_scroll, True, True, 0)

        for scale in (
            self.font_scale,
            self.ratio_scale,
            self.radius_scale,
        ):
            scale.connect("value-changed", self._settings_changed)
        self._apply_tuning()

    @staticmethod
    def _add_scale(parent, label, minimum, maximum, value, step):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        parent.pack_start(column, True, True, 0)
        caption = Gtk.Label(label=label)
        caption.set_xalign(0)
        column.pack_start(caption, False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, step)
        scale.set_digits(0 if step >= 1 else 1)
        scale.set_value(value)
        scale.set_hexpand(True)
        column.pack_start(scale, False, False, 0)
        return scale

    def _text(self):
        buffer = self.text_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def _changed(self, _buffer):
        self._refresh()

    def _settings_changed(self, _scale):
        self._apply_tuning()
        self._refresh()

    def _apply_tuning(self):
        overlay.CHAT_BUBBLE_TEXT_SIZE = self.font_scale.get_value()
        overlay.CHAT_BUBBLE_MAX_WIDTH = TEST_MAX_WIDTH
        overlay.CHAT_BUBBLE_PAD_X = TEST_PAD_X
        overlay.CHAT_BUBBLE_PAD_Y = TEST_PAD_Y
        chat_bubble.CHAT_BUBBLE_PAD_X = overlay.CHAT_BUBBLE_PAD_X
        chat_bubble.CHAT_BUBBLE_PAD_Y = overlay.CHAT_BUBBLE_PAD_Y
        chat_bubble.CHAT_BUBBLE_MAX_WIDTH = TEST_MAX_WIDTH
        # The test uses Pango's Unicode shaping/wrapping metrics instead of the
        # production heuristic, so line count and height track actual UTF-8 text.
        overlay.estimate_bubble_size = self._estimate_bubble_size

    def _make_measure_layout(self, text, width):
        surface = overlay.cairo.ImageSurface(overlay.cairo.FORMAT_ARGB32, 1, 1)
        context = overlay.cairo.Context(surface)
        layout = overlay.PangoCairo.create_layout(context)
        layout.set_font_description(
            overlay.Pango.FontDescription(f"Sans {self.font_scale.get_value()}")
        )
        layout.set_text(text, -1)
        layout.set_single_paragraph_mode(False)
        layout.set_wrap(overlay.Pango.WrapMode.WORD_CHAR)
        layout.set_ellipsize(overlay.Pango.EllipsizeMode.NONE)
        layout.set_width(width * overlay.Pango.SCALE)
        return layout

    def _estimate_bubble_size(self, text, max_width=TEST_MAX_WIDTH):
        text = text if isinstance(text, str) else ""
        max_width = min(TEST_MAX_WIDTH, max(1, int(max_width)))
        pad_x = TEST_PAD_X
        pad_y = TEST_PAD_Y
        minimum_width = min(max_width, max(96, 2 * pad_x + 1))
        ratio = self.ratio_scale.get_value()

        def measure(width):
            height = width / ratio
            lines, line_height = self._wrap_text_to_shape(text, width, height)
            return 2 * pad_y + len(lines) * line_height, len(lines)

        # Find the narrowest width whose ratio-derived height can contain the
        # Pango-measured Unicode text. Wrapping makes required height monotonic
        # as width grows, so a binary search avoids arbitrary character counts.
        low, high = minimum_width, max_width
        high_height, high_lines = measure(high)
        if high / ratio < high_height:
            return int(high), int(high_height), int(high_lines)
        for _ in range(18):
            middle = (low + high) / 2
            required_height, _ = measure(middle)
            if middle / ratio >= required_height:
                high = middle
            else:
                low = middle
        width = int(math.ceil(high))
        # Integer geometry can cross a wrap boundary after rounding. Recheck
        # and advance to the next fitting width instead of silently stretching
        # the height away from the chosen ratio.
        while width < max_width:
            content_height, lines = measure(width)
            if content_height <= width / ratio:
                break
            width += 1
        content_height, lines = measure(width)
        if content_height > width / ratio:
            return width, int(content_height), int(lines)
        return width, int(round(width / ratio)), int(lines)

    def _measure_text_width(self, text):
        layout = self._make_measure_layout(text, 10000)
        return layout.get_pixel_size()[0]

    def _shape_text_width(self, width, height, top, bottom):
        """Available inner width across a line band inside the rounded shape."""
        pct = self.radius_scale.get_value() / 100
        rx = min(width * pct, width / 2)
        ry = min(height * pct, height / 2)
        pad_x = TEST_PAD_X

        def inset(y):
            if rx <= 0 or ry <= 0:
                return 0
            if y < ry:
                normalized = (ry - y) / ry
            elif y > height - ry:
                normalized = (y - (height - ry)) / ry
            else:
                return 0
            normalized = min(1, max(0, normalized))
            return rx * (1 - (max(0, 1 - normalized * normalized) ** 0.5))

        inset_x = max(inset(top), inset(bottom))
        return max(1, int(width - 2 * pad_x - 2 * inset_x))

    def _wrap_text_to_shape(self, text, width, height):
        """Greedily wrap Unicode words into centered lines following the shape."""
        pad_y = TEST_PAD_Y
        _, line_height = self._make_measure_layout("Mg", 1000).get_pixel_size()
        line_height = max(1, line_height)

        initial = self._make_measure_layout(
            text, max(1, int(width) - 2 * TEST_PAD_X)
        )
        guess = max(1, initial.get_line_count())
        result = [""]
        for _ in range(16):
            start_y = max(pad_y, (height - guess * line_height) / 2)
            result = self._wrap_once_to_shape(
                text, width, height, start_y, line_height
            )
            if len(result) <= guess:
                break
            guess = len(result)
        return result or [""], line_height

    def _wrap_once_to_shape(self, text, width, height, start_y, line_height):
        lines = []
        for paragraph in text.split("\n"):
            remaining = paragraph.split()
            if not remaining:
                lines.append("")
                continue
            while remaining:
                line_top = start_y + len(lines) * line_height
                available = self._shape_text_width(
                    width, height, line_top, line_top + line_height
                )
                current = ""
                while remaining:
                    candidate = remaining[0] if not current else current + " " + remaining[0]
                    if self._measure_text_width(candidate) > available:
                        break
                    current = candidate
                    remaining.pop(0)

                if not current:
                    word = remaining.pop(0)
                    take = 0
                    fragment = ""
                    for char in word:
                        candidate = fragment + char
                        if self._measure_text_width(candidate) > available and fragment:
                            break
                        fragment = candidate
                        take += 1
                        if self._measure_text_width(fragment) > available:
                            break
                    current = fragment or word[:1]
                    rest = word[take:]
                    if rest:
                        remaining.insert(0, rest)
                lines.append(current)
        return lines or [""]

    def _refresh(self):
        message = {
            "threadId": "visual-test",
            "kind": "chat",
            "status": "message",
            "text": self._text(),
        }
        self.layout = overlay.compute_layout(
            CANVAS_WIDTH,
            CELL_WIDTH,
            CELL_HEIGHT,
            plugin_side="left",
            chat_message=message,
        )
        self.canvas.set_size_request(
            CANVAS_WIDTH, max(CANVAS_HEIGHT, self.layout.total_height + 32)
        )
        bubble = self.layout.chat_bubble
        if bubble is None:
            self.info.set_text("Nhập nội dung để xem bong bóng.")
        else:
            rect = bubble.rect
            radius_percent = self.radius_scale.get_value()
            radius_x = rect.width * radius_percent / 100
            radius_y = rect.height * radius_percent / 100
            self.info.set_text(
                f"Kích thước thực tế: {rect.width} × {rect.height} px  ·  "
                f"Pango đo {bubble.lines} dòng UTF-8  ·  "
                f"tỷ lệ thực {rect.width / max(1, rect.height):.2f}:1 "
                f"(đang chọn {self.ratio_scale.get_value():.1f}:1)  ·  "
                f"góc {radius_percent:.0f}% = {radius_x:.0f} × {radius_y:.0f} px  ·  "
                "nền trắng / chữ đen"
            )
        self.canvas.queue_draw()

    def _draw(self, _widget, context):
        context.set_source_rgb(0.88, 0.89, 0.91)
        context.paint()

        # This alternate speech-balloon shape is intentionally test-only.
        self._draw_speech_bubble(context)

        # Simple placeholder pet to show where the bubble tail is pointing.
        pet = self.layout.pet_rect
        cx = pet.x + pet.width / 2
        head_y = pet.y + pet.height * 0.28
        context.set_source_rgb(0.55, 0.43, 0.78)
        context.arc(cx, head_y, pet.width * 0.23, 0, 6.283185307)
        context.fill()
        context.set_source_rgb(0.39, 0.30, 0.62)
        context.arc(cx, pet.y + pet.height * 0.72, pet.width * 0.34, 3.14159, 6.28318)
        context.line_to(cx + pet.width * 0.34, pet.y + pet.height * 0.95)
        context.line_to(cx - pet.width * 0.34, pet.y + pet.height * 0.95)
        context.close_path()
        context.fill()
        return False

    def _draw_speech_bubble(self, context):
        bubble = self.layout.chat_bubble
        if bubble is None:
            return
        rect = bubble.rect
        pad_x = TEST_PAD_X
        pad_y = TEST_PAD_Y
        radius_percent = self.radius_scale.get_value() / 100
        radius_x = rect.width * radius_percent
        radius_y = rect.height * radius_percent

        # A single tapered pointer aims from the bubble at the pet's face.
        pet = self.layout.pet_rect
        half_contact = min(8, rect.height * 0.12)
        contact_top_y = rect.height / 2 - half_contact
        contact_bottom_y = rect.height / 2 + half_contact
        if bubble.side == "right":
            contact_top_x = rect.x + self._side_inset(
                rect.height, radius_y, radius_x, contact_top_y
            )
            contact_bottom_x = rect.x + self._side_inset(
                rect.height, radius_y, radius_x, contact_bottom_y
            )
            tip_x = pet.x + pet.width * 0.76
            tip_y = pet.y + pet.height * 0.30
        else:
            contact_top_x = rect.x + rect.width - self._side_inset(
                rect.height, radius_y, radius_x, contact_top_y
            )
            contact_bottom_x = rect.x + rect.width - self._side_inset(
                rect.height, radius_y, radius_x, contact_bottom_y
            )
            tip_x = pet.x + pet.width * 0.24
            tip_y = pet.y + pet.height * 0.30

        context.set_source_rgb(1, 1, 1)
        context.move_to(contact_top_x, rect.y + contact_top_y)
        context.line_to(tip_x, tip_y)
        context.line_to(contact_bottom_x, rect.y + contact_bottom_y)
        context.close_path()
        context.fill()

        self._rounded_rect(context, rect, radius_x, radius_y)
        context.fill()

        # Comic ink outline. Erase the short border segment at the tail root,
        # then draw the two pointer edges into that opening for a continuous
        # speech-balloon silhouette instead of a detached triangle.
        context.set_source_rgb(0, 0, 0)
        context.set_line_width(2.5)
        self._rounded_rect(context, rect, radius_x, radius_y)
        context.stroke()
        # Erase precisely the contour segment between the two computed contact
        # points. Following the actual curved edge avoids a gap at high radius.
        context.set_source_rgb(1, 1, 1)
        context.set_line_width(4)
        context.set_line_cap(overlay.cairo.LINE_CAP_ROUND)
        context.move_to(contact_top_x, rect.y + contact_top_y)
        context.line_to(contact_bottom_x, rect.y + contact_bottom_y)
        context.stroke()
        context.set_source_rgb(0, 0, 0)
        context.set_line_width(2.5)
        context.set_line_cap(overlay.cairo.LINE_CAP_ROUND)
        context.move_to(tip_x, tip_y)
        context.line_to(contact_top_x, rect.y + contact_top_y)
        context.move_to(tip_x, tip_y)
        context.line_to(contact_bottom_x, rect.y + contact_bottom_y)
        context.stroke()

        text = bubble.message.get("text", "").strip()
        lines, line_height = self._wrap_text_to_shape(
            text, rect.width, rect.height
        )
        text_start_y = max(
            pad_y, (rect.height - len(lines) * line_height) / 2
        )
        context.save()
        self._rounded_rect(context, rect, radius_x, radius_y)
        context.clip()
        context.set_source_rgb(0, 0, 0)
        for index, line in enumerate(lines):
            line_layout = overlay._text_layout(
                context, line, overlay.CHAT_BUBBLE_TEXT_SIZE,
                max(1, rect.width - 2 * pad_x),
            )
            line_layout.set_single_paragraph_mode(True)
            line_layout.set_ellipsize(overlay.Pango.EllipsizeMode.NONE)
            text_width, _ = line_layout.get_pixel_size()
            line_x = rect.x + (rect.width - text_width) / 2
            line_y = rect.y + text_start_y + index * line_height
            context.move_to(line_x, line_y)
            overlay.PangoCairo.show_layout(context, line_layout)
        context.restore()

    @staticmethod
    def _side_inset(height, radius_y, radius_x, y):
        if radius_x <= 0 or radius_y <= 0:
            return 0
        if y < radius_y:
            normalized = (radius_y - y) / radius_y
        elif y > height - radius_y:
            normalized = (y - (height - radius_y)) / radius_y
        else:
            return 0
        normalized = min(1, max(0, normalized))
        return radius_x * (1 - math.sqrt(max(0, 1 - normalized * normalized)))

    @staticmethod
    def _rounded_rect(context, rect, radius_x, radius_y):
        """Append a rectangle whose horizontal/vertical radii scale separately."""
        x, y, width, height = rect.x, rect.y, rect.width, rect.height
        rx = min(radius_x, width / 2)
        ry = min(radius_y, height / 2)
        # Standard cubic approximation of a quarter circle. At 50% on both
        # dimensions, a square body becomes a true circular silhouette.
        k = 0.5522847498
        context.new_sub_path()
        context.move_to(x + rx, y)
        context.line_to(x + width - rx, y)
        context.curve_to(
            x + width - rx * (1 - k), y,
            x + width, y + ry * (1 - k),
            x + width, y + ry,
        )
        context.line_to(x + width, y + height - ry)
        context.curve_to(
            x + width, y + height - ry * (1 - k),
            x + width - rx * (1 - k), y + height,
            x + width - rx, y + height,
        )
        context.line_to(x + rx, y + height)
        context.curve_to(
            x + rx * (1 - k), y + height,
            x, y + height - ry * (1 - k),
            x, y + height - ry,
        )
        context.line_to(x, y + ry)
        context.curve_to(
            x, y + ry * (1 - k),
            x + rx * (1 - k), y,
            x + rx, y,
        )
        context.close_path()

    def run(self):
        self._refresh()
        self.window.show_all()
        Gtk.main()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text",
        default="Chào bạn! Đây là bong bóng chat thử. Bạn có thể sửa nội dung ở ô phía trên.",
        help="initial bubble text",
    )
    args = parser.parse_args()
    BubbleTester(args.text).run()


if __name__ == "__main__":
    main()
