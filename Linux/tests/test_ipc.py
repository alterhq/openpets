"""Real AF_UNIX client framing and socket-lifecycle tests, no GUI needed."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from openpets_linux.ipc import (
    IPCError, MAX_FRAME, ProtocolError, SocketAlreadyInUse,
    bind_socket, cleanup_socket, send,
)


@contextmanager
def peer(path, response=b'{"ok":true,"message":"pong"}\n', delay=0):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    listener.settimeout(1)
    received = []

    def worker():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(1)
                data = bytearray()
                while b"\n" not in data:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    data.extend(chunk)
                received.append(bytes(data))
                if delay:
                    time.sleep(delay)
                connection.sendall(response)
        except (OSError, TimeoutError):
            pass  # Client may intentionally close on timeout/oversized frame.

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        yield received
    finally:
        listener.close()
        thread.join(timeout=2)


class SocketTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="openpets-ipc-")
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "host.sock")

    def test_newline_json_roundtrip(self):
        command = {"type": "notify", "notification": {"title": "Tiếng Việt"}}
        with peer(self.path) as received:
            self.assertEqual(send(command, self.path), {"ok": True, "message": "pong"})
        self.assertTrue(received[0].endswith(b"\n"))
        self.assertEqual(json.loads(received[0]), command)

    def test_missing_server(self):
        with self.assertRaises(IPCError):
            send({"type": "ping"}, self.path)

    def test_invalid_response(self):
        for response in (b"not-json\n", b"[]\n", b"{}\n", b'{"ok":1}\n', b'\xff\n'):
            with self.subTest(response=response):
                path = self.path + str(len(response))
                # Unique path even when response lengths coincide.
                if os.path.exists(path):
                    os.unlink(path)
                with peer(path, response):
                    with self.assertRaises(ProtocolError):
                        send({"type": "ping"}, path)

    def test_oversized_command(self):
        with self.assertRaises(IPCError):
            send({"text": "x" * MAX_FRAME}, self.path)

    def test_oversized_response_with_newline(self):
        response = b'{"ok":true,"text":"' + b"x" * MAX_FRAME + b'"}\n'
        with peer(self.path, response):
            with self.assertRaises(IPCError):
                send({"type": "ping"}, self.path)

    def test_timeout(self):
        with peer(self.path, delay=0.15):
            started = time.monotonic()
            with self.assertRaises(IPCError):
                send({"type": "ping"}, self.path, timeout=0.03)
            self.assertLess(time.monotonic() - started, 0.3)

    def test_invalid_timeout(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(IPCError):
                send({"type": "ping"}, self.path, timeout=timeout)

    def test_permissions_and_cleanup(self):
        listener = bind_socket(self.path)
        try:
            identity = os.lstat(self.path)
            self.assertEqual(stat.S_IMODE(identity.st_mode), 0o600)
            self.assertFalse(listener.getblocking())
        finally:
            listener.close()
        cleanup_socket(self.path, identity)
        self.assertFalse(os.path.lexists(self.path))

    def test_refuse_live_listener(self):
        listener = bind_socket(self.path)
        identity = os.lstat(self.path)
        try:
            with self.assertRaises(SocketAlreadyInUse):
                bind_socket(self.path)
            self.assertEqual(os.lstat(self.path).st_ino, identity.st_ino)
        finally:
            listener.close()
            cleanup_socket(self.path, identity)

    def test_refuse_file(self):
        Path(self.path).write_text("preserve me")
        with self.assertRaises(IPCError):
            bind_socket(self.path)
        self.assertEqual(Path(self.path).read_text(), "preserve me")

    def test_refuse_symlink(self):
        target = Path(self.path + ".target")
        target.write_text("preserve me")
        Path(self.path).symlink_to(target)
        with self.assertRaises(IPCError):
            bind_socket(self.path)
        self.assertEqual(target.read_text(), "preserve me")

    def test_reclaim_stale_socket(self):
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(self.path)
        stale.close()
        listener = bind_socket(self.path)
        identity = os.lstat(self.path)
        listener.close()
        cleanup_socket(self.path, identity)
        self.assertFalse(os.path.exists(self.path))

    def test_cleanup_does_not_remove_replaced_file(self):
        listener = bind_socket(self.path)
        identity = os.lstat(self.path)
        listener.close()
        os.unlink(self.path)
        Path(self.path).write_text("new user file")
        cleanup_socket(self.path, identity)
        cleanup_socket(self.path, None)
        self.assertEqual(Path(self.path).read_text(), "new user file")

    def test_probe_error_does_not_remove_socket(self):
        listener = bind_socket(self.path)
        identity = os.lstat(self.path)
        listener.close()
        with patch("openpets_linux.ipc.socket.socket") as constructor:
            constructor.return_value.connect.side_effect = PermissionError("denied")
            with self.assertRaises(IPCError):
                bind_socket(self.path)
        self.assertEqual(os.lstat(self.path).st_ino, identity.st_ino)
        cleanup_socket(self.path, identity)

    def test_frame_cap(self):
        self.assertEqual(MAX_FRAME, 64 * 1024)
