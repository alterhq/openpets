# Claude Code Instructions

Claude Code needs two things to use OpenPets well:

- the OpenPets MCP server configured for the session
- OpenPets behavior guidance in a Claude instruction file

## Setup

1. Configure the OpenPets MCP server in the matching MCP config file below.
2. Copy the shared OpenPets snippet from [README.md](./README.md#shared-openpets-snippet).
3. Paste it into the matching `CLAUDE.md` instruction file below.
4. Start a new session if the current session does not pick up changed instructions automatically.

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

The built-in Claude Code cloud plugin reads Claude Code OAuth credentials and polls Anthropic's usage endpoint for authoritative `5h` and `7d` quota data. OpenPets looks for credentials in macOS Keychain item `Claude Code-credentials`, `~/.claude/.credentials.json`, or `CLAUDE_CODE_OAUTH_TOKEN`.

When a token is expired, OpenPets tries to refresh Claude Code credentials by running `claude update`, then `claude auth status` as a fallback. The plugin polls at a conservative interval to avoid OAuth usage API rate limits.

## Notes

The assistant must have access to the OpenPets MCP tools for the guidance to take effect. If the tools are not available in a session, Claude should not claim it notified through OpenPets.

The shared snippet is intentionally explicit about final responses because small tasks are easy for assistants to treat as exempt unless the behavior is stated directly.
