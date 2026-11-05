"""GTK3/X11 pet host. All GUI and socket callbacks run on the GLib loop."""

import json
import math
import os
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

from .core import ANIMATIONS, FINITE_STATUS_ANIMATIONS, State
from .ipc import bind_socket, cleanup_socket
from .pet import load_atlas, sprite_version
from .settings import Settings
from .library import PetLibrary
from .notifications import NotificationHistory
from .mcp_runtime import MCPProcess
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
        self.history = NotificationHistory()
        self.notification_center = None
        self.chooser = None
        self.position_source = 0
        self.mcp_process = MCPProcess()
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
        display_settings = self.settings.read().get("display", {})
        if not isinstance(display_settings, dict):
            display_settings = {}
        self.appearance = {
            "themeMode": display_settings.get("themeMode", "dark"),
            "pluginOpacity": display_settings.get("pluginOpacity", 0.94),
            "messageOpacity": display_settings.get("messageOpacity", 0.94),
            "blurBackground": display_settings.get("blurBackground", False),
            "borderRadius": display_settings.get("borderRadius", 14.0),
            "messagesCollapsed": display_settings.get("messagesCollapsed", False),
        }
        self.unread_message_count = 0
        self._known_message_ids = set()
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
        self.width = max(360, self.cell_width, self.message_bubble_width + 2 * MESSAGE_PAD_X)
        self.layout = compute_layout(
            self.width, self.cell_width, self.cell_height,
            message_max_width=self.message_bubble_width,
            message_font_size=self.message_font_size,
        )
        self.height = self.layout.total_height
        self.set_default_size(self.width, self.height)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_button)
        self.connect("button-release-event", self.on_button_release)
        self.connect("motion-notify-event", self.on_motion_notify)
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
        from .plugin_ui import PluginController
        self.plugins = PluginController(self, self.settings, lambda: self.refresh())
        for label, callback in (("Appearance settings…", self.open_appearance_settings),
                                ("Choose pet…", self.open_chooser),
                                ("Display settings…", self.open_display_settings),
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

    def open_display_settings(self):
        dialog = Gtk.Dialog(title="Display settings", transient_for=self,
                            flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apply", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, margin=16)
        dialog.get_content_area().add(grid)
        controls = (
            ("Pet size", self.scale, 0.2, 2.0, 0.05, 2),
            ("Message bubble width", self.message_bubble_width, 120, 600, 10, 0),
            ("Message font size", self.message_font_size, 6, 24, 1, 0),
        )
        scales = []
        for row, (label, value, minimum, maximum, step, digits) in enumerate(controls):
            text = Gtk.Label(label=label, xalign=0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, step)
            scale.set_digits(digits)
            scale.set_value(value)
            scale.set_hexpand(True)
            scale.set_size_request(260, -1)
            grid.attach(text, 0, row, 1, 1)
            grid.attach(scale, 1, row, 1, 1)
            scales.append(scale)
        dialog.show_all()
        response = dialog.run()
        values = [scale.get_value() for scale in scales]
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            self.apply_display_settings(*values)
        except (OSError, ValueError, RuntimeError) as error:
            self.plugins.show_error(f"Could not apply display settings: {error}")

    def apply_display_settings(self, pet_scale, bubble_width, font_size):
        pet_scale = self._bounded_display_value(pet_scale, self.scale, 0.2, 2.0)
        bubble_width = self._bounded_display_value(bubble_width, self.message_bubble_width,
                                                  120, 600, integer=True)
        font_size = self._bounded_display_value(font_size, self.message_font_size, 6, 24)
        atlas, cell_width, cell_height = load_atlas(self.pet_directory, pet_scale)
        self.settings.update({"display": {
            "scale": pet_scale,
            "messageBubbleWidth": bubble_width,
            "messageFontSize": font_size,
        }})
        self.scale = pet_scale
        self.atlas, self.cell_width, self.cell_height = atlas, cell_width, cell_height
        self.message_bubble_width = bubble_width
        self.message_font_size = font_size
        self.width = max(360, self.cell_width,
                         self.message_bubble_width + 2 * MESSAGE_PAD_X)
        self.relayout()
        self.update_input_shape()
        self.queue_draw()

    def start(self):
        if "X11" not in self.get_display().__gtype__.name:
            raise RuntimeError("This experimental host requires an X11 desktop session")
        if not self.composited:
            print("openpets: no compositor detected; alpha transparency may be unavailable", file=sys.stderr)
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
        self.restore_position()
        self.plugins.start()
        if self.settings.read().get("linuxMCPEnabled") is True:
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
        self.width = max(360, width,
                         self.message_bubble_width + 2 * MESSAGE_PAD_X)
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

    def open_appearance_settings(self):
        dialog = Gtk.Dialog(title="Overlay appearance", transient_for=self,
                            flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apply", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=16)
        dialog.get_content_area().add(grid)
        theme = Gtk.ComboBoxText()
        theme.append("dark", "Dark (black backgrounds)")
        theme.append("light", "Light (white backgrounds)")
        theme.set_active_id(self.appearance["themeMode"] if self.appearance["themeMode"] in ("dark", "light") else "dark")
        grid.attach(Gtk.Label(label="Theme", xalign=0), 0, 0, 1, 1)
        grid.attach(theme, 1, 0, 1, 1)
        controls = []
        for row, (label, key, low, high, step, value) in enumerate((
            ("Plugin background opacity", "pluginOpacity", 0.1, 1.0, 0.05, self.appearance["pluginOpacity"]),
            ("Message background opacity", "messageOpacity", 0.1, 1.0, 0.05, self.appearance["messageOpacity"]),
            ("Border radius", "borderRadius", 0, 32, 1, self.appearance["borderRadius"]),
        ), start=1):
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
            scale.set_digits(2 if "Opacity" in label else 0)
            try:
                scale.set_value(float(value))
            except (TypeError, ValueError):
                scale.set_value(high)
            scale.set_hexpand(True)
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(scale, 1, row, 1, 1)
            controls.append((key, scale))
        blur = Gtk.CheckButton(label="Request compositor background blur")
        blur.set_active(self.appearance["blurBackground"] is True)
        grid.attach(blur, 0, 4, 2, 1)
        blur_note = Gtk.Label(
            label="Blur requires an X11 compositor configured for ARGB-window blur (for example Picom with blur-background). OpenPets cannot enable compositor blur by itself.",
            xalign=0, wrap=True,
        )
        grid.attach(blur_note, 0, 5, 2, 1)
        dialog.show_all()
        response = dialog.run()
        values = {key: scale.get_value() for key, scale in controls}
        values["themeMode"] = theme.get_active_id() or "dark"
        values["blurBackground"] = blur.get_active()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self.apply_appearance_settings(values)

    def apply_appearance_settings(self, values):
        theme = values.get("themeMode", self.appearance["themeMode"])
        if theme not in ("dark", "light"):
            theme = "dark"
        def bounded(key, default, low, high):
            value = values.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return default
            try:
                if not math.isfinite(value):
                    return default
            except OverflowError:
                return default
            return max(low, min(high, float(value)))
        updated = {
            "themeMode": theme,
            "pluginOpacity": bounded("pluginOpacity", .94, .1, 1),
            "messageOpacity": bounded("messageOpacity", .94, .1, 1),
            "blurBackground": values.get("blurBackground") is True,
            "borderRadius": bounded("borderRadius", 14, 0, 32),
        }
        try:
            self.settings.update({"display": updated})
        except (OSError, ValueError) as error:
            self.plugins.show_error(f"Could not save appearance settings: {error}")
            return
        self.appearance.update(updated)
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

    def toggle_messages_collapsed(self, collapsed):
        collapsed = bool(collapsed and self.message_rows)
        if self.appearance.get("messagesCollapsed") == collapsed:
            return
        self.appearance["messagesCollapsed"] = collapsed
        if collapsed:
            self.unread_message_count = len(self.message_rows)
        else:
            self.unread_message_count = 0
            self._known_message_ids = {str(message.get("threadId", "")) for message in self.message_rows}
        try:
            self.settings.update({"display": {"messagesCollapsed": collapsed}})
        except (OSError, ValueError) as error:
            self.appearance["messagesCollapsed"] = not collapsed
            self.plugins.show_error(f"Could not save message display preference: {error}")
            return
        self.relayout()
        self.update_input_shape()
        self.queue_draw()

    def dismiss_message(self, thread_id):
        self.state.apply({"type": "clearMessage", "threadId": thread_id})
        self.refresh()

    def open_notifications(self):
        from .notification_ui import NotificationCenter
        if self.notification_center is None or self.notification_center.closed:
            self.notification_center = NotificationCenter(self, self.history, self.dismiss_message)
        self.notification_center.present()

    def on_mcp_toggle(self, item):
        try:
            if item.get_active():
                self.mcp_process.start(self.socket_path, self.settings.read())
                self.settings.update({"linuxMCPEnabled": True})
            else:
                self.mcp_process.stop()
                self.settings.update({"linuxMCPEnabled": False})
        except (OSError, ValueError, RuntimeError) as error:
            self.mcp_process.stop()
            item.handler_block_by_func(self.on_mcp_toggle)
            item.set_active(False)
            item.handler_unblock_by_func(self.on_mcp_toggle)
            try:
                self.settings.update({"linuxMCPEnabled": False})
            except (OSError, ValueError):
                pass
            self.plugins.show_error(str(error))

    def handle_command(self, command):
        if isinstance(command, dict):
            kind = command.get("type")
            if kind == "getState":
                return {"ok": True, "animation": self.state.animation, "activePet": self.pet_id,
                        "position": list(self.get_position()), "messages": list(self.state.messages.values())[-3:],
                        "messageCount": len(self.state.messages),
                        "historyCount": len(self.history.items()), "plugins": self.plugins.surfaces(),
                        "mcpRunning": self.mcp_process.running()}
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

    def visible_animation(self):
        return self.drag_animation or self.motion_animation or self.state.animation

    def relayout(self):
        previous_top = self.layout.pet_top
        previous_width = self.layout.width
        previous_pet_x = self.layout.pet_x
        self.layout = compute_layout(self.width, self.cell_width, self.cell_height,
                                     self.plugins.surfaces(), self.message_rows,
                                     theme_mode=self.appearance["themeMode"],
                                     plugin_opacity=self.appearance["pluginOpacity"],
                                     message_opacity=self.appearance["messageOpacity"],
                                     border_radius=self.appearance["borderRadius"],
                                     messages_collapsed=self.appearance["messagesCollapsed"],
                                     unread_count=self.unread_message_count,
                                     message_max_width=self.message_bubble_width,
                                     message_font_size=self.message_font_size)
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

    def update_input_shape(self):
        if self.get_window() is None:
            return
        # Outside these rectangles clicks reach the desktop. Alpha-pixel precision
        # within the sprite is not implemented in this initial Linux backend.
        region = cairo.Region()
        for rect in input_rectangles(self.layout):
            region.union(rect)
        self.input_shape_combine_region(region)

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
        draw_overlay(context, self.layout)
        return False

    def on_button(self, _widget, event):
        if event.button == 3:
            self.menu.popup_at_pointer(event)
        elif event.button == 1:
            hit = hit_test(self.layout, event.x, event.y)
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
        self.schedule_idle()
        # Direction is applied on the first motion event; keep drawing until then.
        self.queue_draw()

    def on_motion_notify(self, _widget, event):
        """V2 pets look toward the drag; legacy pets retain window dragging."""
        if not self.drag_active:
            return False
        if hasattr(event, "state") and not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            self.end_drag()
            return True
        x = int(event.x_root)
        y = int(event.y_root)
        sx, sy, px, py = self.drag_origin
        dx = x - self.drag_start[0]
        dy = y - self.drag_start[1]
        self.drag_last = (x, y)
        if self.look_direction_enabled:
            self.look_direction = look_direction_cell(x - sx, y - sy)
            self.queue_draw()
            return True
        self.move(px + x - sx, py + y - sy)
        # Vertical/sub-threshold drag returns "idle" and is deliberately not
        # applied: a mid-drag vertical move preserves the last horizontal gait,
        # and a vertical-only drag falls back to the underlying idle/task
        # animation instead of overriding it with an explicit idle.
        anim = drag_animation(dx, dy)
        if anim != "idle":
            self.drag_start = (x, y)
            if anim != self.drag_animation:
                first_motion = self.drag_animation is None
                self.drag_animation = anim
                if first_motion:
                    self.refresh(reset_animation=True)
                else:
                    # Keep the gait clock moving when direction changes quickly.
                    # Restarting every motion can pin the sprite on frame zero.
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

    def _on_show(self):
        """Window shown: resume the motion timer if a mode is active."""
        if self.motion.mode != MODE_OFF:
            self.motion.resume()
        self._reschedule_motion_timer()

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
        if gait is not None and gait != self.motion_animation:
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
        """Schedule the next spontaneous-idle check (rearming as needed)."""
        if self.idle_source:
            GLib.source_remove(self.idle_source)
            self.idle_source = 0
        if not self._idle_eligible():
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
            response = self.handle_command(command)
            reset = (isinstance(command, dict) and response.get("ok") and
                     command.get("type") in ("notify", "playAnimation", "stopAnimation"))
            if reset:
                status = (command["notification"]["status"].lower()
                          if command["type"] == "notify" else "")
                self.cycles_remaining = 3 if status in FINITE_STATUS_ANIMATIONS else None
            self.refresh(reset_animation=reset)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as error:
            response = {"ok": False, "message": str(error)}
        except OSError:
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
        for fd, connection in list(self.connections.items()):
            if connection["deadline"] <= now:
                self.close_connection(fd)
        before = (self.state.animation, repr(self.state.messages))
        self.state.expire(now)
        if self.mcp_item.get_active() and not self.mcp_process.running():
            self.mcp_item.set_label("MCP server stopped — check port / SDK")
        if before != (self.state.animation, repr(self.state.messages)):
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def shutdown(self):
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
        for source in list(getattr(self, "signal_sources", [])):
            if source:
                GLib.source_remove(source)
        self.signal_sources = []
        for fd in list(self.connections):
            self.close_connection(fd)

        self.save_position()
        self.plugins.close()
        self.mcp_process.stop()
        if self.listener is not None:
            self.listener.close()
            cleanup_socket(self.socket_path, self.socket_identity)
        self.destroy()


def run(pet_directory, scale, socket_path):
    if not Gtk.init_check()[0]:
        raise RuntimeError("Cannot open a display; run from an X11 desktop session")
    window = PetWindow(pet_directory, scale, socket_path)
    try:
        window.start()
        Gtk.main()
    finally:
        window.cleanup()
