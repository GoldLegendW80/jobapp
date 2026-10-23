"""We Work Remotely (opportunities O7): the RSS feed https://weworkremotely.com/remote-jobs.rss,
one request per sweep.

Built against the feed notice https://weworkremotely.com/remote-job-rss-feed as
SPEC-opportunities quotes it on 2026-10-20: anyone may use the feed, and the links are attributed
back to We Work Remotely. So every posting keeps its address on weworkremotely.com and the source
`weworkremotely`, and the card says "via We Work Remotely" linking to it. The spec notes that the
page answered 403 to a plain fetch from a laptop on 2026-10-20; the owner checks the feed itself
from the Jetson (Checkpoint O) before relying on it, and a 403 is reported as `blocked`.

Each `<item>`: `<title>` ("Company: Role"), `<link>` (the posting on weworkremotely.com),
`<description>` (HTML), `<pubDate>` (RFC 822), `<region>`, `<category>`, `<type>` (Full-Time,
Contract, ...). An item without a role, a company or an address on weworkremotely.com is
skipped. The XML is read with defusedxml (no entity expansion, no external entity). Every posting
is remote. A posting whose type or category says contract or freelance, or whose text says it is
a freelance or contract mission, is a freelance mission; any other has the contract its title
says (the jobs module's rule).
"""

import asyncio
import email.utils
import itertools
import re
from typing import Any

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from app.jobs import ats
from app.opportunities.sources import (
    FREELANCE,
    ITEMS_MAX,
    NAME_MAX,
    TITLE_MAX,
    FeedClient,
    FeedError,
    Found,
    Parsed,
    day_of,
    line,
    on_host,
    plain_text,
    remote_location,
    too_many,
)
from app.profile.schemas import Feed

FEED = "https://weworkremotely.com/remote-jobs.rss"
STUB_PREFIX = "weworkremotely"
HOSTS = frozenset({"weworkremotely.com", "www.weworkremotely.com"})
_LABEL = re.compile(r"\b(?:contract|contractor|freelance|freelancer)\b", re.IGNORECASE)
_TEXT = re.compile(
    r"\b(?:freelance|freelancer|contractor)\b"
    r"|\bcontract\s+(?:role|position|basis|job|work|mission|engagement)\b",
    re.IGNORECASE,
)
TEXT_READ = 2_000  # characters of the text read for the freelance rule


def freelance(kind: str, category: str, text: str) -> bool:
    return bool(_LABEL.search(kind) or _LABEL.search(category) or _TEXT.search(text[:TEXT_READ]))


async def read(client: FeedClient, feed: Feed) -> Found:
    url, hosts = client.address(FEED, STUB_PREFIX)
    body = await client.get(url, hosts)
    return await asyncio.to_thread(parse, body)


def _published(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return day_of(email.utils.parsedate_to_datetime(raw.strip()))
    except (TypeError, ValueError, IndexError):
        return ""


def parse(body: bytes) -> Found:
    """The postings of a feed. Raises FeedError `invalid_response` when it is not RSS,
    `too_many` (before any item is parsed) when it holds more than ITEMS_MAX items."""
    try:
        root = ElementTree.fromstring(body, forbid_dtd=True)
    except (ElementTree.ParseError, DefusedXmlException, ValueError):
        raise FeedError("invalid_response") from None
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        raise FeedError("invalid_response")
    too_many(sum(1 for _ in itertools.islice(channel.iter("item"), ITEMS_MAX + 1)))
    found = Parsed()
    for item in channel.iter("item"):
        try:
            posting = _posting(item)
        except Exception:  # one bad item is skipped and counted, never the feed
            found.skipped += 1
            continue
        if posting is not None:
            found.append(posting)
    return found


def _posting(item: Any) -> tuple[str, ats.Posting] | None:
    heading = line(item.findtext("title"), TITLE_MAX + NAME_MAX)
    company, colon, role = heading.partition(":")
    company, role = company.strip()[:NAME_MAX], role.strip()[:TITLE_MAX]
    url = on_host(item.findtext("link") or item.findtext("guid"), HOSTS)
    if not colon or not company or not role or url is None:
        return None
    text = plain_text(item.findtext("description"))
    kind = line(item.findtext("type"), NAME_MAX)
    category = line(item.findtext("category"), NAME_MAX)
    return (
        company,
        ats.Posting(
            title=role,
            location=remote_location(line(item.findtext("region"), NAME_MAX)),
            url=url,
            published=_published(item.findtext("pubDate")),
            text=text,
            remote=True,
            work_mode="remote",
            contract_type=FREELANCE if freelance(kind, category, text) else None,
        ),
    )
