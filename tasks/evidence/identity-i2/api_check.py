"""Real-world check of identity I2 through Caddy, with Playwright's APIRequestContext.

    uv run --native-tls --with playwright==1.55.0 python tasks/evidence/identity-i2/api_check.py \
        <file with the create-admin output> <compose project> [base url]

Accepts the setup link printed by `python -m app.identity.cli create-admin`, signs in, reads
/me, signs out, and checks every answer on the way. Writes exchange.json next to this file:
every request and response, with tokens, passwords and cookie values redacted. Then scans the
api and caddy container logs for any secret seen during the run.
"""

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import APIResponse, sync_playwright

HERE = Path(__file__).parent
CSRF = {"X-Requested-With": "jobapp"}
PASSWORD = "a long setup passphrase 2026"
WRONG = "not the setup passphrase"

secrets: set[str] = {PASSWORD, WRONG}
log: list[dict[str, Any]] = []
failures: list[str] = []


def redact(text: str) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def redact_set_cookie(value: str) -> str:
    name, _, rest = value.partition("=")
    cookie_value, sep, attributes = rest.partition(";")
    shown = "[redacted]" if cookie_value.strip('"') else cookie_value
    return f"{name}={shown}{sep}{attributes}"


def record(
    step: str, method: str, path: str, response: APIResponse, body: Any = None
) -> Any:
    try:
        payload = response.json()
    except ValueError:  # no body (204) or not JSON
        payload = response.text() or None
    for header in response.headers_array:
        if header["name"].lower() == "set-cookie":
            # A cleared cookie has the value "" (two quotes): not a secret.
            secrets.add(header["value"].split(";", 1)[0].partition("=")[2].strip('"'))
    secrets.discard("")
    headers = [
        {
            "name": h["name"],
            "value": redact_set_cookie(h["value"])
            if h["name"].lower() == "set-cookie"
            else redact(h["value"]),
        }
        for h in response.headers_array
    ]
    log.append(
        {
            "step": step,
            "request": {
                "method": method,
                "path": redact(path),
                "body": json.loads(redact(json.dumps(body)))
                if body is not None
                else None,
            },
            "response": {
                "status": response.status,
                "headers": headers,
                "body": json.loads(redact(json.dumps(payload))),
            },
        }
    )
    return payload


def expect(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def set_cookie_of(response: APIResponse) -> str:
    values = [
        h["value"] for h in response.headers_array if h["name"].lower() == "set-cookie"
    ]
    return values[0] if values else ""


def main() -> int:
    cli_output = Path(sys.argv[1]).read_text(encoding="utf-8")
    project = sys.argv[2]
    base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1"
    match = re.search(r"/invite/([A-Za-z0-9_-]{43})", cli_output)
    assert match, "no setup link in the create-admin output"
    token = match.group(1)
    secrets.add(token)

    with sync_playwright() as p:
        ctx = p.request.new_context(base_url=base)

        r = ctx.get("/api/health")
        record("health", "GET", "/api/health", r)
        expect(r.status == 200, "health answers 200 through caddy")

        r = ctx.get("/api/auth/me")
        body = record("me before anything", "GET", "/api/auth/me", r)
        expect(
            r.status == 401 and body == {"detail": "Sign in to continue."},
            "me without a session is 401",
        )

        path = f"/api/auth/invites/{token}"
        r = ctx.get(path)
        body = record("check setup link", "GET", path, r)
        expect(
            r.status == 200
            and body["email"] == "admin@example.org"
            and body["kind"] == "invite",
            "setup link is valid, kind invite, for admin@example.org",
        )

        accept_path = f"{path}/accept"
        form = {"name": "  Harald  Admin ", "password": PASSWORD}
        r = ctx.post(accept_path, data=form)
        record("accept without CSRF header", "POST", accept_path, r, form)
        expect(r.status == 403, "accept without X-Requested-With is 403")

        weak = {"name": "Harald", "password": "password123"}
        r = ctx.post(accept_path, data=weak, headers=CSRF)
        body = record("accept with a common password", "POST", accept_path, r, weak)
        expect(
            r.status == 422 and "too common" in body["detail"],
            "common password refused, 422",
        )

        r = ctx.post(accept_path, data=form, headers=CSRF)
        body = record("accept setup link", "POST", accept_path, r, form)
        cookie = set_cookie_of(r)
        expect(
            r.status == 200 and body["role"] == "admin",
            "accept creates an admin and signs in",
        )
        expect(body.get("name") == "Harald Admin", "name trimmed and collapsed")
        lowered = cookie.lower()
        expect(
            cookie.startswith("jobapp_session=")
            and "httponly" in lowered
            and "samesite=lax" in lowered
            and "path=/" in lowered
            and "max-age=2592000" in lowered
            and "secure" not in lowered,
            "cookie HttpOnly, SameSite=Lax, Path=/, Max-Age 30 days, not Secure over http",
        )

        r = ctx.get("/api/auth/me")
        body = record("me after accept", "GET", "/api/auth/me", r)
        expect(
            r.status == 200 and body["email"] == "admin@example.org",
            "me answers the admin",
        )

        r = ctx.post(accept_path, data=form, headers=CSRF)
        record("accept again", "POST", accept_path, r, form)
        expect(r.status == 404, "the link works once: second accept is 404")

        r = ctx.post("/api/auth/logout", headers=CSRF)
        record("logout", "POST", "/api/auth/logout", r)
        expect(
            r.status == 204 and "max-age=0" in set_cookie_of(r).lower(),
            "logout 204 clears the cookie",
        )

        r = ctx.get("/api/auth/me")
        record("me after logout", "GET", "/api/auth/me", r)
        expect(r.status == 401, "me after logout is 401")

        bad = {"email": "admin@example.org", "password": WRONG}
        r = ctx.post("/api/auth/login", data=bad, headers=CSRF)
        body = record("login wrong password", "POST", "/api/auth/login", r, bad)
        expect(
            r.status == 401 and body == {"detail": "Incorrect email or password."},
            "wrong password 401",
        )

        unknown = {"email": "nobody@example.org", "password": WRONG}
        r = ctx.post("/api/auth/login", data=unknown, headers=CSRF)
        body2 = record("login unknown email", "POST", "/api/auth/login", r, unknown)
        expect(
            r.status == 401 and body2 == body, "unknown email answers exactly the same"
        )

        good = {"email": "ADMIN@Example.org", "password": PASSWORD}
        r = ctx.post("/api/auth/login", data=good, headers=CSRF)
        body = record("login", "POST", "/api/auth/login", r, good)
        expect(
            r.status == 200 and body["role"] == "admin",
            "login (email in another case) 200",
        )

        r = ctx.get("/api/auth/me")
        record("me after login", "GET", "/api/auth/me", r)
        expect(r.status == 200, "me after login is 200")

        r = ctx.post("/api/auth/logout", headers=CSRF)
        record("logout again", "POST", "/api/auth/logout", r)
        expect(r.status == 204, "logout 204")

        r = ctx.get("/api/auth/me")
        record("me after second logout", "GET", "/api/auth/me", r)
        expect(r.status == 401, "me after logout is 401")
        ctx.dispose()

    logs = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            "compose.yml",
            "logs",
            "--no-color",
            "api",
            "caddy",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    leaked = [s for s in secrets if s in logs]
    expect(
        not leaked,
        f"no password, token or cookie value in {len(logs.splitlines())} log lines",
    )
    expect(
        "/invite/[redacted]" in logs or "/invites/{token}" in logs,
        "link paths are masked in logs",
    )

    out = {
        "task": "identity I2",
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": base,
        "client": "Playwright APIRequestContext (Python)",
        "note": "tokens, passwords and cookie values replaced by [redacted]",
        "log_scan": {"lines": len(logs.splitlines()), "secrets_found": len(leaked)},
        "failures": failures,
        "exchanges": log,
    }
    (HERE / "exchange.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    )
    print(
        f"{len(log)} exchanges, {len(failures)} failures, written to {HERE / 'exchange.json'}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
