# AI Assistant Instructions

Use these instructions to help AI assistants use the OpenPets desktop pet for visible task progress, results, blockers, and review requests.

Copy the shared snippet below into your assistant's persistent system, developer, or agent instructions. Then see the assistant-specific guide for where to paste it.

| Assistant | Setup Guide | Where to Paste |
| --- | --- | --- |
| Zed | [zed.md](./zed.md) | Project `.rules` file or Rules Library |
| OpenCode | [opencode.md](./opencode.md) | `~/.config/opencode/AGENTS.md` |
| Claude Code | [claude-code.md](./claude-code.md) | User or project instructions |
| Cursor | [cursor.md](./cursor.md) | Project Rules, `AGENTS.md`, User Rules, or Team Rules |
| Pi CLI | [pi-cli.md](./pi-cli.md) | `~/.pi/agent/AGENTS.md`, `AGENTS.md`, or `CLAUDE.md` |
| Generic MCP client | [generic-mcp-client.md](./generic-mcp-client.md) | System or developer prompt area |

## Shared OpenPets Snippet

```md
## OpenPets MCP

When OpenPets MCP tools are available, use the desktop pet as the visible task-state channel.

Before any final response that reports a task result, answer, decision, blocker, or completed action, call `notify` with the same outcome you are about to report to the user. This applies even to small requests, direct answers, file searches, shell commands, edits, reviews, and failed attempts.

For non-trivial or multi-step work, also call `notify` with `running` when work starts or when meaningful progress changes. Do not notify for every internal step.

Threaded message workflow:

1. When starting a distinct task or agent run, call `notify` without `threadId`. OpenPets creates a new bubble and returns a `threadId`.
2. Store that `threadId` for the life of the task.
3. For every later progress, waiting, review, failed, done, or follow-up update for that same task, call `notify` with the same `threadId` so the existing bubble is replaced.
4. If multiple tasks or agents run concurrently, each task or agent should keep its own `threadId`; do not reuse one task's `threadId` for another task.
5. Use `clear_pet_message` with a task's `threadId` only when that specific task bubble is no longer relevant.

If the first `notify` call fails or indicates the pet is not running/visible, call `wake_pet` and retry `notify` once before sending the final response. Do not call `get_openpets_status` before normal updates.

Use statuses consistently:

- `running`: work is actively in progress
- `done`: the requested task completed successfully
- `failed`: the task failed or hit a blocker
- `review`: user review, confirmation, or attention is needed
- `waiting`: work is paused or waiting on external input
- `message`: neutral informational message that is not a task outcome

Final notifications must be specific. Include the actual outcome in `text`, such as "No README file was found in `/Users/sam/code/openpets`," rather than generic text like "Done."

Keep `title` short and put useful detail in `text`. Use `ttlSeconds` only for temporary updates; omit it when the message should remain visible until replaced by another notify call with the same `threadId` or cleared.

Use `play_pet_animation` only for non-message visual feedback. If you need to communicate text, use `notify` instead.

Use `stop_pet` only when the user explicitly asks to hide, stop, quit, or dismiss the pet.

Do not notify for greetings, thanks, or purely conversational replies unless the user asks for visible pet feedback.
```

## Placement Guidance

Prefer user-level instructions when you want OpenPets behavior across all projects.

Prefer project-level instructions when the behavior should only apply to one repository or team workspace.

If your assistant supports both system and developer instructions, put this guidance in the most persistent instruction layer that the assistant reads before each task.
