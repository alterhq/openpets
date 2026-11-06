"""Official SDK MCP transports forwarding to the local GTK Unix socket."""

import importlib.util
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

from .core import ANIMATIONS, ANIMATION_ALIASES, list_pet_animations as core_list_pet_animations, resolve_animation
from .ipc import IPCError, send

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
STATUS_VALUES = ("running", "review", "done", "failed", "waiting", "message")
ANIMATION_VALUES = ANIMATIONS
TOOL_NAMES = ("get_openpets_status", "wake_pet", "stop_pet", "notify", "play_pet_animation",
              "stop_pet_animation", "clear_pet_message", "ping_pet", "list_pet_animations")
_wake_lock = threading.Lock()


class MCPUnavailable(RuntimeError):
    pass


def _resolve_socket_path(path):
    return path or f"/tmp/openpets-{os.getuid()}.sock"


def _resolve_animation(name):
    return resolve_animation(name)


def _ipc(command, socket_path):
    try:
        return send(command, _resolve_socket_path(socket_path))
    except (IPCError, OSError, ValueError) as error:
        return {"ok": False, "message": str(error)}


def get_openpets_status(socket_path=None):
    result = _ipc({"type": "ping"}, socket_path)
    result["socketPath"] = _resolve_socket_path(socket_path)
    if result["ok"]:
        result["message"] = "pet is running"
    return result


def wake_pet(socket_path=None):
    path = _resolve_socket_path(socket_path)
    with _wake_lock:
        if _ipc({"type": "showPet"}, path)["ok"]:
            return {"ok": True, "message": "pet is awake"}
        if importlib.util.find_spec("gi") is None:
            return {"ok": False, "message": "GTK/PyGObject (gi) is unavailable in this Python"}
        environment = dict(os.environ, PYTHONPATH=str(PACKAGE_ROOT))
        try:
            process = subprocess.Popen([sys.executable, "-m", "openpets_linux", "run", "--socket", path],
                                       env=environment, cwd=PACKAGE_ROOT, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
        except OSError as error:
            return {"ok": False, "message": f"cannot launch pet: {error}"}
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if _ipc({"type": "ping"}, path)["ok"]:
                # Reap an owned host eventually, without killing it on MCP exit.
                threading.Thread(target=process.wait, daemon=True).start()
                return {"ok": True, "message": "pet is awake"}
            if process.poll() is not None:
                return {"ok": False, "message": "Pet exited during startup; check X11 display and pet assets"}
            time.sleep(0.05)
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return {"ok": False, "message": "Pet startup timed out"}


def stop_pet(socket_path=None):
    return _ipc({"type": "shutdown"}, socket_path)


def notify(socket_path=None, title="", text="", status="", threadId=None,
           url=None, buttonLabel=None, ttlSeconds=None, kind="chat"):
    if not isinstance(title, str) or not title or status not in STATUS_VALUES:
        return {"ok": False, "message": "A title and supported status are required"}
    if kind not in (None, "status", "chat", "conversation"):
        return {"ok": False, "message": "kind must be status, chat, or conversation"}
    # All notifications are presented as conversational text by comic chat.
    notification = {"title": title, "text": text, "status": status, "kind": "chat"}
    for key, value in (("threadId", threadId), ("url", url), ("buttonLabel", buttonLabel), ("ttlSeconds", ttlSeconds)):
        if value is not None:
            notification[key] = value
    return _ipc({"type": "notify", "notification": notification}, socket_path)


def play_pet_animation(socket_path=None, name="", loop=True, ttlSeconds=None):
    name = resolve_animation(name)
    if name is None or not isinstance(loop, bool):
        return {"ok": False, "message": "Invalid animation name or loop flag"}
    command = {"type": "playAnimation", "name": name, "loop": loop}
    if ttlSeconds is not None:
        command["ttlSeconds"] = ttlSeconds
    return _ipc(command, socket_path)


def stop_pet_animation(socket_path=None):
    return _ipc({"type": "stopAnimation"}, socket_path)


def clear_pet_message(socket_path=None, threadId=None):
    if not isinstance(threadId, str) or not threadId:
        return {"ok": False, "message": "threadId is required"}
    return _ipc({"type": "clearMessage", "threadId": threadId}, socket_path)


def ping_pet(socket_path=None):
    return _ipc({"type": "ping"}, socket_path)


def list_pet_animations(socket_path=None):
    """Return the animation catalog (local; does not require the pet to be running)."""
    return {"ok": True, "animations": core_list_pet_animations()}


def validate_server(host, port, endpoint):
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("MCP must bind to loopback")
        except ValueError:
            raise ValueError("MCP host must be localhost or a loopback IP") from None
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Invalid MCP port")
    if not isinstance(endpoint, str) or not endpoint.startswith("/") or any(c in endpoint for c in "?#\r\n"):
        raise ValueError("Invalid MCP endpoint")


def create_server(socket_path=None, host="127.0.0.1", port=3001, endpoint="/mcp"):
    validate_server(host, port, endpoint)
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.exceptions import ToolError
        from mcp.server.fastmcp import server as sdk_module
    except ImportError as error:
        raise MCPUnavailable("Install MCP using 'openpets setup-mcp' or requirements-mcp.txt") from error
    # SDK 1.26 leaves a forward annotation unresolved with newer pydantic-settings.
    # Rebuild using its defining module, instead of suppressing validation warnings.
    if not sdk_module.Settings.__pydantic_complete__:
        sdk_module.Settings.model_rebuild(_types_namespace=vars(sdk_module))
    server = FastMCP("openpets", instructions=(
        "Use wake_pet before reporting work if the pet is asleep. Use notify for "
        "meaningful progress, review, completion or failure. Every notify is a chat "
        "message and must be marked kind='chat' for the optional comic bubble. "
        "Preserve its threadId "
        "for updates to the same task. Do not spam updates or stop the pet unless "
        "the user requests it. Notification action URLs open only after a user click."
    ), host=host, port=port, streamable_http_path=endpoint,
                     json_response=True, stateless_http=True)
    path = _resolve_socket_path(socket_path)

    def result(value):
        if not value.get("ok"):
            raise ToolError(value.get("message", "Pet command failed"))
        return value

    @server.tool(name="get_openpets_status")
    def tool_status() -> dict[str, Any]:
        """Read whether the local pet is awake and its socket path."""
        return get_openpets_status(path)

    @server.tool(name="wake_pet")
    def tool_wake() -> dict[str, Any]:
        """Start or reveal the desktop pet before sending progress notifications."""
        return result(wake_pet(path))

    @server.tool(name="stop_pet")
    def tool_stop() -> dict[str, Any]:
        """Stop the pet only when the user explicitly requests it."""
        return result(stop_pet(path))

    @server.tool(name="notify")
    def tool_notify(title: str, status: str, text: str = "", threadId: str | None = None,
                    url: str | None = None, buttonLabel: str | None = None,
                    ttlSeconds: float | None = None, kind: str = "chat") -> dict[str, Any]:
        """Notify an update. Always mark every notify as kind='chat'.

        Save returned threadId and reuse it to update the same task/chat thread.
        """
        return result(notify(path, title, text, status, threadId, url, buttonLabel, ttlSeconds, kind))

    @server.tool(name="play_pet_animation")
    def tool_animation(name: str, loop: bool = True, ttlSeconds: float | None = None) -> dict[str, Any]:
        """Play an animation without text: idle, running-right/left, waving, jumping, failed, waiting, running, review.
        Accepts aliases runningRight/right/runright and runningLeft/left/runleft."""
        return result(play_pet_animation(path, name, loop, ttlSeconds))

    @server.tool(name="stop_pet_animation")
    def tool_idle() -> dict[str, Any]:
        """Return the pet to idle without clearing notifications."""
        return result(stop_pet_animation(path))

    @server.tool(name="list_pet_animations")
    def tool_list_animations() -> dict[str, Any]:
        """List discoverable pet animations: canonical names, aliases, purpose, atlas row."""
        return list_pet_animations(path)

    @server.tool(name="clear_pet_message")
    def tool_clear(threadId: str) -> dict[str, Any]:
        """Dismiss one task thread; do not clear another agent's thread."""
        return result(clear_pet_message(path, threadId))

    @server.tool(name="ping_pet")
    def tool_ping() -> dict[str, Any]:
        """Confirm the desktop pet can receive commands."""
        return result(ping_pet(path))

    return server


def run_server(socket_path, transport="streamable-http", host="127.0.0.1", port=3001, endpoint="/mcp"):
    if transport not in ("stdio", "streamable-http"):
        raise ValueError("Unsupported MCP transport")
    server = create_server(socket_path, host, port, endpoint)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        import uvicorn
        # MCP uses HTTP/SSE, not WebSockets. Avoid importing an unrelated system
        # websockets version exposed by the system-site-packages GI environment.
        uvicorn.run(server.streamable_http_app(), host=host, port=port, ws="none", log_level="warning")
