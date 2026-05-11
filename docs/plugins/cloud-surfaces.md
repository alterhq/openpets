# Plugin Cloud Surfaces

OpenPets V1 has one plugin surface renderer: a pet-matched cloud hotspot with a white icon and value. Plugins provide semantic status data. OpenPets owns the cloud palette, rendering, placement, conflict handling, and click details.

Plugins never provide absolute coordinates, custom UI, renderer templates, windows, web views, or drawing code.

Plugin support is currently built-in and pull-request based. External plugin install, update, sandboxing, and per-plugin repository distribution are planned but not shipped yet.

The first-party battery, Claude Code, and Codex Usage plugins are the canonical built-in examples. They stay in the OpenPets repository until the external plugin runtime and install contract are stable.

Built-in plugins can be enabled or disabled from the OpenPets menu bar app's Plugins submenu.

## Surface Updates

Plugins send `surface.update` messages:

```json
{
  "type": "surface.update",
  "surfaceID": "battery.badge",
  "slotPreference": ["hotspot.topTrailing", "hotspot.right"],
  "priority": 40,
  "icon": "battery.75",
  "value": "64%",
  "label": "Battery",
  "tone": "normal",
  "detail": {
    "title": "Battery",
    "rows": [
      {
        "label": "Charge",
        "value": "64%",
        "tone": "normal"
      },
      {
        "label": "State",
        "value": "Unplugged"
      }
    ],
    "actionURL": "x-apple.systempreferences:com.apple.Battery-Settings.extension",
    "actionLabel": "Settings",
    "ttlSeconds": 8
  }
}
```

Compact clouds show the `icon` and `value`. `label` is metadata for accessibility and future settings. `detail` is optional; when present, clicking the cloud opens a plain OpenPets detail bubble. `detail.ttlSeconds` can auto-clear that clicked bubble after a short delay, and `detail.actionURL` plus `detail.actionLabel` add a small action button.

## Icons

The `icon` field is an SF Symbols name rendered by OpenPets. Examples:

```text
battery.25
battery.50
battery.75
battery.100
bolt.fill
cylinder.split.1x2.fill
clock.fill
timer
chart.bar.fill
exclamationmark.triangle.fill
checkmark.circle.fill
info.circle.fill
sparkles
gauge
link
cpu
memorychip.fill
network
```

## Slots

OpenPets resolves placement by priority. Higher-priority surfaces get compatible hotspot slots first. Lower-priority surfaces fall back to the next compatible slot, and if no slot is available they are hidden instead of creating another window.

Default slot order:

```text
hotspot.topTrailing
hotspot.topLeading
hotspot.right
hotspot.bottomTrailing
hotspot.bottomLeading
hotspot.left
hotspot.belowLeading
hotspot.belowTrailing
```

## Rendering

The cloud gradient is extracted from the currently selected pet sprite at startup and reused until the pet changes. Hidden hotspots appear as tiny color-matched glows. Nearby hotspots reveal the full cloud with white icon and value.

Multi-metric plugins should emit one surface per metric. The built-in Claude Code plugin reads OpenPets' own quota cache written by `openpets claude-statusline` and emits separate `claude.5h` and `claude.7d` cloud surfaces, with reset times and pace in click details. The built-in Codex Usage plugin reads `~/.codex/auth.json` for the ChatGPT Codex usage API, then emits separate Codex usage clouds for each available rate-limit window.

Plugins can also emit separate `pet.reaction` updates with semantic states such as `low-energy`, `charging`, `alert`, `celebrate`, `working`, and `resting`. Pet reactions remain independent from cloud surfaces.

## Built-In Plugin Publishing

For the first plugin release, OpenPets accepts plugin contributions as pull requests to the main OpenPets repository. This keeps review, permissions, UX, and test coverage close to the host while the plugin contract is still settling.

Built-in plugin PRs must:

- Emit semantic `surface.update` and optional `pet.reaction` data only.
- Use host-owned cloud surfaces; do not create windows, web views, absolute positions, or custom renderers.
- Keep long-running work off the main actor.
- Include tests for emitted surfaces, reactions, and missing-data behavior.
- Document user-facing behavior and any sensitive system/API access.

Separate plugin repositories should wait until OpenPets ships an external plugin installer, manifest, subprocess runtime, sandbox/permissions policy, and update UX.

## Release Checklist

Before tagging OpenPetsKit:

- Review the public names for cloud surface and reaction APIs.
- Publish OpenPetsKit as a minor feature release.
- Suggested commit: `feat: add cloud plugin surfaces`.

Before tagging OpenPets:

- Replace the development `../openpetskit` package dependency with the published OpenPetsKit version.
- Keep the battery, Claude Code, and Codex Usage plugins enabled as first-party built-in plugins.
- Keep this document clear that plugin support is built-in/PR-based for now.
- Suggested commit: `feat: add battery cloud surface plugin`.

After the initial release:

- Add a stable `plugins/` or `Sources/OpenPetsPlugins/` convention if built-in plugin count grows.
- Draft the external plugin RFC covering manifests, arbitrary commands, subprocess execution, seatbelt sandboxing, declared permissions, install, update, disable, and audit UX.
- Use the Claude Code and Codex Usage plugins to validate multiple metric clouds and click details.
