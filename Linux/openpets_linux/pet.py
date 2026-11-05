"""Bounded, local-only Codex pet bundle loading."""

import json
from pathlib import Path


def manifest_image(directory):
    directory = Path(directory).expanduser().resolve(strict=True)
    manifest = directory / "pet.json"
    if manifest.stat().st_size > 65536:
        raise ValueError("Pet manifest exceeds 64 KiB")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("spritesheetPath"), str):
        raise ValueError("pet.json requires a spritesheetPath string")
    image = (directory / data["spritesheetPath"]).resolve(strict=True)
    if not image.is_relative_to(directory) or not image.is_file():
        raise ValueError("Spritesheet must be a file inside the pet bundle")
    if image.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Spritesheet exceeds 32 MiB")
    return image


def load_atlas(directory, scale):
    # Keep CLI and pure tests usable without GTK or an active display.
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    image = manifest_image(directory)
    info = GdkPixbuf.Pixbuf.get_file_info(str(image))
    if info is None:
        raise ValueError("Unsupported spritesheet image")
    _, width, height = info
    if not (0 < width <= 8192 and 0 < height <= 8192 and width % 8 == height % 9 == 0):
        raise ValueError("Spritesheet must be an 8x9 atlas, at most 8192px per side")
    target_width = max(1, round(width / 8 * scale))
    target_height = max(1, round(height / 9 * scale))
    if max(target_width, target_height) > 1024:
        raise ValueError("Scaled pet exceeds 1024px per cell")
    atlas = GdkPixbuf.Pixbuf.new_from_file_at_scale(
        str(image), target_width * 8, target_height * 9, False
    )
    return atlas, target_width, target_height
