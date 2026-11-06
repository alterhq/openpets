"""GTK3/X11 pet host. All GUI and socket callbacks run on the GLib loop."""

import json
import logging
import math
import os
from collections import OrderedDict
from pathlib import Path
import signal
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_foreign("cairo")
import cairo
from gi.repository import Gdk, GLib, Gtk

from .core import ANIMATIONS, FINITE_STATUS_ANIMATIONS, State, has_active_task
from .ipc import bind_socket, cleanup_socket
from .pet import load_atlas, sprite_version
from .settings import Settings
from .library import PetLibrary
from .notifications import NotificationHistory
from .chat_bubble import (
    CHAT_BUBBLE_ASPECT_RATIO,
    CHAT_BUBBLE_CORNER_RADIUS_PERCENT,
    CHAT_BUBBLE_TEXT_SIZE,
)
from .chat_bubble_ui import ChatBubbleOverlay
from .logging_setup import elapsed_ms, log_process_memory
from .mcp_runtime import MCPPortBusy, MCPProcess
from .animation import (
    ANIMATION_DURATIONS as DURATIONS,
    IDLE_INTERVAL_MAX_MS,
    IDLE_INTERVAL_MIN_MS,
    IdlePicker,
    drag_animation,
    look_direction_cell,
)
from .motion import (
    MODE_FREE_ROAM,
    MODE_FOLLOW,
    MODE_OFF,
    MOTION_INTERVAL_MS,
    MotionController,
    clamp_position_to_workarea,
    is_valid_mode,
)

from .overlay import (
    MESSAGE_MAX_WIDTH,
    MESSAGE_PAD_X,
    compute_layout,
    draw_overlay,
    input_rectangles,
    hit_test,
)

MESSAGE_TOGGLE_AUTO_HIDE_MS = 2500
MCP_RETRY_INTERVAL_SECONDS = 2
MCP_SETTINGS_DEFAULT_WIDTH = 500
MCP_SETTINGS_SLIDER_WIDTH = 180
logger = logging.getLogger(__name__)


def message_surfaces_for_layout(messages, previews):
    """Keep the existing message surfaces intact alongside the chat plugin."""
    return list(messages), tuple(previews)

# OpenPetsKit v0.2.1 PetAnimation.swift (MIT), not a fixed high-FPS redraw loop.
# Timing data now lives in the pure :mod:`openpets_linux.animation` module and is
# re-exported here as ``DURATIONS`` for the existing frame loop.


class PetWindow(Gtk.Window):
    def __init__(self, pet_directory, scale, socket_path):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.state = State()
        self.settings = Settings()
        self.library = PetLibrary(self.settings)
        self.pet_directory = Path(pet_directory).resolve()
        self.pet_id = self.identifier(self.pet_directory)
        self.scale = scale
        saved_display = self.settings.read().get("display", {})
        if not isinstance(saved_display, dict):
            saved_display = {}
        self.message_bubble_width = self._bounded_display_value(
            saved_display.get("messageBubbleWidth"), MESSAGE_MAX_WIDTH, 120, 600, integer=True
        )
        self.message_font_size = self._bounded_display_value(
            saved_display.get("messageFontSize"), 10.0, 6, 24
        )
        self.chat_bubble_font_size = self._bounded_display_value(
            saved_display.get("chatBubbleFontSize"), CHAT_BUBBLE_TEXT_SIZE, 8, 28
        )
        self.chat_bubble_aspect_ratio = self._bounded_display_value(
            saved_display.get("chatBubbleAspectRatio"), CHAT_BUBBLE_ASPECT_RATIO, 1, 3.5
        )
        self.chat_bubble_corner_radius = self._bounded_display_value(
            saved_display.get("chatBubbleCornerRadiusPercent"),
            CHAT_BUBBLE_CORNER_RADIUS_PERCENT, 0, 50,
        )
        self.history = NotificationHistory()
        self.notification_center = None
        self.chooser = None
        self.position_source = 0
        self.mcp_process = MCPProcess()
        self._last_memory_log = 0.0
        self.atlas, self.cell_width, self.cell_height = load_atlas(pet_directory, scale)
        self.look_direction_enabled = sprite_version(pet_directory) == 2
        self.look_direction = None
        self.frame = 0
        self.current_animation = self.state.animation
        self.frame_source = 0
        self.cycles_remaining = None
        self.drag_active = False
        self.drag_animation = None
        self.drag_start = None
        self.drag_last = None
        self.drag_origin = None
        self.motion = MotionController()
        self.motion_animation = None
        self.motion_source = 0
        self.motion_settled_save_source = 0
        self.idle_source = 0
        self.idle_picker = IdlePicker()
        self.connections = {}
        self.socket_path = socket_path
        self.listener = None
        self.socket_identity = None
        self.message_rows = []
        plugin_settings = self.settings.read().get("linuxPlugins", {})
        self.chat_bubble_enabled = (
            isinstance(plugin_settings, dict) and plugin_settings.get("chatBubble") is True
        )
        display_settings = self.settings.read().get("display", {})
        if not isinstance(display_settings, dict):
            display_settings = {}
        self.appearance = {
            "themeMode": display_settings.get("themeMode", "dark"),
            "pluginOpacity": display_settings.get("pluginOpacity", 0.94),
            "messageOpacity": display_settings.get("messageOpacity", 0.94),
            "chatBubbleOpacity": display_settings.get("chatBubbleOpacity", 0.94),
            "blurBackground": display_settings.get("blurBackground", False),
            "borderRadius": display_settings.get("borderRadius", 14.0),
            "messagesCollapsed": display_settings.get("messagesCollapsed", False),
        }
        self.plugin_side = display_settings.get("pluginSide", "right")
        if self.plugin_side not in ("left", "right"):
            self.plugin_side = "right"
        preview_seconds = display_settings.get("collapsedMessagePreviewSeconds", 5)
        if isinstance(preview_seconds, bool) or not isinstance(preview_seconds, (int, float)):
            preview_seconds = 5
        try:
            if not math.isfinite(preview_seconds):
                preview_seconds = 5
        except OverflowError:
            preview_seconds = 5
        self.collapsed_message_preview_seconds = int(round(max(1, min(60, float(preview_seconds)))))
        bubble_duration = display_settings.get("chatBubbleDurationSeconds", 5)
        if isinstance(bubble_duration, bool) or not isinstance(bubble_duration, (int, float)):
            bubble_duration = 5
        try:
            if not math.isfinite(bubble_duration):
                bubble_duration = 5
        except OverflowError:
            bubble_duration = 5
        self.chat_bubble_duration_seconds = int(round(max(1, min(60, float(bubble_duration)))))
        self.unread_message_count = 0
        self._known_message_ids = set()
        self._task_motion_frozen = False
        # thread_id -> notification with an independent expiry. Ordered order
        # is display order: older previews above, newest/update at the bottom.
        self.preview_messages = OrderedDict()
        self.preview_source = 0
        self.chat_bubble_source = 0
        self.message_toggle_visible = True
        self._toggle_hide_source = 0
        self._pointer_in_pet = False
        self.set_title("OpenPets Linux")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.composited = screen.is_composited()
        self.width = self._desired_width()
        self.layout = compute_layout(
            self.width, self.cell_width, self.cell_height,
            message_max_width=self.message_bubble_width,
            message_font_size=self.message_font_size,
            plugin_side=self.plugin_side,
            preview_messages=tuple(self.preview_messages.values()),
        )
        self.height = self.layout.total_height
        self.set_default_size(self.width, self.height)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion_notify)
        self.connect("enter-notify-event", self.on_pointer_enter)
        self.connect("leave-notify-event", self.on_pointer_leave)
        self.connect("grab-broken-event", lambda *_: self.end_drag() if self.drag_active else False)
        self.connect("realize", lambda *_: self.update_input_shape())
        self.connect("configure-event", self.on_configure)
        self.connect("destroy", lambda *_: self.shutdown())
        self.connect("hide", lambda *_: self._on_hide())
        self.connect("show", lambda *_: self._on_show())
        self.menu = Gtk.Menu()
        for label, callback in (("Show pet", lambda *_: self.show_all()),
                                ("Hide pet", lambda *_: self.hide()),
                                ("Quit", lambda *_: self.shutdown())):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            self.menu.append(item)
        # Autonomous movement mode (mutually exclusive, default Off).
        sep = Gtk.SeparatorMenuItem()
        self.menu.append(sep)
        self.motion_group = None
        self.motion_off_item = Gtk.RadioMenuItem.new_with_label(
            self.motion_group, "Movement: Off"
        )
        self.motion_group = self.motion_off_item.get_group()
        self.motion_off_item.connect("toggled", lambda item: self._on_motion_toggled(item, MODE_OFF))
        self.menu.append(self.motion_off_item)
        self.motion_follow_item = Gtk.RadioMenuItem.new_with_label(
            self.motion_group, "Movement: Follow cursor"
        )
        self.motion_follow_item.connect("toggled", lambda item: self._on_motion_toggled(item, MODE_FOLLOW))
        self.menu.append(self.motion_follow_item)
        self.motion_free_item = Gtk.RadioMenuItem.new_with_label(
            self.motion_group, "Movement: Free roam"
        )
        self.motion_free_item.connect("toggled", lambda item: self._on_motion_toggled(item, MODE_FREE_ROAM))
        self.menu.append(self.motion_free_item)
        self.menu.show_all()
        self.indicator = None
        self.mcp_retry_source = 0
        self._mcp_requested = False
        self._mcp_indicator_state = "off"
        self._shutdown_requested = False
        from .plugin_ui import PluginController
        self.plugins = PluginController(self, self.settings, self._plugins_changed)
        self.chat_bubble_overlay = ChatBubbleOverlay(self)
        for label, callback in (("Settings…", self.open_settings),
                                ("Choose pet…", self.open_chooser),
                                ("OpenCode setup…", self.open_onboarding),
                                ("Messages / actions…", self.open_notifications)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, cb=callback: cb())
            self.menu.prepend(item)
        plugin_item = Gtk.MenuItem(label="Plugins")
        plugin_item.set_submenu(self.plugins.menu())
        self.menu.prepend(plugin_item)
        self.mcp_item = Gtk.CheckMenuItem(label="MCP server (loopback)")
        self.mcp_item.connect("toggled", self.on_mcp_toggle)
        self._install_mcp_indicator_styles()
        self.menu.prepend(self.mcp_item)
        self.menu.show_all()

    @staticmethod
    def identifier(directory):
        try:
            manifest = json.loads((Path(directory) / "pet.json").read_text())
            name = manifest.get("id")
            if isinstance(name, str) and 0 < len(name) <= 128:
                return name
        except (OSError, ValueError, RecursionError):
            pass
        return Path(directory).name[:128]

    @staticmethod
    def _bounded_display_value(value, default, minimum, maximum, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        try:
            if not math.isfinite(value):
                return default
        except OverflowError:
            return default
        value = max(minimum, min(maximum, value))
        return int(round(value)) if integer else float(value)

    def _desired_width(self, chat_message=None):
        return max(360, self.cell_width, self.message_bubble_width + 2 * MESSAGE_PAD_X)

    def _plugins_changed(self):
        self.chat_bubble_enabled = self.plugins.chat_bubble_enabled
        if not self.chat_bubble_enabled:
            self._cancel_chat_bubble_timer()
            self.plugins.chat_bubble.clear()
        self.width = self._desired_width()
        self.refresh()

    def open_settings(self):
        """Single settings surface: display geometry plus overlay appearance.

        Replaces the former separate "Display settings" and "Overlay appearance"
        dialogs with one cohesive dialog; every change is applied together in a
        single atomic settings write.
        """
        dialog = Gtk.Dialog(title="Settings", transient_for=self,
                            flags=Gtk.DialogFlags.MODAL)
        dialog.set_default_size(MCP_SETTINGS_DEFAULT_WIDTH, -1)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apply", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=10, margin=12)
        dialog.get_content_area().add(grid)

        row = 0
        # --- Display: pet size, message bubble width, message font size -----
        controls = (
            ("Pet size", "scale", self.scale, 0.2, 2.0, 0.05, 2),
            ("Message bubble width", "messageBubbleWidth",
             self.message_bubble_width, 120, 600, 10, 0),
            ("Message font size", "messageFontSize",
             self.message_font_size, 6, 24, 1, 0),
            ("Comic bubble font size", "chatBubbleFontSize",
             self.chat_bubble_font_size, 8, 28, 0.5, 1),
            ("Comic bubble ratio (W:H)", "chatBubbleAspectRatio",
             self.chat_bubble_aspect_ratio, 1, 3.5, 0.1, 1),
            ("Comic corner radius (%)", "chatBubbleCornerRadiusPercent",
             self.chat_bubble_corner_radius, 0, 50, 1, 0),
        )
        scales = []
        for label, key, value, minimum, maximum, step, digits in controls:
            text = Gtk.Label(label=label, xalign=0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                             minimum, maximum, step)
            scale.set_digits(digits)
            scale.set_value(value)
            scale.set_hexpand(True)
            scale.set_size_request(MCP_SETTINGS_SLIDER_WIDTH, -1)
            grid.attach(text, 0, row, 1, 1)
            grid.attach(scale, 1, row, 1, 1)
            scales.append((key, scale))
            row += 1

        # --- Appearance: theme, opacities, border radius, blur --------------
        theme = Gtk.ComboBoxText()
        theme.append("dark", "Dark (black backgrounds)")
        theme.append("light", "Light (white backgrounds)")
        theme.set_active_id(self.appearance["themeMode"]
                            if self.appearance["themeMode"] in ("dark", "light")
                            else "dark")
        grid.attach(Gtk.Label(label="Theme", xalign=0), 0, row, 1, 1)
        grid.attach(theme, 1, row, 1, 1)
        row += 1

        for label, key in (("Plugin background opacity", "pluginOpacity"),
                           ("Message background opacity", "messageOpacity"),
                           ("Comic chat bubble opacity", "chatBubbleOpacity")):
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
            scale.set_digits(2)
            try:
                scale.set_value(float(self.appearance[key]))
            except (TypeError, ValueError):
                scale.set_value(1.0)
            scale.set_hexpand(True)
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(scale, 1, row, 1, 1)
            scales.append((key, scale))
            row += 1

        radius = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 32, 1)
        radius.set_digits(0)
        try:
            radius.set_value(float(self.appearance["borderRadius"]))
        except (TypeError, ValueError):
            radius.set_value(14.0)
        radius.set_hexpand(True)
        grid.attach(Gtk.Label(label="Border radius", xalign=0), 0, row, 1, 1)
        grid.attach(radius, 1, row, 1, 1)
        scales.append(("borderRadius", radius))
        row += 1

        preview = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 60, 1)
        preview.set_digits(0)
        try:
            preview.set_value(float(self.collapsed_message_preview_seconds))
        except (TypeError, ValueError):
            preview.set_value(5)
        preview.set_hexpand(True)
        grid.attach(Gtk.Label(label="Collapsed message preview (seconds)", xalign=0), 0, row, 1, 1)
        grid.attach(preview, 1, row, 1, 1)
        scales.append(("collapsedMessagePreviewSeconds", preview))
        row += 1

        chat_duration = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 60, 1)
        chat_duration.set_digits(0)
        chat_duration.set_value(float(self.chat_bubble_duration_seconds))
        chat_duration.set_hexpand(True)
        grid.attach(Gtk.Label(label="Comic chat bubble duration (seconds)", xalign=0), 0, row, 1, 1)
        grid.attach(chat_duration, 1, row, 1, 1)
        scales.append(("chatBubbleDurationSeconds", chat_duration))
        row += 1

        side_left = Gtk.RadioButton.new_with_label_from_widget(None, "Plugin stack: Left")
        side_right = Gtk.RadioButton.new_with_label_from_widget(side_left, "Plugin stack: Right")
        side_left.set_active(self.plugin_side == "left")
        side_right.set_active(self.plugin_side == "right")
        side_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        side_box.pack_start(side_left, False, False, 0)
        side_box.pack_start(side_right, False, False, 0)
        grid.attach(Gtk.Label(label="Plugin stack", xalign=0), 0, row, 1, 1)
        grid.attach(side_box, 1, row, 1, 1)
        row += 1

        blur = Gtk.CheckButton(label="Request compositor background blur")
        blur.set_active(self.appearance["blurBackground"] is True)
        grid.attach(blur, 0, row, 2, 1)
        row += 1
        blur_note = Gtk.Label(
            label="Blur requires an X11 compositor configured for ARGB-window blur (for example Picom with blur-background). OpenPets cannot enable compositor blur by itself.",
            xalign=0, wrap=True,
        )
        grid.attach(blur_note, 0, row, 2, 1)

        dialog.show_all()
        response = dialog.run()
        values = {key: scale.get_value() for key, scale in scales}
        values["themeMode"] = theme.get_active_id() or "dark"
        values["blurBackground"] = blur.get_active()
        values["pluginSide"] = "left" if side_left.get_active() else "right"
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            self.apply_settings(values)
        except (OSError, ValueError, RuntimeError) as error:
            self.plugins.show_error(f"Could not apply settings: {error}")

    def apply_settings(self, values):
        """Validate, persist and apply every display/appearance value together.

        Opacity values accept the full continuous 0.0..1.0 range (0 = fully
        transparent, 1 = opaque) and are clamped there; everything else keeps its
        existing bounded ranges. Writes one merged ``display`` object to disk.
        """
        def bounded(key, default, low, high, integer=False):
            value = values.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return default
            try:
                if not math.isfinite(value):
                    return default
            except OverflowError:
                return default
            value = max(low, min(high, float(value)))
            return int(round(value)) if integer else value

        pet_scale = bounded("scale", self.scale, 0.2, 2.0)
        bubble_width = bounded("messageBubbleWidth", self.message_bubble_width,
                               120, 600, integer=True)
        font_size = bounded("messageFontSize", self.message_font_size, 6, 24)
        chat_font_size = bounded("chatBubbleFontSize", self.chat_bubble_font_size, 8, 28)
        chat_aspect_ratio = bounded(
            "chatBubbleAspectRatio", self.chat_bubble_aspect_ratio, 1, 3.5
        )
        chat_corner_radius = bounded(
            "chatBubbleCornerRadiusPercent", self.chat_bubble_corner_radius, 0, 50
        )
        chat_duration = bounded(
            "chatBubbleDurationSeconds", self.chat_bubble_duration_seconds,
            1, 60, integer=True,
        )
        theme = values.get("themeMode", self.appearance["themeMode"])
        if theme not in ("dark", "light"):
            theme = "dark"
        plugin_side = values.get("pluginSide", self.plugin_side)
        if plugin_side not in ("left", "right"):
            plugin_side = "right"
        updated = {
            "scale": pet_scale,
            "messageBubbleWidth": bubble_width,
            "messageFontSize": font_size,
            "chatBubbleFontSize": chat_font_size,
            "chatBubbleAspectRatio": chat_aspect_ratio,
            "chatBubbleCornerRadiusPercent": chat_corner_radius,
            "chatBubbleDurationSeconds": chat_duration,
            "themeMode": theme,
            "pluginOpacity": bounded("pluginOpacity", 0.94, 0.0, 1.0),
            "messageOpacity": bounded("messageOpacity", 0.94, 0.0, 1.0),
            "chatBubbleOpacity": bounded("chatBubbleOpacity", 0.94, 0.0, 1.0),
            "blurBackground": values.get("blurBackground") is True,
            "borderRadius": bounded("borderRadius", 14.0, 0.0, 32.0),
            "pluginSide": plugin_side,
            "collapsedMessagePreviewSeconds": bounded(
                "collapsedMessagePreviewSeconds",
                self.collapsed_message_preview_seconds,
                1, 60, integer=True,
            ),
        }
        try:
            self.settings.update({"display": updated})
        except (OSError, ValueError) as error:
            self.plugins.show_error(f"Could not save settings: {error}")
            return
        self.scale = pet_scale
        self.message_bubble_width = bubble_width
        self.message_font_size = font_size
        self.chat_bubble_font_size = chat_font_size
        self.chat_bubble_aspect_ratio = chat_aspect_ratio
        self.chat_bubble_corner_radius = chat_corner_radius
        self.chat_bubble_duration_seconds = chat_duration
        if self.chat_bubble_enabled and self.plugins.chat_bubble.current is not None:
            self._schedule_chat_bubble_advance()
        self.appearance.update({
            "themeMode": theme,
            "pluginOpacity": updated["pluginOpacity"],
            "messageOpacity": updated["messageOpacity"],
            "chatBubbleOpacity": updated["chatBubbleOpacity"],
            "blurBackground": updated["blurBackground"],
            "borderRadius": updated["borderRadius"],
            "pluginSide": plugin_side,
        })
        self.plugin_side = plugin_side
        self.collapsed_message_preview_seconds = updated["collapsedMessagePreviewSeconds"]
        atlas, cell_width, cell_height = load_atlas(self.pet_directory, pet_scale)
        self.atlas, self.cell_width, self.cell_height = atlas, cell_width, cell_height
        self.width = self._desired_width()
        self.relayout()
        self.update_input_shape()
        self.queue_draw()
        if updated["blurBackground"]:
            if not self.composited:
                self.plugins.show_error(
                    "Background blur unavailable: no compositor is active. Install and configure a compositor such as Picom with blur-background enabled!"
                )
            else:
                self.plugins.show_error(
                    "Background blur requested. Configure your compositor (for example Picom blur-background); compositor settings determine whether blur is actually applied!"
                )

    def start(self):
        if "X11" not in self.get_display().__gtype__.name:
            raise RuntimeError("This experimental host requires an X11 desktop session")
        if not self.composited:
            logger.warning("no compositor detected; alpha transparency may be unavailable")
        logger.info(
            "GUI starting pet_id=%s scale=%.3f socket=%s compositor=%s",
            self.pet_id, self.scale, self.socket_path, self.composited,
        )
        log_process_memory(logger, role="app-start")
        self.listener = bind_socket(self.socket_path)
        self.socket_identity = os.lstat(self.socket_path)
        self.listener_source = GLib.io_add_watch(
            self.listener.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self.on_accept
        )
        self.sweep_source = GLib.timeout_add_seconds(1, self.sweep)
        def signal_shutdown():
            self.shutdown()
            return GLib.SOURCE_CONTINUE
        self.signal_sources = [GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, signal_shutdown)
                               for sig in (signal.SIGINT, signal.SIGTERM)]
        self.setup_tray()
        self.show_all()
        self._schedule_toggle_hide()
        self.restore_position()
        self._sync_chat_bubble_overlay()
        self.plugins.start()
        if self.settings.read().get("linuxMCPEnabled", True) is not False:
            self.mcp_item.set_active(True)
        self.schedule_frame()
        self.schedule_idle()
        self._setup_motion()

    def restore_position(self):
        position = self.settings.get_position(self.pet_id)
        monitor = (self.get_display().get_monitor_at_point(*position) if position
                   else self.get_display().get_primary_monitor())
        if monitor is not None:
            area = monitor.get_workarea()
            x, y = position or (area.x + area.width - self.width - 24,
                                area.y + area.height - self.height - 24)
            self.move(max(area.x, min(x, area.x + max(0, area.width - self.width))),
                      max(area.y, min(y, area.y + max(0, area.height - self.height))))

    def on_configure(self, *_args):
        if self.position_source:
            GLib.source_remove(self.position_source)
        self.position_source = GLib.timeout_add(400, self.save_position)
        # Update motion size/workarea only. Do NOT overwrite the controller's
        # subpixel position with the window's integer position: that round-trip
        # truncates subpixel motion and causes jitter during owned movement.
        self.motion.set_size(self.width, self.height)
        self._sync_motion_workarea(*self.get_position())
        self._sync_chat_bubble_overlay()
        return False

    def _sync_motion_workarea(self, x, y):
        """Keep autonomous motion initialized across X11 placement races."""
        display = self.get_display()
        monitor = display.get_monitor_at_point(int(x), int(y))
        if monitor is None:
            monitor = display.get_primary_monitor()
        if monitor is None and display.get_n_monitors() > 0:
            monitor = display.get_monitor(0)
        if monitor is None:
            return False
        area = monitor.get_workarea()
        self.motion.set_workarea((area.x, area.y, area.width, area.height))
        return True

    def save_position(self):
        if self.position_source:
            GLib.source_remove(self.position_source)
        self.position_source = 0
        try:
            x, y = self.get_position()
            self.settings.set_position(self.pet_id, x, y)
        except (OSError, ValueError):
            pass
        return GLib.SOURCE_REMOVE

    def select_pet(self, pet_id):
        entries = self.library.list_pets()
        chosen = next((entry for entry in entries if entry["id"] == pet_id), None)
        if chosen is None:
            raise ValueError("Unknown pet ID")
        atlas, width, height = load_atlas(chosen["directory"], self.scale)
        look_direction_enabled = sprite_version(chosen["directory"]) == 2
        self.save_position()
        directory = self.library.activate(pet_id)
        self.pet_directory, self.pet_id = directory, self.identifier(directory)
        self.atlas, self.cell_width, self.cell_height = atlas, width, height
        self.look_direction_enabled = look_direction_enabled
        self.look_direction = None
        self.width = max(self._desired_width(), width)
        self.relayout()
        self.restore_position()
        # Re-bind the motion controller to the restored geometry so a pet
        # switch does not leave it with a stale size/workarea/position.
        self.motion.set_position(*self.get_position())
        self.motion.set_size(self.width, self.height)
        self._sync_motion_workarea(*self.get_position())
        self.refresh(reset_animation=True)

    def open_chooser(self):
        from .library_ui import PetChooser
        if self.chooser is not None:
            self.chooser.destroy()
        self.chooser = PetChooser(self, self.library, self.select_pet)
        self.chooser.present()

    def open_onboarding(self):
        from .onboarding_ui import OpenCodeSetup
        try:
            dialog = OpenCodeSetup(self, self.socket_path, self.settings.read())
            dialog.present()
        except (OSError, ValueError, RuntimeError) as error:
            self.plugins.show_error(str(error))

    def toggle_messages_collapsed(self, collapsed):
        collapsed = bool(collapsed)
        if self.appearance.get("messagesCollapsed") == collapsed:
            return
        self.appearance["messagesCollapsed"] = collapsed
        try:
            self.settings.update({"display": {"messagesCollapsed": collapsed}})
        except (OSError, ValueError) as error:
            self.appearance["messagesCollapsed"] = not collapsed
            self.plugins.show_error(f"Could not save message display preference: {error}")
            return
        if collapsed:
            self.unread_message_count = len(self.message_rows)
            # Collapsing an already populated message stack should preview its
            # current notifications too; otherwise the new compact view looks
            # empty until another notify arrives.
            now = time.monotonic()
            self.preview_messages.clear()
            for message in self.message_rows:
                thread_id = message.get("threadId")
                if not isinstance(thread_id, str) or not thread_id:
                    continue
                entry = dict(message)
                entry["expires_at"] = now + self._preview_ttl()
                self.preview_messages[thread_id] = entry
            self._reschedule_preview_sweep()
        else:
            self.unread_message_count = 0
            self._known_message_ids = {str(message.get("threadId", "")) for message in self.message_rows}
            self.preview_messages.clear()
            self._reschedule_preview_sweep()
        self._sync_motion_task_state()
        self.relayout()
        self.update_input_shape()
        self.queue_draw()

    def dismiss_message(self, thread_id):
        self.state.apply({"type": "clearMessage", "threadId": thread_id})
        self.preview_messages.pop(thread_id, None)
        self.refresh()

    # -- collapsed-message previews -------------------------------------

    def _preview_ttl(self) -> float:
        """Return the configured preview lifetime, defaulting to five seconds."""
        try:
            value = float(self.collapsed_message_preview_seconds)
        except (TypeError, ValueError):
            return 5.0
        return value if math.isfinite(value) and value > 0 else 5.0

    def _add_preview(self, notification: dict) -> None:
        """Add/update one collapsed preview and reset only its own timer."""
        if not self.appearance.get("messagesCollapsed"):
            return
        thread_id = notification.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            return
        entry = dict(notification)
        entry["expires_at"] = time.monotonic() + self._preview_ttl()
        self.preview_messages.pop(thread_id, None)
        self.preview_messages[thread_id] = entry
        # A new collapsed notification must not arrive into a visually hidden
        # state: briefly reveal its preview/control even if the user has not
        # hovered the pet since the auto-hide timer expired.
        self._show_message_toggle()
        if not self._pointer_in_pet:
            self._schedule_toggle_hide()
        self._reschedule_preview_sweep()
        self._sync_motion_task_state()
        self.relayout()
        self.update_input_shape()
        self.queue_draw()

    def _reschedule_preview_sweep(self) -> None:
        if self.preview_source:
            GLib.source_remove(self.preview_source)
            self.preview_source = 0
        if self.preview_messages:
            self.preview_source = GLib.timeout_add_seconds(1, self._preview_sweep)

    def _cancel_chat_bubble_timer(self):
        if self.chat_bubble_source:
            GLib.source_remove(self.chat_bubble_source)
            self.chat_bubble_source = 0

    def _schedule_chat_bubble_advance(self):
        self._cancel_chat_bubble_timer()
        if self.chat_bubble_enabled and self.plugins.chat_bubble.current is not None:
            self.chat_bubble_source = GLib.timeout_add_seconds(
                self._chat_bubble_ttl(), self._advance_chat_bubble
            )

    def _chat_bubble_ttl(self) -> int:
        """Return chat queue duration independently of message preview lifetime."""
        try:
            value = int(round(float(self.chat_bubble_duration_seconds)))
        except (TypeError, ValueError, OverflowError):
            return 5
        return max(1, min(60, value))

    def _advance_chat_bubble(self):
        self.chat_bubble_source = 0
        if not self.chat_bubble_enabled:
            return GLib.SOURCE_REMOVE
        self.plugins.chat_bubble.advance()
        if not self._chat_bubble_queue_active():
            # Keep expired collapsed previews in place while any comic bubble
            # is visible, then remove them in the same relayout that clears the
            # final bubble so the pet and bubble do not jump mid-message.
            self._remove_expired_previews()
        self._sync_motion_task_state()
        self.relayout()
        self.update_input_shape()
        self.queue_draw()
        if self.plugins.chat_bubble.current is not None:
            self._schedule_chat_bubble_advance()
        return GLib.SOURCE_REMOVE

    def _preview_sweep(self):
        self.preview_source = 0
        now = time.monotonic()
        removed_expired = self._remove_expired_previews(now)
        if removed_expired:
            self._sync_motion_task_state()
            self.relayout()
            self.update_input_shape()
            self.queue_draw()
        if self.preview_messages:
            self._reschedule_preview_sweep()
            # Rescheduling creates the next source; keeping this one alive too
            # doubles the timers every second while a queued bubble holds previews.
        return GLib.SOURCE_REMOVE

    def _chat_bubble_queue_active(self):
        queue = getattr(getattr(self, "plugins", None), "chat_bubble", None)
        return self.chat_bubble_enabled and queue is not None and (
            queue.current is not None or queue.pending_count > 0
        )

    def _remove_expired_previews(self, now=None):
        """Remove expired previews unless a comic bubble still owns the layout."""
        if self._chat_bubble_queue_active():
            return False
        if now is None:
            now = time.monotonic()
        expired = [
            thread_id for thread_id, entry in self.preview_messages.items()
            if now >= entry["expires_at"]
        ]
        for thread_id in expired:
            del self.preview_messages[thread_id]
        return bool(expired)

    def open_notifications(self):
        from .notification_ui import NotificationCenter
        if self.notification_center is None or self.notification_center.closed:
            self.notification_center = NotificationCenter(self, self.history, self.dismiss_message)
        self.notification_center.present()

    def _install_mcp_indicator_styles(self):
        """Give the checked menu indicator distinct pending/active colors."""
        self.mcp_css_provider = Gtk.CssProvider()
        self.mcp_css_provider.load_from_data(b"""
            menuitem.mcp-pending:checked check { color: #e6a700; }
            menuitem.mcp-active:checked check { color: #28a745; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), self.mcp_css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self.mcp_item.set_tooltip_text("MCP server is off")

    def _set_mcp_indicator(self, state, detail=None):
        previous_state = self._mcp_indicator_state
        style = self.mcp_item.get_style_context()
        for css_class in ("mcp-pending", "mcp-active"):
            style.remove_class(css_class)
        self._mcp_indicator_state = state
        if state == "pending":
            style.add_class("mcp-pending")
            label = "MCP server (waiting — retrying)"
            tooltip = detail or "Waiting for the MCP port to be released; OpenPets will retry automatically."
        elif state == "active":
            style.add_class("mcp-active")
            label = "MCP server (loopback)"
            tooltip = detail or "MCP server is listening and ready."
        else:
            label = "MCP server (loopback)"
            tooltip = detail or "MCP server is off"
        self.mcp_item.set_label(label)
        self.mcp_item.set_tooltip_text(tooltip)
        if state != previous_state:
            log = logger.warning if state == "pending" else logger.info
            log("MCP state changed %s -> %s", previous_state, state)

    def _cancel_mcp_retry(self):
        if self.mcp_retry_source:
            GLib.source_remove(self.mcp_retry_source)
            self.mcp_retry_source = 0

    def _schedule_mcp_retry(self):
        if not self.mcp_retry_source and self._mcp_requested:
            self.mcp_retry_source = GLib.timeout_add_seconds(
                MCP_RETRY_INTERVAL_SECONDS, self._retry_mcp_start
            )

    def _fail_mcp_start(self, error):
        logger.error("MCP server startup failed: %s", error)
        self._cancel_mcp_retry()
        self._mcp_requested = False
        self.mcp_process.stop()
        self._set_mcp_indicator("off")
        self.mcp_item.handler_block_by_func(self.on_mcp_toggle)
        self.mcp_item.set_active(False)
        self.mcp_item.handler_unblock_by_func(self.on_mcp_toggle)
        try:
            self.settings.update({"linuxMCPEnabled": False})
        except (OSError, ValueError):
            pass
        self.plugins.show_error(str(error))

    def _attempt_mcp_start(self):
        """Try once; return true while pending and false after success/failure."""
        process = self.mcp_process.process
        if process is not None and process.poll() is not None:
            self._fail_mcp_start(RuntimeError(
                f"MCP server exited during startup (code {process.returncode}). "
                "Check the MCP SDK and server configuration."
            ))
            return False
        try:
            if not self.mcp_process.running():
                self.mcp_process.start(self.socket_path, self.settings.read())
        except MCPPortBusy as error:
            if self._mcp_indicator_state != "pending":
                logger.warning("MCP endpoint unavailable; will retry: %s", error)
            self._set_mcp_indicator("pending", str(error))
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self._fail_mcp_start(error)
            return False

        if self.mcp_process.ready():
            logger.info("MCP server endpoint ready host=%s port=%s", self.mcp_process.host,
                        self.mcp_process.port)
            self._set_mcp_indicator("active")
            return False
        self._set_mcp_indicator("pending", "MCP server is starting; checking until its endpoint is ready.")
        return True

    def _retry_mcp_start(self):
        self.mcp_retry_source = 0
        if not self._mcp_requested or not self.mcp_item.get_active():
            return GLib.SOURCE_REMOVE
        if self._attempt_mcp_start():
            self._schedule_mcp_retry()
        return GLib.SOURCE_REMOVE

    def on_mcp_toggle(self, item):
        try:
            if item.get_active():
                logger.info("MCP server enabled by user")
                self._mcp_requested = True
                self._set_mcp_indicator("pending", "Starting the MCP server and checking for readiness.")
                self.settings.update({"linuxMCPEnabled": True})
                if self._attempt_mcp_start():
                    self._schedule_mcp_retry()
            else:
                logger.info("MCP server disabled by user")
                self._mcp_requested = False
                self._cancel_mcp_retry()
                self.mcp_process.stop()
                self.settings.update({"linuxMCPEnabled": False})
                self._set_mcp_indicator("off")
        except (OSError, ValueError) as error:
            logger.error("could not change MCP server state: %s", error)
            self._fail_mcp_start(error)

    def handle_command(self, command):
        if isinstance(command, dict):
            kind = command.get("type")
            if kind == "getState":
                return {"ok": True, "animation": self.state.animation, "activePet": self.pet_id,
                        "position": list(self.get_position()), "messages": list(self.state.messages.values())[-3:],
                        "messageCount": len(self.state.messages),
                        "historyCount": len(self.history.items()), "plugins": self.plugins.surfaces(),
                        "mcpRunning": self.mcp_process.ready(),
                        "mcpPending": self._mcp_requested and self._mcp_indicator_state == "pending"}
            if kind == "showPet":
                self.show_all()
                return {"ok": True}
            if kind == "listPets":
                return {"ok": True, "pets": self.library.list_pets()}
            if kind == "selectPet":
                self.select_pet(command.get("id"))
                return {"ok": True, "message": "Selected " + self.pet_id}
            if kind == "setPlugin":
                self.plugins.set_enabled(command.get("name"), command.get("enabled"))
                return {"ok": True}
            if kind == "notify":
                response = self.state.apply(command)
                if response.get("ok"):
                    notification = self.state.messages.get(response.get("threadId"))
                    if notification is not None and self.appearance.get("messagesCollapsed"):
                        self._add_preview(notification)
                    if notification is not None and self.chat_bubble_enabled:
                        if self.plugins.chat_bubble.enqueue(notification):
                            self._schedule_chat_bubble_advance()
                            # enqueue() makes the first item current immediately;
                            # sync its separate overlay now rather than waiting
                            # until the expiry timer advances the queue.
                            self._sync_motion_task_state()
                            self.relayout()
                            self.update_input_shape()
                            self.queue_draw()
                return response
        return self.state.apply(command)

    def setup_tray(self):
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return  # No session bus in headless tests; right-click remains available.
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3
            self.indicator = AyatanaAppIndicator3.Indicator.new(
                "openpets-linux", "face-smile", AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS
            )
            self.indicator.set_menu(self.menu)
            self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        except (ImportError, ValueError):
            # A missing tray must not hide the only way to quit: right-click the pet.
            pass

    def schedule_frame(self):
        if self.frame_source:
            GLib.source_remove(self.frame_source)
        self.frame_source = GLib.timeout_add(
            DURATIONS[self.current_animation][self.frame], self.advance_frame
        )

    def advance_frame(self):
        self.frame_source = 0
        animation = self.visible_animation()
        if animation != self.current_animation:
            self.current_animation = animation
            self.frame = 0
        else:
            self.frame += 1
            if self.frame >= len(DURATIONS[self.current_animation]):
                self.frame = 0
                if not self.drag_active and self.cycles_remaining is not None:
                    self.cycles_remaining -= 1
                if not self.drag_active and (not self.state.loop or self.cycles_remaining == 0):
                    self.state.stop_animation()
                    self.current_animation = self.state.animation
                    self.cycles_remaining = None
                    self.schedule_idle()
        self.queue_draw()
        self.schedule_frame()
        return GLib.SOURCE_REMOVE

    def refresh(self, reset_animation=False):
        animation = self.visible_animation()
        if reset_animation or self.current_animation != animation:
            if self.frame_source:
                GLib.source_remove(self.frame_source)
                self.frame_source = 0
            self.current_animation = animation
            self.frame = 0
            if not reset_animation:
                self.cycles_remaining = None
            self.schedule_frame()
        self.message_rows = list(self.state.messages.values())[-3:]
        self._sync_motion_task_state()
        current_ids = {str(message.get("threadId", "")) for message in self.message_rows}
        if self.appearance.get("messagesCollapsed"):
            self.unread_message_count = min(
                999, self.unread_message_count + len(current_ids - self._known_message_ids)
            )
        self._known_message_ids = current_ids
        if not self.message_rows:
            self.unread_message_count = 0
        self.relayout()
        self.history.sync(self.state.messages)
        if self.notification_center is not None and not self.notification_center.closed:
            self.notification_center.refresh()
        self.update_input_shape()
        self.queue_draw()
        if self._idle_eligible():
            if not self.idle_source:
                self.schedule_idle()
        elif self.idle_source:
            GLib.source_remove(self.idle_source)
            self.idle_source = 0

    def _sync_motion_task_state(self):
        """Keep motion paused while tasks or their transient messages remain visible."""
        task_active = has_active_task(self.state.messages)
        preview_visible = (
            self.appearance.get("messagesCollapsed") and bool(self.preview_messages)
        )
        bubble_visible = self._chat_bubble_queue_active()
        should_freeze = task_active or preview_visible or bubble_visible
        if should_freeze != self._task_motion_frozen:
            self._task_motion_frozen = should_freeze
            self.motion.set_task_active(should_freeze)

    def visible_animation(self):
        return self.drag_animation or self.motion_animation or self.state.animation

    def relayout(self):
        previous_top = self.layout.pet_top
        previous_width = self.layout.width
        previous_pet_x = self.layout.pet_x
        visible_messages, visible_previews = message_surfaces_for_layout(
            self.message_rows, self.preview_messages.values()
        )
        self.width = self._desired_width()
        self.layout = compute_layout(self.width, self.cell_width, self.cell_height,
                                     self.plugins.surfaces(), visible_messages,
                                     theme_mode=self.appearance["themeMode"],
                                     plugin_opacity=self.appearance["pluginOpacity"],
                                      message_opacity=self.appearance["messageOpacity"],
                                      chat_bubble_opacity=self.appearance["chatBubbleOpacity"],
                                     border_radius=self.appearance["borderRadius"],
                                     messages_collapsed=self.appearance["messagesCollapsed"],
                                     unread_count=self.unread_message_count,
                                     message_max_width=self.message_bubble_width,
                                      message_font_size=self.message_font_size,
                                      plugin_side=self.plugin_side,
                                       preview_messages=visible_previews,
                                       chat_font_size=self.chat_bubble_font_size,
                                       chat_aspect_ratio=self.chat_bubble_aspect_ratio,
                                       chat_corner_radius_percent=self.chat_bubble_corner_radius,
                                         chat_message=None)
        if self.height != self.layout.total_height or previous_width != self.width:
            x, y = self.get_position()
            # Preserve sprite screen anchor when content changes above/below
            # the sprite (chips appear, messages appear/disappear) or the
            # window width changes. Adjust so window_x + pet_top stays constant.
            y += previous_top - self.layout.pet_top
            x += previous_pet_x - self.layout.pet_x
            self.height = self.layout.total_height
            self.resize(self.width, self.height)
            # Clamp the whole layout (chips above pet, messages below) inside
            # the monitor workarea. total_height includes both bands.
            self.motion.set_size(self.width, self.height)
            display = self.get_display()
            monitor = display.get_monitor_at_point(x, y)
            if monitor is None:
                monitor = display.get_primary_monitor()
            if monitor is not None:
                area = monitor.get_workarea()
                x, y = clamp_position_to_workarea(
                    x, y, self.width, self.height,
                    (area.x, area.y, area.width, area.height),
                )
                self.motion.set_workarea((area.x, area.y, area.width, area.height))
            self.move(x, y)
            self.motion.set_position(x, y)
        self._sync_chat_bubble_overlay()

    def _sync_chat_bubble_overlay(self):
        overlay = getattr(self, "chat_bubble_overlay", None)
        if overlay is None:
            return
        if not self.chat_bubble_enabled or not self.get_visible():
            overlay.set_parent_visible(False)
            return
        message = self.plugins.chat_bubble.current
        if message is None:
            overlay.set_bubble(None, pet_x=0, pet_y=0, cell_width=0,
                               cell_height=0, workarea=None, plugin_side=self.plugin_side)
            return
        window_x, window_y = self.get_position()
        pet_x = window_x + self.layout.pet_x
        pet_y = window_y + self.layout.pet_top
        display = self.get_display()
        monitor = display.get_monitor_at_point(pet_x, pet_y) or display.get_primary_monitor()
        area = monitor.get_workarea() if monitor is not None else None
        workarea = (area.x, area.y, area.width, area.height) if area is not None else None
        overlay.set_bubble(
            message, pet_x=pet_x, pet_y=pet_y,
            cell_width=self.cell_width, cell_height=self.cell_height,
            workarea=workarea, plugin_side=self.plugin_side,
            font_size=self.chat_bubble_font_size,
            aspect_ratio=self.chat_bubble_aspect_ratio,
            corner_radius_percent=self.chat_bubble_corner_radius,
            opacity=self.appearance["chatBubbleOpacity"],
        )

    def update_input_shape(self):
        if self.get_window() is None:
            return
        # Outside these rectangles clicks reach the desktop. Alpha-pixel precision
        # within the sprite is not implemented in this initial Linux backend.
        region = cairo.Region()
        for rect in input_rectangles(
            self.layout, include_toggle=self.message_toggle_visible
        ):
            region.union(rect)
        self.input_shape_combine_region(region)

    def _point_in_pet(self, x, y):
        rect = self.layout.pet_rect
        return rect.x <= x < rect.x + rect.width and rect.y <= y < rect.y + rect.height

    def _cancel_toggle_hide(self):
        if self._toggle_hide_source:
            GLib.source_remove(self._toggle_hide_source)
            self._toggle_hide_source = 0

    def _schedule_toggle_hide(self):
        self._cancel_toggle_hide()
        if not self.drag_active:
            self._toggle_hide_source = GLib.timeout_add(
                MESSAGE_TOGGLE_AUTO_HIDE_MS, self._hide_message_toggle
            )

    def _hide_message_toggle(self):
        self._toggle_hide_source = 0
        if not self._pointer_in_pet and not self.drag_active and self.message_toggle_visible:
            self.message_toggle_visible = False
            self.update_input_shape()
            self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _show_message_toggle(self):
        self._cancel_toggle_hide()
        if not self.message_toggle_visible:
            self.message_toggle_visible = True
            self.update_input_shape()
            self.queue_draw()

    def on_pointer_enter(self, _widget, event):
        if self._point_in_pet(event.x, event.y):
            self._pointer_in_pet = True
            self._show_message_toggle()
        return False

    def on_pointer_leave(self, *_args):
        if self.drag_active:
            return False
        self._pointer_in_pet = False
        self._schedule_toggle_hide()
        return False

    def _track_toggle_pointer(self, x, y):
        inside = self._point_in_pet(x, y)
        if inside:
            self._pointer_in_pet = True
            self._show_message_toggle()
        elif self._pointer_in_pet:
            self._pointer_in_pet = False
            self._schedule_toggle_hide()

    def on_draw(self, _widget, context):
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.set_source_rgba(0, 0, 0, 0)
        context.paint()
        context.set_operator(cairo.OPERATOR_OVER)
        row = list(ANIMATIONS).index(self.current_animation)
        column = self.frame
        if self.drag_active and self.look_direction_enabled and self.look_direction is not None:
            row, column = self.look_direction
        context.save()
        context.translate(self.layout.pet_x, self.layout.pet_top)
        context.rectangle(0, 0, self.cell_width, self.cell_height)
        context.clip()
        Gdk.cairo_set_source_pixbuf(context, self.atlas,
                                    -column * self.cell_width, -row * self.cell_height)
        context.paint()
        context.restore()
        draw_overlay(
            context, self.layout, show_toggle=self.message_toggle_visible,
            draw_chat=False,
        )
        return False

    def on_button(self, _widget, event):
        if event.button == 3:
            self.menu.popup_at_pointer(event)
        elif event.button == 1:
            hit = hit_test(
                self.layout, event.x, event.y,
                include_toggle=self.message_toggle_visible,
            )
            if hit is not None and hit.kind == "chip":
                self.plugins.show_details()
            elif hit is not None and hit.kind == "message-expand":
                self.toggle_messages_collapsed(False)
            elif hit is not None and hit.kind == "message-collapse":
                self.toggle_messages_collapsed(True)
            elif hit is not None and hit.kind == "message":
                self.open_notifications()
            elif hit is not None and hit.kind == "pet":
                self.begin_drag(event)
        return True

    def begin_drag(self, event):
        """Start a pointer-followed drag and remember the animation to restore."""
        self.drag_active = True
        self.drag_start = (int(event.x_root), int(event.y_root))
        self.drag_last = self.drag_start
        self.drag_origin = (*self.drag_start, *self.get_position())
        self.drag_animation = None
        self.look_direction = (9, 0) if self.look_direction_enabled else None
        # Sync the motion controller to the drag-start position so resume
        # re-anchors correctly; pause autonomous movement during the drag.
        self.motion.set_position(*self.get_position())
        self.motion.pause()
        # Cancel the current autonomous gait/action (running-left/right) so a
        # stale turn animation does not remain visible during or after drag.
        self.motion_animation = None
        self.schedule_idle()
        # Direction is applied on the first motion event; keep drawing until then.
        self.queue_draw()

    def on_motion_notify(self, _widget, event):
        """V2 pets look toward the instantaneous drag motion vector; legacy pets retain window dragging."""
        if not self.drag_active:
            self._track_toggle_pointer(event.x, event.y)
            return False
        if hasattr(event, "state") and not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            self.end_drag()
            return True
        x = int(event.x_root)
        y = int(event.y_root)
        sx, sy, px, py = self.drag_origin
        dx = x - self.drag_start[0]
        dy = y - self.drag_start[1]
        # Facing direction uses the instantaneous pointer velocity (current root
        # coords minus previous root coords), not displacement from the drag
        # origin. drag_last is seeded from drag_start in begin_drag so the first
        # motion event reports motion from press to first sample.
        vx = x - self.drag_last[0]
        vy = y - self.drag_last[1]
        self.drag_last = (x, y)
        if self.look_direction_enabled:
            # Zero/no movement: keep the last gaze so the pet does not snap to a
            # default pose while the cursor is stationary mid-drag.
            if vx != 0 or vy != 0:
                self.look_direction = look_direction_cell(vx, vy)
            # V2 pets follow the pointer while displaying the matching gaze pose.
            self.move(px + x - sx, py + y - sy)
            self.queue_draw()
            return True
        self.move(px + x - sx, py + y - sy)
        # Legacy 8x9 atlases (including Jinhsi) have directional running rows
        # rather than the V2 look-around grid. Use instantaneous pointer motion
        # so reversing direction while dragging immediately flips the pet.
        anim = drag_animation(dx, dy)
        if anim != "idle":
            self.drag_start = (x, y)
            if anim != self.drag_animation:
                first_motion = self.drag_animation is None
                self.drag_animation = anim
                if first_motion:
                    self.refresh(reset_animation=True)
                else:
                    self.current_animation = anim
                    self.frame %= len(DURATIONS[anim])
                    self.queue_draw()
        return True

    def on_button_release(self, _widget, event):
        """End a drag and restore the animation that was active before it."""
        if event.button == 1 and self.drag_active:
            self.end_drag()
        return True

    def end_drag(self):
        self.drag_active = False
        self.drag_start = None
        self.drag_last = None
        self.drag_origin = None
        self.drag_animation = None
        self.look_direction = None
        self.state.expire()
        self.refresh(reset_animation=True)
        # Re-anchor motion controller to the drag-end window position so
        # autonomous movement resumes from here (not the drag start).
        self.motion.set_position(*self.get_position())
        self.motion.reanchor_after_drag()
        self.motion.resume()
        self._reschedule_motion_timer()

    # -- autonomous movement -------------------------------------------

    def _setup_motion(self):
        """Bind the motion controller to window geometry and restore mode."""
        x, y = self.get_position()
        self.motion.set_position(x, y)
        self.motion.set_size(self.width, self.height)
        self._sync_motion_workarea(x, y)
        # Restore persisted motion mode (default Off).
        saved = self.settings.read().get("motionMode", MODE_OFF)
        if is_valid_mode(saved):
            if saved == MODE_FOLLOW:
                self.motion_follow_item.set_active(True)
            elif saved == MODE_FREE_ROAM:
                self.motion_free_item.set_active(True)
            else:
                self.motion_off_item.set_active(True)
        self._reschedule_motion_timer()

    def _on_motion_toggled(self, item, mode):
        """User toggled a movement mode radio item (mutually exclusive)."""
        if not item.get_active():
            return
        if not is_valid_mode(mode):
            return
        self.motion.set_mode(mode)
        # Persist via settings (only valid modes are stored).
        try:
            self.settings.update({"motionMode": mode})
        except (OSError, ValueError):
            pass
        self._reschedule_motion_timer()
        # Reflect the new gait immediately if already moving.
        if self.motion.is_moving():
            self._apply_motion_gait()
        self.queue_draw()

    def _reschedule_motion_timer(self):
        """Start or stop the ~30 Hz motion tick based on mode/visibility."""
        if self.motion_source:
            GLib.source_remove(self.motion_source)
            self.motion_source = 0
        if self.motion.mode != MODE_OFF and self.get_visible():
            self.motion_source = GLib.timeout_add(
                MOTION_INTERVAL_MS, self.on_motion_tick
            )

    def _on_hide(self):
        """Window hidden: stop moving and the motion timer."""
        if self.motion.mode != MODE_OFF:
            self.motion.pause()
        if self.motion_source:
            GLib.source_remove(self.motion_source)
            self.motion_source = 0
        overlay = getattr(self, "chat_bubble_overlay", None)
        if overlay is not None:
            overlay.set_parent_visible(False)

    def _on_show(self):
        """Window shown: resume the motion timer if a mode is active."""
        if self.motion.mode != MODE_OFF:
            self.motion.resume()
        self._reschedule_motion_timer()
        self._sync_chat_bubble_overlay()

    def _query_cursor_position(self):
        """Query the global pointer position. Returns (x, y) or None."""
        try:
            display = Gdk.Display.get_default()
            if display is None:
                return None
            seat = display.get_default_seat()
            device = seat.get_pointer()
            result = device.get_position()
            if result is not None:
                _screen, x, y = result
                return (int(x), int(y))
        except Exception:
            pass
        return None

    def on_motion_tick(self, now=None):
        """~30 Hz autonomous motion tick. Move window, update gait, settle.

        ``now`` (seconds) is injected for deterministic tests; in production
        ``None`` uses the motion controller's monotonic clock.
        """
        # Do not move hidden windows.
        if not self.get_visible():
            self.motion.pause()
            if self.motion_animation is not None:
                self.motion_animation = None
                self.refresh(reset_animation=True)
            return GLib.SOURCE_CONTINUE

        # Refresh cursor target for follow mode. The target is expressed in
        # window top-left space such that the sprite center (not the window
        # corner) approaches the cursor; the deadzone is then measured at the
        # sprite. ``cursor`` and ``get_position()`` are both screen coords, so
        # the desired window top-left is ``cursor - sprite_center_offset`` --
        # do NOT add ``win_x`` again (that double-counts and flips direction).
        cursor = self._query_cursor_position()
        if cursor is not None:
            cx = self.layout.pet_x + self.cell_width / 2.0
            cy = self.layout.pet_top + self.cell_height / 2.0
            target_x = cursor[0] - cx
            target_y = cursor[1] - cy
            self.motion.set_cursor(target_x, target_y)

        result = self.motion.tick(now)
        if result is None:
            # Not moving: ensure settled state and possibly save position.
            if self.motion.is_settled() and self.motion_settled_save_source == 0:
                self._schedule_settled_save()
            # Clear any motion animation override so the underlying
            # task/idle animation shows through at rest / off / paused.
            if self.motion_animation is not None:
                self.motion_animation = None
                self.refresh(reset_animation=True)
            return GLib.SOURCE_CONTINUE

        position, gait, settled = result
        self.move(position[0], position[1])

        # Apply gait override (running-left/running-right); None = no override.
        if self.state.animation == "running":
            # Running status is authoritative while the task is active; roaming
            # may still move the window, but must not replace its run animation.
            if self.motion_animation is not None:
                self.motion_animation = None
                self.refresh(reset_animation=True)
        elif gait is not None and gait != self.motion_animation:
            self.motion_animation = gait
            self.refresh(reset_animation=True)
        elif gait is None and self.motion_animation is not None:
            self.motion_animation = None
            self.refresh(reset_animation=True)

        # Save position when settled, debounced (not every tick).
        if settled:
            self._schedule_settled_save()

        return GLib.SOURCE_CONTINUE

    def _apply_motion_gait(self):
        """Apply the current motion gait to the visible animation."""
        gait = self.motion.state.gait
        if gait != self.motion_animation:
            self.motion_animation = gait
            self.refresh(reset_animation=True)

    def _schedule_settled_save(self):
        """Debounced position save once autonomous movement settles."""
        if self.motion_settled_save_source:
            GLib.source_remove(self.motion_settled_save_source)
        self.motion_settled_save_source = GLib.timeout_add(
            400, self._save_settled_position
        )

    def _save_settled_position(self):
        self.motion_settled_save_source = 0
        try:
            x, y = self.get_position()
            self.settings.set_position(self.pet_id, x, y)
        except (OSError, ValueError):
            pass
        return GLib.SOURCE_REMOVE

    # -- spontaneous idle actions ---------------------------------------

    def _idle_eligible(self) -> bool:
        """True when spontaneous idle actions may run.

        Blocked while dragging, while autonomous movement is active, or while
        any status-driven / manual animation is playing (state.animation !=
        "idle"). Lingering messages whose animation already returned to idle
        do not block.
        """
        return (
            not self.state.stopping
            and not self.drag_active
            and not self.motion.is_moving()
            and self.state.animation == "idle"
        )

    def schedule_idle(self):
        """Schedule the next spontaneous-idle check (rearming as needed).

        A gesture only fires when the pet is idle: not dragging, not stopping,
        not actually moving (roam legs are preserved), and not running a
        status-driven/manual animation. When a gesture just fired and the
        cooldown is still active, re-check after it clears instead of hammering
        idle checks between gestures.
        """
        if self.idle_source:
            GLib.source_remove(self.idle_source)
            self.idle_source = 0
        if not self._idle_eligible():
            return GLib.SOURCE_REMOVE
        remaining = self.idle_picker.cooldown_remaining_ms()
        if remaining > 0:
            self.idle_source = GLib.timeout_add(remaining, self.on_idle_tick)
            return GLib.SOURCE_REMOVE
        interval = self.idle_picker.next_interval_ms(IDLE_INTERVAL_MIN_MS, IDLE_INTERVAL_MAX_MS)
        self.idle_source = GLib.timeout_add(interval, self.on_idle_tick)
        return GLib.SOURCE_REMOVE

    def on_idle_tick(self):
        """Fire one spontaneous-idle check, then reschedule."""
        self.idle_source = 0
        if self._idle_eligible():
            action = self.idle_picker.pick()
            if action != "idle":
                self.state.apply(
                    {"type": "playAnimation", "name": action, "loop": False}
                )
                self.refresh(reset_animation=True)
        self.schedule_idle()
        return GLib.SOURCE_REMOVE

    def on_accept(self, _source, _condition):
        try:
            client, _ = self.listener.accept()
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        client.setblocking(False)
        if len(self.connections) >= 8:
            client.close()
            return GLib.SOURCE_CONTINUE
        fd = client.fileno()
        self.connections[fd] = {"socket": client, "buffer": bytearray(),
                                "deadline": time.monotonic() + 2, "watch": 0}
        self.connections[fd]["watch"] = GLib.io_add_watch(
            fd, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_read
        )
        return GLib.SOURCE_CONTINUE

    def close_connection(self, fd, remove_source=True):
        connection = self.connections.pop(fd, None)
        if connection:
            if remove_source:
                GLib.source_remove(connection["watch"])
            connection["socket"].close()

    def on_read(self, fd, condition):
        connection = self.connections[fd]
        started = time.perf_counter()
        command_type = "unknown"
        try:
            chunk = connection["socket"].recv(min(4096, 65537 - len(connection["buffer"])))
            if not chunk:
                self.close_connection(fd, False)
                return GLib.SOURCE_REMOVE
            connection["buffer"].extend(chunk)
            if len(connection["buffer"]) > 65536:
                raise ValueError("Command exceeds 64 KiB")
            if b"\n" not in connection["buffer"]:
                return GLib.SOURCE_CONTINUE
            command = json.loads(connection["buffer"].split(b"\n", 1)[0])
            command_type = (
                str(command.get("type", "unknown"))
                if isinstance(command, dict) else "invalid"
            )
            response = self.handle_command(command)
            reset = (isinstance(command, dict) and response.get("ok") and
                     command.get("type") in ("notify", "playAnimation", "stopAnimation"))
            if reset:
                status = (command["notification"]["status"].lower()
                          if command["type"] == "notify" else "")
                self.cycles_remaining = 3 if status in FINITE_STATUS_ANIMATIONS else None
            self.refresh(reset_animation=reset)
            if command_type not in ("ping", "getState"):
                status = ""
                if command_type == "notify":
                    notification = command.get("notification", {})
                    if isinstance(notification, dict):
                        status = str(notification.get("status", ""))[:32]
                duration = elapsed_ms(started)
                logger.info(
                    "IPC handled type=%s status=%s ok=%s elapsed_ms=%.1f",
                    command_type, status, bool(response.get("ok")), duration,
                )
                if duration >= 100.0:
                    logger.warning(
                        "slow IPC request type=%s elapsed_ms=%.1f",
                        command_type, duration,
                    )
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as error:
            logger.warning("invalid IPC request type=%s: %s", command_type, error)
            response = {"ok": False, "message": str(error)}
        except OSError:
            logger.debug("IPC connection closed type=%s", command_type, exc_info=True)
            self.close_connection(fd, False)
            return GLib.SOURCE_REMOVE
        encoded = json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(encoded) > 65536:
            encoded = b'{"ok":false,"message":"Response exceeds 64 KiB"}\n'
        connection["output"] = bytearray(encoded)
        connection["watch"] = GLib.io_add_watch(
            fd, GLib.PRIORITY_DEFAULT, GLib.IO_OUT | GLib.IO_HUP | GLib.IO_ERR, self.on_write
        )
        return GLib.SOURCE_REMOVE

    def on_write(self, fd, condition):
        connection = self.connections[fd]
        try:
            written = connection["socket"].send(connection["output"])
            del connection["output"][:written]
            if connection["output"]:
                return GLib.SOURCE_CONTINUE
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            pass
        self.close_connection(fd, False)
        if self.state.stopping:
            GLib.idle_add(self.shutdown)
        return GLib.SOURCE_REMOVE

    def sweep(self):
        now = time.monotonic()
        if now - self._last_memory_log >= 60.0:
            self._last_memory_log = now
            log_process_memory(logger, role="app-periodic")
        for fd, connection in list(self.connections.items()):
            if connection["deadline"] <= now:
                self.close_connection(fd)
        before = (self.state.animation, repr(self.state.messages))
        self.state.expire(now)
        if self._mcp_requested and self._mcp_indicator_state == "active" and not self.mcp_process.ready():
            process = self.mcp_process.process
            if process is not None and process.poll() is not None:
                self.mcp_process.process = None
                self.mcp_process.host = None
                self.mcp_process.port = None
            self._set_mcp_indicator("pending", "MCP server is reconnecting; retrying automatically.")
            self._schedule_mcp_retry()
        if before != (self.state.animation, repr(self.state.messages)):
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def shutdown(self):
        logger.info("GUI shutdown requested")
        # Stop child services before leaving the GTK loop. Relying only on the
        # run() finally block can leave MCP behind if shutdown is initiated by
        # a menu/tray action while other GLib sources are still active.
        if not self._shutdown_requested:
            self._shutdown_requested = True
            self._mcp_requested = False
            self._cancel_mcp_retry()
            self.mcp_process.stop()
        # Stop movement timers on shutdown.
        if self.motion_source:
            GLib.source_remove(self.motion_source)
            self.motion_source = 0
        if self.motion_settled_save_source:
            GLib.source_remove(self.motion_settled_save_source)
            self.motion_settled_save_source = 0
        if Gtk.main_level():
            Gtk.main_quit()
        return GLib.SOURCE_REMOVE

    def _drop_source(self, attr_name):
        """Remove a stored GLib source id once and clear the attribute.

        Safe to call repeatedly; already-removed / zero ids are skipped so we
        never emit GLib "Source ID was not found" warnings.
        """
        source = getattr(self, attr_name, 0)
        setattr(self, attr_name, 0)
        if source:
            GLib.source_remove(source)

    def cleanup(self):
        logger.info("GUI cleanup starting")
        # Tear down the listener first so failures in unrelated UI/plugin
        # cleanup cannot orphan the MCP child and keep its port occupied.
        self._mcp_requested = False
        self._cancel_mcp_retry()
        self.mcp_process.stop()
        # Stop every GLib source first, before any teardown (socket close,
        # destroy, plugin teardown) can iterate the main loop and remove
        # sources under us, which would produce "Source ID not found" warnings.
        self._drop_source("position_source")
        self._drop_source("motion_source")
        self._drop_source("motion_settled_save_source")
        self._drop_source("frame_source")
        self._drop_source("idle_source")
        self._drop_source("sweep_source")
        self._drop_source("listener_source")
        self._drop_source("preview_source")
        self._drop_source("chat_bubble_source")
        self._drop_source("mcp_retry_source")
        self._drop_source("_toggle_hide_source")
        for source in list(getattr(self, "signal_sources", [])):
            if source:
                GLib.source_remove(source)
        self.signal_sources = []
        for fd in list(self.connections):
            self.close_connection(fd)

        self.save_position()
        self.plugins.close()
        overlay = getattr(self, "chat_bubble_overlay", None)
        if overlay is not None:
            overlay.destroy()
        if self.listener is not None:
            self.listener.close()
            cleanup_socket(self.socket_path, self.socket_identity)
        self.destroy()
        logger.info("GUI cleanup complete")


def run(pet_directory, scale, socket_path):
    if not Gtk.init_check()[0]:
        raise RuntimeError("Cannot open a display; run from an X11 desktop session")
    window = PetWindow(pet_directory, scale, socket_path)
    try:
        window.start()
        Gtk.main()
    except Exception:
        logger.exception("GUI main loop failed")
        raise
    finally:
        window.cleanup()
