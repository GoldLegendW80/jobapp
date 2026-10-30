"""Adzuna (https://developer.adzuna.com/docs/search): jobs in France (`/jobs/fr/search`), searched
by keyword, posted within `search.days` (`max_days_old`), 50 a page. It needs the person's own free
key (app id and app key, from developer.adzuna.com, stored encrypted in Account: keys.py); without
one the feed is skipped (`no_key`). Its terms allow a personal job search, ask for few requests on
a free key and a link to Adzuna: a sweep searches the first QUERIES_MAX saved titles, one page each
and a second when the first is full, within BUDGET requests; each posting's address is its Adzuna
page (`redirect_url` without its tracking query, so two sweeps give the same address), shown as
"via Adzuna". Adzuna gives a snippet of the text, not the whole posting.
"""

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.jobs.feeds import (
    Criteria,
    FeedContext,
    FeedError,
    Fetched,
    Finding,
    https_url,
    iso,
    line,
    plain_text,
)
from app.jobs.models import ContractType

API = "https://api.adzuna.com/v1/api/jobs/fr/search/{page}"
BUDGET = 6
PAGE_SIZE = 50
PAGES_PER_QUERY = 2
HOSTS = ("adzuna.fr", "adzuna.com")

CONTRACTS = {"permanent": ContractType.CDI, "contract": ContractType.CDD}


def address_of(item: dict[str, Any]) -> str | None:
    url = https_url(item.get("redirect_url"), HOSTS)
    if url is None:
        return None
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def finding_of(item: Any, now: datetime) -> Finding | None:
    if not isinstance(item, dict):
        return None
    external = item.get("id")
    external = str(external) if isinstance(external, str | int) else ""
    title = line(item.get("title"))
    url = address_of(item)
    if not external or not title or url is None:
        return None
    place = item.get("location") if isinstance(item.get("location"), dict) else {}
    names = [part.strip() for part in line(place.get("display_name")).split(",")]
    area = place.get("area") if isinstance(place.get("area"), list) else []
    names += [line(name, 80) for name in reversed(area) if isinstance(name, str)]
    seen: set[str] = set()
    location = ", ".join(
        n for n in names if n and not (n.casefold() in seen or seen.add(n.casefold()))
    )
    company = item.get("company") if isinstance(item.get("company"), dict) else {}
    contract_type = item.get("contract_type")
    return Finding(
        source="adzuna",
        external_id=external,
        title=title,
        company=line(company.get("display_name")) or None,
        location=location[:300],
        url=url,
        text=plain_text(item.get("description")),
        posted_at=iso(item.get("created")),
        contract=CONTRACTS.get(contract_type) if isinstance(contract_type, str) else None,
        read_at=now,
    )


class Adzuna:
    name = "adzuna"
    host = "api.adzuna.com"
    budget = BUDGET
    needs_key = True
    place_filter = True

    async def fetch(self, ctx: FeedContext, criteria: Criteria) -> Fetched:
        key = ctx.key or {}
        fetched = Fetched(queries_left_out=criteria.queries_left_out)
        for query in criteria.queries:
            if ctx.http.left <= 0 or fetched.error:
                fetched.queries_left_out += 1
                continue
            fetched.searched.append(query)
            for page in range(1, PAGES_PER_QUERY + 1):
                if ctx.http.left <= 0:
                    break
                params = {
                    "app_id": key.get("app_id"),
                    "app_key": key.get("app_key"),
                    "what": query,
                    "max_days_old": criteria.days,
                    "results_per_page": PAGE_SIZE,
                    "sort_by": "date",
                    "content-type": "application/json",
                }
                try:
                    data = await ctx.http.get_json(API.format(page=page), params)
                except FeedError as error:
                    # 401 or 403: Adzuna refused the stored key, not the app.
                    fetched.error = "key_refused" if error.code == "blocked" else error.code
                    break
                items = data.get("results") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    fetched.error = "invalid_response"
                    break
                for item in items:
                    found = finding_of(item, ctx.now)
                    if found is not None:
                        fetched.findings.append(found)
                if len(items) < PAGE_SIZE:
                    break
        fetched.requests = ctx.http.requests
        return fetched
