#!/usr/bin/env python3
"""How long Caddy holds a fully stalled connection by default (no bytes after the head / after
a partial header). Loopback only. Prints seconds until each kind of connection is closed."""

import socket
import time

HOST, PORT = "127.0.0.1", 8173
MAX = 80.0


def hold(kind: str) -> None:
    s = socket.create_connection((HOST, PORT), timeout=5)
    if kind == "stalled-body":
        s.sendall(
            b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nX-Requested-With: jobapp\r\n"
            b"Content-Type: application/json\r\nContent-Length: 9000000\r\n\r\n"
            b'{"email":"a@b.co","password":"'
        )  # ~30 bytes sent of 9 MB, then silence
    elif kind == "slow-header":
        s.sendall(b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nX-Partial")
    elif kind == "idle-keepalive":
        pass  # connect, send nothing
    s.settimeout(MAX + 5)
    start = time.monotonic()
    try:
        data = s.recv(32)
        closed = time.monotonic() - start
        status = (
            data.split(b"\r\n", 1)[0].decode(errors="replace")
            if data
            else "peer closed, no response"
        )
    except TimeoutError:
        closed = time.monotonic() - start
        status = f"still open after {MAX}s"
    except OSError as e:
        closed = time.monotonic() - start
        status = f"error {e}"
    finally:
        s.close()
    print(f"{kind}: closed/answered after {closed:.1f}s -> {status}")


for k in ("stalled-body", "slow-header", "idle-keepalive"):
    hold(k)
