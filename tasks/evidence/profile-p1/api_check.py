"""Real-world check of profile P1 through Caddy, with Playwright's APIRequestContext.

    uv run --native-tls --with playwright==1.55.0 python tasks/evidence/profile-p1/api_check.py \
        <alice create-admin output> <bob create-admin output> <compose project> [base url]

Accepts two setup links printed by `python -m app.identity.cli create-admin` (Alice and Bob),
then as Alice: reads the empty profile, saves every section, gets 403 without the CSRF header,
422 on a bad regex and on an unknown field, 404 on an unknown section, 409 on a stale revision,
20 concurrent saves on the same base (one wins), pages through the revisions and reads one. As
Bob: an empty profile of his own and 404 on every revision of Alice. Without a session: 401.
Writes exchange.json next to this file (tokens, passwords and cookie values redacted), then
scans the api and caddy logs for any secret and for the profile contents.
"""

import asyncio
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import APIRequestContext, APIResponse, async_playwright

HERE = Path(__file__).parent
CSRF = {"X-Requested-With": "jobapp"}
PASSWORD = "a long profile passphrase 2026"
MARKER = "Profile-content-marker-7f3a"  # must never show up in a log line

secrets: set[str] = {PASSWORD}
log: list[dict[str, Any]] = []
failures: list[str] = []

SAMPLE: dict[str, dict[str, Any]] = {
    "identity": {
        "full_name": f"Zoë Ünal {MARKER}",
        "headline": "GenAI Engineer",
        "email": "zoe@example.org",
        "phone": "+33 6 12 34 56 78",
        "city": "Bordeaux",
        "country": "France",
        "linkedin_url": "https://www.linkedin.com/in/zoe-example/",
        "website": "https://zoe.example.org/",
        "links": [],
        "languages": [{"name": "Français", "level": "Langue maternelle"}],
    },
    "experience": {
        "employers": [
            {
                "key": "acme",
                "organisation": "Acme",
                "title": "GenAI Engineer",
                "start": "2025-09",
                "end": None,
                "location": "Bordeaux",
                "contract": "CDI",
            }
        ],
        "education": [
            {
                "school": "École d'ingénieurs",
                "degree": "MSc",
                "start": "2022-09",
                "end": "2025-08",
                "location": None,
                "details": None,
            }
        ],
    },
    "facts": {
        "blocks": [
            {"title": "Règles", "body": f"1. Every claim traces here.\n2. 🚀 {MARKER}"},
            {"title": "عربي", "body": "نص من اليمين إلى اليسار"},
        ]
    },
    "skills": {
        "years_experience": 3.5,
        "have": [
            {"name": "agents", "terms": ["agent", "agentic"], "advanced_regex": False}
        ],
        "adjacent": [],
        "none": [],
        "gates": [
            {
                "name": "phd",
                "terms": [r"phd (is )?required"],
                "advanced_regex": True,
                "penalty": 25,
            }
        ],
    },
    "search": {
        "title_include": {"terms": ["ai engineer", "llm"], "advanced_regex": False},
        "title_exclude": {"terms": [r"\b(staff|principal)\b"], "advanced_regex": True},
        "location": {"terms": ["paris", "remote"], "advanced_regex": False},
        "contract_types": ["CDI", "VIE"],
        "remote_stance": "hybrid",
        "min_fit": 60,
        "days": 14,
        "boards": [
            {
                "company": "Mistral AI",
                "ats": "ashby",
                "org": "mistral.ai",
                "enabled": True,
                "extra_exclude": {"terms": [], "advanced_regex": False},
            }
        ],
        "never_apply": ["No cybersecurity roles."],
    },
    "answers": {
        "work_authorization": [
            {"country": "France", "answer": "EU citizen.", "needs_sponsorship": False}
        ],
        "notice_period": "One to three months.",
        "earliest_start": "2026-12-01",
        "salary_policy": "Never invent a number.",
        "relocation": None,
        "travel": None,
        "motivation": "Build agents that run real operations.",
        "heard_about": None,
        "leave_blank": ["gender"],
    },
}


def redact(text: str) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def redact_set_cookie(value: str) -> str:
    name, _, rest = value.partition("=")
    cookie_value, sep, attributes = rest.partition(";")
    shown = "[redacted]" if cookie_value.strip('"') else cookie_value
    return f"{name}={shown}{sep}{attributes}"


async def record(
    step: str, method: str, path: str, response: APIResponse, body: Any = None
) -> Any:
    try:
        payload = await response.json()
    except ValueError:  # no body (204) or not JSON
        payload = (await response.text()) or None
    for header in response.headers_array:
        if header["name"].lower() == "set-cookie":
            secrets.add(header["value"].split(";", 1)[0].partition("=")[2].strip('"'))
    secrets.discard("")
    keep = {"cache-control", "content-type", "set-cookie", "x-request-id", "via"}
    headers = [
        {
            "name": h["name"],
            "value": redact_set_cookie(h["value"])
            if h["name"].lower() == "set-cookie"
            else redact(h["value"]),
        }
        for h in response.headers_array
        if h["name"].lower() in keep
    ]
    log.append(
        {
            "step": step,
            "request": {
                "method": method,
                "path": redact(path),
                "body": json.loads(redact(json.dumps(body, ensure_ascii=False)))
                if body is not None
                else None,
            },
            "response": {
                "status": response.status,
                "headers": headers,
                "body": json.loads(redact(json.dumps(payload, ensure_ascii=False))),
            },
        }
    )
    return payload


def expect(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


def link_token(path: str) -> str:
    match = re.search(
        r"/invite/([A-Za-z0-9_-]{43})", Path(path).read_text(encoding="utf-8")
    )
    assert match, f"no setup link in {path}"
    secrets.add(match.group(1))
    return match.group(1)


async def sign_up(ctx: APIRequestContext, token: str, name: str) -> None:
    path = f"/api/auth/invites/{token}/accept"
    form = {"name": name, "password": PASSWORD}
    r = await ctx.post(path, data=form, headers=CSRF)
    await record(f"accept invite ({name})", "POST", path, r, form)
    expect(r.status == 200, f"{name} accepts the setup link and is signed in")


async def put(
    ctx: APIRequestContext,
    step: str,
    section: str,
    data: Any,
    base: int,
    csrf: bool = True,
) -> tuple[APIResponse, Any]:
    path = f"/api/profile/{section}"
    body = {"base_revision": base, "data": data}
    r = await ctx.put(path, data=body, headers=CSRF if csrf else {})
    return r, await record(step, "PUT", path, r, body)


async def get(ctx: APIRequestContext, step: str, path: str) -> tuple[APIResponse, Any]:
    r = await ctx.get(path)
    return r, await record(step, "GET", path, r)


async def run(base: str, alice_link: str, bob_link: str) -> None:
    async with async_playwright() as p:
        anon = await p.request.new_context(base_url=base)
        alice = await p.request.new_context(base_url=base)
        bob = await p.request.new_context(base_url=base)

        r, body = await get(anon, "profile without a session", "/api/profile")
        expect(
            r.status == 401 and body == {"detail": "Sign in to continue."},
            "anonymous 401",
        )
        r, _ = await get(anon, "revisions without a session", "/api/profile/revisions")
        expect(r.status == 401, "anonymous revisions 401")

        await sign_up(alice, alice_link, "Alice")
        await sign_up(bob, bob_link, "Bob")

        r, body = await get(alice, "first read creates the profile", "/api/profile")
        expect(
            r.status == 200
            and body["revision"] == 0
            and all(v is None for v in body["sections"].values())
            and body["completeness"]["overall"] == 0,
            "first GET: empty profile, revision 0, completeness 0",
        )
        expect(r.headers.get("cache-control") == "no-store", "profile answers no-store")

        r, body = await put(
            alice, "save without CSRF header", "facts", SAMPLE["facts"], 0, False
        )
        expect(r.status == 403, "PUT without X-Requested-With is 403")

        revision = 0
        for section, data in SAMPLE.items():
            r, body = await put(alice, f"save {section}", section, data, revision)
            ok = r.status == 200 and body["revision"] == revision + 1
            expect(
                ok and body["sections"][section] == data,
                f"save {section}: 200, stored as sent",
            )
            expect(
                ok and body["completeness"]["sections"][section]["percent"] == 100,
                f"{section} 100 percent complete",
            )
            revision += 1
        expect(
            body["completeness"]["overall"] == 100, "whole profile 100 percent complete"
        )

        bad = {"title_include": {"terms": ["(ai engineer"], "advanced_regex": True}}
        r, body = await put(alice, "bad regex", "search", bad, revision)
        expect(
            r.status == 422
            and "invalid regular expression at position 0" in body["detail"],
            "bad regex 422 names the position",
        )
        r, body = await put(
            alice, "unknown field", "identity", {"nickname": "x"}, revision
        )
        expect(
            r.status == 422
            and body["detail"] == "data.nickname: Extra inputs are not permitted",
            "unknown field 422",
        )
        r, body = await put(alice, "unknown section", "hobbies", {}, revision)
        expect(r.status == 404, "unknown section 404")

        r, body = await put(alice, "stale base revision", "facts", {"blocks": []}, 2)
        expect(
            r.status == 409
            and body["revision"] == revision
            and body["sections"]["facts"] == SAMPLE["facts"],
            "stale base revision 409 with current revision and data",
        )

        # Twenty saves at once on the same base, through Caddy: exactly one wins.
        path = "/api/profile/identity"
        tasks = [
            alice.put(
                path,
                data={
                    "base_revision": revision,
                    "data": {**SAMPLE["identity"], "city": f"C{n}"},
                },
                headers=CSRF,
            )
            for n in range(20)
        ]
        results = await asyncio.gather(*tasks)
        statuses = sorted(r.status for r in results)
        log.append(
            {
                "step": "20 concurrent saves with the same base_revision",
                "request": {"method": "PUT", "path": path, "base_revision": revision},
                "response": {"statuses": statuses},
            }
        )
        expect(
            statuses == [200] + [409] * 19, "20 concurrent saves: one 200, nineteen 409"
        )
        revision += 1

        r, body = await get(alice, "profile after the race", "/api/profile")
        expect(
            body["revision"] == revision, f"revision incremented once, now {revision}"
        )

        r, body = await get(alice, "revisions page 1", "/api/profile/revisions?limit=3")
        items = [i["revision"] for i in body["items"]]
        expect(
            items == [revision, revision - 1, revision - 2], "newest first, 3 per page"
        )
        r, body2 = await get(
            alice,
            "revisions page 2",
            f"/api/profile/revisions?limit=3&before={body['next_before']}",
        )
        expect(
            [i["revision"] for i in body2["items"]]
            == [revision - 3, revision - 4, revision - 5],
            "second page continues",
        )
        r, body = await get(
            alice, "revisions of identity", "/api/profile/revisions?section=identity"
        )
        expect(
            [i["revision"] for i in body["items"]] == [revision, 1],
            "filtered by section",
        )
        r, body = await get(alice, "one past revision", "/api/profile/revisions/3")
        expect(
            r.status == 200
            and body["section"] == "facts"
            and body["data"] == SAMPLE["facts"],
            "revision 3 is the facts as saved, unicode intact",
        )

        r, body = await get(bob, "Bob reads his own profile", "/api/profile")
        expect(
            r.status == 200
            and body["revision"] == 0
            and all(v is None for v in body["sections"].values()),
            "Bob sees an empty profile of his own",
        )
        bob_404 = True
        for n in range(1, revision + 1):
            r, body = await get(
                bob, f"Bob asks for Alice's revision {n}", f"/api/profile/revisions/{n}"
            )
            bob_404 = bob_404 and r.status == 404 and MARKER not in json.dumps(body)
        expect(bob_404, "Bob gets 404 for every revision of Alice")
        r, body = await get(bob, "Bob lists revisions", "/api/profile/revisions")
        expect(body["items"] == [], "Bob's history is empty")

        for ctx in (anon, alice, bob):
            await ctx.dispose()


def main() -> int:
    alice_link = link_token(sys.argv[1])
    bob_link = link_token(sys.argv[2])
    project = sys.argv[3]
    base = sys.argv[4] if len(sys.argv) > 4 else "http://127.0.0.1"
    asyncio.run(run(base, alice_link, bob_link))

    logs = subprocess.run(
        ["docker", "compose", "-p", project, "-f", "compose.yml", "logs", "--no-color", "api",
         "caddy"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout  # fmt: skip
    leaked = [s for s in secrets if s in logs]
    expect(
        not leaked,
        f"no password, token or cookie value in {len(logs.splitlines())} log lines",
    )
    expect(MARKER not in logs and "Zoë" not in logs, "no profile content in the logs")
    expect("/api/profile/revisions/{revision}" in logs, "logs carry route templates")

    out = {
        "task": "profile P1",
        "when": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": base,
        "client": "Playwright APIRequestContext (Python, async)",
        "note": "tokens, passwords and cookie values replaced by [redacted]; sample data is fictional",
        "log_scan": {
            "lines": len(logs.splitlines()),
            "secrets_found": len(leaked),
            "profile_content_found": MARKER in logs,
        },
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
