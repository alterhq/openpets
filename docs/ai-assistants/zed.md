# Zed Instructions

Zed can connect to OpenPets through a custom MCP server in `~/.config/zed/settings.json`.

Zed rules can be supplied through a project `.rules` file or through the Rules Library. Project `.rules` files are auto-included in Agent Panel interactions for that project.

## Setup

OpenPets can configure Zed automatically from the menu bar assistant setup window when the `zed` executable is installed. The automatic setup writes the OpenPets remote MCP server to `~/.config/zed/settings.json`.

OpenPets does not automatically append the shared snippet for Zed. Zed rules are project-scoped unless you use Zed's Rules Library, so choose the right scope in Zed and add the snippet manually.

Manual setup:

1. Add the OpenPets remote MCP server to `~/.config/zed/settings.json`.
2. Copy the shared OpenPets snippet from [README.md](./README.md#shared-openpets-snippet).
3. Paste it into your project's `.rules` file, or create a Rules Library rule and mark it as a default rule.
4. Restart or reload Zed if your current Agent Panel session does not pick up changed settings.

Example `~/.config/zed/settings.json` entry:

```json
{
  "context_servers": {
    "openpets": {
      "url": "http://127.0.0.1:3010/mcp",
      "headers": {
        "Authorization": "Bearer openpets-local"
      }
    }
  }
}
```

## Scope

Use `~/.config/zed/settings.json` when every Zed project should have access to the OpenPets MCP server.

Use a repository-root `.rules` file when a project should tell Zed's Agent Panel to use OpenPets notifications.

Use the Rules Library default rule option when you want the OpenPets behavior across Zed Agent Panel conversations without adding a project file.

## Notes

Zed supports project `.rules` files and also recognizes several compatible instruction filenames such as `AGENTS.md` and `CLAUDE.md`. If more than one exists in a project, Zed uses the first supported file it finds.

The shared snippet only tells Zed when and how to notify. Zed must also have the OpenPets MCP server configured and active in the Agent Panel settings.

OpenPets includes a local placeholder `Authorization` header in the Zed config because Zed prompts for OAuth when a remote MCP server has no configured `Authorization` header. OpenPets does not require or validate this token.
