"""Read-only OpenCode V2 setup helper; clipboard changes require a button click."""

import json
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from .mcp_runtime import opencode_configuration


class OpenCodeSetup(Gtk.Dialog):
    def __init__(self, parent, socket_path, config):
        super().__init__(title="Connect OpenCode V2", transient_for=parent, destroy_with_parent=True)
        self.set_default_size(570, 420)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())
        area = self.get_content_area()
        area.set_border_width(14)
        area.set_spacing(10)
        hint = Gtk.Label(label=(
            "1. Install the optional SDK with setup-mcp.\n"
            "2. Enable the loopback MCP server in the pet menu.\n"
            "3. Merge this server into OpenCode's mcp.servers settings.\n"
            "4. Check /mcps or opencode mcp list; use /reload if needed.\n\n"
            "This window does not edit your OpenCode configuration."
        ))
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        area.pack_start(hint, False, False, 0)
        self.fragment = json.dumps(opencode_configuration(socket_path, port=config.get("mcpPort", 3001),
                                  endpoint=config.get("mcpEndpoint", "/mcp")), indent=2)
        text = Gtk.TextView()
        text.set_editable(False)
        text.set_monospace(True)
        text.get_buffer().set_text(self.fragment)
        scroll = Gtk.ScrolledWindow()
        scroll.add(text)
        area.pack_start(scroll, True, True, 0)
        copy = Gtk.Button(label="Copy configuration fragment")
        copy.connect("clicked", self.copy_fragment)
        area.pack_start(copy, False, False, 0)
        self.show_all()

    def copy_fragment(self, _button):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self.fragment, -1)
