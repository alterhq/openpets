# OpenPets

OpenPets is a native macOS desktop pet for visible agent progress, review prompts, completion states, and lightweight animations across local coding tools.

It gives Codex Pets a shared home outside one assistant. The menu bar app, MCP server, CLI, and apps built with [OpenPetsKit](https://github.com/alterhq/OpenPetsKit) can all talk to the same local pet, so Codex, Claude Code, Cursor, OpenCode, Pi CLI, generic MCP clients, local apps, and scripts can report work through one visible desktop companion.

This repository is the desktop app, CLI, MCP server, assistant setup, and release packaging project. If you are embedding OpenPets in your own Swift app, use [OpenPetsKit](https://github.com/alterhq/OpenPetsKit), the separate Swift package for the embeddable runtime and client APIs.

## Install

Install OpenPets from the [latest GitHub release](https://github.com/alterhq/openpets/releases/latest).

Download the app from the latest release and move it to Applications. OpenPets requires macOS 14 or later.

After installing the app, launch OpenPets from Applications. The menu bar app can wake the bundled Starcorn pet, start the local MCP server, copy the MCP URL, open the config folder, and stop the pet.

## Quick Start

The default MCP endpoint is:

```text
http://127.0.0.1:3001/mcp
```

Configure your assistant or MCP client with that endpoint. Once connected, agents can call OpenPets tools to wake the pet, show task state, update threaded messages, and play lightweight animations.

Add the recommended assistant instructions so the assistant uses the desktop pet consistently. See [docs/ai-assistants](./docs/ai-assistants/) for setup guidance covering OpenCode, Claude Code, Cursor, Pi CLI, and generic MCP clients.

## What You Can Do

- Show task progress, completion, review, waiting, and failure states through a visible desktop companion.
- Let multiple local tools share one pet instead of each app owning a separate status UI.
- Send notifications and animations from an MCP client, a Swift app, or CLI scripts.
- Run the bundled Starcorn pet or install custom Codex Pets using an 8x9 sprite atlas.
- Use action URLs on notifications for lightweight follow-up flows.

## Roadmap

- Plugin ecosystem for assistant and local tool behaviors.
- Catalog of apps and agents using OpenPets.
- Pet catalog and gallery improvements for discovering and installing compatible pet bundles.
- Easier assistant onboarding for Codex, Claude Code, Cursor, OpenCode, Pi CLI, and generic MCP clients.
- Richer task states with action buttons and shared task workflows.
- Continued focus on one shared local desktop companion across multiple tools.

## Integration Details

OpenPets exposes local MCP tools from the menu bar app:

| Tool                  | Purpose                                                                  |
| --------------------- | ------------------------------------------------------------------------ |
| `get_openpets_status` | Read MCP server, pet, socket, and config status.                         |
| `wake_pet`            | Start or bring back the desktop pet.                                     |
| `stop_pet`            | Stop the desktop pet.                                                    |
| `notify`              | Show or update a threaded message bubble with a status-driven animation. |
| `play_pet_animation`  | Play an animation without showing text.                                  |
| `stop_pet_animation`  | Return the pet to idle without stopping it or clearing messages.         |
| `clear_pet_message`   | Clear one message bubble by `threadId`.                                  |
| `ping_pet`            | Confirm the pet process can receive commands.                            |

Valid notification statuses are `running`, `review`, `done`, `failed`, `waiting`, and `message`.

The shared pet can show multiple task bubbles at once. A `notify` call returns a `threadId`; pass that ID back on later updates to replace the same task bubble instead of creating a new one.

See [Shared Pet System](./docs/shared-pet-system.md) for the default socket topology, MCP behavior, `threadId` workflow, and guidance for app integrations.

## Swift App Integration

Swift apps should use [OpenPetsKit](https://github.com/alterhq/OpenPetsKit), the separate Swift package for embedding OpenPets. It contains the embeddable runtime, client APIs, and bundled Starcorn pet with minimal dependencies.

In Xcode, add OpenPetsKit as a package dependency:

```text
https://github.com/alterhq/OpenPetsKit.git
```

In a `Package.swift` file, add OpenPetsKit to `dependencies`:

```swift
.package(url: "https://github.com/alterhq/OpenPetsKit.git", from: "0.1.0")
```

Then add the library product to the target that should send pet commands:

```swift
.target(
    name: "YourApp",
    dependencies: [
        .product(name: "OpenPetsKit", package: "OpenPetsKit")
    ]
)
```

Import the module and send commands through the shared local pet socket:

```swift
import OpenPetsKit

let client = OpenPetsClient()

let response = try client.send(.notify(PetNotification(
    title: "Build Passed",
    text: "All tests completed.",
    status: "done"
)))

print(response.threadId ?? "")
```

## Development

Source builds require Swift 6.0 or later and Xcode command line tools.

From a local checkout, build the package:

```sh
cd openpets
swift build
```

Run the test suite:

```sh
swift test
```

Start the menu bar app from source:

```sh
swift run openpets-menubar
```

Build optimized executables:

```sh
swift build -c release
```

The release binaries are written under `.build/release/`. Release packaging is handled by `scripts/package-release.sh`.

See `CONTRIBUTING.md` for contributor setup, workflow, and pull request guidance.

## Configuration

OpenPets creates a JSON config file at:

```text
~/.config/openpets/config.json
```

If `XDG_CONFIG_HOME` is set, OpenPets uses:

```text
$XDG_CONFIG_HOME/openpets/config.json
```

Default configuration:

```json
{
  "display": {
    "messageAreaHeight": 56,
    "scale": 0.42
  },
  "mcpEndpoint": "/mcp",
  "mcpHost": "127.0.0.1",
  "mcpPort": 3001,
  "socketPath": "/tmp/openpets-UID.sock"
}
```

Settings:

- `display.scale`: Sprite display scale.
- `display.messageAreaHeight`: Reserved height for the message bubble area.
- `socketPath`: Unix socket used by the CLI and pet host.
- `mcpHost`: HTTP bind host for the MCP server.
- `mcpPort`: HTTP port for the MCP server.
- `mcpEndpoint`: HTTP path for the MCP endpoint.

By default, the MCP server only listens on `127.0.0.1`. Binding to `0.0.0.0`, `::`, or an empty host can expose the MCP server to other devices on your network. Only do this on trusted networks.

Pet window positions are stored in:

```text
~/.config/openpets/positions.json
```

Installed pets are stored in:

```text
~/Library/Application Support/OpenPets/Pets/
```

OpenPets also discovers valid pet bundles from these user locations:

```text
~/.codex/pets/
~/.local/share/openpets/pets/
~/.config/openpets/pets/
~/.config/openpets/Pets/
~/.config/openpets/
```

If `XDG_DATA_HOME` is set, OpenPets checks `$XDG_DATA_HOME/openpets/pets/` instead of `~/.local/share/openpets/pets/`.

## Codex Pets

OpenPets uses the Codex Pets format. A pet bundle is a directory containing a `pet.json` manifest and a spritesheet.

Example:

```text
my-pet/
  pet.json
  spritesheet.webp
```

Manifest format:

```json
{
  "id": "my-pet",
  "displayName": "My Pet",
  "description": "A short description.",
  "spritesheetPath": "spritesheet.webp"
}
```

Spritesheets are expected to use an 8 column by 9 row atlas. The current animation rows are:

| Row | Animation       |
| --- | --------------- |
| 0   | `idle`          |
| 1   | `running-right` |
| 2   | `running-left`  |
| 3   | `waving`        |
| 4   | `jumping`       |
| 5   | `failed`        |
| 6   | `waiting`       |
| 7   | `running`       |
| 8   | `review`        |

The spritesheet width must be divisible by 8 and the height must be divisible by 9.

## CLI Usage

The CLI is available for scripts, manual checks, and local development. For AI assistants, prefer the MCP endpoint above.

To install the CLI shim, choose `Install CLI` from the paw menu. This creates `~/.local/bin/openpets`; add `~/.local/bin` to `PATH` if your shell does not already include it.

Run a pet from a pet bundle directory:

```sh
openpets run --pet /path/to/starcorn
```

Send a notification to a running pet:

```sh
openpets notify --title "Build Passed" --status done --text "All tests completed."
```

The command prints a `threadId`. Pass it back with `--thread` to replace that task's bubble instead of creating a new one:

```sh
openpets notify --thread THREAD_ID --title "Build Passed" --status done --text "All tests completed."
```

Play an animation:

```sh
openpets animate waving --once
```

Stop the current animation and return the pet to idle without clearing messages:

```sh
openpets stop-animation
```

Check connectivity:

```sh
openpets ping
```

Clear one message bubble or stop the pet process:

```sh
openpets clear --thread THREAD_ID
openpets stop
```

Available animations are `idle`, `running-right`, `running-left`, `waving`, `jumping`, `failed`, `waiting`, `running`, and `review`.

## Security

OpenPets is intended to run locally. Be careful when enabling network access for the MCP server or passing URLs to notifications. Please report security issues privately to the maintainers.

## License

OpenPets is released under the MIT License. See `LICENSE` for details.
