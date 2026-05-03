# Cursor Instructions

Cursor uses Rules and `AGENTS.md` files to guide Agent behavior. Cursor Rules are documented at <https://cursor.com/docs/rules>.

To use OpenPets well in Cursor, make the OpenPets MCP server available to Agent and add the shared OpenPets snippet as a rule that Cursor includes in chat context.

## Setup

1. Configure the OpenPets MCP server for Cursor.
2. Copy the shared OpenPets snippet from [README.md](./README.md#shared-openpets-snippet).
3. Paste it into one of the rule locations below.
4. Use an always-applied rule when you want Cursor to notify through OpenPets for every task result.

## Project Rules

Use a project rule when the repository should share OpenPets behavior with everyone using Cursor:

- `.cursor/rules/openpets.mdc`

Recommended frontmatter:

```md
---
alwaysApply: true
---
```

Then paste the shared OpenPets snippet below the frontmatter.

## AGENTS.md

Use `AGENTS.md` when you want a simpler project instruction file instead of a structured Cursor rule:

- `AGENTS.md`

Cursor supports `AGENTS.md` in the project root and nested `AGENTS.md` files in subdirectories. For OpenPets, prefer the project root so the notification behavior applies across the whole repository.

## User Rules

Use Cursor User Rules when OpenPets behavior should apply to your Cursor Agent chats across projects:

- `Cursor Settings -> Rules`

User Rules are configured in Cursor's UI, not as a global settings file in the repository.

## Team Rules

Use Team Rules when an organization wants OpenPets behavior to apply across a team:

- Cursor dashboard team rules

Team Rules are available on Cursor Team and Enterprise plans.

## Notes

Rules apply to Cursor Agent chat. Cursor's docs note that User Rules do not apply to Inline Edit.

If Cursor exposes MCP tools with provider-prefixed names, keep the behavior from the shared snippet and adapt only the tool names if needed.

The OpenPets MCP server must be configured separately from the rule. The rule tells Cursor when and how to use OpenPets; the MCP configuration makes the OpenPets tools available.
