# OpenCode Instructions

OpenCode can load persistent instructions from `~/.config/opencode/AGENTS.md`.

## Setup

1. Open or create `~/.config/opencode/AGENTS.md`.
2. Copy the shared OpenPets snippet from [README.md](./README.md#shared-openpets-snippet).
3. Paste it into the file.
4. Restart or reload OpenCode if your session does not pick up changed instructions automatically.

## Scope

Use `~/.config/opencode/AGENTS.md` for global behavior across all repositories.

If your workflow supports repository-local instructions, use them when only one project should require OpenPets notifications.

## Notes

OpenCode may expose OpenPets tools with names like `openpets_notify`, `openpets_wake_pet`, and `openpets_get_openpets_status`. The shared snippet uses short tool names such as `notify` and `wake_pet` because assistants often map MCP tool names into local tool namespaces.

The important behavior is that OpenCode calls the notification tool before final task results and uses wake-and-retry behavior when the pet is unavailable.
