"""Arbeitnow (https://www.arbeitnow.com/api/job-board-api): jobs in Europe from employers'
applicant tracking systems, no key, newest first, 300 a page, updated every hour. It has no
keyword search, so a sweep pages back from the newest posting to the cursor of the previous
sweep (the newest `created_at` it read) or to `search.days`, whichever is later, at most PAGES
pages, and the shared criteria pick what it keeps. Its terms ask for no abuse and a link back to
the site: every posting's address is its page on arbeitnow.com, shown as "via Arbeitnow".

The cursor moves to the newest posting read only when the run was not stopped by an error
(a 429 or a failed page): a stopped run reads the same pages again next time.
"""

import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.jobs.feeds import (
    Criteria,
    FeedContext,
    FeedError,
    Fetched,
    Finding,
    epoch,
    line,
    plain_text,
)
from app.jobs.models import ContractType

API = "https://www.arbeitnow.com/api/job-board-api"
PAGES = 10  # about five days of postings on 2026-10-30
# Its postings live on its country sites (www.arbeitnow.com, .co.uk, .ch, .nl, .fr seen on
# 2026-10-30): exactly arbeitnow.com, arbeitnow.co.uk or arbeitnow.<two letters>, with or without
# www, over https.
HOST = re.compile(r"(?:www\.)?arbeitnow\.(?:com|co\.uk|[a-z]{2})")
# The cursor's overlap: a posting stamped a little before the previous run's newest one still
# reads (the addresses dedupe what was stored).
OVERLAP = timedelta(hours=1)

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
        source="arbeitnow",
        external_id=slug,
        title=title,
        company=line(item.get("company_name")) or None,
        location=location,
        url=url,
        text=plain_text(item.get("description")),
        posted_at=epoch(item.get("created_at")),
        remote=remote or None,
        contract=contract_of(item.get("job_types")),
        read_at=now,
    )


class Arbeitnow:
    name = "arbeitnow"
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
        fetched = Fetched()
        newest = ctx.cursor
        page = 1
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
                break
            page += 1
        fetched.requests = ctx.http.requests
        if fetched.error is None:
            fetched.cursor = newest
        return fetched
