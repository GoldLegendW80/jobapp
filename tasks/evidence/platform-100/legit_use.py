#!/usr/bin/env python3
"""Legitimate use through Caddy with the limits of issue #100 in place, on a scratch stack only
(loopback, a Compose project of your own; it creates an account and a sweep row there):

- a sign in with a wrong password is answered by the route;
- an event stream stays open past Caddy's 60 second idle timeout (the progress stream of a
  sweep left running by hand: the same `sse.response` as an agent run's stream, not an agent
  run);
- the profile photo upload, the document upload (small, and 12 MiB, which only the upload
  route's own edge limit lets through) and the paste.

    python3 tasks/evidence/platform-100/legit_use.py --base-url http://127.0.0.1:8173 \
        --project jobapp-p100
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import re
import subprocess
import sys
import time
from ipaddress import ip_address
from urllib.parse import urlsplit

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
EMAIL = "legit-use@example.org"
PASSWORD = "a long scratch passphrase 7"
STREAM_S = 75


def compose(project: str, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", "compose.yml", "exec", "-T", *args],
        capture_output=True,
        text=True,
        input=stdin,
        timeout=60,
        check=True,
    ).stdout


def call(
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes | None,
    headers: dict[str, str],
) -> tuple[int, dict[str, str], bytes, float]:
    connection = http.client.HTTPConnection(host, port, timeout=120)
    started = time.monotonic()
    connection.request(
        method, path, body=body, headers={"X-Requested-With": "jobapp", **headers}
    )
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return (
        response.status,
        dict(response.getheaders()),
        data,
        round(time.monotonic() - started, 2),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    split = urlsplit(args.base_url)
    host, port = split.hostname or "", split.port or 80
    try:
        loopback = ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        print("refusing a target that is not loopback", file=sys.stderr)
        return 2
    out: dict[str, object] = {}
    js = {"Content-Type": "application/json"}

    status, _, _, took = call(
        host,
        port,
        "POST",
        "/api/auth/login",
        b'{"email":"nobody@example.org","password":"wrong-one-1"}',
        js,
    )
    out["sign_in_wrong_password"] = {"status": status, "seconds": took}

    link = compose(
        args.project, "api", "python", "-m", "app.identity.cli", "create-admin", EMAIL
    )
    token = re.search(r"/(?:invite|reset)/([A-Za-z0-9_-]+)", link)
    assert token, "no link printed"
    accept = json.dumps({"name": "Legit Use", "password": PASSWORD}).encode()
    status, headers, _, _ = call(
        host, port, "POST", f"/api/auth/invites/{token.group(1)}/accept", accept, js
    )
    cookie = re.search(r"jobapp_session=([^;]+)", headers.get("Set-Cookie", ""))
    assert status == 200 and cookie, f"accept answered {status}"
    auth = {"Cookie": f"jobapp_session={cookie.group(1)}"}

    sweep = compose(
        args.project,
        "db",
        "sh",
        "-c",
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA',
        stdin=(
            'insert into sweeps (user_id, "trigger") select id, (enum_range(null::sweep_trigger))[1]'
            f" from users where email = '{EMAIL}' returning id;"
        ),
    ).split()[0]
    connection = http.client.HTTPConnection(host, port, timeout=30)
    connection.request("GET", f"/api/jobs/sweeps/{sweep}/events", headers=auth)
    response = connection.getresponse()
    started, received, ended = time.monotonic(), b"", "still open when the client left"
    try:
        while time.monotonic() - started < STREAM_S:
            chunk = response.read1(4096)
            if not chunk:
                ended = "ended by the server"
                break
            received += chunk
    except OSError as exc:
        ended = f"error: {exc}"
    connection.close()
    out["event_stream"] = {
        "status": response.status,
        "content_type": response.getheader("Content-Type"),
        "open_seconds": round(time.monotonic() - started, 1),
        "ended": ended,
        "heartbeats": received.count(b": ping"),
        "events": received.count(b"event: "),
    }

    binary = {**auth, "Content-Type": "application/octet-stream"}
    status, _, _, took = call(host, port, "PUT", "/api/profile/photo", PNG, binary)
    out["photo_upload"] = {"status": status, "seconds": took}
    small = ("A line of a document.\n" * 200).encode()
    named = {**binary, "X-Filename": "UTF-8''notes.txt"}
    status, _, data, took = call(
        host, port, "POST", "/api/ingestion/documents", small, named
    )
    out["document_upload_4_kb"] = {
        "status": status,
        "seconds": took,
        "answer": data[:120].decode("utf-8", "replace"),
    }
    large = (b"0123456789 abcdefghij klmnopqrst\n" * (12 * 1024 * 1024 // 33 + 1))[
        : 12 * 1024 * 1024
    ]
    named = {**binary, "X-Filename": "UTF-8''large.txt"}
    status, _, data, took = call(
        host, port, "POST", "/api/ingestion/documents", large, named
    )
    out["document_upload_12_mib"] = {
        "status": status,
        "seconds": took,
        "answer": data[:120].decode("utf-8", "replace"),
    }
    paste = json.dumps({"text": "A pasted paragraph. " * 50, "name": "paste"}).encode()
    status, _, data, took = call(
        host, port, "POST", "/api/ingestion/documents/text", paste, {**auth, **js}
    )
    out["paste"] = {
        "status": status,
        "seconds": took,
        "answer": data[:120].decode("utf-8", "replace"),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
