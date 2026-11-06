"""Isolated GTK/IPC integration test; never touches the user's pet/config/socket."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .ipc import IPCError, send


def smoke_test():
    import cairo

    with tempfile.TemporaryDirectory(prefix="openpets-smoke-") as temporary:
        directory = Path(temporary)
        # Deterministic local fixture: no network or upstream asset redistribution.
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8 * 32, 9 * 32)
        context = cairo.Context(surface)
        for row in range(9):
            for column in range(8):
                context.set_source_rgba(0.2 + row / 20, 0.5, 0.2 + column / 20, 1)
                context.arc(column * 32 + 16, row * 32 + 16, 13, 0, 6.28319)
                context.fill()
        surface.write_to_png(str(directory / "sprite.png"))
        (directory / "pet.json").write_text(json.dumps({
            "id": "smoke", "displayName": "Smoke fixture", "spritesheetPath": "sprite.png"
        }))
        socket_path = str(directory / "host.sock")
        environment = dict(os.environ, XDG_CONFIG_HOME=str(directory / "config"),
                           PYTHONPATH=str(Path(__file__).resolve().parent.parent))
        process = subprocess.Popen([
            sys.executable, "-m", "openpets_linux", "run", "--pet", str(directory),
            "--socket", socket_path, "--scale", "1"
        ], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    _, error = process.communicate()
                    raise RuntimeError(f"GTK host failed to start: {error}")
                try:
                    if send({"type": "ping"}, socket_path)["ok"]:
                        break
                except (OSError, IPCError):
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("GTK host startup timeout")
                time.sleep(0.05)
            notification = send({"type": "notify", "notification": {
                "title": "Smoke", "status": "running", "text": "GTK + IPC", "ttlSeconds": 2
            }}, socket_path)
            assert notification["ok"] and notification["threadId"]
            thread = notification["threadId"]
            assert send({"type": "notify", "notification": {
                "title": "Done", "status": "done", "threadId": thread
            }}, socket_path)["threadId"] == thread
            from .core import ANIMATIONS
            for animation in ANIMATIONS:
                assert send({"type": "playAnimation", "name": animation}, socket_path)["ok"]
                assert send({"type": "getState"}, socket_path)["animation"] == animation
            assert send({"type": "stopAnimation"}, socket_path)["ok"]
            assert send({"type": "clearMessage", "threadId": thread}, socket_path)["ok"]
            assert send({"type": "shutdown"}, socket_path)["ok"]
            _, error = process.communicate(timeout=5)
            if process.returncode != 0 or "Traceback" in error or "CRITICAL" in error:
                raise RuntimeError(f"GTK host exited incorrectly: {error}")
            if Path(socket_path).exists():
                raise RuntimeError("GTK host left its socket behind")
            print("GTK3 smoke passed: window, notification threads, animations, IPC, shutdown cleanup")
            return 0
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
