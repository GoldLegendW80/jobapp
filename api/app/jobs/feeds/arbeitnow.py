"""Arbeitnow (https://www.arbeitnow.com/api/job-board-api): jobs in Europe from employers'
applicant tracking systems, no key, newest first, 300 a page, updated every hour. It has no
keyword search, so a sweep pages back from the newest posting to the cursor of the previous
sweep (the newest `created_at` it read) or to `search.days`, whichever is later, at most PAGES
pages, and the shared criteria pick what it keeps. Its terms ask for no abuse and a link back to
the site: every posting's address is its page on arbeitnow.com, shown as "via Arbeitnow".

The cursor moves to the newest posting read only when the run was not stopped (a 429, a failed
page, the time budget, a refused store): a stopped run reads the same pages again next time.

Reaching PAGES before the window's end stops the run with `page_cap`: the postings older than
the last page read are not read in this sweep, the entry says so (outcome `partial`), and the
cursor does not move, so the next sweep pages back to the same point again rather than skipping
what this one could not reach. Postings of that gap that are still newer than `search.days`
are read once a sweep reaches them within its pages.

Jobs J39 (issue #877): some answers hold their description's HTML escaped twice ("&lt;p&gt;",
"&amp;nbsp;"): converted once, the tags came out as words and were stored as such. `text_of`
decides on the answer itself, before any conversion (`escaped_twice`): a description holding an
escaped tag of the converter's block list (`_Text.BLOCKS`) and no real tag is unescaped once, then
converted once, as every feed's text is, only when the conversion keeps every word a reader sees
(`kept_words`; else it is converted the plain way, its tags staying words); nothing is converted
a second time, so the 50,000 characters of `plain_text` bound the final text. The pass over old
postings (app/jobs/visible_pass.py) repairs this feed's stored texts (`is_its`) by `repair`, which
removes markup and never a word: only a text of the failed shape (`failed_shape`) whose conversion
keeps every word a reader sees (`kept_words`); any other text is left as stored, and said why.
"""

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.entities import html5
from typing import Any

import httpx

from app.jobs.feeds import (
    TEXT_MAX,
    Criteria,
    FeedContext,
    FeedError,
    Fetched,
    Finding,
    _Text,
    epoch,
    line,
    plain_text,
)
from app.jobs.models import ContractType
from app.jobs.sweep import noted_sources
from app.jobs.visible_text import visible

NAME = "arbeitnow"
API = "https://www.arbeitnow.com/api/job-board-api"
PAGES = 10  # about five days of postings on 2026-10-30
# Its postings live on its country sites (www.arbeitnow.com, .co.uk, .ch, .nl, .fr seen on
# 2026-10-30): exactly arbeitnow.com, arbeitnow.co.uk or arbeitnow.<two letters>, with or without
# www, over https.
HOST = re.compile(r"(?:www\.)?arbeitnow\.(?:com|co\.uk|[a-z]{2})")
# The cursor's overlap: a posting stamped a little before the previous run's newest one still
# reads (the addresses dedupe what was stored).
OVERLAP = timedelta(hours=1)

BLOCKS = _Text.BLOCKS
_BLOCK_NAMES = "|".join(sorted(BLOCKS))
# An escaped start or end tag of BLOCKS in an answer ("&lt;p&gt;", "&lt;/li&gt;", "&lt;br/&gt;").
ESCAPED_BLOCK = re.compile(rf"&lt;/?(?:{_BLOCK_NAMES})(?=[\s/]|&gt;)", re.IGNORECASE)
REAL_TAG = re.compile(r"</?[A-Za-z]")
# html.escape writes at most 6 characters for one: the 4 * TEXT_MAX `plain_text` reads are here.
RAW_MAX = 6 * 4 * TEXT_MAX

# The repair. Markup is a tag of BLOCKS or of these inline tags, whose words the conversion keeps;
# any other tag written in a stored text (`<String>`, `<script>`) is a reader's words.
INLINE = frozenset({"a", "b", "em", "font", "i", "s", "small", "span", "strong", "sub", "sup", "u"})
MARKUP = BLOCKS | INLINE
TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)(?=[\s/>])[^<>]*>")
# A tag cut by the end of a text the store cut at TEXT_MAX ("<span style=\"col").
CUT_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)(?:[\s/][^<>]*)?\Z")
BLOCK_TAG = re.compile(rf"<(/?)(?:{_BLOCK_NAMES})(?=[\s/>])[^<>]*>", re.IGNORECASE)
ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{0,31});")
# The failed shape (`failed_shape`): at least SHAPE_TAGS tags of BLOCKS, SHAPE_EACH of them start
# tags and SHAPE_EACH end tags, on more than half of the text's lines.
SHAPE_TAGS = 20
SHAPE_EACH = 5

# Its `job_types` words (English and German), mapped where the contract is clear.
CONTRACTS: tuple[tuple[tuple[str, ...], ContractType], ...] = (
    (("unbefristet", "permanent"), ContractType.CDI),
    (("befristet", "fixed term", "fixed-term", "temporary", "zeitarbeit"), ContractType.CDD),
    (("internship", "praktikum"), ContractType.Stage),
    (("ausbildung", "apprenticeship", "werkstudent", "working student"), ContractType.Alternance),
    (("freelance", "freiberuflich", "contractor"), ContractType.Freelance),
)


def contract_of(types: object) -> ContractType | None:
    if not isinstance(types, list):
        return None
    words = " ".join(t.casefold() for t in types if isinstance(t, str))
    for needles, contract in CONTRACTS:
        # "unbefristet" before "befristet": the first table entry that matches wins.
        if any(needle in words for needle in needles):
            return contract
    return None


def escaped_twice(description: str) -> bool:
    """Whether an answer's description is its HTML escaped twice: an escaped tag of BLOCKS in it,
    and no real tag."""
    return ESCAPED_BLOCK.search(description) is not None and REAL_TAG.search(description) is None


def text_of(description: object) -> str:
    """A posting's text: its description converted once, as every feed's is. When it is escaped
    twice (`escaped_twice`) it is unescaped once first, if the conversion then keeps every word a
    reader sees (`kept_words`), in order; else (a tag outside MARKUP, a comment, a conversion that
    raises) it is converted the plain way, its tags staying words."""
    if isinstance(description, str) and escaped_twice(raw := description[:RAW_MAX]):
        unescaped = html.unescape(raw)
        try:
            converted = plain_text(unescaped)
            if visible(converted).split() == kept_words(unescaped):
                return converted
        except ValueError:  # a numeric reference too long for int(): read the plain way
            pass
    return plain_text(description)


@dataclass(frozen=True)
class Repair:
    """What the pass does with a stored text: `text` converted, or left as stored with `left`
    saying why when it holds a tag of BLOCKS (None when it holds none)."""

    text: str | None = None
    left: str | None = None


def failed_shape(text: str) -> bool:
    """Whether a stored text is the failed shape as a document, not prose that names a tag:
    SHAPE_TAGS tags of BLOCKS at least, SHAPE_EACH start and SHAPE_EACH end tags among them, on
    more than half of its lines."""
    starts = ends = tagged = lines = 0
    for row in text.split("\n"):
        if not row.strip():
            continue
        lines += 1
        tags = [match.group(1) for match in BLOCK_TAG.finditer(row)]
        tagged += bool(tags)
        ends += tags.count("/")
        starts += len(tags) - tags.count("/")
    return starts + ends >= SHAPE_TAGS and min(starts, ends) >= SHAPE_EACH and 2 * tagged > lines


def _markup_out(match: re.Match[str]) -> str:
    name = match.group(2).lower()
    if name not in MARKUP:
        return match.group()
    return "\n" if name in BLOCKS else ""


def _entity(match: re.Match[str]) -> str:
    found = match.group()
    if found[1] != "#" and found[1:] not in html5:
        return found
    return html.unescape(found)


def kept_words(text: str) -> list[str]:
    """The words a reader sees in a stored text once its markup goes: MARKUP tags out, as the
    conversion takes them out, and the entities written whole ("&nbsp;", "&#233;") read; any
    other tag, an entity without its semicolon and the rest stay words."""
    return visible(ENTITY.sub(_entity, TAG.sub(_markup_out, text))).split()


def repair(text: str) -> Repair:
    """A stored text of this feed converted, when it is the failed shape (`failed_shape`) and the
    conversion keeps every word a reader sees in it (`kept_words`), in order; else left."""
    if BLOCK_TAG.search(text) is None:
        return Repair()
    if not failed_shape(text):
        return Repair(left="not_a_document")
    cut = CUT_TAG.search(text) if len(text) >= TEXT_MAX else None
    body = text[: cut.start()] if cut and cut.group(2).lower() in MARKUP else text
    try:
        converted = visible(plain_text(body))
    except ValueError:  # a numeric reference too long for int(): the conversion cannot read it
        return Repair(left="loses_words")
    if converted.split() != kept_words(body):
        return Repair(left="loses_words")
    if failed_shape(converted):  # escaped once more: a second walk would convert it again
        return Repair(left="still_markup")
    return Repair(text=converted)


def is_its(source: str | None) -> bool:
    """Whether a stored posting came from this feed: the first source that stored it."""
    names = noted_sources(source)
    return bool(names) and names[0] == NAME


def address_of(value: object) -> str | None:
    """The posting's page on an Arbeitnow site (HOST), None for any other address."""
    if not isinstance(value, str) or len(value) > 2000 or not value.startswith("https://"):
        return None
    try:
        host = httpx.URL(value).host.lower()
    except httpx.InvalidURL:
        return None
    return value if HOST.fullmatch(host) else None


def finding_of(item: Any, now: datetime) -> Finding | None:
    """One posting of a page, None when it lacks what a row needs."""
    if not isinstance(item, dict):
        return None
    slug, title = line(item.get("slug"), 300), line(item.get("title"))
    url = address_of(item.get("url"))
    if not slug or not title or url is None:
        return None
    remote = item.get("remote") is True
    location = line(item.get("location"))
    if remote:
        location = f"{location}, Remote" if location else "Remote"
    return Finding(
        source=NAME,
        external_id=slug,
        title=title,
        company=line(item.get("company_name")) or None,
        location=location,
        url=url,
        text=text_of(item.get("description")),
        posted_at=epoch(item.get("created_at")),
        remote=remote or None,
        contract=contract_of(item.get("job_types")),
        read_at=now,
    )


class Arbeitnow:
    name = NAME
    host = "www.arbeitnow.com"
    budget = PAGES
    needs_key = False
    place_filter = True

    async def fetch(self, ctx: FeedContext, criteria: Criteria) -> Fetched:
        cutoff = ctx.now - timedelta(days=criteria.days)
        if ctx.cursor is not None:
            previous = epoch(ctx.cursor)
            if previous is not None:
                cutoff = max(cutoff, previous - OVERLAP)
        fetched = ctx.fetched  # added to as it reads: a cancelled run keeps its findings
        newest = ctx.cursor
        page = 1
        ended = False  # the window's end, or the feed's last page, was reached
        while ctx.http.left > 0:
            try:
                data = await ctx.http.get_json(API, {"page": page})
            except FeedError as error:
                fetched.error = error.code
                break
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                fetched.error = "invalid_response"
                break
            reached = not items
            for item in items:
                stamp = item.get("created_at") if isinstance(item, dict) else None
                if type(stamp) is int and (newest is None or stamp > newest):
                    newest = stamp
                found = finding_of(item, ctx.now)
                if found is None:
                    continue
                if found.posted_at is not None and found.posted_at < cutoff:
                    reached = True  # newest first: the rest of the feed is older
                    continue
                fetched.findings.append(found)
            links = data.get("links") if isinstance(data.get("links"), dict) else {}
            if reached or not links.get("next"):
                ended = True
                break
            page += 1
        fetched.requests = ctx.http.requests
        if fetched.error is None and not ended:
            fetched.error = "page_cap"  # module docstring: said, and the cursor stays
        if fetched.error is None:
            fetched.cursor = newest
        return fetched
