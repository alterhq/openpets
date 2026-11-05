# Linux feature team — integration contract v1

Branch/worktree: `feat/linux-gtk`, repository root `.`. Existing macOS files and
the user's uncommitted Linux preview must be preserved. No commits, pushes,
release publishing, host package installs or personal assistant-config edits.

## Communication

The available harness provides parent/child sessions, not a direct teammate
message bus. The integrator routes messages; we do not pretend TeamCreate,
SendMessage or TaskUpdate exist. Reuse the existing `general` agent profile;
do not create or alter persistent agent definitions or model routing.

- Work only inside the assigned file ownership below; one writer per file.
- Plans are approved by this contract. Changes to a contract require the lead's
  approval **before** editing a shared interface.
- Report blockers with: affected interface, evidence, proposed workaround, and
  owner needed. Don't wait on another teammate without reporting the dependency.
- Routine updates are point-to-point via the integrator, not broadcasts.
- Handoff includes changed files, public API, commands/tests run, unresolved
  limitations and privacy/network behavior. Never include credentials.
- No live-account probes, automatic OAuth refresh or credential-file reads in
  tests. Use fixtures. Provider network access is user-opt-in only.
- Parent integrates after handoff, runs focused then full tests, then requests
  independent review. Completed agents exit; do not leave background workers.

## Ownership and public interfaces

### MCP teammate

Owns `Linux/openpets_linux/mcp_server.py`, `Linux/requirements-mcp.txt`,
`Linux/tests/test_mcp.py`, `docs/linux-mcp.md`.

Provide `run_server(socket_path, transport="streamable-http", host="127.0.0.1",
port=3001, endpoint="/mcp")`. Use the official MCP Python SDK, lazily imported;
GUI/core remain usable without SDK. Expose the eight original tools, forwarding
commands with `ipc.send`. Wake a host with `python -m openpets_linux run --socket
PATH` (pet optional after integration). Bind only loopback, retain SDK DNS
rebinding protections. No shell execution, no stdout pollution on stdio. Tools
must describe actual socket errors rather than return false success. Do not
edit CLI/UI; parent will wire `mcp` and onboarding commands. Tests may skip
SDK-specific cases when absent, but must distinguish skip from pass.

### Pet/settings teammate

Owns `Linux/openpets_linux/settings.py`, `Linux/openpets_linux/library.py`,
`Linux/openpets_linux/resources/`, `Linux/tests/test_library.py`,
`Linux/tests/test_settings.py`.

`Settings(path=None)` defaults to XDG config. Methods `read()->dict`,
`update(changes:dict)->dict` preserve unrelated fields, atomic 0600 writes,
bounded JSON, safe explicit path behavior. `get_position(pet_id)->tuple|None`,
`set_position(pet_id,x,y)` use adjacent `positions.json`. Serialize writers with
advisory locking so read-modify-write doesn't lose unrelated settings.

`PetLibrary(settings=None)` methods `list_pets()->list[dict]` with keys `id`,
`displayName`, `directory` (string); `active_pet()->Path` resolves configured
active ID then bundled Starcorn; `activate(pet_id)->Path` persists selection.
Bundle exact surveyed OpenPetsKit v0.2.1 Starcorn and MIT notice (source clone
at `/tmp/opencode/openpetskit-linux-survey`). Discover XDG pets and ~/.codex/pets
without following manifest escapes. No network download/install from arbitrary
URLs in this milestone. Parent owns GUI chooser and CLI wiring/packaging.

### Notifications teammate

Owns `Linux/openpets_linux/notifications.py`,
`Linux/openpets_linux/notification_ui.py`, `Linux/tests/test_notifications.py`.

Provide pure `safe_action_url(value)->str|None` (only http/https, reject malformed,
credentials/control chars/file/javascript/command URIs). `NotificationHistory`
methods `sync(messages:dict)`, `items()->list[dict]`, `clear()` retain bounded
session history, no disk storage; thread updates replace, not duplicate.

`NotificationCenter(parent, history, clear_callback, opener=None)` creates a GTK3
window/dialog with full text, status, optional safe URL button and per-thread
dismiss. `refresh()` updates content. `present()`/`destroy()` inherited/available.
Only a user click opens an action via Gio; no markup interpretation or auto-open.
No changes to core/CLI/main pet UI (integrator owns them). Pure tests must run
without GI; UI imports lazy. Provide a GUI smoke helper if practical.

### Plugins teammate

Owns `Linux/openpets_linux/plugins.py`, `Linux/tests/test_plugins.py`,
`docs/linux-plugins.md`.

Pure stdlib, no GI imports. Functions `read_battery(sysfs_root=None)->dict`,
`read_claude_usage(credentials_path=None, fetcher=None)->dict`,
`read_codex_usage(auth_path=None, fetcher=None)->dict`. Return normalized
`{"id":str,"label":str,"value":str,"details":list[str],"state":str}` snapshots;
quota may include `windows` list of normalized snapshots. Read credentials only
on explicit provider calls (never import/startup), support documented Linux
credential formats, HTTPS fixed provider endpoints, bounded timeout/body,
sanitized errors, no token logging. Follow existing Swift provider behavior,
record unsupported formats/provider changes honestly. No token refresh or
credential writes. Parent adds opt-in menus, background refresh and clouds.

### Integrator

Owns `core.py`, `__main__.py`, `ui.py`, `pet.py`, `smoke.py`, packaging/Docker/CI,
root docs, plugin UI/controller and integration tests. Shared modules remain
unchanged until teammate handoffs; contracts above remove waiting dependencies.

## Verification gates

1. Preserve all existing tests and byte-identical macOS protected files.
2. Focused + full stdlib tests; GI smoke under Xvfb in Docker and real XFCE.
3. Official SDK client initializes, lists tools and calls them via stdio/HTTP;
   use private sockets/configs and generated fixtures, no personal config writes.
4. Bundled pet works with no --pet; live switch and position restoration tested.
5. URL actions only on explicit clicks; quota disabled until opt-in; fixtures only.
6. Rebuild/install `.deb` in disposable container and rerun smoke. Never install
   on host without separate approval. Report actual resource measurements.
7. Independent review; do not claim provider live-account validation or hosted CI
   unless actually performed.
