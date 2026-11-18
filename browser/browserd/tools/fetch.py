"""web_fetch, the website search's read of one public page without driving it (browser B28,
owner 2026-11-17: "It must work, even if i'm not logged in", #792).

A discovery's agent may read a public http or https address the way Claude Code's WebFetch reads
one on the owner's Mac: a GET from a fresh HTTP client (`client`: no cookie kept or sent, no
storage, never the person's signed in context) through browserd's egress proxy (public addresses
only, proxy.py; no environment setting is read, so nothing bypasses it), bounded to FETCH_MAX
bytes and FETCH_S seconds. The body is read as a stream and counted as it comes, before and
after decoding (`Body`): `identity` is asked for, a gzip or deflate answer is decoded part by
part with the same bound, any other encoding is refused, so neither a stream without end nor a
compressed bomb outgrows the bound (round 1 of #805: Playwright's request context read the
whole body, and inflated it, before any bound was checked). Redirects are followed by this code,
one hop at a time, and only to an address the search may open (any public http or https
address, no user part, no sign out address); a redirect's body is never read; anything else
stops the fetch.

The page is answered as a snapshot of the same grammar as Playwright's (`as_snapshot`: headings,
paragraphs, links with their addresses), so the api reads, keeps and verifies it as any page of
the search. No script of the page runs.

No bypass: a page that is a security check (Cloudflare's, DataDome's, PerimeterX's, Imperva's,
a captcha, an error status with almost no text) or a sign in page answers `blocked` with its
reason (`blocked`), never solved, never retried here (the api fetches a host at most twice once
it was blocked). Accept and refuse tables: api/tests/browser/test_b28_web_fetch.py.
"""

import asyncio
import ipaddress
import logging
import re
import unicodedata
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from browserd import proxy
from browserd.config import PROXY_PORT
from browserd.tools import discovery, line, policy

log = logging.getLogger("browserd.tools.fetch")

FETCH_MAX = 10 * 1024 * 1024  # bytes of one answer's body
FETCH_S = 30.0  # the whole fetch, redirects included
REDIRECTS_MAX = 10
LINKS_MAX = 5_000
NAME_MAX = 1_000  # characters of a heading's or a link's name
TEXT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml",
     "application/json", "application/ld+json", ""}
)  # fmt: skip
# Elements whose content a person never reads as the page's text.
HIDDEN = frozenset({"script", "style", "noscript", "template", "svg", "iframe", "object"})
BLOCKS = frozenset(
    {"p", "div", "li", "ul", "ol", "dl", "dt", "dd", "tr", "td", "th", "table", "section",
     "article", "main", "header", "footer", "nav", "aside", "form", "br", "hr", "blockquote",
     "pre", "address", "figcaption", "label", "option", "button", "summary", "details"}
)  # fmt: skip
HEADINGS = {f"h{n}": n for n in range(1, 7)}
VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "col"})

# What a security check page carries in its code, wherever it is on the page: Cloudflare's
# challenge options and widget, DataDome's, PerimeterX's and Imperva's challenge scripts. Never
# Cloudflare's `challenge-platform/scripts/jsd`, which ordinary pages behind it load too.
CHECK_CODE = (
    "cf_chl_opt", "cf-chl-widget", "/cdn-cgi/challenge-platform/h/", "captcha-delivery.com",
    "px-captcha", "_incapsula_resource", "challenges.cloudflare.com/turnstile",
)  # fmt: skip
# What a security check page says, folded (lower case, accents aside), read in its title, or
# anywhere in a page with little text (SHORT_TEXT): a posting's own text may say "captcha".
CHECK_WORDS = (
    "just a moment", "attention required", "additional verification required",
    "verification supplementaire requise", "checking your browser", "verify you are human",
    "verify you are a human", "are you a robot", "not a robot", "captcha", "security check",
    "access denied", "acces refuse", "zugriff verweigert", "sicherheitsuberprufung",
    "verification de securite", "please enable javascript and cookies", "unusual traffic",
)  # fmt: skip
SHORT_TEXT = 3_000  # characters of visible text: a check or an error page has less
CHECK_STATUSES = frozenset({401, 403, 429, 503})
# A sign in page: a password box, and its address, its title or its little text says sign in.
SIGNIN_PATHS = frozenset(
    {"login", "signin", "sign-in", "sign_in", "log-in", "logon", "auth", "authwall",
     "authenticate", "connexion", "se-connecter", "anmelden", "anmeldung", "einloggen",
     "uas", "checkpoint", "account-login", "sso"}
)  # fmt: skip
SIGNIN_WORDS = (
    "sign in", "signin", "log in", "login", "connexion", "se connecter", "identifiez-vous",
    "anmelden", "einloggen", "authwall",
)  # fmt: skip
SPACES = re.compile(r"\s+")
NUMERIC = re.compile(r"(?:0x[0-9a-f]*|[0-9]+)")


def fold(text: str) -> str:
    """Lower case, accents aside, spaces made one."""
    decomposed = unicodedata.normalize("NFKD", text)
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return SPACES.sub(" ", bare.lower()).strip()


# Host names that are never public: the machine itself and local networks' names.
PRIVATE_NAMES = ("localhost", ".localhost", ".local", ".internal", ".home.arpa", ".lan")


def private_host(host: str) -> bool:
    """An address literal that is not public unicast (proxy.address_allowed), a local name, or a
    single label name (`api`, `worker`, `caddy`: the Compose services, a local network's
    machines; no public site has one). The egress proxy refuses a private address whatever its
    name resolves to; this refuses the obvious ones before any request (the line in two
    places)."""
    name = host.lower().rstrip(".").strip("[]")
    if name == "localhost" or name.endswith(PRIVATE_NAMES):
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        if "." not in name:
            return True
        # 2130706433, 0x7f.1, 127.1, 0177.0.0.1: a number a resolver reads as an address.
        return all(NUMERIC.fullmatch(label) for label in name.split(".") if label)
    return not proxy.address_allowed(name)


def allowed(url: object) -> str | None:
    """None when web_fetch may GET `url` (any public http or https address of a discovery, no
    user part, no private host, no sign out address), else the refusal's code."""
    if not discovery.public_url(url):
        code = policy.url_verdict(url, ())
        return code if code and code != "host_not_allowed" else "bad_url"
    try:
        host = urlsplit(str(url)).hostname or ""
    except ValueError:
        return "bad_url"
    if "%" in host:
        # A percent encoded host (`%31%32%37.0.0.1`, `localhost%2e`, an IPv6 zone): a browser
        # decodes it (WHATWG) to a name this check never read. No public site needs one.
        return "bad_url"
    if not host or private_host(host):
        return "private_address"
    if line.signs_out(str(url)):
        return "line"
    return None


def _quoted(text: str) -> str:
    return text[:NAME_MAX].replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class Read:
    """A page as web_fetch reads it: its title, its snapshot lines, its visible text's size,
    the addresses of its links and whether it holds a password box."""

    title: str = ""
    lines: list[str] = field(default_factory=list)
    text_size: int = 0
    links: int = 0
    password: bool = False


class _Reader(HTMLParser):
    """HTML to Playwright's snapshot grammar, linear in the page's size: a heading as
    `- heading "name" [level=n]`, its links nested under it; text as `- paragraph: text`; a link
    as `- link "name":` with its `- /url:`. Hidden elements (HIDDEN) are left out."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.read = Read()
        # The hidden element being skipped (its tag) and how many of that tag are open in it:
        # only its own tag counts, so an unclosed <p> inside a <noscript> hides nothing after.
        self.hidden_tag = ""
        self.hidden = 0
        self.in_title = False
        self.title: list[str] = []
        self.text: list[str] = []
        self.heading: tuple[int, list[str], list[str]] | None = None  # level, words, links
        self.link: tuple[str, list[str]] | None = None  # href, words

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        given = {k: v or "" for k, v in attrs}
        if tag == "title":
            self.in_title = True
            return
        if tag == "input" and given.get("type", "").lower() == "password":
            self.read.password = True
        if tag in VOID:
            if tag == "br":
                self._flush()
            return
        if self.hidden:
            self.hidden += tag == self.hidden_tag
            return
        if tag in HIDDEN or "hidden" in given or given.get("aria-hidden") == "true":
            self.hidden_tag, self.hidden = tag, 1
            return
        if tag in HEADINGS:
            self._flush()
            self.heading = (HEADINGS[tag], [], [])
        elif tag == "a" and given.get("href"):
            if self.heading is None:
                self._flush()
            self.link = (given["href"], [])
        elif tag in BLOCKS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
            return
        if tag in VOID:
            return
        if self.hidden:
            self.hidden -= tag == self.hidden_tag
            return
        if tag == "a" and self.link is not None:
            href, words = self.link
            self.link = None
            name = " ".join(" ".join(words).split())
            target = urljoin(self.base, href.strip())
            if urlsplit(target).scheme in ("http", "https") and self.read.links < LINKS_MAX:
                self.read.links += 1
                entry = [f'- link "{_quoted(name)}":', f"  - /url: {target}"]
                if self.heading is not None:
                    self.heading[2].extend("  " + line for line in entry)
                else:
                    self.read.lines.extend(entry)
            if self.heading is not None:
                self.heading[1].append(name)
            else:
                self.text.append(name)
                self.read.text_size += len(name) + 1
        elif tag in HEADINGS and self.heading is not None:
            level, words, links = self.heading
            self.heading = None
            name = " ".join(" ".join(words).split())
            if name:
                self.read.text_size += len(name) + 1
                self.read.lines.append(
                    f'- heading "{_quoted(name)}" [level={level}]' + (":" if links else "")
                )
                self.read.lines.extend(links)
        elif tag in BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
            return
        if self.hidden:
            return
        if self.link is not None:
            self.link[1].append(data)
        elif self.heading is not None:
            self.heading[1].append(data)
        else:
            self.text.append(data)
            self.read.text_size += len(data.strip())

    def _flush(self) -> None:
        text = " ".join(" ".join(self.text).split())
        self.text = []
        if text:
            self.read.lines.append(f"- paragraph: {text}")

    def done(self) -> Read:
        if self.link is not None:
            self.handle_endtag("a")
        if self.heading is not None:
            self.handle_endtag(f"h{self.heading[0]}")
        self._flush()
        self.read.title = " ".join(" ".join(self.title).split())[:NAME_MAX]
        return self.read


def as_snapshot(url: str, body: str, content_type: str) -> Read:
    """A fetched page as a snapshot (module docstring). Plain text, JSON and XML are one
    paragraph per line, with no link. Pure."""
    if content_type not in ("text/html", "application/xhtml+xml", ""):
        read = Read()
        for raw in body.splitlines():
            text = " ".join(raw.split())
            if text:
                read.lines.append(f"- paragraph: {text}")
                read.text_size += len(text) + 1
        return read
    reader = _Reader(url)
    try:
        reader.feed(body)
        reader.close()
    except Exception:  # html.parser is lenient; whatever it still refuses ends the page there
        log.warning("web_fetch: the page could not be read whole")
    return reader.done()


def blocked(url: str, status: int, headers: Mapping[str, str], body: str, read: Read) -> str:
    """Why a fetched page is no page to read: `security_check`, `signin_wall`, else "".
    Fails closed: a check's code anywhere, a check's words in the title or on a page with little
    text, or an error status with little text, is a security check. Pure and linear."""
    lowered = body.lower()
    short = read.text_size < SHORT_TEXT
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return "security_check"
    if any(code in lowered for code in CHECK_CODE):
        return "security_check"
    title = fold(read.title)
    if any(word in title for word in CHECK_WORDS):
        return "security_check"
    if short and any(word in fold(body) for word in CHECK_WORDS):
        return "security_check"
    if status in CHECK_STATUSES and short:
        return "security_check"
    segments = {fold(part) for part in urlsplit(url).path.split("/") if part}
    if "authwall" in segments:
        return "signin_wall"
    if read.password and (
        segments & SIGNIN_PATHS or any(word in title for word in SIGNIN_WORDS) or short
    ):
        return "signin_wall"
    return ""


def failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _charset(content_type: str) -> str:
    found = re.search(r"charset=\"?([A-Za-z0-9_.:-]{1,40})", content_type)
    return found.group(1) if found else "utf-8"


class Body:
    """A body read as a stream, bounded as it comes (module docstring): its bytes on the wire
    and, once decoded, its bytes again, each at most FETCH_MAX. `identity` is passed through,
    `gzip` and `deflate` are inflated part by part (zlib's `max_length`, so a bomb never yields
    more than the room left), any other encoding is refused."""

    ENCODINGS = frozenset({"", "identity", "gzip", "x-gzip", "deflate"})

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.wire = 0
        self.size = 0
        self.parts: list[bytes] = []
        self.inflate: Any = None
        if encoding in ("gzip", "x-gzip"):
            self.inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            self.inflate = zlib.decompressobj()
        self.raw_deflate = encoding == "deflate"  # zlib wrapped, else raw on the first error

    def feed(self, chunk: bytes) -> bool:
        """Take one chunk; False once the body passed FETCH_MAX on the wire or decoded."""
        self.wire += len(chunk)
        if self.wire > FETCH_MAX:
            return False
        if self.inflate is None:
            return self._keep(chunk)
        data = chunk
        while data:
            try:
                out = self.inflate.decompress(data, FETCH_MAX - self.size + 1)
            except zlib.error:
                if not (self.raw_deflate and self.size == 0 and self.wire == len(chunk)):
                    raise
                self.raw_deflate = False  # a raw deflate stream, as some servers send
                self.inflate = zlib.decompressobj(-zlib.MAX_WBITS)
                continue
            if not self._keep(out):
                return False
            data = self.inflate.unconsumed_tail
        return True

    def _keep(self, data: bytes) -> bool:
        self.size += len(data)
        if self.size > FETCH_MAX:
            return False
        self.parts.append(data)
        return True

    def joined(self) -> bytes:
        return b"".join(self.parts)


def client() -> httpx.AsyncClient:
    """A fresh client for one fetch: every request through browserd's egress proxy (http by its
    absolute form, https by CONNECT), with no environment setting read (`trust_env=False`: no
    proxy variable, no NO_PROXY bypass, no netrc), no redirect followed by itself, no cookie kept
    or sent (a cookie policy that allows no domain), certificates checked, and `identity` asked
    for (Body reads any other answer)."""
    return httpx.AsyncClient(
        proxy=f"http://127.0.0.1:{PROXY_PORT}",
        trust_env=False,
        follow_redirects=False,
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        timeout=FETCH_S,
        headers={"Accept-Encoding": "identity"},
    )


async def run(tab: Any, args: dict[str, Any]) -> dict[str, Any]:
    """web_fetch(url) on a discovery's lease (module docstring)."""
    if not tab.state.discovery or tab.state.native_failed:
        return failure(discovery.UNSUPPORTED, "Refused: web_fetch belongs to a website search.")
    if args.get("method", "GET") != "GET":
        return failure("method", "Refused: web_fetch only reads (GET).")
    url = args.get("url")
    code = allowed(url)
    if code:
        log.warning("lease %s: web_fetch refused: %s", tab.lease_tag, code)
        return failure(code, "Refused: the app does not fetch this address.")
    try:
        async with asyncio.timeout(FETCH_S):
            return await _fetch(tab, str(url))
    except TimeoutError:
        return failure("fetch_timeout", f"The page did not answer within {FETCH_S:.0f} s.")


async def _fetch(tab: Any, url: str) -> dict[str, Any]:
    # A fresh client, never the lease's context: no cookie of any context is sent.
    async with client() as http:
        for _ in range(REDIRECTS_MAX + 1):
            try:
                response = await http.send(http.build_request("GET", url), stream=True)
            except Exception as exc:  # httpx's errors, a proxy refusal, a reset: no page
                log.warning("lease %s: web_fetch failed: %s", tab.lease_tag, type(exc).__name__)
                return failure("fetch_failed", "The page could not be fetched.")
            try:
                status, headers = response.status_code, response.headers
                location = headers.get("location", "")
                if 300 <= status < 400 and location:
                    url = urljoin(url, location.strip())  # the body of a redirect is never read
                    if code := allowed(url):
                        log.warning("lease %s: web_fetch redirect refused: %s", tab.lease_tag, code)
                        return failure(
                            "redirect_refused", "Refused: the page redirects off limits."
                        )
                    continue
                return await _answer(tab, url, status, headers, response)
            finally:
                await response.aclose()
        return failure("redirects_max", f"More than {REDIRECTS_MAX} redirects.")


TOO_LARGE = f"The page is larger than {FETCH_MAX // 1_048_576} MB."


async def _answer(
    tab: Any, url: str, status: int, headers: Mapping[str, str], response: Any
) -> dict[str, Any]:
    kind = headers.get("content-type", "")
    content_type = kind.split(";")[0].strip().lower()
    try:
        declared = int(headers.get("content-length", "0"))
    except ValueError:
        declared = 0
    if declared > FETCH_MAX:
        return failure("too_large", TOO_LARGE)
    if content_type not in TEXT_TYPES:
        return failure("not_text", "The address is no text page (a file or an image).")
    encoding = headers.get("content-encoding", "").strip().lower()
    if encoding not in Body.ENCODINGS:
        return failure("encoding", "The page came in an encoding the app does not read.")
    body = Body(encoding)
    try:
        async for chunk in response.aiter_raw():
            if not body.feed(chunk):
                return failure("too_large", TOO_LARGE)
    except Exception as exc:  # a reset, a broken gzip stream: no page
        log.warning("lease %s: web_fetch read failed: %s", tab.lease_tag, type(exc).__name__)
        return failure("fetch_failed", "The page could not be fetched.")
    data = body.joined()
    try:
        text = data.decode(_charset(kind), errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    read = await asyncio.to_thread(as_snapshot, url, text, content_type)
    reason = blocked(url, status, headers, text, read)
    if reason:
        return {
            "ok": False,
            "error": "blocked",
            "reason": reason,
            "url": url,
            "status": status,
            "message": "Blocked: the page is a security check or a sign in page.",
        }
    snapshot, cut = policy.cut_snapshot("\n".join(read.lines), policy.DISCOVERY_SNAPSHOT_MAX)
    return {
        "ok": True,
        "url": url,
        "status": status,
        "title": read.title,
        "snapshot": snapshot,
        "cut": cut,
        "links": read.links,
    }
