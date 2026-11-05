"""Unix-domain socket IPC for OpenPets on Linux (standard library only).

Wire protocol (matches openpetskit-linux-survey v0.2.1):

    * client connects to an AF_UNIX SOCK_STREAM socket
    * sends one JSON command line terminated with 0x0a (UTF-8)
    * reads one JSON response line

Frames larger than MAX_FRAME (64 KiB) are refused on both sides.

The GTK host uses bind_socket with GLib I/O watches on listener and clients.
"""

from __future__ import annotations

import json
import math
import os
import socket
import stat
import time

__all__ = [
    "MAX_FRAME",
    "IPCError",
    "ProtocolError",
    "SocketAlreadyInUse",
    "send",
    "bind_socket",
    "cleanup_socket",
]

MAX_FRAME = 64 * 1024


class IPCError(Exception):
    """Base class for IPC failures."""


class ProtocolError(IPCError):
    """Malformed protocol data."""


class SocketAlreadyInUse(IPCError):
    """A live listener already owns the socket path."""


def send(command, socket_path: str, timeout: float = 2) -> dict:
    """Send one command dict and return the decoded PetResponse dict.

    The payload is newline-terminated UTF-8 JSON and is capped at MAX_FRAME.
    Connect, send, and receive are all bounded by ``timeout`` seconds.
    """
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise IPCError("timeout must be a positive finite number")
    deadline = time.monotonic() + timeout
    payload = (
        json.dumps(command, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise IPCError(
            f"command payload is {len(payload)} bytes, limit {MAX_FRAME}"
        )

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as fd:
        fd.settimeout(timeout)
        try:
            fd.connect(socket_path)
        except FileNotFoundError as exc:
            raise IPCError(f"no server listening at {socket_path}") from exc
        except socket.timeout as exc:
            raise IPCError(f"connect to {socket_path} timed out") from exc
        except OSError as exc:
            raise IPCError(f"could not connect to {socket_path}: {exc}") from exc

        view = memoryview(payload)
        while view:
            _set_deadline(fd, deadline)
            try:
                written = fd.send(view)
            except socket.timeout as exc:
                raise IPCError("write to socket timed out") from exc
            except OSError as exc:
                raise IPCError(f"socket write failed: {exc}") from exc
            if written == 0:
                raise IPCError("socket closed while writing command")
            view = view[written:]

        line = _read_line(fd, deadline)

    if line is None:
        raise IPCError("server closed the connection without a response")

    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("server response was not valid UTF-8") from exc
    try:
        response = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"server response was not valid JSON: {exc}") from exc
    if not isinstance(response, dict):
        raise ProtocolError("server response was not a JSON object")
    if not isinstance(response.get("ok"), bool):
        raise ProtocolError("server response requires a boolean ok field")
    return response


def _set_deadline(fd, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IPCError("socket deadline exceeded")
    fd.settimeout(remaining)


def _read_line(fd, deadline=None) -> bytes | None:
    """Read one newline-terminated line from a socket, or None on EOF/timeout.

    Frames larger than MAX_FRAME are refused (None).
    """
    data = bytearray()
    while len(data) <= MAX_FRAME:
        if deadline is not None:
            _set_deadline(fd, deadline)
        try:
            chunk = fd.recv(min(4096, MAX_FRAME + 1 - len(data)))
        except socket.timeout:
            return None
        except InterruptedError:
            continue
        except OSError:
            return None
        if not chunk:
            break
        data += chunk
        newline = data.find(b"\n")
        if newline >= 0:
            if newline + 1 > MAX_FRAME:
                return None
            return bytes(data[:newline])
    return None


def bind_socket(path: str) -> socket.socket:
    """Create, bind, and listen on a Unix socket at ``path``.

    Refuses symlinks, regular files, live sockets, and sockets owned by
    another user; reclaims stale (unlistened) sockets owned by us. The
    returned listener is nonblocking and the node is created mode 0600.
    """
    original_stat = None
    if os.path.lexists(path):
        current = os.lstat(path)
        original_stat = current
        if stat.S_ISLNK(current.st_mode):
            raise IPCError(f"refusing to bind over symlink: {path}")
        if not stat.S_ISSOCK(current.st_mode):
            raise IPCError(
                f"socket path exists and is not a Unix socket: {path}"
            )
        if current.st_uid != os.getuid():
            raise IPCError(f"socket path is owned by another user: {path}")
        if _socket_has_listener(path):
            raise SocketAlreadyInUse(f"socket already in use: {path}")
        # Stale socket owned by us: nobody is listening, reclaim it.
        try:
            latest = os.lstat(path)
            if (latest.st_dev, latest.st_ino) != (current.st_dev, current.st_ino):
                raise IPCError(f"socket path changed during stale check: {path}")
            os.remove(path)
        except OSError as exc:
            raise IPCError(f"could not remove stale socket {path}: {exc}") from exc

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.setblocking(False)
    mask = os.umask(0o077)
    try:
        listener.bind(path)
    except OSError as exc:
        listener.close()
        raise IPCError(f"could not bind {path}: {exc}") from exc
    finally:
        os.umask(mask)
    original_stat = os.lstat(path)
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        listener.close()
        _remove_if_ours(path, original_stat)
        raise IPCError(f"could not set permissions on {path}: {exc}") from exc
    try:
        listener.listen(128)
    except OSError as exc:
        listener.close()
        _remove_if_ours(path, original_stat)
        raise IPCError(f"could not listen on {path}: {exc}") from exc
    return listener


def _socket_has_listener(path: str) -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        try:
            probe.connect(path)
        except (ConnectionRefusedError, FileNotFoundError):
            return False
        except OSError as exc:
            raise IPCError(f"cannot determine whether socket is live: {path}: {exc}") from exc
        return True
    finally:
        probe.close()


def _remove_if_ours(path: str, original_stat) -> None:
    if original_stat is None:
        return
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(current.st_mode) or current.st_uid != os.getuid():
        return
    if (
        current.st_dev,
        current.st_ino,
    ) != (original_stat.st_dev, original_stat.st_ino):
        return
    try:
        os.remove(path)
    except OSError:
        pass


def cleanup_socket(path: str, original_stat) -> None:
    """Remove the socket node at ``path`` only if it is still the one we bound.

    Identity is compared via (st_dev, st_ino) against ``original_stat``, so a
    socket created by another process after we unbound is never deleted.
    """
    _remove_if_ours(path, original_stat)
