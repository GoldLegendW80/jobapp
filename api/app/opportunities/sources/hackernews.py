"""Hacker News "Who is hiring?" and "Freelancer? Seeking freelancer?" (opportunities O7): the
official public API https://hacker-news.firebaseio.com/v0/.

Built against https://github.com/HackerNews/API as SPEC-opportunities quotes it on 2026-10-20:
the API makes the public Hacker News data available, needs no authentication, and states no rate
limit. Each posting keeps its comment's address on news.ycombinator.com, and the card says "via
Hacker News" linking to it.

The read, per sweep and kind: the `whoishiring` account (`/v0/user/whoishiring.json`), its newest
submissions one by one (at most THREAD_LOOKUP) until the newest thread of the kind is found (a
story of that account whose title starts with the thread's words), then its top level comments
(`kids`, at most FEEDS_HN_MAX_COMMENTS, HN_CONCURRENCY at a time). A comment that is deleted,
dead, a reply (its parent is not the thread), without text, or that could not be read, is
skipped; when every comment of a thread with comments failed, the feed reports the first error.

A comment is one posting, read from its first line (the text before the first paragraph break),
whose parts are separated by "|":
- "Freelancer? Seeking freelancer?": only the posts of clients, whose first line starts with
  "SEEKING FREELANCER"; the posts of freelancers offering their work ("SEEKING WORK") are not
  opportunities and are skipped. Every post kept is a freelance mission. The client is the first
  part after those words, the title the second (a post with one part is titled by it, its client
  "Client on Hacker News").
- "Who is hiring?": the company is the first part, the title the first later part that names a
  role (engineer, developer, ...), else the second part; a post with one part is skipped. A
  part saying contract or freelance makes it a freelance mission, else the contract its title
  says (the jobs module's rule).
The location is the other parts that are short and carry no address or figure, at most two.
Such posts often give an e-mail contact: it stays in the text, for the outreach the person
writes himself (O9a); nothing is sent.
"""

import asyncio
import re
from typing import Any

from app.jobs import ats
from app.opportunities.sources import (
    FREELANCE,
    HN_CONCURRENCY,
    HN_MAX_COMMENTS,
    NAME_MAX,
    TITLE_MAX,
    FeedClient,
    FeedError,
    Found,
    from_epoch,
    line,
    parse_json,
    plain_text,
)

API = "https://hacker-news.firebaseio.com/v0"
STUB_PREFIX = "hackernews"
ACCOUNT = "whoishiring"
ITEM_ADDRESS = "https://news.ycombinator.com/item?id={id}"
THREAD_LOOKUP = 9  # the account posts three threads a month
THREADS = {
    "freelance": re.compile(r"^\s*ask hn:\s*freelancer\?\s*seeking freelancer\?", re.IGNORECASE),
    "hiring": re.compile(r"^\s*ask hn:\s*who is hiring\?", re.IGNORECASE),
}
UNNAMED_CLIENT = "Client on Hacker News"
_SEEKING = re.compile(r"^\s*seeking\s+freelancers?\b[\s|:,.\-\u2013\u2014]*", re.IGNORECASE)
_FIRST_BREAK = re.compile(r"<\s*p\b[^>]*>|\n", re.IGNORECASE)
_ROLE = re.compile(
    r"\b(?:engineers?|developers?|scientists?|designers?|managers?|leads?|architects?|analysts?"
    r"|researchers?|devops|sre|consultants?|specialists?|head|director|cto|programmers?"
    r"|freelancers?|contractors?|writers?|marketers?|ing[ée]nieur|d[ée]veloppeur)\b",
    re.IGNORECASE,
)
_CONTRACT = re.compile(r"\b(?:contract|contractor|contracting|freelance)\b", re.IGNORECASE)
_NOT_PLACE = re.compile(
    r"https?://|www\.|@|\d{2}|[$€£]|\b(?:full|part)[\s-]?time\b|\bsalary\b|\bequity\b|\bvisa\b",
    re.IGNORECASE,
)
PLACE_MAX = 80


def _item_url(client: FeedClient, path: str) -> tuple[str, frozenset[str]]:
    return client.address(f"{API}/{path}", STUB_PREFIX)


async def _get_json(client: FeedClient, path: str) -> Any:
    url, hosts = _item_url(client, path)
    return parse_json(await client.get(url, hosts))


async def thread_of(client: FeedClient, thread: str) -> dict[str, Any] | None:
    """The newest thread of this kind posted by the account, None when none of its newest
    submissions is one."""
    user = await _get_json(client, f"user/{ACCOUNT}.json")
    if not isinstance(user, dict) or not isinstance(user.get("submitted"), list):
        raise FeedError("invalid_response")
    pattern = THREADS[thread]
    for item_id in user["submitted"][:THREAD_LOOKUP]:
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            continue
        item = await _get_json(client, f"item/{item_id}.json")
        if (
            isinstance(item, dict)
            and item.get("by") == ACCOUNT
            and item.get("type") == "story"
            and not item.get("deleted")
            and not item.get("dead")
            and isinstance(item.get("title"), str)
            and pattern.search(item["title"])
        ):
            return item
    return None


async def read(client: FeedClient, thread: str, max_comments: int = HN_MAX_COMMENTS) -> Found:
    story = await thread_of(client, thread)
    if story is None:
        return []
    kids = [k for k in story.get("kids") or [] if isinstance(k, int) and not isinstance(k, bool)]
    kids = kids[:max_comments]
    gate = asyncio.Semaphore(HN_CONCURRENCY)
    errors: list[FeedError] = []

    async def one(item_id: int) -> Any:
        async with gate:
            try:
                return await _get_json(client, f"item/{item_id}.json")
            except FeedError as exc:
                errors.append(exc)
                return None

    comments = await asyncio.gather(*(one(item_id) for item_id in kids))
    if kids and len(errors) == len(kids):
        raise errors[0]
    return parse(thread, story.get("id"), comments)


def parse(thread: str, story_id: object, comments: list[Any]) -> Found:
    """The postings of a thread's top level comments (module docstring), pure."""
    found: Found = []
    for comment in comments:
        posting = parse_comment(thread, story_id, comment)
        if posting is not None:
            found.append(posting)
    return found


def _parts(first_line: str) -> list[str]:
    return [part.strip() for part in first_line.split("|") if part.strip()]


def _place(parts: list[str]) -> str:
    places = [
        part
        for part in parts
        if len(part) <= PLACE_MAX and not _NOT_PLACE.search(part) and not _CONTRACT.search(part)
    ]
    return ", ".join(places[:2])[:NAME_MAX]


def parse_comment(thread: str, story_id: object, comment: Any) -> tuple[str, ats.Posting] | None:
    if (
        not isinstance(comment, dict)
        or comment.get("deleted")
        or comment.get("dead")
        or comment.get("type") != "comment"
        or comment.get("parent") != story_id
        or isinstance(comment.get("id"), bool)
        or not isinstance(comment.get("id"), int)
        or not isinstance(comment.get("text"), str)
    ):
        return None
    raw = comment["text"]
    first = line(_FIRST_BREAK.split(raw, maxsplit=1)[0], TITLE_MAX + NAME_MAX * 2)
    text = plain_text(raw)
    if not first or not text:
        return None
    contract: str | None
    if thread == "freelance":
        seeking = _SEEKING.match(first)
        if seeking is None:
            return None  # a freelancer offering work, not a mission
        parts = _parts(first[seeking.end() :])
        if not parts:
            return None
        if len(parts) == 1:
            company, title, rest = UNNAMED_CLIENT, parts[0], []
        else:
            company, title, rest = parts[0], parts[1], parts[2:]
        contract = FREELANCE
    else:
        parts = _parts(first)
        if len(parts) < 2:
            return None
        company = parts[0]
        title = next((part for part in parts[1:] if _ROLE.search(part)), parts[1])
        rest = [part for part in parts[1:] if part is not title]
        contract = FREELANCE if any(_CONTRACT.search(part) for part in parts[1:]) else None
    company, title = company[:NAME_MAX].strip(), title[:TITLE_MAX].strip()
    if not company or not title:
        return None
    return company, ats.Posting(
        title=title,
        location=_place(rest),
        url=ITEM_ADDRESS.format(id=comment["id"]),
        published=from_epoch(comment.get("time")),
        text=text,
        remote=bool(re.search(r"\bremote\b", first, re.IGNORECASE)),
        contract_type=contract,
    )
