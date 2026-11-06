"""CLI compatible with the initial OpenPets Unix-socket command surface."""

import argparse
import json
import math
import logging
import sys
import subprocess

from .core import ANIMATIONS, CoreError
from .ipc import IPCError, send


def parser():
    result = argparse.ArgumentParser(prog="openpets", description="OpenPets Linux GTK3 preview")
    result.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    commands = result.add_subparsers(dest="command")
    host = commands.add_parser("run", help="Show a local pet bundle on X11")
    host.add_argument("--pet")
    host.add_argument("--scale", type=float)
    notify = commands.add_parser("notify")
    notify.add_argument("--title", required=True)
    notify.add_argument("--status", required=True,
                        choices=("running", "review", "done", "failed", "waiting", "message"))
    notify.add_argument("--text")
    notify.add_argument("--thread")
    notify.add_argument("--ttl", type=float)
    notify.add_argument("--url")
    notify.add_argument("--button")
    animate = commands.add_parser("animate")
    animate.add_argument("animation", choices=ANIMATIONS)
    loop = animate.add_mutually_exclusive_group()
    loop.add_argument("--loop", action="store_true", default=None)
    loop.add_argument("--once", action="store_true")
    animate.add_argument("--ttl", type=float)
    clear = commands.add_parser("clear")
    clear.add_argument("--thread", required=True)
    for name in ("ping", "stop", "stop-animation", "state"):
        commands.add_parser(name)
    pets = commands.add_parser("pets", help="List or select a local/bundled pet")
    pets.add_argument("action", choices=("list", "select"))
    pets.add_argument("pet_id", nargs="?")
    mcp = commands.add_parser("mcp", help="Run the optional MCP server")
    mcp.add_argument("--transport", choices=("stdio", "streamable-http"), default="streamable-http")
    mcp.add_argument("--host", default="127.0.0.1")
    mcp.add_argument("--port", type=int, default=3001)
    mcp.add_argument("--endpoint", default="/mcp")
    setup = commands.add_parser("setup-mcp", help="Explicitly install the SDK into a user venv")
    setup.add_argument("--directory")
    onboarding = commands.add_parser("opencode-config", help="Print V2 config, without editing OpenCode")
    onboarding.add_argument("--transport", choices=("http", "stdio"), default="http")
    onboarding.add_argument("--port", type=int, default=3001)
    onboarding.add_argument("--endpoint", default="/mcp")
    plugin = commands.add_parser("plugins")
    plugin.add_argument("action", choices=("list", "show", "enable", "disable"))
    plugin.add_argument("name", nargs="?", choices=("battery", "claude", "codex"))
    plugin.add_argument("--allow-network", action="store_true")
    for command_parser in commands.choices.values():
        command_parser.add_argument("--socket")
    return result


def discover_pet(config):
    from .library import PetLibrary
    return PetLibrary().active_pet()


def main(argv=None):
    args = parser().parse_args(argv)
    if args.smoke_test:
        from .smoke import smoke_test
        return smoke_test()
    role = "mcp" if args.command == "mcp" else (
        "app" if args.command in (None, "run") else "cli"
    )
    from .logging_setup import configure_logging
    log_path = configure_logging(role)
    logger = logging.getLogger("openpets.cli")
    logger.info("process starting role=%s command=%s log=%s", role, args.command or "run", log_path)
    from .settings import Settings
    settings = Settings()
    config = settings.read()
    socket_path = getattr(args, "socket", None) or config["socketPath"]
    if args.command == "mcp":
        from .mcp_runtime import run_mcp
        run_mcp(socket_path, args.transport, args.host, args.port, args.endpoint)
        return 0
    if args.command == "setup-mcp":
        from .mcp_runtime import setup_environment
        print(setup_environment(args.directory))
        return 0
    if args.command == "opencode-config":
        from .mcp_runtime import opencode_configuration
        print(json.dumps(opencode_configuration(socket_path, args.transport, args.port, args.endpoint), indent=2))
        return 0
    if args.command == "pets":
        from .library import PetLibrary
        library = PetLibrary(settings)
        if args.action == "list":
            print(json.dumps(library.list_pets(), indent=2))
        else:
            if not args.pet_id:
                raise ValueError("pets select requires a pet ID")
            try:
                response = send({"type": "selectPet", "id": args.pet_id}, socket_path)
                if not response["ok"]:
                    raise ValueError(response.get("message", "Cannot select pet"))
                print(response.get("message", "Pet selected"))
            except IPCError:
                print(library.activate(args.pet_id))
        return 0
    if args.command == "plugins":
        from . import plugins
        enabled = config.get("linuxPlugins", {})
        if args.action == "list":
            print(json.dumps({name: isinstance(enabled, dict) and enabled.get(name) is True
                              for name in ("battery", "claude", "codex")}, indent=2))
            return 0
        if args.name is None:
            raise ValueError("A plugin name is required")
        if args.name != "battery" and args.action in ("enable", "show") and not args.allow_network:
            raise ValueError("Quota access reads local credentials and uses HTTPS; pass --allow-network to consent")
        if args.action == "show":
            provider = {"battery": plugins.read_battery, "claude": plugins.read_claude_usage,
                        "codex": plugins.read_codex_usage}[args.name]
            print(json.dumps(provider(), indent=2))
        else:
            command = {"type": "setPlugin", "name": args.name, "enabled": args.action == "enable"}
            try:
                response = send(command, socket_path)
                if not response["ok"]:
                    raise ValueError(response.get("message", "Cannot configure plugin"))
            except IPCError:
                flags = enabled if isinstance(enabled, dict) else {}
                settings.update({"linuxPlugins": dict(flags, **{args.name: command["enabled"]})})
            print("Plugin setting saved")
        return 0
    if args.command in (None, "run"):
        scale = getattr(args, "scale", None)
        if scale is None:
            scale = config["display"]["scale"]
        if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not math.isfinite(scale) or not 0.05 <= scale <= 2:
            raise ValueError("Scale must be between 0.05 and 2")
        directory = args.pet if args.command == "run" and args.pet else discover_pet(config)
        from .ui import run
        run(directory, scale, socket_path)
        return 0
    if args.command == "notify":
        notification = {"title": args.title, "status": args.status}
        for key, value in (("text", args.text), ("threadId", args.thread), ("ttlSeconds", args.ttl),
                           ("url", args.url), ("buttonLabel", args.button)):
            if value is not None:
                notification[key] = value
        command = {"type": "notify", "notification": notification}
    elif args.command == "animate":
        command = {"type": "playAnimation", "name": args.animation}
        if args.once:
            command["loop"] = False
        elif args.loop:
            command["loop"] = True
        if args.ttl is not None:
            command["ttlSeconds"] = args.ttl
    elif args.command == "clear":
        command = {"type": "clearMessage", "threadId": args.thread}
    else:
        command = {"type": {"ping": "ping", "stop": "shutdown", "state": "getState",
                             "stop-animation": "stopAnimation"}[args.command]}
    response = send(command, socket_path)
    print(response.get("message") or response.get("threadId") or json.dumps(response))
    return 0 if response.get("ok") else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, ImportError, IPCError, CoreError, subprocess.SubprocessError) as error:
        logging.getLogger("openpets.cli").error("command failed: %s", error)
        print(f"openpets: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        logging.getLogger("openpets.cli").exception("unhandled command failure")
        print("openpets: unexpected error; see the OpenPets log file", file=sys.stderr)
        sys.exit(1)
