"""Real-world check of profile P3 (the photo) through Caddy, with Playwright.

    uv run --native-tls --no-project --with playwright==1.55.0 --with pillow==12.3.0 \
        python tasks/evidence/profile-p3/photo_check.py \
        <alice create-admin output> <bob create-admin output> [base url]

Accepts two setup links printed by `python -m app.identity.cli create-admin` (Alice and Bob).
Anonymous calls get 401. Alice uploads a landscape 3000x2000 picture stored sideways (2000x3000
pixels) with EXIF orientation 6, a GPS position, an author, a camera name, XMP and a comment; the
answer and the download are upright (1024x683) and carry none of that metadata. A second GET
with If-None-Match is a
304. Hostile files are refused (SVG, GIF, WebP, PDF, truncated, polyglot 415; 2 MB + 1 byte 413;
a 20000x20000 PNG 422) and leave the stored photo alone. Bob never sees, nor removes, Alice's
photo. The photo is shown in Chromium (cookie of Alice's session) and screenshotted: "TOP" reads
at the top only if the server applied the orientation, since the stored file has no EXIF left.
DELETE twice is 204 both times, then GET is 404.

Writes exchange.json (tokens, passwords and cookie values redacted), downloaded-photo.jpg and
photo-in-browser.png next to this file.
"""

import asyncio
import hashlib
import io
import json
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import APIRequestContext, APIResponse, async_playwright

HERE = Path(__file__).parent
CSRF = {"X-Requested-With": "jobapp"}
PASSWORD = "a long photo passphrase 2026"
URL = "/api/profile/photo"
COOKIE = "jobapp_session"
MAX_BYTES = 2 * 1024 * 1024

secrets: set[str] = {PASSWORD}
log: list[dict[str, Any]] = []
failures: list[str] = []


def redact(text: str) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def expect(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


KEEP = {
    "cache-control", "content-type", "content-length", "etag", "x-content-type-options",
    "content-disposition", "x-request-id",
}  # fmt: skip


async def record(
    step: str, method: str, response: APIResponse, sent: str | None = None
) -> Any:
    for header in response.headers_array:
        if header["name"].lower() == "set-cookie":
            secrets.add(header["value"].split(";", 1)[0].partition("=")[2].strip('"'))
    secrets.discard("")
    body: Any
    kind = response.headers.get("content-type", "")
    raw = await response.body()
    if kind.startswith("application/json"):
        body = json.loads(redact(raw.decode("utf-8")))
    elif raw:
        body = f"<{len(raw)} bytes of {kind}>"
    else:
        body = None
    log.append(
        {
            "step": step,
            "request": {"method": method, "path": redact(response.url.split("8093", 1)[-1]),
                        "body": sent},
            "response": {
                "status": response.status,
                "headers": [
                    {"name": h["name"], "value": redact(h["value"])}
                    for h in response.headers_array
                    if h["name"].lower() in KEEP
                ],
                "body": body,
            },
        }
    )  # fmt: skip
    return body


def link_token(path: str) -> str:
    match = re.search(
        r"/invite/([A-Za-z0-9_-]{43})", Path(path).read_text(encoding="utf-8")
    )
    assert match, f"no setup link in {path}"
    secrets.add(match.group(1))
    return match.group(1)


# --- the files ----------------------------------------------------------------------------------


def sideways_jpeg() -> bytes:
    """3000x2000 upright picture with TOP written at its top, stored turned 90 degrees
    counter-clockwise (2000x3000) with EXIF orientation 6, so only a viewer (or server) that
    applies the orientation shows TOP at the top."""
    upright = Image.new("RGB", (3000, 2000), (238, 242, 247))
    draw = ImageDraw.Draw(upright)
    draw.rectangle((0, 0, 3000, 420), fill=(37, 99, 235))
    font = ImageFont.load_default(size=300)
    draw.text((1500, 210), "TOP", fill="white", font=font, anchor="mm")
    draw.text((1500, 1200), "P3 photo", fill=(15, 23, 42), font=font, anchor="mm")
    draw.rectangle((0, 1700, 3000, 2000), fill=(220, 38, 38))
    stored = upright.rotate(
        90, expand=True
    )  # counter-clockwise: orientation 6 undoes it
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation
    exif[0x013B] = "Ada Example"  # artist
    exif[0x0110] = "SecretCam X100"  # camera model
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (44.0, 50.0, 16.0), "W", (0.0, 34.0, 45.0)
    xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><City>Bordeaux</City></x:xmpmeta>'
    out = io.BytesIO()
    stored.save(
        out, "JPEG", quality=90, exif=exif.tobytes(), xmp=xmp, comment=b"secret note"
    )
    return out.getvalue()


def raw_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    compressor = zlib.compressobj(9)
    row = b"\x00" * (width + 1)
    idat = (
        b"".join(compressor.compress(row) for _ in range(height)) + compressor.flush()
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def small(fmt: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 0, 0)).save(out, fmt)
    return out.getvalue()


# --- the run ------------------------------------------------------------------------------------


async def put(
    ctx: APIRequestContext,
    step: str,
    data: bytes,
    csrf: bool = True,
    content_type: str = "image/jpeg",
) -> tuple[APIResponse, Any]:
    headers = {"Content-Type": content_type, **(CSRF if csrf else {})}
    r = await ctx.put(URL, data=data, headers=headers)
    return r, await record(step, "PUT", r, f"<{len(data)} bytes>")  # fmt: skip


async def run(base: str, alice_link: str, bob_link: str) -> None:
    async with async_playwright() as p:
        anon = await p.request.new_context(base_url=base)

        for method in ("GET", "PUT", "DELETE"):
            r = await anon.fetch(URL, method=method, headers=CSRF, data=small("PNG"))
            await record(f"anonymous {method}", method, r)
            expect(r.status == 401, f"anonymous {method} {URL} is 401")

        # The session cookie is Secure (production), so a client does not send it back over
        # plain http: it is read from Set-Cookie and sent explicitly, as over loopback in P2.
        sessions: dict[str, str] = {}
        for link, name in ((alice_link, "Alice"), (bob_link, "Bob")):
            path = f"/api/auth/invites/{link}/accept"
            r = await anon.post(
                path, data={"name": name, "password": PASSWORD}, headers=CSRF
            )
            await record(f"accept invite ({name})", "POST", r, "<name and password>")
            expect(r.status == 200, f"{name} accepts the setup link and is signed in")
            cookie = next(
                h["value"] for h in r.headers_array if h["name"].lower() == "set-cookie"
            )
            expect(
                "Secure" in cookie and "HttpOnly" in cookie,
                f"{name}'s cookie: Secure HttpOnly",
            )
            sessions[name] = cookie.split(";", 1)[0].partition("=")[2]
            await anon.dispose()
            anon = await p.request.new_context(base_url=base)
        alice = await p.request.new_context(
            base_url=base,
            extra_http_headers={"Cookie": f"{COOKIE}={sessions['Alice']}"},
        )
        bob = await p.request.new_context(
            base_url=base, extra_http_headers={"Cookie": f"{COOKIE}={sessions['Bob']}"}
        )

        r = await alice.get(URL)
        await record("GET before any upload", "GET", r)
        expect(r.status == 404, "GET without a photo is 404")
        r = await alice.get("/api/profile")
        body = await record("profile before upload", "GET", r)
        expect(
            body["has_photo"] is False and body["photo_etag"] is None, "has_photo false"
        )

        source = sideways_jpeg()
        original = Image.open(io.BytesIO(source))
        print(f"     upload: {len(source)} bytes, {original.size}, EXIF tags "
              f"{sorted(original.getexif())}, GPS {dict(original.getexif().get_ifd(0x8825))}")  # fmt: skip
        expect(original.getexif()[0x0112] == 6, "the upload carries EXIF orientation 6")

        r, _ = await put(alice, "PUT without the CSRF header", source, csrf=False)
        expect(r.status == 403, "PUT without X-Requested-With is 403")

        r, body = await put(alice, "PUT the sideways JPEG", source)
        expect(r.status == 200, "PUT is 200")
        expect(
            (body["mime"], body["width"], body["height"]) == ("image/jpeg", 1024, 683),
            f"stored upright and downscaled: {body['width']}x{body['height']}",
        )
        etag = r.headers["etag"]
        expect(body["etag"] == etag, "the answer carries the ETag")

        r = await alice.get(URL)
        await record("GET the photo", "GET", r)
        data = await r.body()
        (HERE / "downloaded-photo.jpg").write_bytes(data)
        image = Image.open(io.BytesIO(data))
        expect(
            r.status == 200 and r.headers["content-type"] == "image/jpeg",
            "GET is the JPEG",
        )
        expect(
            r.headers["cache-control"] == "private, no-cache",
            "Cache-Control private, no-cache",
        )
        expect(r.headers["etag"] == etag, "same ETag")
        expect(r.headers.get("x-content-type-options") == "nosniff", "nosniff")
        expect(
            f'"{hashlib.sha256(data).hexdigest()}"' == etag,
            "ETag is the SHA-256 of the bytes",
        )
        expect(image.size == (1024, 683), f"downloaded {image.size}")
        info = {
            k: v
            for k, v in image.info.items()
            if not k.startswith(("jfif", "progress"))
        }
        expect(info == {}, f"no metadata in the file info: {sorted(info)}")
        expect(
            len(image.getexif()) == 0, "no EXIF at all (orientation, artist, camera)"
        )
        expect(dict(image.getexif().get_ifd(0x8825)) == {}, "no GPS")
        leaks = [s for s in (b"Exif", b"GPS", b"Ada Example", b"SecretCam", b"Bordeaux",
                             b"secret note", b"xmpmeta") if s in data]  # fmt: skip
        expect(leaks == [], f"none of the metadata strings in the bytes: {leaks}")
        print(
            f"     download: {len(data)} bytes, {image.size}, info keys {sorted(image.info)}"
        )

        r = await alice.get(URL, headers={"If-None-Match": etag})
        await record("GET again with If-None-Match", "GET", r)
        expect(
            r.status == 304 and await r.body() == b"",
            "second GET is 304 without a body",
        )

        r = await alice.get("/api/profile")
        body = await record("profile after upload", "GET", r)
        expect(
            body["has_photo"] is True and body["photo_etag"] == etag,
            "profile reports it",
        )
        expect(body["revision"] == 0, "no revision for the photo")

        hostile = {
            "SVG": (
                b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
                415,
            ),
            "GIF": (small("GIF"), 415),
            "WebP": (small("WEBP"), 415),
            "PDF": (b"%PDF-1.7\n%%EOF\n", 415),
            "empty": (b"", 415),
            "truncated JPEG": (source[: len(source) // 2], 415),
            "JPEG magic then HTML": (
                b"\xff\xd8\xff\xe0<html><script>alert(1)</script>",
                415,
            ),
            "2 MB + 1 byte": (source + b"\x00" * (MAX_BYTES + 1 - len(source)), 413),
            "20000x20000 PNG": (raw_png(20000, 20000), 422),
        }
        for name, (payload, status) in hostile.items():
            r, body = await put(alice, f"PUT {name}", payload)
            expect(
                r.status == status,
                f"{name} ({len(payload)} bytes) is {r.status}: {body}",
            )
        r = await alice.get(URL, headers={"If-None-Match": etag})
        expect(r.status == 304, "the refused files left the stored photo alone")

        r = await bob.get(URL)
        await record("Bob GET", "GET", r)
        expect(r.status == 404, "Bob gets 404, not Alice's photo")
        r = await bob.get(URL, headers={"If-None-Match": etag})
        expect(r.status == 404, "Bob with Alice's ETag still gets 404")
        r = await bob.delete(URL, headers=CSRF)
        await record("Bob DELETE", "DELETE", r)
        expect(r.status == 204, "Bob's DELETE is 204")
        r = await alice.get(URL, headers={"If-None-Match": etag})
        expect(r.status == 304, "and Alice still has hers")

        # The photo as a browser shows it, with Alice's session cookie.
        browser = await p.chromium.launch(
            channel="chrome"
        )  # the installed Google Chrome
        page = await browser.new_page(
            viewport={"width": 1100, "height": 760},
            extra_http_headers={"Cookie": f"{COOKIE}={sessions['Alice']}"},
        )
        await page.goto(base + URL)
        await page.screenshot(path=str(HERE / "photo-in-browser.png"))
        size = await page.evaluate(
            "[document.images[0].naturalWidth, document.images[0].naturalHeight]"
        )
        expect(size == [1024, 683], f"the browser shows a {size} image")
        await browser.close()

        r = await alice.delete(URL, headers=CSRF)
        await record("DELETE", "DELETE", r)
        expect(r.status == 204, "DELETE is 204")
        r = await alice.delete(URL, headers=CSRF)
        await record("DELETE again", "DELETE", r)
        expect(r.status == 204, "DELETE again is 204 (idempotent)")
        r = await alice.get(URL)
        await record("GET after delete", "GET", r)
        expect(r.status == 404, "GET after delete is 404")


def main() -> int:
    alice_file, bob_file = sys.argv[1], sys.argv[2]
    base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8093"
    asyncio.run(run(base, link_token(alice_file), link_token(bob_file)))
    (HERE / "exchange.json").write_text(
        json.dumps({"base": base, "note": "tokens, passwords and cookie values redacted",
                    "exchanges": log}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
