"""Tests for conversational notification classification and comic geometry."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_foreign("cairo")
import cairo  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpets_linux.chat_bubble import (  # noqa: E402
    ChatBubblePlugin,
    estimate_bubble_size,
    is_chat_message,
)
from openpets_linux.core import State, has_active_task  # noqa: E402
from openpets_linux.overlay import (  # noqa: E402
    CHAT_BUBBLE_MAX_WIDTH,
    draw_chat_bubble,
    compute_layout,
    estimate_chat_bubble_size,
    hit_test,
    input_rectangles,
    wrap_chat_text_to_shape,
    _text_layout,
    _text_graphemes,
    _cached_chat_lines,
)
from openpets_linux.settings import DEFAULT_CONFIG  # noqa: E402
from openpets_linux.ui import PetWindow  # noqa: E402
from openpets_linux.ui import message_surfaces_for_layout  # noqa: E402
from openpets_linux.chat_bubble_ui import ChatBubbleOverlay  # noqa: E402
from openpets_linux import mcp_server  # noqa: E402
from openpets_linux.overlay import _estimate_chat_bubble_size_cached  # noqa: E402


class ChatClassificationTests(unittest.TestCase):
    def test_chat_bubble_plugin_is_opt_in(self):
        self.assertFalse(DEFAULT_CONFIG["linuxPlugins"]["chatBubble"])
        self.assertEqual(DEFAULT_CONFIG["display"]["chatBubbleOpacity"], 0.94)
        self.assertEqual(DEFAULT_CONFIG["display"]["chatBubbleDurationSeconds"], 5)

    def test_chat_bubble_duration_is_independent_of_message_preview_duration(self):
        class Durations:
            collapsed_message_preview_seconds = 11
            chat_bubble_duration_seconds = 4

        durations = Durations()
        self.assertEqual(PetWindow._preview_ttl(durations), 11)
        self.assertEqual(PetWindow._chat_bubble_ttl(durations), 4)

    def test_chat_kind_stays_in_existing_message_and_preview_surfaces(self):
        notification = {
            "threadId": "chat-test", "kind": "chat", "status": "message",
            "title": "A chat message", "text": "Keep the existing preview visible",
        }
        messages, previews = message_surfaces_for_layout([notification], [notification])
        self.assertEqual(messages, [notification])
        self.assertEqual(previews, (notification,))

    def test_known_lifecycle_status_is_not_chat(self):
        self.assertFalse(is_chat_message({"status": "running", "text": "Working"}))
        self.assertFalse(is_chat_message({"status": "done", "text": "Finished"}))

    def test_unknown_status_is_conversation_for_legacy_callers(self):
        self.assertTrue(is_chat_message({"status": "thinking", "text": "One moment"}))

    def test_explicit_kind_overrides_status(self):
        self.assertTrue(is_chat_message({"kind": "chat", "status": "running", "text": "Hi"}))
        self.assertFalse(is_chat_message({"kind": "status", "status": "chat", "text": "Hi"}))

    def test_plugin_returns_latest_nonempty_chat(self):
        older = {"status": "chat", "threadId": "old", "text": "Hello", "updated_at": 5}
        newest = {"status": "chat", "threadId": "new", "text": "Hi", "updated_at": 9}
        empty = {"status": "chat", "threadId": "empty", "text": "  ", "updated_at": 10}
        self.assertIs(ChatBubblePlugin.latest([newest, older, empty]), newest)

    def test_core_marks_every_notify_as_chat(self):
        state = State(clock=lambda: 10)
        response = state.apply({"type": "notify", "notification": {
            "title": "internal title", "status": "running", "kind": "status",
            "text": "Hello", "threadId": "chat-thread",
        }})
        self.assertTrue(response["ok"])
        self.assertEqual(state.messages["chat-thread"]["kind"], "chat")

    def test_mcp_notify_forwards_explicit_chat_kind(self):
        with patch.object(mcp_server, "_ipc", return_value={"ok": True}) as send:
            result = mcp_server.notify(
                "/tmp/test.sock", "Internal title", "Hello", "running",
                threadId="chat-thread", kind="chat",
            )
        self.assertTrue(result["ok"])
        notification = send.call_args.args[0]["notification"]
        self.assertEqual(notification["kind"], "chat")
        with patch.object(mcp_server, "_ipc", return_value={"ok": True}) as send:
            mcp_server.notify("/tmp/test.sock", "Title", "text", "running")
        self.assertEqual(send.call_args.args[0]["notification"]["kind"], "chat")
        with patch.object(mcp_server, "_ipc", return_value={"ok": True}) as send:
            mcp_server.notify("/tmp/test.sock", "Title", "text", "running", kind="status")
        self.assertEqual(send.call_args.args[0]["notification"]["kind"], "chat")

    def test_mcp_rejects_unknown_kind(self):
        result = mcp_server.notify("/tmp/test.sock", "Title", "text", "running", kind="banana")
        self.assertFalse(result["ok"])

    def test_chat_presentation_preserves_running_task_semantics(self):
        state = State(clock=lambda: 10)
        state.apply({"type": "notify", "notification": {
            "title": "Chat", "status": "running", "kind": "chat",
            "text": "Hello", "threadId": "chat-thread",
        }})
        self.assertEqual(state.messages["chat-thread"]["kind"], "chat")
        self.assertTrue(has_active_task(state.messages))

    def test_motion_remains_frozen_until_collapsed_preview_expires(self):
        class Motion:
            def __init__(self):
                self.paused = False
                self.updates = []

            def set_task_active(self, active):
                self.paused = active
                self.updates.append(active)

        motion = Motion()
        window = SimpleNamespace(
            state=SimpleNamespace(messages={"task": {"status": "done"}}),
            appearance={"messagesCollapsed": True},
            preview_messages={"task": {"expires_at": 10}},
            chat_bubble_enabled=False,
            plugins=SimpleNamespace(chat_bubble=None),
            _task_motion_frozen=False,
            motion=motion,
        )
        window._chat_bubble_queue_active = (
            lambda: PetWindow._chat_bubble_queue_active(window)
        )

        PetWindow._sync_motion_task_state(window)
        self.assertTrue(motion.paused)

        window.preview_messages.clear()
        PetWindow._sync_motion_task_state(window)
        self.assertFalse(motion.paused)
        self.assertEqual(motion.updates, [True, False])

    def test_motion_remains_frozen_while_final_chat_bubble_is_current_or_pending(self):
        class Motion:
            def __init__(self):
                self.paused = False

            def set_task_active(self, active):
                self.paused = active

        motion = Motion()
        queue = SimpleNamespace(current={"text": "done"}, pending_count=0)
        window = SimpleNamespace(
            state=SimpleNamespace(messages={"task": {"status": "done"}}),
            appearance={"messagesCollapsed": False},
            preview_messages={},
            chat_bubble_enabled=True,
            plugins=SimpleNamespace(chat_bubble=queue),
            _task_motion_frozen=False,
            motion=motion,
        )
        window._chat_bubble_queue_active = (
            lambda: PetWindow._chat_bubble_queue_active(window)
        )

        PetWindow._sync_motion_task_state(window)
        self.assertTrue(motion.paused)

        queue.current = None
        queue.pending_count = 1
        PetWindow._sync_motion_task_state(window)
        self.assertTrue(motion.paused)

        queue.pending_count = 0
        PetWindow._sync_motion_task_state(window)
        self.assertFalse(motion.paused)


class ChatBubbleQueueTests(unittest.TestCase):
    def test_successive_same_thread_messages_are_queued_not_replaced(self):
        plugin = ChatBubblePlugin()
        first = {"status": "message", "kind": "chat", "threadId": "same", "text": "first"}
        second = {"status": "message", "kind": "chat", "threadId": "same", "text": "second"}
        third = {"status": "message", "kind": "chat", "threadId": "same", "text": "third"}
        self.assertTrue(plugin.enqueue(first))
        self.assertFalse(plugin.enqueue(second))
        self.assertFalse(plugin.enqueue(third))
        self.assertEqual(plugin.current["text"], "first")
        self.assertEqual(plugin.pending_count, 2)
        self.assertEqual(plugin.advance()["text"], "second")
        self.assertEqual(plugin.advance()["text"], "third")
        self.assertIsNone(plugin.advance())

    def test_non_chat_messages_are_not_queued(self):
        plugin = ChatBubblePlugin()
        self.assertFalse(plugin.enqueue({"status": "running", "text": "task"}))
        self.assertIsNone(plugin.current)

    def test_clear_drops_current_and_pending_messages(self):
        plugin = ChatBubblePlugin()
        plugin.enqueue({"kind": "chat", "status": "message", "text": "first"})
        plugin.enqueue({"kind": "chat", "status": "message", "text": "second"})
        plugin.clear()
        self.assertIsNone(plugin.current)
        self.assertEqual(plugin.pending_count, 0)


class ChatBubbleSizingTests(unittest.TestCase):
    def test_estimated_measurement_is_cached_for_relayout(self):
        text = "A repeatable cached bubble measurement."
        _estimate_chat_bubble_size_cached.cache_clear()
        first = estimate_chat_bubble_size(text)
        second = estimate_chat_bubble_size(text)
        self.assertEqual(first, second)
        self.assertEqual(_estimate_chat_bubble_size_cached.cache_info().hits, 1)

    def test_width_grows_with_shorter_than_wrap_limit_content(self):
        short = estimate_bubble_size("Hi")
        longer = estimate_bubble_size("A friendly message")
        self.assertGreater(longer[0], short[0])
        self.assertEqual(short[1], longer[1])

    def test_height_grows_when_content_wraps(self):
        one_line = estimate_bubble_size("A short note")
        wrapped = estimate_bubble_size("A longer note " * 16)
        self.assertEqual(wrapped[0], CHAT_BUBBLE_MAX_WIDTH)
        self.assertGreater(wrapped[1], one_line[1])
        self.assertGreater(wrapped[2], one_line[2])

    def test_explicit_newlines_and_cjk_count_toward_size(self):
        one_line = estimate_bubble_size("你好")
        multiline = estimate_bubble_size("你好\n世界\n再见")
        self.assertGreaterEqual(one_line[0], 96)
        self.assertGreater(multiline[1], one_line[1])

    def test_size_remains_bounded_for_maximum_text(self):
        width, height, lines = estimate_bubble_size("word " * 2000)
        self.assertEqual(width, CHAT_BUBBLE_MAX_WIDTH)
        self.assertLessEqual(height, 320)
        self.assertLessEqual(lines, 13)

    def test_pango_utf8_sizing_respects_selected_aspect_ratio(self):
        width, height, lines = estimate_chat_bubble_size(
            "Tiếng Việt có dấu và xuống dòng theo Pango.",
            max_width=CHAT_BUBBLE_MAX_WIDTH,
            font_size=14,
            aspect_ratio=1.8,
            corner_radius_percent=22,
        )
        self.assertGreater(lines, 0)
        self.assertLessEqual(width, CHAT_BUBBLE_MAX_WIDTH)
        self.assertAlmostEqual(width / height, 1.8, delta=0.08)


class ChatBubbleGeometryTests(unittest.TestCase):
    def setUp(self):
        self.message = {"threadId": "chat-thread", "status": "chat", "title": "Hidden", "text": "A conversational detail."}

    def test_bubble_is_opposite_plugin_stack_with_tapered_tail(self):
        layout = compute_layout(560, 140, 100, plugin_side="right", chat_message=self.message)
        bubble = layout.chat_bubble
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.side, "left")
        self.assertEqual(len(bubble.tail), 1)
        self.assertGreater(bubble.tail[0].width, 0)
        self.assertLess(bubble.rect.x + bubble.rect.width, layout.pet_rect.x)
        self.assertLessEqual(bubble.rect.width, CHAT_BUBBLE_MAX_WIDTH)

    def test_right_side_when_plugins_are_left(self):
        layout = compute_layout(560, 140, 100, plugin_side="left", chat_message=self.message)
        self.assertEqual(layout.chat_bubble.side, "right")
        self.assertGreater(layout.chat_bubble.rect.x, layout.pet_rect.x + layout.pet_rect.width)

    def test_bubble_and_tail_are_hit_targets(self):
        layout = compute_layout(560, 140, 100, chat_message=self.message)
        bubble = layout.chat_bubble
        hit = hit_test(layout, bubble.rect.x + 4, bubble.rect.y + 4)
        self.assertEqual((hit.kind, hit.thread_id), ("chat", "chat-thread"))
        rectangles = input_rectangles(layout)
        self.assertIn(bubble.rect, rectangles)
        self.assertTrue(all(dot in rectangles for dot in bubble.tail))

    def test_bubble_draws_to_in_memory_cairo_surface(self):
        layout = compute_layout(560, 140, 100, chat_message=self.message)
        surface = cairo.ImageSurface(cairo.Format.ARGB32, 560, layout.total_height)
        context = cairo.Context(surface)
        _cached_chat_lines.cache_clear()
        draw_chat_bubble(context, layout)
        draw_chat_bubble(context, layout)
        self.assertEqual(_cached_chat_lines.cache_info().hits, 1)
        surface.flush()
        self.assertGreater(len(surface.get_data()), 0)
        bubble = layout.chat_bubble.rect
        data, stride = surface.get_data(), surface.get_stride()

        def rgb(x, y):
            offset = y * stride + x * 4
            pixel = data[offset:offset + 4]
            return tuple(pixel[i] for i in (2, 1, 0)) if sys.byteorder == "little" else tuple(pixel[i] for i in (1, 2, 3))

        self.assertTrue(all(channel >= 220 for channel in rgb(
            bubble.x + bubble.width // 2, bubble.y + 5
        )))
        self.assertTrue(any(channel < 100 for channel in rgb(
            bubble.x + bubble.width // 2, bubble.y
        )))
        text_pixels = [rgb(x, y) for y in range(bubble.y + 17, bubble.y + 45)
                       for x in range(bubble.x + 20, bubble.x + bubble.width - 20)]
        self.assertTrue(any(max(pixel) < 100 for pixel in text_pixels))

    def test_opacity_applies_to_the_complete_balloon_as_one_layer(self):
        layout = compute_layout(
            560, 140, 100, chat_message=self.message, chat_bubble_opacity=0.4
        )
        surface = cairo.ImageSurface(cairo.Format.ARGB32, 560, layout.total_height)
        draw_chat_bubble(cairo.Context(surface), layout)
        surface.flush()
        bubble = layout.chat_bubble.rect
        offset = (bubble.y + 5) * surface.get_stride() + (bubble.x + bubble.width // 2) * 4
        alpha_index = 3 if sys.byteorder == "little" else 0
        alpha = surface.get_data()[offset + alpha_index]
        self.assertGreaterEqual(alpha, 95)
        self.assertLessEqual(alpha, 110)

    def test_bubble_opacity_is_bounded(self):
        layout = compute_layout(
            560, 140, 100, chat_message=self.message, chat_bubble_opacity=2
        )
        self.assertEqual(layout.chat_bubble_opacity, 1.0)

    def test_layout_dimensions_follow_message_text(self):
        short = compute_layout(560, 140, 100, chat_message={**self.message, "text": "Hi"})
        long = compute_layout(800, 140, 100, chat_message={**self.message, "text": "A friendly message" * 8})
        self.assertGreater(long.chat_bubble.rect.width, short.chat_bubble.rect.width)
        self.assertGreater(long.chat_bubble.rect.height, short.chat_bubble.rect.height)

    def test_circle_text_lines_follow_rounded_outline(self):
        surface = cairo.ImageSurface(cairo.Format.ARGB32, 1, 1)
        context = cairo.Context(surface)
        text = "A circle fits words into short lines near its top and bottom, and longer lines through its middle."
        rect = cairo.RectangleInt(0, 0, 218, 218)
        lines, _ = wrap_chat_text_to_shape(context, text, rect.width, rect.height, 15, 50)
        widths = [
            # Measure natural Pango width without applying line wrapping.
            _text_layout(context, line, 15, 10000).get_pixel_size()[0]
            for line in lines
        ]
        self.assertGreater(len(lines), 3)
        self.assertLess(widths[0], max(widths))
        self.assertLess(widths[-1], max(widths))

    def test_unicode_combining_marks_stay_with_their_base_character(self):
        self.assertEqual(list(_text_graphemes("e\u0301x")), ["e\u0301", "x"])

    def test_overlay_movement_reuses_cached_raster(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        parent = Gtk.Window()
        overlay = ChatBubbleOverlay(parent)
        try:
            overlay.set_bubble(
                self.message, pet_x=300, pet_y=250, cell_width=140,
                cell_height=100, workarea=(0, 0, 1920, 1080),
                plugin_side="right",
            )
            rendered = overlay.render_count
            original_surface = overlay.surface
            overlay.move_with_pet(420, 330)
            self.assertEqual(overlay.render_count, rendered)
            self.assertIs(overlay.surface, original_surface)

            overlay.set_bubble(
                {**self.message, "text": "Changed content"},
                pet_x=420, pet_y=330, cell_width=140,
                cell_height=100, workarea=(0, 0, 1920, 1080),
                plugin_side="right",
            )
            self.assertGreater(overlay.render_count, rendered)
        finally:
            overlay.destroy()
            parent.destroy()

    def test_expired_bubble_releases_cached_raster(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        parent = Gtk.Window()
        overlay = ChatBubbleOverlay(parent)
        try:
            overlay.set_bubble(
                self.message, pet_x=300, pet_y=250, cell_width=140,
                cell_height=100, workarea=(0, 0, 1920, 1080),
                plugin_side="right",
            )
            self.assertIsNotNone(overlay.surface)
            overlay.set_bubble(
                None, pet_x=0, pet_y=0, cell_width=0, cell_height=0,
                workarea=None, plugin_side="right",
            )
            self.assertIsNone(overlay.surface)
            self.assertIsNone(overlay.cache_key)
        finally:
            overlay.destroy()
            parent.destroy()

    def test_overlay_initial_window_is_large_enough_for_cached_surface(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        parent = Gtk.Window()
        overlay = ChatBubbleOverlay(parent)
        try:
            parent.show()
            overlay.set_bubble(
                self.message, pet_x=500, pet_y=300, cell_width=100,
                cell_height=100, workarea=(0, 0, 1366, 768),
                plugin_side="right",
            )
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            window_width, window_height = overlay.get_size()
            self.assertGreaterEqual(window_width, overlay.surface.get_width())
            self.assertGreaterEqual(window_height, overlay.surface.get_height())
        finally:
            overlay.destroy()
            parent.destroy()

    def test_pet_layout_size_is_independent_of_chat_bubble_text(self):
        window_width = PetWindow._desired_width(
            SimpleNamespace(cell_width=140, message_bubble_width=320)
        )
        long_text_width = PetWindow._desired_width(
            SimpleNamespace(cell_width=140, message_bubble_width=320),
            {**self.message, "text": self.message["text"] * 40},
        )
        self.assertEqual(window_width, long_text_width)


if __name__ == "__main__":
    unittest.main()
