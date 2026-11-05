# OpenPets Linux GTK3

An additive Python/GTK3 implementation for Mint 22 / Ubuntu 24.04, XFCE/X11.
All Swift/macOS sources, dependency pins and the macOS release workflow remain
unchanged. Docker is for development/testing, never required to run the pet.

## Implemented

- Bundled Starcorn from OpenPetsKit v0.2.1, with its MIT notice; no asset
  downloads on launch. `run --pet PATH` remains available for local bundles.
  Custom Codex pet atlases support v1 (8x9) and v2 (8x11); v2's two extra
  look-direction rows are accepted while the nine standard animation rows are
  played by the current host.
- Pet chooser with preview, local/XDG discovery, persistent active pet and
  per-pet position; restored positions are clamped to a current monitor.
- Frame-timed sprites, drag, tray menu, outside-rectangle click-through.
- **Autonomous movement** (right-click → "Movement:"): **Off** (default),
  **Follow cursor** (pet approaches the pointer, stops in a deadzone without
  obstructing it, alternates running-left/running-right for vertical targets
  without jumping), and **Free roam** (random diagonal/horizontal waypoints
  inside the current monitor workarea, never straight vertical). Mutually
  exclusive, persisted in `config.json` as `motionMode`. Dragging temporarily
  pauses autonomous movement and resumes safely on release.
- Threaded notifications, TTL, bounded in-memory history and details window;
  HTTP(S) buttons open only on an explicit click, never shell/command/file URLs.
- Optional official-SDK MCP HTTP/stdio service and OpenCode V2 config generator.
- Opt-in battery, Claude Code quota and Codex quota clouds, colored status,
  detail windows; quota credentials/network remain untouched until opt-in.
- Atomic locked config writes preserve unrelated settings and refuse to replace
  corrupt existing JSON. Session notification history is never written to disk.

## Quick start from source

Runtime system packages: `python3`, `python3-gi`, `python3-gi-cairo`,
`gir1.2-gtk-3.0`, `gir1.2-ayatanaappindicator3-0.1`, `webp-pixbuf-loader`.
MCP setup additionally needs `python3-venv`. Use system Python or a venv with
system-site-packages for GI.

```sh
PYTHONPATH=Linux /usr/bin/python3 -m openpets_linux run
PYTHONPATH=Linux /usr/bin/python3 -m openpets_linux pets list
PYTHONPATH=Linux /usr/bin/python3 -m openpets_linux pets select PET_ID
PYTHONPATH=Linux /usr/bin/python3 -m openpets_linux notify --title Build --status review \
  --text 'Review the result' --url https://example.com --button Review --thread build
```

Click a notification row or **Messages / actions…** to view full content,
actions and session history. **Choose pet…** previews and switches local pets.
**Appearance settings…** selects black-background dark mode or white-background
light mode, plugin/message opacity, and corner radius. Expanded messages have a
collapse control; the compact bubble shows the unread count and expands on click.
Background blur is compositor-owned: enable it in a compatible compositor such
as Picom (`blur-background` for transparent ARGB windows). OpenPets reports that
setup is needed, but cannot detect the compositor's active blur rules reliably
or configure them automatically.
Use **Display settings…** to adjust pet scale, message-bubble width and message
font size; settings persist in `config.json` and apply immediately.

For a Codex Pets bundle such as Cute Yaya, install it with
`npx codex-pets add cute-yaya`, then choose **Choose pet…** → **小娅娅** (or
run `openpets pets select cute-yaya`). Version 2 bundles use an 8-column by
11-row atlas; the current Linux host plays the nine standard animation rows and
accepts the two additional look-direction rows without rejecting the bundle.
Use **Plugins** to explicitly enable providers. For quota, the confirmation
explains credential access/network use. No plugin is enabled by default.

See [MCP/OpenCode](linux-mcp.md) and [plugins](linux-plugins.md).
The CLI supports `plugins list`, `plugins show battery`, `plugins enable battery`
and `plugins disable battery`. Quota `show`/`enable` explicitly requires
`--allow-network`; `disable` never needs account access.

## Data and security

Config: `$XDG_CONFIG_HOME/openpets/config.json`, default `~/.config/openpets/`.
Positions: adjacent `positions.json`. Pet discovery: XDG data pets,
`~/.codex/pets`, config `pets`/`Pets`, then bundled Starcorn. Unknown settings
are preserved; invalid config/positions need manual repair before saving.
No assistant config, autostart or desktop settings are modified automatically.
Pet IDs must be unique: the first discovered custom ID wins, and `starcorn`
is reserved for the bundled default. Rename a custom manifest ID to select it
separately instead of silently overriding the default.

Unix socket defaults to `/tmp/openpets-UID.sock`, mode 0600; `--socket PATH`
overrides it. Active sockets and unrelated files are not overwritten. JSON
frames, retained messages/history, image dimensions and local file sizes are
bounded. Assets cannot escape their bundle via manifest paths.

## Test and package

```sh
PYTHONPATH=Linux /usr/bin/python3 -m unittest discover -s Linux/tests
PYTHONPATH=Linux /usr/bin/python3 -m openpets_linux --smoke-test
docker build --memory=768m --cpuset-cpus=0 -f Linux/Dockerfile -t openpets-linux-test .
docker run --rm --init --memory=512m --cpus=1 openpets-linux-test
docker run --rm --init --memory=512m --cpus=1 openpets-linux-test \
  xvfb-run -a python3 -m unittest discover -s Linux/tests
bash scripts/package-linux.sh 0.1.0+linux.2 /path/to/output
```

Without the optional SDK, SDK tests explicitly skip. Without a display, GUI
tests explicitly skip; use Docker/Xvfb to execute them. `--init` prevents
`xvfb-run` waiting indefinitely as container PID 1. The Docker test image has
the SDK in a separate venv; the `.deb` keeps SDK installation opt-in so a
desktop-pet-only installation stays light. Packaging never installs on the host.

GitHub Actions tests Linux and produces a `.deb` artifact; that is not a
published release. Hosted CI and macOS regression tests require their runners.
Work is isolated on `feat/linux-gtk`; no commit, push, PR or release is automatic.
Meld and additional skills are not prerequisites.

## Remaining boundaries

X11 only; no Wayland backend. No arbitrary remote pet installer, external plugin
ecosystem, automatic updater or autostart UI. Quota endpoints are private/provider
dependent and fixture-tested, not validated against personal live accounts.
Notification history is session-only; click-through is rectangle-based; TTL
scheduling granularity is roughly one second. This is not complete macOS parity.

See [team protocol](linux-team-protocol.md), [board](linux-team-board.md), and
[initial verification](linux-verification.md) (historical preview milestone).
