"""Real GTK + SDK client tests, using only private config/socket/fixture paths."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from openpets_linux.ipc import IPCError, send
from openpets_linux.library import bundled_starcorn_path
from openpets_linux.mcp_server import TOOL_NAMES

ROOT = str(Path(__file__).resolve().parents[1])
HAS_SDK = importlib.util.find_spec("mcp") is not None
HAS_DISPLAY = bool(os.environ.get("DISPLAY")) and importlib.util.find_spec("gi") is not None


@unittest.skipUnless(HAS_DISPLAY, "GTK integration requires DISPLAY (use xvfb-run)")
class FeatureIntegration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="openpets-features-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.socket_path = str(self.root / "pet.sock")
        self.environment = dict(os.environ, HOME=str(self.root), PYTHONPATH=ROOT,
                                XDG_CONFIG_HOME=str(self.root / "config"),
                                XDG_DATA_HOME=str(self.root / "data"))
        self.log = tempfile.TemporaryFile(mode="w+")
        self.addCleanup(self.log.close)
        self.host = None
        self.start_host()

    def start_host(self):
        self.host = subprocess.Popen([sys.executable, "-m", "openpets_linux", "run",
                                      "--socket", self.socket_path], env=self.environment,
                                     stdout=subprocess.DEVNULL, stderr=self.log)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.host.poll() is not None:
                self.log.seek(0)
                self.fail("GTK host exited: " + self.log.read())
            try:
                if send({"type": "ping"}, self.socket_path, timeout=.2)["ok"]:
                    return
            except IPCError:
                pass
            time.sleep(.05)
        self.fail("GTK startup timed out")

    def tearDown(self):
        if self.host and self.host.poll() is None:
            try:
                send({"type": "shutdown"}, self.socket_path)
                self.host.wait(timeout=4)
            except (IPCError, subprocess.TimeoutExpired):
                self.host.terminate()
                self.host.wait(timeout=3)
        self.log.seek(0)
        output = self.log.read()
        self.assertNotIn("Traceback", output)
        self.assertNotIn("CRITICAL", output)

    def test_default_pet_live_switch_history_and_position(self):
        initial = send({"type": "getState"}, self.socket_path)
        self.assertEqual(initial["activePet"], "starcorn")
        alternate = self.root / "data/openpets/pets/test-pet"
        shutil.copytree(bundled_starcorn_path(), alternate)
        manifest = json.loads((alternate / "pet.json").read_text())
        manifest.update(id="test-pet", displayName="Integration pet")
        (alternate / "pet.json").write_text(json.dumps(manifest))
        self.assertTrue(send({"type": "selectPet", "id": "test-pet"}, self.socket_path)["ok"])
        result = send({"type": "notify", "notification": {
            "title": "Action", "status": "review", "url": "https://example.com/task", "buttonLabel": "Review"
        }}, self.socket_path)
        self.assertTrue(result["ok"])
        send({"type": "clearMessage", "threadId": result["threadId"]}, self.socket_path)
        state = send({"type": "getState"}, self.socket_path)
        self.assertEqual(state["messageCount"], 0)
        self.assertEqual(state["historyCount"], 1)
        self.assertEqual(state["activePet"], "test-pet")
        self.assertEqual(state["plugins"], [])
        self.assertFalse(send({"type": "notify", "notification": {
            "title": "Unsafe", "status": "message", "url": "file:///etc/passwd"
        }}, self.socket_path)["ok"])
        send({"type": "shutdown"}, self.socket_path)
        self.host.wait(timeout=4)
        positions = json.loads((self.root / "config/openpets/positions.json").read_text())
        expected = positions["test-pet"]
        self.start_host()
        restored = send({"type": "getState"}, self.socket_path)
        self.assertEqual(restored["activePet"], "test-pet")
        self.assertEqual(restored["position"], expected)

    @unittest.skipUnless(HAS_SDK, "Official MCP SDK required")
    def test_stdio_official_client(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def run():
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "openpets_linux", "mcp", "--transport", "stdio", "--socket", self.socket_path],
                env=self.environment)
            async with stdio_client(params, errlog=self.log) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    self.assertEqual({tool.name for tool in (await client.list_tools()).tools}, set(TOOL_NAMES))
                    response = await client.call_tool("ping_pet", {})
                    self.assertFalse(response.isError)
                    self.assertTrue(response.structuredContent["ok"])
                    invalid = await client.call_tool("play_pet_animation", {"name": "invalid"})
                    self.assertTrue(invalid.isError)
                    stopped = await client.call_tool("stop_pet", {})
                    self.assertFalse(stopped.isError)
                    self.host.wait(timeout=4)
                    try:
                        awake = await client.call_tool("wake_pet", {})
                        self.assertFalse(awake.isError, str(awake))
                        self.assertTrue(send({"type": "getState"}, self.socket_path)["ok"])
                    finally:
                        await client.call_tool("stop_pet", {})
        asyncio.run(asyncio.wait_for(run(), timeout=20))

    @unittest.skipUnless(HAS_SDK, "Official MCP SDK required")
    def test_http_all_tools_official_client(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-m", "openpets_linux", "mcp",
            "--socket", self.socket_path, "--port", str(port)], env=self.environment,
            stdout=subprocess.DEVNULL, stderr=self.log)
        self.addCleanup(lambda: self.stop_process(server))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if server.poll() is not None:
                self.fail("MCP HTTP server exited")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(.05)

        async def run():
            url = f"http://127.0.0.1:{port}/mcp"
            async with httpx.AsyncClient(trust_env=False) as http:
                response = await http.post(url, headers={"Origin": "https://evil.example"}, json={})
                self.assertIn(response.status_code, (400, 403, 421))
                response = await http.post(url, headers={"Host": "evil.example"}, json={})
                self.assertIn(response.status_code, (400, 403, 421))
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    self.assertEqual({t.name for t in (await client.list_tools()).tools}, set(TOOL_NAMES))
                    async def call(name, arguments=None):
                        result = await client.call_tool(name, arguments or {})
                        self.assertFalse(result.isError, str(result))
                        self.assertTrue(result.structuredContent["ok"])
                        return result.structuredContent
                    await call("get_openpets_status")
                    await call("wake_pet")
                    await call("ping_pet")
                    thread = (await call("notify", {"title": "HTTP", "status": "running"}))["threadId"]
                    updated = await call("notify", {"title": "Done", "status": "done", "threadId": thread})
                    self.assertEqual(updated["threadId"], thread)
                    await call("play_pet_animation", {"name": "waving", "loop": False})
                    await call("stop_pet_animation")
                    await call("clear_pet_message", {"threadId": thread})
                    await call("stop_pet")
        asyncio.run(asyncio.wait_for(run(), timeout=20))

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
