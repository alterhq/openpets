"""Unit tests for openpets_linux.library (standard library only, no GI, no display).

Tests run in isolated temporary directories and restore the process environment,
so the root user's config is never read or written. The bundled Starcorn asset
is verified byte-identical to the surveyed OpenPetsKit v0.2.1 checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys_path_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path_here not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_path_here)

from openpets_linux.library import (  # noqa: E402
    BUNDLED_PET_ID,
    PetLibrary,
    bundled_starcorn_path,
)
from openpets_linux.settings import Settings  # noqa: E402

# SHA-256 recorded from OpenPetsKit v0.2.1, revision
# 83497ed34d1831dd453130af77552bfecaef264b. No external checkout required by CI.
SURVEY_HASHES = {
    "pet.json": "fbd6f8832081b1b2208038c29ed98cdea8102f2a0f9b48f21dc6599429a621d5",
    "spritesheet.webp": "d2d91b5339bc46662feddbab643e4fb92278de18281859fb37fcf77cc2a65d66",
}
REPO_ROOT = Path(__file__).resolve().parents[2]
RESOURCES_STARCORN = REPO_ROOT / "Linux" / "openpets_linux" / "resources" / "starcorn"


class _EnvRestore:
    def __init__(self):
        self._saved = {key: os.environ.get(key) for key in (
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HOME",
        )}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _make_pet(root: Path, pet_id: str, display_name: str, sprite_name="sprite.png",
              escape: bool = False) -> Path:
    """Create a valid pet bundle under root; optionally with an escaping sprite."""
    pet_dir = root / pet_id
    pet_dir.mkdir(parents=True, exist_ok=True)
    sprite_path = sprite_name
    if escape:
        # Sprite lives outside the bundle -> must be rejected by discovery.
        (root / "outside.png").write_bytes(b"fixture")
        sprite_path = "../outside.png"
    else:
        (pet_dir / sprite_name).write_bytes(b"fixture")
    (pet_dir / "pet.json").write_text(json.dumps({
        "id": pet_id,
        "displayName": display_name,
        "spritesheetPath": sprite_path,
    }), encoding="utf-8")
    return pet_dir


def _isolated_settings(temporary=None):
    if temporary is None:
        temporary = tempfile.mkdtemp(prefix="openpets-lib-")
    config_home = Path(temporary) / "config"
    os.environ["XDG_CONFIG_HOME"] = str(config_home)
    os.environ["XDG_DATA_HOME"] = str(Path(temporary) / "data")
    os.environ["HOME"] = str(temporary)
    return Settings(), temporary


class BundledAssetTests(unittest.TestCase):
    def test_bundled_starcorn_files_present(self):
        self.assertTrue((RESOURCES_STARCORN / "pet.json").is_file())
        self.assertTrue((RESOURCES_STARCORN / "spritesheet.webp").is_file())
        self.assertTrue((RESOURCES_STARCORN.parent / "LICENSE.starcorn").is_file())

    def test_bundled_pet_matches_surveyed_revision(self):
        for name, expected in SURVEY_HASHES.items():
            bundled = RESOURCES_STARCORN / name
            self.assertEqual(
                hashlib.sha256(bundled.read_bytes()).hexdigest(),
                expected,
                f"bundled {name} differs from surveyed rev 83497ed",
            )

    def test_bundled_pet_json_is_starcorn(self):
        data = json.loads((RESOURCES_STARCORN / "pet.json").read_text(encoding="utf-8"))
        self.assertEqual(data["id"], "starcorn")
        self.assertEqual(data["displayName"], "Starcorn")


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp(prefix="openpets-libtests-")
        self.addCleanup(lambda: shutil.rmtree(self.temporary, ignore_errors=True))
        self._env = _EnvRestore()
        self._env.__enter__()
        self.addCleanup(self._env.__exit__, None, None, None)
        self.settings, _ = _isolated_settings(self.temporary)
        self.library = PetLibrary(settings=self.settings)

    def test_list_pets_returns_bundled_starcorn_by_default(self):
        pets = self.library.list_pets()
        self.assertEqual(len(pets), 1)
        pet = pets[0]
        self.assertEqual(pet["id"], "starcorn")
        self.assertEqual(pet["displayName"], "Starcorn")
        self.assertEqual(pet["directory"], str(bundled_starcorn_path()))

    def test_list_pet_keys(self):
        pets = self.library.list_pets()
        for pet in pets:
            self.assertEqual(set(pet.keys()), {"id", "displayName", "directory"})
            self.assertIsInstance(pet["directory"], str)

    def test_list_pets_no_duplicates_across_roots(self):
        data_pets = Path(os.environ["XDG_DATA_HOME"]) / "openpets" / "pets"
        codex_pets = Path(os.environ["HOME"]) / ".codex" / "pets"
        _make_pet(data_pets, "dupe", "Dupe")
        _make_pet(codex_pets, "dupe", "Dupe")
        pets = self.library.list_pets()
        directories = [p["directory"] for p in pets]
        self.assertEqual(len(directories), len(set(directories)))

    def test_list_pets_discovers_xdg_and_codex(self):
        data_pets = Path(os.environ["XDG_DATA_HOME"]) / "openpets" / "pets"
        codex_pets = Path(os.environ["HOME"]) / ".codex" / "pets"
        _make_pet(data_pets, "xdg-pet", "XDG Pet")
        _make_pet(codex_pets, "codex-pet", "Codex Pet")
        ids = {p["id"] for p in self.library.list_pets()}
        self.assertIn("xdg-pet", ids)
        self.assertIn("codex-pet", ids)
        self.assertIn("starcorn", ids)

    def test_list_pets_skips_invalid_manifest(self):
        data_pets = Path(os.environ["XDG_DATA_HOME"]) / "openpets" / "pets"
        bad = data_pets / "bad"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "pet.json").write_text("{ not json", encoding="utf-8")
        (bad / "sprite.png").write_bytes(b"fx")
        ids = {p["id"] for p in self.library.list_pets()}
        self.assertNotIn("bad", ids)

    def test_list_pets_skips_missing_sprite(self):
        data_pets = Path(os.environ["XDG_DATA_HOME"]) / "openpets" / "pets"
        pet_dir = data_pets / "nosprite"
        pet_dir.mkdir(parents=True, exist_ok=True)
        (pet_dir / "pet.json").write_text(json.dumps({
            "id": "nosprite", "displayName": "No Sprite",
            "spritesheetPath": "missing.png",
        }), encoding="utf-8")
        ids = {p["id"] for p in self.library.list_pets()}
        self.assertNotIn("nosprite", ids)

    def test_list_pets_rejects_manifest_escape(self):
        data_pets = Path(os.environ["XDG_DATA_HOME"]) / "openpets" / "pets"
        _make_pet(data_pets, "escape", "Escape", escape=True)
        ids = {p["id"] for p in self.library.list_pets()}
        self.assertNotIn("escape", ids)

    def test_active_pet_defaults_to_bundled_starcorn(self):
        self.assertEqual(self.library.active_pet(), bundled_starcorn_path())

    def test_active_pet_resolves_configured_id(self):
        data_pets = Path(os.environ["XDG_DATA_HOME"]) / "openpets" / "pets"
        _make_pet(data_pets, "wanted", "Wanted Pet")
        self.settings.update({"activePetID": "wanted"})
        self.assertEqual(self.library.active_pet(), Path(data_pets / "wanted"))

    def test_active_pet_unknown_id_falls_back_to_bundled(self):
        self.settings.update({"activePetID": "does-not-exist"})
        self.assertEqual(self.library.active_pet(), bundled_starcorn_path())

    def test_activate_persists_selection_and_returns_path(self):
        data_pets = Path(os.environ["XDG_DATA_HOME"]) / "openpets" / "pets"
        pet_dir = _make_pet(data_pets, "newpet", "New Pet")
        result = self.library.activate("newpet")
        self.assertEqual(result, pet_dir)
        self.assertEqual(self.settings.read().get("activePetID"), "newpet")
        self.assertEqual(self.library.active_pet(), pet_dir)

    def test_activate_rejects_unknown_pet(self):
        with self.assertRaises(ValueError):
            self.library.activate("no-such-pet")

    def test_activate_bundled_starcorn(self):
        result = self.library.activate("starcorn")
        self.assertEqual(result, bundled_starcorn_path())
        self.assertEqual(self.settings.read().get("activePetID"), "starcorn")


if __name__ == "__main__":
    unittest.main()
