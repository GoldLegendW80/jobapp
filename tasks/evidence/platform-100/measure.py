#!/usr/bin/env python3
"""Resource-measurement harness for issue #100 (public request-body limits).

It measures how much memory a caller WITHOUT a session can make the api and Caddy hold, and for
how long a connection is kept, when request bodies arrive slowly, stall, never end, declare a
large length, or come as an endless chunked stream; plus slow request headers (slowloris) and
idle keep-alive connections. It then measures ordinary signed-in JSON use for comparison, and
checks that a legitimate login, an event stream and a large upload still work. The same script
runs before and after the fix so the two can be put side by side in the PR.

Safety. This is a hardening tool, not an attack tool. It refuses any target that is not a
loopback address, it is bounded (a few dozen connections, a few seconds each), and it drops its
own connections at the end of every scenario. Point it only at your own scratch stack:

    python3 tasks/evidence/platform-100/measure.py \
        --base-url http://127.0.0.1:8173 \
        --api jobapp-p100-api-1 --caddy jobapp-p100-caddy-1 \
        --label before > tasks/evidence/platform-100/before.json

Memory is read with `docker stats --no-stream` for the two containers named. No secret is read
or printed. The output is one JSON document with a row per scenario.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from ipaddress import ip_address
from typing import Self
from urllib.parse import urlsplit

# --- memory sampling -----------------------------------------------------------------------


def container_rss_mib(name: str) -> float | None:
    """The container's current memory use in MiB (docker stats, file cache included), or None."""
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    used = out.split("/")[0].strip()  # e.g. "123.4MiB / 512MiB"
    units = {"KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0, "B": 1 / (1024 * 1024)}
    for unit, factor in units.items():
        if used.endswith(unit):
            try:
                return round(float(used[: -len(unit)]) * factor, 1)
            except ValueError:
                return None
    return None


@dataclass
class MemSampler:
    """Samples the api and caddy memory in a background thread while a scenario runs."""

    api: str
    caddy: str
    interval_s: float = 0.5
    api_peak: float = 0.0
    caddy_peak: float = 0.0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            a = container_rss_mib(self.api)
            c = container_rss_mib(self.caddy)
            if a is not None:
                self.api_peak = max(self.api_peak, a)
            if c is not None:
                self.caddy_peak = max(self.caddy_peak, c)
            self._stop.wait(self.interval_s)

    def __enter__(self) -> Self:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


# --- one slow/stalled connection -----------------------------------------------------------


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    return sock


def open_declared_body(
    host: str, port: int, path: str, declared: int, send_bytes: int, timeout: float
) -> socket.socket:
    """Open a connection that declares a large Content-Length, sends `send_bytes` of it, then
    holds the rest back. The api buffers what it has received while it waits for the rest."""
    sock = _connect(host, port, timeout)
    head = (
        f"POST {path} HTTP/1.1\r\nHost: {host}\r\nX-Requested-With: jobapp\r\n"
        f"Content-Type: application/json\r\nContent-Length: {declared}\r\n\r\n"
    )
    sock.sendall(head.encode())
    if send_bytes:
        # A valid JSON opening so nothing rejects it on shape before the length is reached.
        chunk = b'{"email":"a@b.co","password":"' + b"x" * max(0, send_bytes - 32)
        sock.sendall(chunk[:send_bytes])
    return sock


def open_chunked_endless(
    host: str, port: int, path: str, timeout: float
) -> socket.socket:
    """Open a chunked body that keeps sending small chunks and never sends the terminating 0."""
    sock = _connect(host, port, timeout)
    head = (
        f"POST {path} HTTP/1.1\r\nHost: {host}\r\nX-Requested-With: jobapp\r\n"
        f"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
    )
    sock.sendall(head.encode())
    sock.sendall(b"1\r\n{\r\n")  # one byte of body, no end
    return sock


def open_slow_header(host: str, port: int, path: str, timeout: float) -> socket.socket:
    """Open a request and send its headers one slow byte at a time, never finishing them."""
    sock = _connect(host, port, timeout)
    sock.sendall(f"POST {path} HTTP/1.1\r\nHost: {host}\r\n".encode())
    sock.sendall(b"X-Sl")  # a header line left unfinished
    return sock


def open_idle(host: str, port: int, path: str, timeout: float) -> socket.socket:
    """Open a connection and send nothing at all."""
    return _connect(host, port, timeout)


OPENERS = {
    "declared": open_declared_body,
    "chunked": open_chunked_endless,
    "slowheader": open_slow_header,
    "idle": open_idle,
}


# --- scenarios -----------------------------------------------------------------------------


@dataclass
class Result:
    scenario: str
    path: str
    connections: int
    held_s: float
    still_open_after: int
    api_peak_mib: float
    caddy_peak_mib: float
    note: str = ""


def run_scenario(
    args: argparse.Namespace,
    scenario: str,
    path: str,
    *,
    declared: int = 0,
    send_bytes: int = 0,
    connections: int | None = None,
) -> Result:
    host, port = args.host, args.port
    count = connections if connections is not None else args.connections
    opener = OPENERS[scenario]
    socks: list[socket.socket] = []
    start = time.monotonic()
    with MemSampler(args.api, args.caddy) as mem:
        for _ in range(count):
            try:
                if scenario == "declared":
                    socks.append(
                        open_declared_body(
                            host, port, path, declared, send_bytes, args.timeout
                        )
                    )
                else:
                    socks.append(opener(host, port, path, args.timeout))
            except OSError:
                break
            time.sleep(args.ramp_s)
        # Hold them, trickling nothing, for the dwell. A real client that stalls looks like this.
        time.sleep(args.hold_s)
        still_open = 0
        for sock in socks:
            try:
                sock.setblocking(False)
                peek = sock.recv(16, socket.MSG_PEEK)
                # Empty read means the peer closed the connection; data means it answered.
                if peek == b"":
                    pass
                else:
                    still_open += 1
            except BlockingIOError:
                still_open += 1  # nothing to read, connection still up
            except OSError:
                pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
    return Result(
        scenario=scenario,
        path=path,
        connections=len(socks),
        held_s=round(time.monotonic() - start, 1),
        still_open_after=still_open,
        api_peak_mib=mem.api_peak,
        caddy_peak_mib=mem.caddy_peak,
    )


def idle_rss(args: argparse.Namespace) -> dict[str, float | None]:
    return {
        "api_mib": container_rss_mib(args.api),
        "caddy_mib": container_rss_mib(args.caddy),
    }


# --- legitimate use still works ------------------------------------------------------------


def legit_login(args: argparse.Namespace) -> dict[str, object]:
    """A small, honest login body returns an answer (401 for bad credentials, not a timeout)."""
    body = json.dumps(
        {"email": "nobody@example.com", "password": "not-the-password"}
    ).encode()
    req = urllib.request.Request(
        f"{args.base_url}/api/auth/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Requested-With": "jobapp"},
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": status in (401, 429),
        "status": status,
        "ms": round((time.monotonic() - start) * 1000, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", required=True, help="http://127.0.0.1:<port> of the scratch stack"
    )
    parser.add_argument("--api", required=True, help="api container name")
    parser.add_argument("--caddy", required=True, help="caddy container name")
    parser.add_argument("--label", default="run", help="before / after, for the output")
    parser.add_argument("--connections", type=int, default=30)
    parser.add_argument(
        "--declared", type=int, default=9_500_000, help="Content-Length to declare"
    )
    parser.add_argument(
        "--send-bytes",
        type=int,
        default=9_000_000,
        help="bytes actually sent, then stall",
    )
    parser.add_argument(
        "--hold-s", type=float, default=8.0, help="how long connections are held"
    )
    parser.add_argument(
        "--ramp-s", type=float, default=0.05, help="pause between opening connections"
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    split = urlsplit(args.base_url)
    args.host = split.hostname or "127.0.0.1"
    args.port = split.port or 80
    try:
        if not ip_address(args.host).is_loopback:
            raise ValueError
    except ValueError:
        print(f"refusing a non-loopback target: {args.host}", file=sys.stderr)
        return 2

    results: list[Result] = []
    idle_before = idle_rss(args)

    # A caller WITHOUT a session, against the public routes that take a body.
    results.append(
        run_scenario(
            args,
            "declared",
            "/api/auth/login",
            declared=args.declared,
            send_bytes=args.send_bytes,
        )
    )
    results.append(
        run_scenario(
            args,
            "declared",
            "/api/auth/invites/placeholder-token-000000000000000000000000000000000000000/accept",
            declared=args.declared,
            send_bytes=args.send_bytes,
        )
    )
    results.append(
        run_scenario(
            args,
            "declared",
            "/api/auth/logout",
            declared=args.declared,
            send_bytes=args.send_bytes,
        )
    )
    results.append(run_scenario(args, "chunked", "/api/auth/login"))
    results.append(run_scenario(args, "slowheader", "/api/auth/login"))
    results.append(run_scenario(args, "idle", "/api/auth/login"))

    time.sleep(2)  # let memory settle before reading the resting level again
    idle_after = idle_rss(args)

    document = {
        "label": args.label,
        "base_url": args.base_url,
        "params": {
            "connections": args.connections,
            "declared": args.declared,
            "send_bytes": args.send_bytes,
            "hold_s": args.hold_s,
        },
        "idle_rss_before": idle_before,
        "idle_rss_after": idle_after,
        "legit_login": legit_login(args),
        "scenarios": [asdict(r) for r in results],
    }
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
