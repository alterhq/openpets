"""Opt-in plugin scheduling and GTK menu/details; no account access by default."""

import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from . import plugins


PROVIDERS = {"battery": ("Battery", plugins.read_battery),
             "claude": ("Claude Code quota", plugins.read_claude_usage),
             "codex": ("Codex quota", plugins.read_codex_usage)}


class PluginController:
    """At most one daemon worker; GTK mutations are marshalled with idle_add.

    Quota requests only happen after explicit opt-in stored under linuxPlugins.
    No token refresh or credential writes. Disable invalidates in-flight results.
    """

    def __init__(self, parent, settings, changed):
        self.parent = parent
        self.settings = settings
        self.changed = changed
        saved = settings.read().get("linuxPlugins", {})
        self.enabled = {name: isinstance(saved, dict) and saved.get(name) is True
                        for name in PROVIDERS}
        self.snapshots = {}
        self.next_refresh = {}
        self.busy = False
        self.closed = False
        self.generation = 0
        self.refresh_requested = False
        self.timer = 0
        self.dialog = None

    def menu(self):
        menu = Gtk.Menu()
        for name, (label, _) in PROVIDERS.items():
            item = Gtk.CheckMenuItem(label=label)
            item.set_active(self.enabled[name])
            item.connect("toggled", self.on_toggle, name)
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        refresh = Gtk.MenuItem(label="Refresh enabled plugins now")
        refresh.connect("activate", lambda *_: self.refresh_enabled())
        menu.append(refresh)
        details = Gtk.MenuItem(label="Plugin details…")
        details.connect("activate", lambda *_: self.show_details())
        menu.append(details)
        menu.show_all()
        return menu

    def start(self):
        self.timer = GLib.timeout_add_seconds(5, self.tick)
        self.tick()

    def on_toggle(self, item, name):
        enabled = item.get_active()
        if enabled and name != "battery":
            prompt = Gtk.MessageDialog(
                transient_for=self.parent, modal=True,
                message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.YES_NO,
                text=f"Enable {PROVIDERS[name][0]}?"
            )
            prompt.format_secondary_text(
                "This reads this account's local authentication file and contacts its "
                "provider over HTTPS. Tokens are not displayed, logged or refreshed. "
                "The setting persists until you disable this plugin."
            )
            accepted = prompt.run() == Gtk.ResponseType.YES
            prompt.destroy()
            if not accepted:
                item.handler_block_by_func(self.on_toggle)
                item.set_active(False)
                item.handler_unblock_by_func(self.on_toggle)
                return
        try:
            self.set_enabled(name, enabled)
        except (OSError, ValueError, RuntimeError):
            item.handler_block_by_func(self.on_toggle)
            item.set_active(self.enabled[name])
            item.handler_unblock_by_func(self.on_toggle)
            self.show_error("Could not save plugin settings. No new provider request was made.")

    def set_enabled(self, name, enabled):
        if name not in PROVIDERS or not isinstance(enabled, bool):
            raise ValueError("Unknown plugin or invalid enabled flag")
        updated = dict(self.enabled, **{name: enabled})
        self.settings.update({"linuxPlugins": updated})
        self.enabled = updated
        self.generation += 1
        if not enabled:
            self.snapshots.pop(name, None)
        self.next_refresh.pop(name, None)
        self.changed()
        self.tick()

    def refresh_enabled(self):
        """Immediately refresh opted-in plugins; never enables a provider."""
        if self.closed:
            return
        if self.busy:
            # Finish the in-flight read first, then force one fresh read.
            self.refresh_requested = True
            return
        now = time.monotonic()
        for name, enabled in self.enabled.items():
            if enabled:
                self.next_refresh[name] = now
        self.tick()

    def tick(self):
        if self.closed:
            return GLib.SOURCE_REMOVE
        if self.busy:
            return GLib.SOURCE_CONTINUE
        now = time.monotonic()
        due = [name for name, enabled in self.enabled.items()
               if enabled and self.next_refresh.get(name, 0) <= now]
        if not due:
            return GLib.SOURCE_CONTINUE
        self.busy = True
        generation = self.generation

        def worker():
            results = {}
            for name in due:
                if self.closed or generation != self.generation:
                    break
                try:
                    results[name] = PROVIDERS[name][1]()
                except Exception:
                    results[name] = {"id": name, "label": PROVIDERS[name][0],
                                     "value": "Unavailable", "state": "error",
                                     "details": ["Provider could not be read."]}
            if not self.closed:
                GLib.idle_add(self.finish, generation, results)

        threading.Thread(target=worker, name="openpets-plugins", daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def finish(self, generation, results):
        self.busy = False
        if self.closed:
            return GLib.SOURCE_REMOVE
        if generation == self.generation:
            for name, snapshot in results.items():
                if self.enabled[name]:
                    previous = self.snapshots.get(name)
                    self.snapshots[name] = snapshot
                    self.next_refresh[name] = time.monotonic() + (30 if name == "battery" else 60)
                    if (name == "battery" and self.parent.state.animation == "idle" and
                        (previous is None or previous.get("state") != snapshot.get("state"))):
                        reaction = {"critical": "failed", "warning": "waiting", "success": "jumping"}.get(snapshot.get("state"))
                        if reaction:
                            self.parent.state.apply({"type": "playAnimation", "name": reaction,
                                                     "loop": False, "ttlSeconds": 4})
            self.changed()
        if self.refresh_requested:
            self.refresh_requested = False
            for name, enabled in self.enabled.items():
                if enabled:
                    self.next_refresh[name] = 0
            GLib.idle_add(self.tick)
        return GLib.SOURCE_REMOVE

    def surfaces(self):
        surfaces = []
        for name in PROVIDERS:
            if not self.enabled[name]:
                continue
            snapshot = self.snapshots.get(name)
            if snapshot is None:
                surfaces.append({"id": name, "label": PROVIDERS[name][0], "value": "Loading…"})
            else:
                surfaces.extend(snapshot.get("windows") or [snapshot])
        return surfaces[:5]

    def show_details(self):
        if self.dialog is not None:
            self.dialog.destroy()
        dialog = Gtk.Dialog(title="OpenPets plugins", transient_for=self.parent,
                            destroy_with_parent=True)
        self.dialog = dialog
        dialog.set_default_size(400, 300)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.connect("response", lambda *_: dialog.destroy())
        dialog.connect("destroy", self.clear_dialog)
        area = dialog.get_content_area()
        area.set_border_width(14)
        scroll = Gtk.ScrolledWindow()
        label = Gtk.Label()
        label.set_selectable(True)
        label.set_line_wrap(True)
        label.set_xalign(0)
        lines = []
        for name, (title, _) in PROVIDERS.items():
            lines.append(title + (" — enabled" if self.enabled[name] else " — disabled"))
            snapshot = self.snapshots.get(name, {})
            for window in snapshot.get("windows") or [snapshot]:
                if window:
                    lines.append(f"  {window.get('label', title)}: {window.get('value', 'Unavailable')}")
                    lines.extend("  " + str(line) for line in window.get("details", []))
            lines.append("")
        label.set_text("\n".join(lines))
        scroll.add(label)
        area.pack_start(scroll, True, True, 0)
        dialog.show_all()
        dialog.present()

    def clear_dialog(self, dialog):
        if self.dialog is dialog:
            self.dialog = None

    def show_error(self, text):
        dialog = Gtk.MessageDialog(transient_for=self.parent,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text=text)
        dialog.set_destroy_with_parent(True)
        dialog.connect("response", lambda *_: dialog.destroy())
        dialog.show_all()

    def close(self):
        self.closed = True
        self.generation += 1
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.dialog:
            self.dialog.destroy()
