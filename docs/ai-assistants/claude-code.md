# Claude Code Instructions

Claude Code needs two things to use OpenPets well:

- the OpenPets MCP server configured for the session
- OpenPets behavior guidance in a Claude instruction file
- the OpenPets statusline bridge if you want Claude Code quota clouds

## Setup

1. Configure the OpenPets MCP server in the matching MCP config file below.
2. Copy the shared OpenPets snippet from [README.md](./README.md#shared-openpets-snippet).
3. Paste it into the matching `CLAUDE.md` instruction file below.
4. Optional: configure the OpenPets statusline bridge so the Claude Code plugin can show quota clouds.
5. Start a new session if the current session does not pick up changed instructions automatically.

## MCP Config

Use user-level MCP config when OpenPets should be available in Claude Code across projects:

- `~/.claude.json`

Use project-level MCP config when a repository should declare OpenPets as part of its shared Claude Code setup:

- `.mcp.json`

## Claude Instructions

Use user-level instructions when every Claude Code workspace should tell Claude to use OpenPets:

- `~/.claude/CLAUDE.md`

Use project-level instructions when a repository should share OpenPets behavior with the team:

- `CLAUDE.md`
- `.claude/CLAUDE.md`

Use local instructions when only your checkout should tell Claude to use OpenPets:

- `CLAUDE.local.md`

The OpenPets snippet belongs in a `CLAUDE.md` instruction file, not in the MCP config file. The MCP config makes the tools available; the `CLAUDE.md` guidance tells Claude when and how to use them.

## Quota Cloud Plugin

Claude Code exposes rate-limit usage to [statusline commands](https://code.claude.com/docs/en/statusline). OpenPets uses that statusline JSON to maintain its own local quota cache for the built-in Claude Code cloud plugin.

Install the OpenPets CLI from the menu bar app, then add this to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "openpets claude-statusline"
  }
}
```

The command prints a compact terminal status line for Claude Code and writes quota data to OpenPets' config directory. The desktop pet reads that cache and renders the `5h` and `7d` clouds. No third-party statusline plugin is required.

## Notes

The assistant must have access to the OpenPets MCP tools for the guidance to take effect. If the tools are not available in a session, Claude should not claim it notified through OpenPets.

The shared snippet is intentionally explicit about final responses because small tasks are easy for assistants to treat as exempt unless the behavior is stated directly.
