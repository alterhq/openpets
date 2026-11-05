"""Small GTK3 pet chooser; selection is committed only after asset validation."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class PetChooser(Gtk.Dialog):
    def __init__(self, parent, library, select_callback):
        super().__init__(title="Choose an OpenPets companion", transient_for=parent,
                         destroy_with_parent=True)
        self.set_default_size(420, 330)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())
        self.select_callback = select_callback
        area = self.get_content_area()
        area.set_spacing(10)
        area.set_border_width(14)
        explanation = Gtk.Label(label="Choose a local pet. Add bundles to your XDG pet folder.")
        explanation.set_line_wrap(True)
        area.pack_start(explanation, False, False, 0)
        self.preview = Gtk.Image()
        area.pack_start(self.preview, False, False, 0)
        self.choices = Gtk.ComboBoxText()
        self.entries = library.list_pets()
        for index, entry in enumerate(self.entries):
            self.choices.append(str(index), entry["displayName"])
        self.choices.connect("changed", self.on_preview)
        area.pack_start(self.choices, False, False, 0)
        self.status = Gtk.Label()
        self.status.set_line_wrap(True)
        area.pack_start(self.status, False, False, 0)
        activate = Gtk.Button(label="Use this pet")
        activate.connect("clicked", self.on_select)
        area.pack_start(activate, False, False, 0)
        active = str(library.active_pet().resolve())
        selected = next((i for i, entry in enumerate(self.entries)
                         if entry["directory"] == active), 0)
        if self.entries:
            self.choices.set_active(selected)
        self.show_all()

    def on_preview(self, _combo):
        from .pet import load_atlas
        index = self.choices.get_active()
        if index < 0:
            return
        try:
            atlas, width, height = load_atlas(self.entries[index]["directory"], 0.42)
            frame = atlas.new_subpixbuf(0, 0, width, height)
            self.preview.set_from_pixbuf(frame)
            self.status.set_text(self.entries[index]["directory"])
        except Exception:
            self.preview.clear()
            self.status.set_text("Cannot preview this pet. Check its manifest and spritesheet.")

    def on_select(self, _button):
        index = self.choices.get_active()
        if index < 0:
            return
        try:
            self.select_callback(self.entries[index]["id"])
        except (OSError, ValueError, RuntimeError) as error:
            self.status.set_text(str(error))
            return
        self.status.set_text("Pet selected and saved.")
