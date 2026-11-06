# Linux team control pane

Branch: `feat/linux-gtk`. Worktree: repository root `.`. Integrator owns this board.
Communication/API contract: [linux-team-protocol.md](linux-team-protocol.md).

| ID | Owner | Scope | State | Gate / handoff |
| --- | --- | --- | --- | --- |
| L1 | MCP teammate | SDK server + OpenCode setup documentation | Running | Official SDK stdio/HTTP client tests; `docs/linux-mcp.md` |
| L2 | Pet/settings teammate | Default pet, discovery, atomic config, positions | Running | Pure tests and bundled asset validation |
| L3 | Notifications teammate | History/details/action UI | Running | URL safety tests and GTK click-only actions |
| L4 | Plugins teammate | Battery + Claude/Codex quota readers | Running | Fixture tests, sanitized failures, opt-in integration |
| L5 | Integrator | CLI, main GTK menus, MCP lifecycle, packaging | Running | Full tests + real/headless smoke + isolated package install |
| L6 | Independent reviewer | Security, privacy, lifecycle, contracts | Ready | No unresolved high/critical findings before handoff |

No merge, PR or release is authorized by these cards. A card moves to Review
only with explicit test evidence; integration here does not mean Git merge.

## Animation and minimal overlay pass

Shared existing worktree; exclusive file ownership prevents overlapping writes.
Existing `general` agent profile is reused; no agent profiles/configuration created.

| ID | Owner | Scope | State | Gate / handoff |
| --- | --- | --- | --- | --- |
| A1 | Animation teammate + integrator | `ui.py`, animation helper/tests | Review complete | Directional drag and idle scheduler integrated; 7 GTK regression tests |
| A2 | MCP teammate | `mcp_server.py`, `core.py`, dedicated tests/docs | Review complete | Nine animation rows; new catalog tool; SDK tests pass |
| A3 | Overlay teammate + integrator | `overlay.py` and layout tests | Review complete | Compact geometry, visible-only hit regions; rendered Goku preview inspected |
| A4 | Integrator | Integration, regression/smoke, final review | Verified locally | 335 tests with SDK and ResourceWarning gate; real GTK smoke; unchanged Swift sources |

Implementation teammates were interrupted, then their partial artifacts were
reviewed and completed by the integrator. Follow-up read-only reviews found a
missing test import/re-export (fixed). The suggested slow-drag regression is
covered: sub-threshold motion accumulates until the direction threshold is met.
No branches merged, no commit/PR created. Container verification was unavailable
(Docker daemon not running); the actual X11 display was used with isolated test
configuration/sockets. The existing running GUI/MCP must be restarted to load
the new Python modules and refresh the MCP tool catalog.
