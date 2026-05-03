# OpenPets

OpenPets is a macOS desktop pet that can be controlled from local tools and AI agents. It uses the Codex Pets format and ships with a menu bar app, a command-line client, and an MCP server so agents can show task progress, completion states, review prompts, and lightweight animations through a visible desktop companion.

## Features

- Native macOS desktop pet rendered as a borderless floating panel.
- Menu bar app for starting the MCP server, waking or stopping the pet, and copying the MCP URL.
- Local MCP HTTP server with tools for notifications, animations, status checks, and pet lifecycle control.
- CLI for running a pet directly or sending commands over the local Unix socket.
- Bundled Starcorn pet and support for custom Codex Pets using an 8x9 sprite atlas.

## Goals

- Build an open ecosystem for Codex Pets.
- Support extensibility and customization.
- Liberate pets from Codex so they can be used with Claude Code, Claude Cowork, OpenCode, Pi, OpenClaw, Hermes, Alter, and other AI assistants.
- Have fun experimenting with desktop companions.

## Requirements

- macOS 14 or later.
- Swift 6.0 or later.
- Xcode command line tools.

## Install From Source

From a local checkout, build the package:

```sh
cd openpets
swift build
```

Run the test suite:

```sh
swift test
```

Build optimized executables:

```sh
swift build -c release
```

The release binaries are written under `.build/release/`.

## Quick Start

Start the menu bar app:

```sh
swift run openpets-menubar
```

The menu bar app starts the local MCP server and can wake the bundled Starcorn pet. Use the paw menu to view server status, copy the MCP URL, open the config folder, or stop the pet.

The default MCP endpoint is:

```text
http://127.0.0.1:3001/mcp
```

## CLI Usage

Run a pet from a pet bundle directory:

```sh
swift run openpets run --pet Sources/OpenPets/Resources/Pets/starcorn
```

Send a notification to a running pet:

```sh
swift run openpets notify --title "Build Passed" --status done --text "All tests completed."
```

The command prints a `threadId`. Pass it back with `--thread` to replace that task's bubble instead of creating a new one:

```sh
swift run openpets notify --thread THREAD_ID --title "Build Passed" --status done --text "All tests completed."
```

Play an animation:

```sh
swift run openpets animate waving --once
```

Check connectivity:

```sh
swift run openpets ping
```

Clear one message bubble or stop the pet:

```sh
swift run openpets clear --thread THREAD_ID
swift run openpets stop
```

Available animations are `idle`, `running-right`, `running-left`, `waving`, `jumping`, `failed`, `waiting`, `running`, and `review`.

## MCP Tools

OpenPets exposes these MCP tools from the menu bar app:

| Tool | Purpose |
| --- | --- |
| `get_openpets_status` | Read MCP server, pet, socket, and config status. |
| `wake_pet` | Start or bring back the desktop pet. |
| `stop_pet` | Stop the desktop pet. |
| `notify` | Show or update a threaded message bubble with a status-driven animation. |
| `play_pet_animation` | Play an animation without showing text. |
| `clear_pet_message` | Clear one message bubble by `threadId`. |
| `ping_pet` | Confirm the pet process can receive commands. |

Valid notification statuses are `running`, `review`, `done`, `failed`, `waiting`, and `message`.

If you use OpenPets through an AI coding assistant, add the recommended assistant instructions so the assistant uses the desktop pet consistently instead of treating notifications as optional. See [docs/ai-assistants](./docs/ai-assistants/) for setup guidance covering OpenCode, Claude Code, Cursor, Pi CLI, and generic MCP clients.

Example MCP client URL:

```text
http://127.0.0.1:3001/mcp
```

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
  "display" : {
    "messageAreaHeight" : 56,
    "scale" : 0.42
  },
  "mcpEndpoint" : "/mcp",
  "mcpHost" : "127.0.0.1",
  "mcpPort" : 3001,
  "socketPath" : "/tmp/openpets-UID.sock"
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

| Row | Animation |
| --- | --- |
| 0 | `idle` |
| 1 | `running-right` |
| 2 | `running-left` |
| 3 | `waving` |
| 4 | `jumping` |
| 5 | `failed` |
| 6 | `waiting` |
| 7 | `running` |
| 8 | `review` |

The spritesheet width must be divisible by 8 and the height must be divisible by 9.

## Development

Common commands:

```sh
swift build
swift test
swift run openpets-menubar
```

See `CONTRIBUTING.md` for contributor setup, workflow, and pull request guidance.

## Security

OpenPets is intended to run locally. Be careful when enabling network access for the MCP server or passing URLs to notifications. Please report security issues privately to the maintainers.

## License

OpenPets is released under the MIT License. See `LICENSE` for details.
