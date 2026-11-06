import json
import importlib.util
from pathlib import Path
import tempfile
import unittest

from openpets_linux.pet import load_atlas, manifest_image


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pet = self.root / "pet"
        self.pet.mkdir()

    def write_manifest(self, data):
        (self.pet / "pet.json").write_text(json.dumps(data))

    def test_local_image(self):
        (self.pet / "sprite.png").write_bytes(b"fixture")
        self.write_manifest({"spritesheetPath": "sprite.png"})
        self.assertEqual(manifest_image(self.pet), self.pet / "sprite.png")

    def test_parent_traversal(self):
        (self.root / "outside").write_bytes(b"fixture")
        self.write_manifest({"spritesheetPath": "../outside"})
        with self.assertRaises(ValueError):
            manifest_image(self.pet)

    def test_symlink_escape(self):
        (self.root / "outside").write_bytes(b"fixture")
        (self.pet / "sprite.png").symlink_to(self.root / "outside")
        self.write_manifest({"spritesheetPath": "sprite.png"})
        with self.assertRaises(ValueError):
            manifest_image(self.pet)

    def test_invalid_manifest(self):
        for data in ([], {}, {"spritesheetPath": 123}):
            self.write_manifest(data)
            with self.assertRaises(ValueError):
                manifest_image(self.pet)

    @unittest.skipUnless(importlib.util.find_spec("gi"), "GdkPixbuf is unavailable")
    def test_version_two_eleven_row_atlas_loads(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8 * 16, 11 * 16)
        sheet = self.pet / "spritesheet.png"
        pixbuf.savev(str(sheet), "png", [], [])
        self.write_manifest({"id": "cute-yaya", "spritesheetPath": "spritesheet.png",
                             "spriteVersionNumber": 2})
        atlas, cell_width, cell_height = load_atlas(self.pet, 1.0)
        self.assertEqual((cell_width, cell_height), (16, 16))
        self.assertEqual((atlas.get_width(), atlas.get_height()), (8 * 16, 11 * 16))

    @unittest.skipUnless(importlib.util.find_spec("gi"), "GdkPixbuf is unavailable")
    def test_version_two_rejects_nine_row_atlas_with_actionable_version_error(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8 * 16, 9 * 16)
        sheet = self.pet / "spritesheet.png"
        pixbuf.savev(str(sheet), "png", [], [])
        self.write_manifest({"id": "cute-yaya", "spritesheetPath": "spritesheet.png",
                             "spriteVersionNumber": 2})
        with self.assertRaisesRegex(ValueError, "spriteVersionNumber 2 requires an 8x11 atlas"):
            load_atlas(self.pet, 1.0)
