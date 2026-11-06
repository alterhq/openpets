"""GTK3 notification center, with lazy GI imports for headless callers."""

from __future__ import annotations

import logging

from .notifications import safe_action_url

logger = logging.getLogger(__name__)

__all__ = ["NotificationCenter"]


class NotificationCenter:
    """Window showing in-memory notification history.

    Action URLs are opened only after an explicit button click. ``opener``
    may be injected for tests; otherwise Gio launches the default handler.
    """

    def __init__(self, parent, history, clear_callback, opener=None):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gio, Gtk

        self._gtk = Gtk
        self._gio = Gio
        self._history = history
        self._clear_callback = clear_callback
        self._opener = opener
        self.closed = False

        self._window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self._window.set_title("OpenPets Notifications")
        self._window.connect("destroy", self._on_destroy)
        self._window.set_default_size(560, 420)
        self._window.set_border_width(12)
        self._window.set_modal(True)
        self._window.set_destroy_with_parent(True)
        if parent is not None:
            self._window.set_transient_for(parent)

        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._window.add(layout)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        layout.pack_start(scroller, True, True, 0)
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self._list)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        layout.pack_start(controls, False, False, 0)
        clear_button = Gtk.Button(label="Clear History")
        clear_button.set_tooltip_text("Clear notification history for this session")
        clear_button.connect("clicked", self._clear_history)
        controls.pack_start(clear_button, False, False, 0)
        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda *_: self.destroy())
        controls.pack_end(close_button, False, False, 0)

        self.refresh()
        self._window.show_all()

    def refresh(self):
        """Refresh rows from current history."""
        if self.closed:
            return
        Gtk = self._gtk
        for child in self._list.get_children():
            self._list.remove(child)
        for entry in self._history.items():
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.set_margin_top(6)
            row.set_margin_bottom(6)
            row.set_margin_start(8)
            row.set_margin_end(8)
            outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add(outer)
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            body.set_hexpand(True)
            outer.pack_start(body, True, True, 0)

            title = self._text_label(entry.get("title", ""), bold=True)
            body.pack_start(title, False, False, 0)
            text = self._text_label(entry.get("text") or "")
            text.set_line_wrap(True)
            text.set_selectable(True)
            body.pack_start(text, False, False, 0)
            state = entry.get("historyState", "active")
            status = self._text_label(
                f"Status: {entry.get('status', '')} · {state}"
            )
            body.pack_start(status, False, False, 0)

            buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            outer.pack_start(buttons, False, False, 0)
            safe_url = safe_action_url(entry.get("url"))
            if safe_url is not None:
                label = entry.get("buttonLabel") or "Open"
                action = Gtk.Button(label=label)
                action.connect("clicked", self._open_action, safe_url)
                buttons.pack_start(action, False, False, 0)
            thread_id = entry.get("threadId", "")
            dismiss = Gtk.Button(label="Dismiss")
            dismiss.connect("clicked", self._dismiss, thread_id)
            buttons.pack_start(dismiss, False, False, 0)
            self._list.add(row)
        self._list.show_all()

    def present(self):
        self._window.present()

    def destroy(self):
        self._window.destroy()

    def _on_destroy(self, *_args):
        self.closed = True

    def _text_label(self, text, bold=False):
        # Gtk.Label(label=...) treats content as plain text (never markup).
        label = self._gtk.Label(label=str(text))
        label.set_xalign(0)
        label.set_selectable(True)
        # Deliberately do not apply markup: notification content is plain text.
        return label

    def _open_action(self, _button, url):
        # Validate again at the action boundary, even though row creation did so.
        safe_url = safe_action_url(url)
        if safe_url is None:
            return
        if self._opener is not None:
            self._opener(safe_url)
            return
        try:
            # No shell; use desktop's URI handler only after user click.
            self._gio.AppInfo.launch_default_for_uri(safe_url, None)
        except Exception:
            logger.exception("desktop URI handler failed to open a notification action")

    def _dismiss(self, _button, thread_id):
        if self._clear_callback is not None:
            self._clear_callback(thread_id)
        self.refresh()

    def _clear_history(self, _button):
        self._history.clear()
        self.refresh()
