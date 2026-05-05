# Pi CLI Instructions

Pi loads instruction files at startup. Pi's quickstart is documented at <https://pi.dev/docs/latest/quickstart>.

To use OpenPets well in Pi, make the OpenPets tools available to Pi and add the shared OpenPets snippet to a Pi context file.

## Setup

OpenPets can configure Pi automatically from the menu bar assistant setup window when the `pi` executable is installed. The automatic setup installs `npm:pi-mcp-extension` and writes the OpenPets HTTP MCP server to `~/.pi/agent/mcp.json`.

Manual setup:

1. Install Pi's MCP extension with `pi install npm:pi-mcp-extension`.
2. Add the OpenPets HTTP MCP server to `~/.pi/agent/mcp.json`.
3. Copy the shared OpenPets snippet from [README.md](./README.md#shared-openpets-snippet).
4. Paste it into one of the context files below.
5. Restart Pi or run `/reload` after changing context files.

Example `~/.pi/agent/mcp.json` entry:

```json
{
  "mcpServers": {
    "openpets": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:3010/mcp",
      "lifecycle": "eager"
    }
  }
}
```

## Context Files

Use global instructions when every Pi project should tell the agent to use OpenPets:

- `~/.pi/agent/AGENTS.md`

Use project instructions when a repository should share OpenPets behavior with people using Pi:

- `AGENTS.md`
- `CLAUDE.md`

Pi loads `AGENTS.md` and `CLAUDE.md` from parent directories and the current directory, so a repository-root file is usually the right place for project-wide OpenPets behavior.

## OpenPets Tools

The OpenPets snippet only tells Pi when and how to notify. Pi must also have callable OpenPets tools available in the session.

If OpenPets is exposed to Pi through a custom tool or extension, keep the behavior from the shared snippet and adapt only the tool names if needed.

Pi extensions can be loaded globally or per project:

- Global extensions: `~/.pi/agent/extensions/`
- Project extensions: `.pi/extensions/`

Use a project extension when OpenPets should be part of a repository's shared Pi setup. Use a global extension when you want OpenPets available in all Pi sessions.

## Notes

Pi should not claim it notified through OpenPets unless the OpenPets tools are actually available in the session.

The shared snippet is intentionally explicit about final responses because small tasks are easy for assistants to treat as exempt unless the behavior is stated directly.
