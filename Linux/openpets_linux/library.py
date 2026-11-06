"""OpenPets pet discovery and active-pet selection.

Pure standard library, no GI. Discovers pet bundles under XDG data
(``$XDG_DATA_HOME/openpets/pets``, falling back to ``~/.local/share/openpets/pets``)
and ``~/.codex/pets``, de-duplicates by directory, and never follows manifest
paths that escape a bundle directory. The bundled OpenPetsKit v0.2.1 Starcorn
asset is always available as the default fallback.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .settings import Settings

__all__ = [
    "PetLibrary",
    "BUNDLED_PET_ID",
    "bundled_starcorn_path",
]

BUNDLED_PET_ID = "starcorn"

# Top-level manifest keys required for a valid pet bundle.
_MANIFEST_REQUIRED = frozenset({"id", "displayName", "spritesheetPath"})
# Manifest size bound mirrors the 64 KiB IPC/config bound.
_MAX_MANIFEST_SIZE = 64 * 1024


def bundled_starcorn_path() -> Path:
    """Absolute path to the bundled OpenPetsKit v0.2.1 Starcorn bundle."""
    return Path(__file__).resolve().parent / "resources" / "starcorn"


def _safe_resolve_within(directory: Path, relative: str) -> Path:
    """Resolve ``directory/relative`` and reject anything escaping ``directory``.

    Symlinks and ``..`` components that point outside the bundle are refused.
    The target must exist and be a regular file.
    """
    base = directory.resolve(strict=True)
    target = (base / relative).resolve(strict=True)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("manifest path escapes the pet bundle") from exc
    if not target.is_file():
        raise ValueError("manifest target is not a regular file")
    return target


def _load_pet_manifest(directory: Path) -> dict[str, Any] | None:
    """Validate a pet bundle directory; return its public pet dict or None."""
    try:
        manifest = directory / "pet.json"
        if not manifest.is_file():
            return None
        if manifest.stat().st_size > _MAX_MANIFEST_SIZE:
            return None
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    if not _MANIFEST_REQUIRED.issubset(data.keys()):
        return None
    pet_id = data.get("id")
    display_name = data.get("displayName")
    spritesheet = data.get("spritesheetPath")
    if not isinstance(pet_id, str) or not pet_id or len(pet_id) > 128 or any(c in pet_id for c in "/\\"):
        return None
    if not isinstance(display_name, str) or not display_name or len(display_name) > 256:
        return None
    if not isinstance(spritesheet, str) or not spritesheet:
        return None
    # Never follow a manifest reference that escapes the bundle.
    try:
        _safe_resolve_within(directory, spritesheet)
    except (ValueError, OSError):
        return None
    return {
        "id": pet_id,
        "displayName": display_name,
        "directory": str(directory.resolve()),
    }


def _scan_pet_root(root: Path) -> list[dict[str, Any]]:
    """Return valid pet dicts under one discovery root, without following escapes."""
    pets: list[dict[str, Any]] = []
    if not root.is_dir():
        return pets
    try:
        entries = sorted(root.iterdir())[:256]
    except OSError:
        return pets
    for entry in entries:
        if not entry.is_dir():
            continue
        pet = _load_pet_manifest(entry)
        if pet is not None:
            pets.append(pet)
    return pets


class PetLibrary:
    """Discover pets and manage the active selection persisted via Settings."""

    def __init__(self, settings: Settings | None = None):
        self.settings: Settings = settings or Settings()
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            data_root = Path(xdg_data)
        else:
            data_root = Path.home() / ".local/share"
        self._roots: tuple[Path, ...] = (
            data_root / "openpets" / "pets",
            Path.home() / ".codex" / "pets",
            self.settings.directory / "pets",
            self.settings.directory / "Pets",
        )
        self._bundled = bundled_starcorn_path()

    # -- discovery ----------------------------------------------------------

    def list_pets(self) -> list[dict[str, Any]]:
        """All discoverable pets: XDG data, ~/.codex/pets, then bundled Starcorn.

        No duplicate directories; unsafe/escape manifests are skipped.
        """
        pets: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_ids: set[str] = {BUNDLED_PET_ID}
        for root in self._roots:
            for pet in _scan_pet_root(root):
                directory = pet["directory"]
                if directory in seen or pet["id"] in seen_ids:
                    continue
                seen.add(directory)
                seen_ids.add(pet["id"])
                pets.append(pet)
        # Bundled Starcorn is always available as the default pet.
        bundled = self._bundled_pet_dict()
        if bundled["directory"] not in seen:
            pets.append(bundled)
        return pets

    def _bundled_pet_dict(self) -> dict[str, Any]:
        manifest = self._bundled / "pet.json"
        data: dict[str, Any] = {}
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                data = {}
        return {
            "id": data.get("id") or BUNDLED_PET_ID,
            "displayName": data.get("displayName") or "Starcorn",
            "directory": str(self._bundled),
        }

    def _find_pet(self, pet_id: str) -> dict[str, Any] | None:
        if not isinstance(pet_id, str) or not pet_id:
            return None
        for pet in self.list_pets():
            if pet["id"] == pet_id:
                return pet
        return None

    # -- active pet ---------------------------------------------------------

    def active_pet(self) -> Path:
        """Directory of the configured active pet, else bundled Starcorn."""
        config = self.settings.read()
        active_id = config.get("activePetID")
        if isinstance(active_id, str) and active_id:
            pet = self._find_pet(active_id)
            if pet is not None:
                return Path(pet["directory"])
        return self._bundled

    def activate(self, pet_id: str) -> Path:
        """Persist ``pet_id`` as the active pet and return its directory."""
        pet = self._find_pet(pet_id)
        if pet is None:
            raise ValueError(f"unknown pet: {pet_id!r}")
        self.settings.update({"activePetID": pet["id"]})
        return Path(pet["directory"])
