"""Length-prefixed local messages. Startup payloads never touch disk."""
from __future__ import annotations

import ctypes
import json
import os
import socket
import struct

MAX_FRAME = 16 * 1024 * 1024


def send_frame(connection: socket.socket, message: dict) -> None:
    body = json.dumps(message, ensure_ascii=False).encode()
    if len(body) > MAX_FRAME:
        raise ValueError("Startup payload exceeds the local transport budget")
    connection.sendall(struct.pack("!I", len(body)) + body)


def read_frame(connection: socket.socket) -> dict | None:
    def exact(size):
        result = bytearray()
        while len(result) < size:
            chunk = connection.recv(size - len(result))
            if not chunk:
                if not result:
                    return None
                raise EOFError("Incomplete local startup frame")
            result.extend(chunk)
        return bytes(result)
    header = exact(4)
    if header is None:
        return None
    size = struct.unpack("!I", header)[0]
    if size > MAX_FRAME:
        raise ValueError("Invalid startup frame length")
    payload = exact(size)
    if payload is None:
        raise EOFError("Missing startup frame payload")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Startup message must be an object")
    return data


def peer_uid(connection: socket.socket) -> int:
    if hasattr(socket, "SO_PEERCRED"):
        value = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.unpack("3i", value)[1]
    # macOS / BSD expose getpeereid rather than Linux SO_PEERCRED.
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "getpeereid", None)
    if function is None:
        raise OSError("Peer credential verification is unsupported on this OS")
    function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    function.restype = ctypes.c_int
    if function(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise OSError(ctypes.get_errno(), "Cannot verify local startup peer")
    return uid.value


def verify_peer(connection: socket.socket) -> None:
    if peer_uid(connection) != os.getuid():
        raise PermissionError("Startup peer belongs to a different user")
