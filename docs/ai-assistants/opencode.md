# OpenCode Instructions

OpenCode can load persistent instructions from `~/.config/opencode/AGENTS.md`.

## Setup

OpenPets can configure OpenCode automatically from the menu bar assistant setup window when the `opencode` executable is installed. The automatic setup writes the OpenPets remote MCP server to `~/.config/opencode/opencode.json`, or updates an existing `opencode.jsonc`.

Manual setup:

1. Add the OpenPets remote MCP server to `~/.config/opencode/opencode.json` or `~/.config/opencode/opencode.jsonc`.
2. Open or create `~/.config/opencode/AGENTS.md`.
3. Copy the shared OpenPets snippet from [README.md](./README.md#shared-openpets-snippet).
4. Paste it into the file.
5. Restart or reload OpenCode if your session does not pick up changed instructions automatically.

Example `~/.config/opencode/opencode.json` entry:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "openpets": {
      "type": "remote",
      "url": "http://127.0.0.1:3010/mcp",
      "enabled": true
    }
  }
}
```

## Scope

Use `~/.config/opencode/AGENTS.md` for global behavior across all repositories.

If your workflow supports repository-local instructions, use them when only one project should require OpenPets notifications.

## Notes

OpenCode may expose OpenPets tools with names like `openpets_notify`, `openpets_wake_pet`, and `openpets_get_openpets_status`. The shared snippet uses short tool names such as `notify` and `wake_pet` because assistants often map MCP tool names into local tool namespaces.

The important behavior is that OpenCode calls the notification tool before final task results and uses wake-and-retry behavior when the pet is unavailable.
