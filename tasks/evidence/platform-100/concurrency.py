#!/usr/bin/env python3
"""Two more measurements for issue #100, next to measure.py (same safety rules: loopback only,
bounded, your own scratch stack):

1. What `--limit-concurrency` of the api does through Caddy: N connections each hold a sign in
   body just under the public limit, and while they are held one honest sign in and one health
   request are sent. Reports their status, and the memory the held connections cost.
2. What a few sign ins at once cost: each password check uses 64 MiB by design (argon2id,
   api/app/identity/security.py), whatever the size of the request.

    python3 tasks/evidence/platform-100/concurrency.py --base-url http://127.0.0.1:8173 \
        --api jobapp-p100-api-1 --caddy jobapp-p100-caddy-1 --connections 260 --logins 4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import MemSampler, container_rss_mib, open_declared_body


def cgroup_peak_mib(container: str) -> float | None:
    """The highest memory the container's cgroup has reached since it started (memory.peak),
    which a sampler that asks once a second would miss for a peak of a fraction of a second."""
    try:
        out = subprocess.run(
            ["docker", "exec", container, "cat", "/sys/fs/cgroup/memory.peak"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
        return round(int(out) / 1048576, 1)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def status_of(url: str, body: bytes | None) -> int | str:
    headers = {"X-Requested-With": "jobapp", "Content-Type": "application/json"}
    request = urllib.request.Request(
        url, data=body, method="POST" if body is not None else "GET", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError as exc:
        return f"error: {exc}"


def sign_in(base: str, email: str) -> int | str:
    body = json.dumps({"email": email, "password": "not-the-password-1"}).encode()
    return status_of(f"{base}/api/auth/login", body)


def held_connections(args: argparse.Namespace) -> dict[str, object]:
    rest = {"api": container_rss_mib(args.api), "caddy": container_rss_mib(args.caddy)}
    socks, refused = [], 0
    with MemSampler(args.api, args.caddy) as mem:
        for _ in range(args.connections):
            try:
                socks.append(
                    open_declared_body(
                        args.host, args.port, "/api/auth/login", 65_000, 64_000, 15
                    )
                )
            except OSError:
                refused += 1
        time.sleep(2)  # let the proxy open its connections to the api
        during = {
            "sign_in": sign_in(args.base_url, "held-1@example.org"),
            "health": status_of(f"{args.base_url}/api/health", None),
        }
        time.sleep(2)
        for sock in socks:
            sock.close()
    time.sleep(3)
    return {
        "attempted": args.connections,
        "held": len(socks),
        "refused_while_sending": refused,
        "rest_mib": rest,
        "peak_mib": {"api": mem.api_peak, "caddy": mem.caddy_peak},
        "api_cgroup_peak_mib": cgroup_peak_mib(args.api),
        "while_held": during,
        "after_release": {
            "sign_in": sign_in(args.base_url, "held-2@example.org"),
            "health": status_of(f"{args.base_url}/api/health", None),
        },
    }


def sign_ins_at_once(args: argparse.Namespace) -> dict[str, object]:
    rest = container_rss_mib(args.api)
    peak_before = cgroup_peak_mib(args.api)
    statuses: list[int | str] = []
    with MemSampler(args.api, args.caddy, interval_s=0.1) as mem:
        threads = [
            threading.Thread(
                target=lambda i=i: statuses.append(
                    sign_in(args.base_url, f"at-once-{i}@example.org")
                )
            )
            for i in range(args.logins)
        ]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        took = round(time.monotonic() - started, 2)
    return {
        "sign_ins": args.logins,
        "statuses": statuses,
        "seconds": took,
        "api_rest_mib": rest,
        "api_sampled_peak_mib": mem.api_peak,
        "api_cgroup_peak_mib_before": peak_before,
        "api_cgroup_peak_mib_after": cgroup_peak_mib(args.api),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api", required=True)
    parser.add_argument("--caddy", required=True)
    parser.add_argument("--connections", type=int, default=260)
    parser.add_argument("--logins", type=int, default=4)
    args = parser.parse_args()
    split = urlsplit(args.base_url)
    args.host, args.port = split.hostname or "", split.port or 80
    try:
        loopback = ip_address(args.host).is_loopback
    except ValueError:
        loopback = False
    if not loopback or args.connections > 400 or args.logins > 8:
        print(
            "refusing: loopback only, at most 400 connections and 8 sign ins",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            # The sign ins first: memory.peak only ever rises, so they are read on a fresh api.
            {
                "sign_ins_at_once": sign_ins_at_once(args),
                "held_connections": held_connections(args),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
