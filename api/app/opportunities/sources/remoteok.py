"""Remote OK (opportunities O7): the public JSON API https://remoteok.com/api, one request per
sweep.

Built against the API's own legal notice, the `legal` field of the first element of
https://remoteok.com/api, as SPEC-opportunities quotes it on 2026-10-20: the API may be used if
the postings link back to Remote OK with a followed link and name Remote OK as the source, else
access is suspended; the logo is not used. So every posting keeps its address on remoteok.com and
the source `remoteok`, and the card says "via Remote OK" linking to it.

The answer is a list: the first element is that notice (no `id`), each other one a posting with
`id`, `epoch`, `date`, `company`, `position`, `tags`, `description` (HTML), `location`,
`salary_min`, `salary_max` (a yearly USD figure, 0 when not stated), `url`. An element without
a position, a company or an address on remoteok.com is skipped. Every posting is remote (the
board lists only remote work). A posting whose tags say contract or freelance is a freelance
mission; any other has the contract its title says (the jobs module's rule).
"""

from datetime import datetime
from typing import Any

from app.jobs import ats
from app.opportunities import pay as pays
from app.opportunities.sources import (
    FREELANCE,
    NAME_MAX,
    TITLE_MAX,
    FeedClient,
    FeedError,
    Found,
    day_of,
    from_epoch,
    line,
    on_host,
    parse_json,
    plain_text,
    remote_location,
)
from app.profile.schemas import Feed

API = "https://remoteok.com/api"
STUB_PREFIX = "remoteok"
HOSTS = frozenset({"remoteok.com", "www.remoteok.com"})
FREELANCE_TAGS = frozenset(
    {"contract", "contractor", "contracts", "freelance", "freelancer", "freelancing"}
)


async def read(client: FeedClient, feed: Feed) -> Found:
    url, hosts = client.address(API, STUB_PREFIX)
    return parse(parse_json(await client.get(url, hosts)))


def freelance(tags: object) -> bool:
    """Whether the tags say contract or freelance (whole tags, any case)."""
    if not isinstance(tags, list):
        return False
    return any(isinstance(tag, str) and tag.strip().lower() in FREELANCE_TAGS for tag in tags)


def _published(item: dict[str, Any]) -> str:
    found = from_epoch(item.get("epoch"))
    if found:
        return found
    raw = item.get("date")
    if isinstance(raw, str):
        try:
            return day_of(datetime.fromisoformat(raw.strip()))
        except ValueError:
            return ""
    return ""


def parse(data: object) -> Found:
    """The postings of an answer. Raises FeedError `invalid_response` when it is not a list."""
    if not isinstance(data, list):
        raise FeedError("invalid_response")
    found: Found = []
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            continue  # the legal notice, or not a posting
        title = line(item.get("position"), TITLE_MAX)
        company = line(item.get("company"), NAME_MAX)
        url = on_host(item.get("url"), HOSTS)
        if not title or not company or url is None:
            continue
        found.append(
            (
                company,
                ats.Posting(
                    title=title,
                    location=remote_location(line(item.get("location"), NAME_MAX)),
                    url=url,
                    published=_published(item),
                    text=plain_text(item.get("description")),
                    remote=True,
                    pay=pays.stated_pay(
                        item.get("salary_min"), item.get("salary_max"), "USD", "year"
                    ),
                    work_mode="remote",
                    contract_type=FREELANCE if freelance(item.get("tags")) else None,
                ),
            )
        )
    return found
