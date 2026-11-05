# Linux MCP and OpenCode V2

The MCP service uses the official Python SDK (`mcp==1.26.0`, tested), not a
hand-written JSON-RPC implementation. The GUI starts its loopback service by
default; the tray/right-click checkbox controls and persists that preference.
The CLI does not start an HTTP server unless requested. No OpenCode files are
edited automatically.

## Install the optional SDK

After installing the `.deb`, explicitly run:

```sh
openpets setup-mcp
```

This downloads wheels into `$XDG_DATA_HOME/openpets/mcp-venv` (default
`~/.local/share/openpets/mcp-venv`), using system-site-packages for GTK bindings.
It requires `python3-venv` and network access to the configured Python index.
It does not modify system Python. `--directory PATH` selects another environment;
set `OPENPETS_MCP_PYTHON=/absolute/path/bin/python` when using a custom directory.

For source development the equivalent is:

```sh
uv venv --system-site-packages --python /usr/bin/python3 /path/to/mcp-venv
uv pip install --python /path/to/mcp-venv/bin/python -r Linux/requirements-mcp.txt
```

## Start HTTP

```sh
openpets mcp
```

The default is `http://127.0.0.1:3001/mcp`. `--port`, `--endpoint`, `--socket`
are configurable. Non-loopback bind hosts are refused; SDK Host/Origin checks
are retained. Same-user local clients can call this unauthenticated local service.
Do not proxy it to an untrusted network.

**MCP server (loopback)** is enabled by default in the pet's tray/right-click
menu. This starts an owned child service and saves `linuxMCPEnabled`; closing
the GUI stops only that owned process, not another independently started server.
Host/port/endpoint use `mcpHost`, `mcpPort`, `mcpEndpoint` from OpenPets config.
The checkbox reports an exited child rather than claiming a working listener.

### Troubleshoot “MCP port … is already in use”

The message means another process currently owns the configured TCP port; OpenPets
does not kill or take over another listener. Find the process (replace `3001` with
the port shown in the error):

```sh
ss -ltnp 'sport = :3001'
ps -fp PID
```

Use the PID reported by `ss` in place of `PID`. Check the command before stopping
anything. If it is a standalone `openpets mcp`, stop it with `Ctrl+C` in its
terminal. If it belongs to another application, stop that application normally.
If it is a leftover OpenPets MCP child, send it `TERM` only after confirming its
command and PID (`kill -TERM PID`); do not use `kill -9` as the first step. Then
enable the MCP checkbox again.

Alternatively, change `mcpPort` in the OpenPets settings file (normally
`~/.config/openpets/config.json`, or `$XDG_CONFIG_HOME/openpets/config.json` when
`XDG_CONFIG_HOME` is set) to an unused port, for example `3002`. Preserve the
other settings in that JSON file. Update clients to use the same port; for
OpenCode, print the matching config with `openpets opencode-config --port 3002`.
The service remains loopback-only.

Nine tools: `get_openpets_status`, `wake_pet`, `stop_pet`, `notify`,
`play_pet_animation`, `stop_pet_animation`, `clear_pet_message`, `ping_pet`,
`list_pet_animations`. `notify` returns a `threadId`; reuse it for updates. Optional `url` and
`buttonLabel` produce click-only HTTP(S) actions. Errors are reported as MCP
tool errors (status queries explicitly report a sleeping/unreachable pet).
`wake_pet` verifies the host is actually reachable before reporting success.

`play_pet_animation` accepts the canonical names `idle`, `running-right`,
`running-left`, `waving`, `jumping`, `failed`, `waiting`, `running`, `review`.
Aliases `runningRight`/`right`/`runright` resolve to `running-right` and
`runningLeft`/`left`/`runleft` to `running-left`. `falled` (a misspelling of
`failed`) is deliberately not aliased and is rejected. Use `stop_pet_animation`
to return the pet to idle without clearing notifications.

Use `list_pet_animations` to discover the full catalog at runtime: it returns
the nine canonical animations with their aliases, a brief purpose, and the
zero-based `atlas_row` (the spritesheet row used by the GTK host). This is a
local catalog query and does not require the pet to be running.

Finite vs looping animation (deliberate Linux UX divergence from macOS): only
the completion/success statuses `done`, `success`, `completed`, `complete`,
`committed` play a finite cycle then return to idle. `running`, `review`,
`waiting`, `waving`, `failed` and `message` remain animated while active
(looping) until manually stopped, replaced, or cleared by TTL; `notify` with a
`ttlSeconds` auto-expires non-finite animations to idle.

## OpenCode V2

Print configuration without modifying any files:

```sh
openpets opencode-config
openpets opencode-config --transport stdio
```

Merge the printed server into the appropriate OpenCode config, preserving
unrelated settings. V2 uses **`mcp.servers`**, not servers directly under `mcp`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "openpets": {
        "type": "remote",
        "url": "http://127.0.0.1:3001/mcp"
      }
    }
  }
}
```

For an installed application, a lightweight on-demand stdio alternative is:

```jsonc
{
  "mcp": {
    "servers": {
      "openpets": {
        "type": "local",
        "command": ["/usr/bin/openpets", "mcp", "--transport", "stdio"]
      }
    }
  }
}
```

V2 watches config changes; use `/reload` if needed, then `/mcps` or
`opencode mcp list` to check connection. This local server has no OAuth login.
The generated source-mode stdio config supplies an absolute Python path and
package-root `PYTHONPATH`. Never replace the entire user's configuration with
this fragment. Reference: <https://opencode.ai/v2/docs/mcp-servers>.

## Verification boundary

Tests use official SDK clients over HTTP and stdio, private sockets/configs,
all nine tool calls, cold wake-up and Host/Origin protection. A successful SDK
test does not mean the user's live OpenCode instance has been configured; that
requires a separately approved config change and a connected status.
