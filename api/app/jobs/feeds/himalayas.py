"""Himalayas (https://himalayas.app/api): remote jobs, here those open to people in France
(`country=FR`), searched by keyword, newest first, 20 a page, no key. A sweep searches the
first QUERIES_MAX saved titles (feeds.Criteria), one or two pages each within BUDGET requests,
stopping a title at a page older than `search.days`. The country is the place: the saved places
are not required to match (a city would never match "France"), the unwanted places still apply.
Its terms: keep `applicationLink` unchanged (it is the posting's address here) and show a link to
Himalayas ("via Himalayas")."""

from datetime import datetime, timedelta
from typing import Any

from app.jobs.feeds import (
    Criteria,
    FeedContext,
    FeedError,
    Fetched,
    Finding,
    epoch,
    https_url,
    line,
    plain_text,
)
from app.jobs.models import ContractType

API = "https://himalayas.app/jobs/api/search"
BUDGET = 6
PAGE_SIZE = 20
PAGES_PER_QUERY = 2
HOSTS = ("himalayas.app",)
PLACES_SHOWN = 8

CONTRACTS = {
    "contractor": ContractType.Freelance,
    "temporary": ContractType.CDD,
    "intern": ContractType.Stage,
    "internship": ContractType.Stage,
}


def finding_of(item: Any, now: datetime) -> Finding | None:
    if not isinstance(item, dict):
        return None
    title = line(item.get("title"))
    url = https_url(item.get("applicationLink"), HOSTS)
    external = line(item.get("guid"), 500) or (url or "")
    if not title or url is None or not external:
        return None
    places = [line(p, 80) for p in item.get("locationRestrictions") or [] if isinstance(p, str)]
    places = [p for p in places if p]
    shown = places[:PLACES_SHOWN]
    location = ", ".join(["Remote", *shown])
    if len(places) > len(shown):
        location += f" and {len(places) - len(shown)} more"
    employment = item.get("employmentType")
    contract = CONTRACTS.get(employment.casefold()) if isinstance(employment, str) else None
    return Finding(
        source="himalayas",
        external_id=external,
        title=title,
        company=line(item.get("companyName")) or None,
        location=location,
        url=url,
        text=plain_text(item.get("description")) or plain_text(item.get("excerpt")),
        posted_at=epoch(item.get("pubDate")),
        remote=True,
        contract=contract,
        read_at=now,
    )


class Himalayas:
    name = "himalayas"
    host = "himalayas.app"
    budget = BUDGET
    needs_key = False
    place_filter = False

    async def fetch(self, ctx: FeedContext, criteria: Criteria) -> Fetched:
        cutoff = ctx.now - timedelta(days=criteria.days)
        fetched = Fetched(queries_left_out=criteria.queries_left_out)
        for query in criteria.queries:
            if ctx.http.left <= 0 or fetched.error:
                fetched.queries_left_out += 1
                continue
            fetched.searched.append(query)
            for page in range(1, PAGES_PER_QUERY + 1):
                if ctx.http.left <= 0:
                    break
                params = {"q": query, "country": "FR", "sort": "recent", "page": page}
                try:
                    data = await ctx.http.get_json(API, params)
                except FeedError as error:
                    fetched.error = error.code
                    break
                items = data.get("jobs") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    fetched.error = "invalid_response"
                    break
                old = False
                for item in items:
                    found = finding_of(item, ctx.now)
                    if found is None:
                        continue
                    if found.posted_at is not None and found.posted_at < cutoff:
                        old = True
                        continue
                    fetched.findings.append(found)
                if old or len(items) < PAGE_SIZE:
                    break
        fetched.requests = ctx.http.requests
        return fetched
