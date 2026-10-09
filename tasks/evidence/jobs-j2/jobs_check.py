"""Real-world check of jobs J2 through Caddy, with Playwright, on a scratch prod stack.

    uv run --native-tls --no-project --with playwright==1.55.0 \
        python tasks/evidence/jobs-j2/jobs_check.py <job folder> <converted jobs.json> \
        <alice create-admin output> <bob create-admin output> <compose args...>

Accepts the setup links of two scratch admins, signs both in through Caddy, imports Harald's
converted data for Alice with the CLI inside the api container (twice: the second run must
change nothing), then checks through Caddy: list counts equal to the CSV, every row reached by
the cursor exactly once, filters and the histogram equal to what the CSV says, a detail, a cv.pdf
download byte-identical to the source file (SHA-256), ETag and 304, Bob's isolation, the HTTP
import (small document accepted, Harald's document refused by Caddy's 10 MiB limit). A
screenshot of the list answer in Chromium is saved next to this file.

Prints counts, status codes and hashes only: no posting text, no note, no file content, no email,
no phone, no token.
"""

import asyncio
import csv
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.async_api import APIRequestContext, async_playwright

HERE = Path(__file__).parent
BASE = "http://127.0.0.1:8097"
CSRF = {"X-Requested-With": "jobapp"}
PASSWORD = "a long jobs passphrase 2026"
COOKIE = "jobapp_session"
failures: list[str] = []


def expect(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message, flush=True)
    if not condition:
        failures.append(message)


def link_of(path: str) -> str:
    match = re.search(
        r"/invite/([A-Za-z0-9_-]{43})", Path(path).read_text(encoding="utf-8")
    )
    assert match, f"no setup link in {path}"
    return match.group(1)


def email_of(path: str) -> str:
    match = re.search(
        r"([\w.+-]+@example\.org)", Path(path).read_text(encoding="utf-8")
    )
    assert match, f"no email in {path}"
    return match.group(1)


def mib(text: str) -> float:
    number, unit = re.match(r"([\d.]+)\s*([KMG]i?B)", text).groups()  # type: ignore[union-attr]
    return float(number) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[unit]


def import_cli(
    compose: list[str], email: str, document: Path
) -> tuple[str, float, float]:
    """Run the CLI import in the api container; returns its summary line, seconds, peak MiB of
    the api container (docker stats sampled every 0.3 s)."""
    peak = [0.0]
    done = threading.Event()

    def sample() -> None:
        while not done.is_set():
            out = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}",
                 "jobapp-j2scratch-api-1"],
                capture_output=True, text=True, check=False,
            ).stdout  # fmt: skip
            if out.strip():
                peak[0] = max(peak[0], mib(out.split("/")[0].strip()))
            time.sleep(0.3)

    sampler = threading.Thread(target=sample)
    sampler.start()
    start = time.perf_counter()
    result = subprocess.run(
        [*compose, "exec", "-T", "api", "python", "-m", "app.jobs.cli", "import",
         "--email", email, "--file", "-"],
        stdin=document.open("rb"), capture_output=True, text=True, check=False,
    )  # fmt: skip
    seconds = time.perf_counter() - start
    done.set()
    sampler.join()
    if result.returncode != 0:
        print(result.stderr)
    return result.stdout.splitlines()[0] if result.stdout else "", seconds, peak[0]


async def sign_in(p: Any, link: str, name: str) -> tuple[APIRequestContext, str]:
    anon = await p.request.new_context(base_url=BASE)
    r = await anon.post(
        f"/api/auth/invites/{link}/accept",
        data={"name": name, "password": PASSWORD},
        headers=CSRF,
    )
    expect(r.status == 200, f"{name} accepts the setup link through Caddy ({r.status})")
    cookie = next(
        h["value"] for h in r.headers_array if h["name"].lower() == "set-cookie"
    )
    await anon.dispose()
    token = cookie.split(";", 1)[0].partition("=")[2]
    # The cookie is Secure (production): sent explicitly over this plain-http loopback.
    context = await p.request.new_context(
        base_url=BASE, extra_http_headers={"Cookie": f"{COOKIE}={token}"}
    )
    return context, token


async def get_json(
    ctx: APIRequestContext, path: str, params: Any = None
) -> tuple[int, Any]:
    # Repeated parameters (status=a&status=b) are encoded here: Playwright's params cannot.
    query = f"?{urlencode(params, doseq=True)}" if params else ""
    r = await ctx.get(path + query)
    body = (
        await r.json()
        if r.headers.get("content-type", "").startswith("application/json")
        else None
    )
    return r.status, body


async def run(
    job_root: Path, document: Path, alice_out: str, bob_out: str, compose: list[str]
) -> None:
    data = job_root / "data"
    with (data / "jobs.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    csv_status = Counter(r["status"] for r in rows)
    print(
        f"source: {len(rows)} CSV rows, status counts {dict(sorted(csv_status.items()))}"
    )

    async with async_playwright() as p:
        anon = await p.request.new_context(base_url=BASE)
        r = await anon.get("/api/jobs")
        expect(r.status == 401, f"anonymous GET /api/jobs is 401 ({r.status})")
        r = await anon.post("/api/jobs/import", data="{}", headers=CSRF)
        expect(r.status == 401, f"anonymous POST /api/jobs/import is 401 ({r.status})")
        await anon.dispose()

        alice, alice_token = await sign_in(p, link_of(alice_out), "Alice")
        bob, _ = await sign_in(p, link_of(bob_out), "Bob")

        # --- the CLI import, twice ---------------------------------------------------------
        size = document.stat().st_size
        print(
            f"document: {size / 1_000_000:.1f} MB, sha256 {hashlib.sha256(document.read_bytes()).hexdigest()[:16]}..."
        )
        line, seconds, peak = import_cli(compose, email_of(alice_out), document)
        print(f"first import: {line}")
        print(
            f"first import: {seconds:.1f} s, api container peak {peak:.0f} MiB (limit 512 MiB)"
        )
        expect(
            line.startswith(
                f"Imported {len(rows)} rows: {len(rows)} created, 0 updated"
            ),
            "first import creates every row",
        )
        expect(
            peak < 450,
            f"api container stays under 450 MiB during the import ({peak:.0f} MiB)",
        )
        line, seconds, peak = import_cli(compose, email_of(alice_out), document)
        print(f"second import: {line}")
        print(f"second import: {seconds:.1f} s, api container peak {peak:.0f} MiB")
        expect(
            line.startswith(
                f"Imported {len(rows)} rows: 0 created, 0 updated, {len(rows)} unchanged, 0 skipped. Files: 0 added, 0 replaced"
            ),
            "second import changes nothing (idempotent)",
        )

        # --- list -----------------------------------------------------------------------------
        status, page = await get_json(alice, "/api/jobs", {"limit": 200})
        expect(status == 200, f"GET /api/jobs is 200 ({status})")
        expect(
            page["total"] == len(rows),
            f"total {page['total']} equals the CSV's {len(rows)} rows",
        )
        counts = {k: v for k, v in page["counts_by_status"].items() if v}
        expect(counts == dict(csv_status), f"counts_by_status equal the CSV: {counts}")
        fits = [int(r["fit"]) for r in rows if r["fit"]]
        buckets = [0] * 10
        for fit in fits:
            buckets[min(fit // 10, 9)] += 1
        hist = page["fit_histogram"]
        expect(
            [b["count"] for b in hist["buckets"]] == buckets
            and hist["unscored"] == len(rows) - len(fits),
            f"fit histogram equals the CSV: {[b['count'] for b in hist['buckets']]}, unscored {hist['unscored']}",
        )
        for sort in ("found_on", "fit", "company", "status", "updated"):
            seen: list[str] = []
            cursor = None
            pages = 0
            while True:
                params: dict[str, Any] = {"limit": 37, "sort": sort}
                if cursor:
                    params["cursor"] = cursor
                _, body = await get_json(alice, "/api/jobs", params)
                seen.extend(i["slug"] for i in body["items"])
                pages += 1
                cursor = body["next_cursor"]
                if not cursor:
                    break
            expect(
                len(seen) == len(rows) and set(seen) == {r["id"] for r in rows},
                f"sort {sort}: {pages} pages of 37 reach all {len(seen)} rows once, no gap",
            )

        async def total(**params: Any) -> int:
            _, body = await get_json(alice, "/api/jobs", params)
            return body["total"]

        checks = [
            ({"status": "applied"}, csv_status["applied"]),
            (
                {"status": ["applied", "interview"]},
                csv_status["applied"] + csv_status["interview"],
            ),
            ({"type": "VIE"}, sum(r["type"] == "VIE" for r in rows)),
            (
                {"company": "mistral"},
                sum("mistral" in r["company"].lower() for r in rows),
            ),
            ({"fit_min": 90}, sum(1 for f in fits if f >= 90)),
            ({"fit_min": 50, "fit_max": 79}, sum(1 for f in fits if 50 <= f <= 79)),
            (
                {"found_from": "2026-10-05", "found_to": "2026-10-05"},
                sum(r["found_on"] == "2026-10-05" for r in rows),
            ),
            (
                {"status": "shortlisted", "type": "CDI", "fit_min": 90},
                sum(
                    r["status"] == "shortlisted"
                    and r["type"] == "CDI"
                    and r["fit"]
                    and int(r["fit"]) >= 90
                    for r in rows
                ),
            ),
        ]
        for params, expected in checks:
            got = await total(**params)
            expect(got == expected, f"filter {params}: {got} rows, CSV says {expected}")
        got = await total(q="anthropic")
        expect(
            got >= sum(r["company"].lower() == "anthropic" for r in rows),
            f"q=anthropic: {got} rows",
        )
        status, body = await get_json(
            alice, "/api/jobs", {"fit_min": 80, "fit_max": 20}
        )
        expect(status == 422, f"fit_min above fit_max is 422 ({status})")

        # --- detail and download --------------------------------------------------------------
        target = next(
            r for r in rows
            if r["status"] == "applied" and (data / "jobs" / r["id"] / "cv.pdf").is_file()
            and (data / "jobs" / r["id"] / "posting.txt").is_file()
        )  # fmt: skip
        _, body = await get_json(
            alice,
            "/api/jobs",
            {"company": target["company"], "limit": 200, "status": "applied"},
        )
        job = next(i for i in body["items"] if i["slug"] == target["id"])
        status, detail = await get_json(alice, f"/api/jobs/{job['id']}")
        folder = data / "jobs" / target["id"]
        expected_files = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.iterdir() if p.suffix != ".html" and p.name != "posting.txt"
        }  # fmt: skip
        got_files = {f["name"]: f["sha256"] for f in detail["files"]}
        expect(status == 200, f"detail of an applied row is 200 ({status})")
        expect(
            got_files == expected_files,
            f"detail lists the folder's files with their SHA-256: {sorted(got_files)}",
        )
        expect(
            bool(detail["posting_text"]) and detail["legacy"].get("cv") == target["cv"],
            "detail has the posting text and the legacy columns",
        )
        cv = next(f for f in detail["files"] if f["name"] == "cv.pdf")
        r = await alice.get(f"/api/jobs/{job['id']}/files/{cv['id']}")
        raw = await r.body()
        source = (folder / "cv.pdf").read_bytes()
        expect(r.status == 200 and hashlib.sha256(raw).digest() == hashlib.sha256(source).digest(),
               f"cv.pdf downloaded through Caddy is byte-identical to the source (sha256 {hashlib.sha256(raw).hexdigest()[:16]}..., {len(raw)} bytes)")  # fmt: skip
        headers = r.headers
        print("download headers: " + json.dumps({k: headers.get(k) for k in (
            "content-type", "content-disposition", "x-content-type-options", "cache-control", "etag")}))  # fmt: skip
        expect(headers["content-type"] == "application/pdf" and headers["content-disposition"].startswith("inline;")
               and headers["x-content-type-options"] == "nosniff" and headers["etag"] == f'"{cv["sha256"]}"',
               "PDF served inline, nosniff, ETag = SHA-256")  # fmt: skip
        r = await alice.get(
            f"/api/jobs/{job['id']}/files/{cv['id']}",
            headers={"If-None-Match": headers["etag"]},
        )
        expect(r.status == 304, f"If-None-Match gives 304 ({r.status})")
        json_file = next(
            (f for f in detail["files"] if f["name"].endswith(".json")), None
        )
        if json_file:
            r = await alice.get(f"/api/jobs/{job['id']}/files/{json_file['id']}")
            expect(
                r.headers["content-disposition"].startswith("attachment;"),
                f"{json_file['name']} is an attachment",
            )

        # --- isolation ------------------------------------------------------------------------
        status, body = await get_json(bob, "/api/jobs")
        expect(
            status == 200 and body["total"] == 0,
            f"Bob sees none of Alice's rows (total {body['total']})",
        )
        r = await bob.get(f"/api/jobs/{job['id']}")
        expect(r.status == 404, f"Bob gets 404 on Alice's job ({r.status})")
        r = await bob.get(f"/api/jobs/{job['id']}/files/{cv['id']}")
        expect(r.status == 404, f"Bob gets 404 on Alice's file ({r.status})")

        # --- HTTP import ------------------------------------------------------------------------
        small = {
            "format": "jobapp-jobs", "version": 1, "exported_at": "2026-10-09T08:00:00+00:00",
            "jobs": [
                {"slug": "bob-one", "company": "Bob Co", "title": "One", "status": "shortlisted"},
                {"slug": "bob-two", "company": "Bob Co", "title": "Two", "fit": 72},
            ],
        }  # fmt: skip
        r = await bob.post(
            "/api/jobs/import",
            data=json.dumps(small),
            headers={**CSRF, "Content-Type": "application/json"},
        )
        body = await r.json()
        expect(
            r.status == 200 and body["created"] == 2,
            f"Bob's small HTTP import: {r.status}, created {body.get('created')}",
        )
        r = await bob.post(
            "/api/jobs/import",
            data=json.dumps(small),
            headers={**CSRF, "Content-Type": "application/json"},
        )
        body = await r.json()
        expect(body["unchanged"] == 2, f"again: unchanged {body.get('unchanged')}")
        r = await bob.post(
            "/api/jobs/import",
            data=document.read_bytes(),
            headers={**CSRF, "Content-Type": "application/json"},
        )
        expect(
            r.status == 413,
            f"Harald's {size / 1_000_000:.1f} MB document over HTTP is refused by Caddy: {r.status}",
        )
        status, body = await get_json(alice, "/api/jobs", {"limit": 1})
        expect(body["total"] == len(rows), "Alice still has exactly her rows")

        # --- screenshot ---------------------------------------------------------------------------
        browser = await p.chromium.launch(
            channel="chrome"
        )  # the Mac's Chrome, headless
        context = await browser.new_context(viewport={"width": 1100, "height": 900})
        await context.set_extra_http_headers({"Cookie": f"{COOKIE}={alice_token}"})
        page_ = await context.new_page()
        await page_.goto(f"{BASE}/api/jobs?status=closed&limit=1&sort=fit")
        await page_.screenshot(path=str(HERE / "jobs-list-api.png"), full_page=True)
        await browser.close()
        print(
            "screenshot: jobs-list-api.png (GET /api/jobs?status=closed&limit=1 in Chromium, Alice's session)"
        )

    print()
    print("FAILED: " + "; ".join(failures) if failures else "ALL CHECKS PASSED")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(
        run(
            Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5:]
        )
    )
