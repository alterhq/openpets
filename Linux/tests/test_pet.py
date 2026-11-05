import json
from pathlib import Path
import tempfile
import unittest

from openpets_linux.pet import manifest_image


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
