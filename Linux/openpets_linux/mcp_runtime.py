"""Optional SDK environment and owned MCP HTTP process lifecycle."""

import errno
import importlib.util
import logging
import os
from pathlib import Path
import subprocess
import socket
import sys

logger = logging.getLogger(__name__)


def module_root():
    return Path(__file__).resolve().parent.parent


def child_environment():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(module_root())
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def environment_path():
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "openpets/mcp-venv"


def mcp_python():
    if importlib.util.find_spec("mcp") is not None:
        return sys.executable
    candidate = Path(os.environ.get("OPENPETS_MCP_PYTHON", str(environment_path() / "bin/python")))
    if candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    raise RuntimeError("MCP SDK is missing. Run 'openpets setup-mcp' once, then start MCP again.")


def setup_environment(directory=None):
    target = Path(directory).expanduser().absolute() if directory else environment_path()
    if target.exists() and (target.is_symlink() or not (target / "pyvenv.cfg").is_file()):
        raise ValueError("Refusing to overwrite a non-venv MCP directory")
    requirements = module_root() / "requirements-mcp.txt"
    if not requirements.is_file():
        raise RuntimeError("Packaged MCP requirements file is missing")
    subprocess.run(["/usr/bin/python3", "-I", "-m", "venv", "--system-site-packages", str(target)], check=True)
    subprocess.run([str(target / "bin/python"), "-I", "-m", "pip", "install",
                    "--disable-pip-version-check", "--only-binary=:all:", "-r", str(requirements)], check=True)
    return target


def run_mcp(socket_path, transport, host, port, endpoint):
    logger.info("MCP process initializing transport=%s endpoint=%s", transport, endpoint)
    python = mcp_python()
    if importlib.util.find_spec("mcp") is None:
        if os.environ.get("OPENPETS_MCP_REEXEC") == "1":
            raise RuntimeError("Configured MCP Python does not contain the SDK")
        environment = child_environment()
        environment["OPENPETS_MCP_REEXEC"] = "1"
        logger.info("re-executing MCP process with isolated interpreter=%s", python)
        os.chdir(module_root())
        os.execve(python, [python, "-m", "openpets_linux", "mcp", "--transport", transport,
                          "--host", host, "--port", str(port), "--endpoint", endpoint,
                          "--socket", socket_path], environment)
    from .mcp_server import run_server
    run_server(socket_path, transport=transport, host=host, port=port, endpoint=endpoint)


def opencode_configuration(socket_path, transport="http", port=3001, endpoint="/mcp"):
    from .mcp_server import validate_server
    validate_server("127.0.0.1", port, endpoint)
    if transport == "http":
        entry = {"type": "remote", "url": f"http://127.0.0.1:{port}{endpoint}"}
    else:
        entry = {"type": "local", "command": [mcp_python(), "-m", "openpets_linux", "mcp",
                  "--transport", "stdio", "--socket", socket_path],
                 "cwd": str(module_root()), "environment": {"PYTHONPATH": str(module_root())}}
    return {"$schema": "https://opencode.ai/config.json", "mcp": {"servers": {"openpets": entry}}}


class MCPPortBusy(RuntimeError):
    """Retryable condition: another process currently owns the MCP endpoint."""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        super().__init__(
            f"MCP port {port} on {host} is already in use. OpenPets will retry "
            "automatically when the port is available."
        )


class MCPProcess:
    def __init__(self):
        self.process = None
        self.host = None
        self.port = None

    def start(self, socket_path, config):
        if self.running():
            return
        from .mcp_server import validate_server
        host, port, endpoint = (config.get("mcpHost", "127.0.0.1"),
                                config.get("mcpPort", 3001), config.get("mcpEndpoint", "/mcp"))
        validate_server(host, port, endpoint)
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError as error:
                if error.errno == errno.EADDRINUSE:
                    raise MCPPortBusy(host, port) from None
                detail = error.strerror or str(error)
                raise RuntimeError(
                    f"Cannot check MCP port {port} on {host}: {detail}. "
                    "Check the bind host and local permissions."
                ) from None
        self.process = subprocess.Popen([
            mcp_python(), "-m", "openpets_linux", "mcp", "--transport", "streamable-http",
            "--host", host, "--port", str(port), "--endpoint", endpoint, "--socket", socket_path,
        ], env=child_environment(), cwd=module_root(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.host = host
        self.port = port
        logger.info("spawned MCP child pid=%s host=%s port=%s endpoint=%s",
                    self.process.pid, host, port, endpoint)

    def running(self):
        return self.process is not None and self.process.poll() is None

    def ready(self, timeout=0.15):
        """Return true only once the child is alive and its TCP endpoint accepts connections."""
        if not self.running() or self.host is None or self.port is None:
            return False
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def stop(self):
        process, self.process = self.process, None
        self.host = None
        self.port = None
        if process is None or process.poll() is not None:
            return
        logger.info("stopping MCP child pid=%s", process.pid)
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            logger.warning("MCP child pid=%s did not stop in time; sending kill", process.pid)
            process.kill()
            process.wait(timeout=2)
        logger.info("MCP child pid=%s stopped returncode=%s", process.pid, process.returncode)
