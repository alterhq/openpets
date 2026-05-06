# Shared Pet System

OpenPets is designed around one visible desktop companion per user. In the normal menu bar setup, the MCP server, CLI client commands, and Swift apps using the default client all converge on the same local pet, so separate tools can report progress through one shared UI instead of each app creating its own pet.

## Default Topology

The default setup has three layers:

1. The menu bar app owns the visible pet session.
2. The MCP server runs inside the menu bar app and sends tool calls to that pet session.
3. Socket clients, including CLI commands such as `notify`, `animate`, `stop-animation`, `clear`, `ping`, and Swift apps using `OpenPetsClient`, connect to the pet session through the configured Unix socket.

```text
MCP clients
  -> http://127.0.0.1:3001/mcp
  -> menu bar MCP server
  -> visible pet session

CLI client commands and Swift apps
  -> /tmp/openpets-UID.sock
  -> visible pet session
```

MCP clients do not connect to the Unix socket directly. They connect to the local HTTP MCP endpoint, and the MCP server forwards commands to the pet session it manages. That same pet session also listens on the configured Unix socket for CLI client commands and direct Swift clients.

The `openpets run` command is different: it starts a pet host. Use it when you intentionally want to run a pet host yourself. If another host is already listening on the same socket path, startup fails instead of replacing the existing pet.

## Shared Defaults

By default, OpenPets uses a per-user socket path:

```text
/tmp/openpets-UID.sock
```

The actual default is computed from the current Unix user ID, so two macOS users on the same machine do not collide. The default config stores this value as `socketPath`. The menu bar app and CLI read that config when choosing their socket path.

`OpenPetsClient()` uses the built-in default socket path directly. If your app should honor the user's configured `socketPath`, load the OpenPets configuration and pass the socket path into the client.

Default config location:

```text
~/.config/openpets/config.json
```

If `XDG_CONFIG_HOME` is set, the config lives at:

```text
$XDG_CONFIG_HOME/openpets/config.json
```

## What App Developers Should Do

Use the built-in default when your app wants to participate in the user's shared OpenPets companion and does not need custom configuration:

```swift
import OpenPetsKit

let client = OpenPetsClient()

let response = try client.send(.notify(PetNotification(
    title: "Export Running",
    text: "Generating the customer report.",
    status: "running"
)))

let threadId = response.threadId
```

Honor the user's configured socket when your app should follow `~/.config/openpets/config.json`:

```swift
let configuration = try OpenPetsConfiguration.loadOrCreateDefault()
let client = OpenPetsClient(socketPath: configuration.socketPath)
```

Check whether the shared pet is already running before deciding to start your own host:

```swift
let client = OpenPetsClient()
if client.isPetRunning() {
    try client.send(.playAnimation(name: .waving))
} else {
    // Start or prompt for the user's preferred OpenPets host.
}
```

To stop only the current animation and return the pet to idle without clearing messages or stopping the host, send `.stopAnimation`. Use `.shutdown` or `stop_pet` only when the user explicitly wants the pet process hidden or stopped.

Then reuse the returned `threadId` when updating the same operation:

```swift
try client.send(.notify(PetNotification(
    title: "Export Complete",
    text: "The customer report is ready.",
    status: "done",
    threadId: threadId
)))
```

This keeps one task's bubble updated in place. If you omit `threadId` for every update, OpenPets treats each update as a separate task and creates separate bubbles.

## Threading Model

The shared pet can show multiple task bubbles at once. The unit of identity is `threadId`, not the calling app.

- Omit `threadId` for the first notification of a distinct task.
- Store the returned `threadId` for that task.
- Pass the same `threadId` for progress, waiting, review, failed, and done updates for that task.
- Use a different `threadId` for unrelated concurrent work.
- Clear only the task you own with `clear_pet_message` or `.clearMessage(threadId:)`.

This lets several tools use the same pet without overwriting each other's messages.

## When to Use a Custom Socket

Most apps should not set a custom socket path unless they are intentionally honoring the user's OpenPets config. A custom socket creates an explicit boundary and usually means you are targeting a different pet host.

Use a custom socket only when you intentionally need isolation, such as:

- Running integration tests.
- Developing a second pet host locally.
- Keeping an experimental tool separate from the user's normal desktop pet.

CLI host example:

```sh
swift run openpets run --pet /path/to/starcorn --socket /tmp/openpets-dev.sock
swift run openpets notify --socket /tmp/openpets-dev.sock --title "Dev Pet" --status message --text "This targets the dev socket."
```

Swift example:

```swift
let client = OpenPetsClient(socketPath: "/tmp/openpets-dev.sock")
```

If one process starts a pet host on the default socket and another process starts a different host on a custom socket, those are separate pets. Notifications sent to one socket will not appear on the other pet. Starting two hosts on the same socket is blocked; use a custom socket only when you intentionally want a second pet.

## MCP Integration Notes

Apps and agents using MCP should connect to the configured MCP URL:

```text
http://127.0.0.1:3001/mcp
```

The menu bar app starts the MCP server and wakes the pet automatically for `notify`. MCP clients should still treat `threadId` the same way as socket clients:

1. First `notify` for a task omits `threadId`.
2. The response returns a `threadId`.
3. Later updates for the same task pass that `threadId`.

The MCP server is local by default. Binding it to `0.0.0.0`, `::`, or an empty host can expose the pet controls to other devices on the network, so only do that on trusted networks.

## Practical Guidance

Prefer `notify` for meaningful user-visible task state: running work, completion, failure, waiting states, and review requests. Prefer `play_pet_animation` only for visual feedback that does not need text.

Use concise titles and concrete status text. The pet is shared across apps, so messages should identify the actual operation well enough for the user to understand which tool or task changed.

Do not stop the pet from an app unless the user explicitly asked your app to hide or quit it. The shared pet may be in use by other tools.
