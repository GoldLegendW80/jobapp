"""LinkedIn's public job search PAGE read in the browser, signed out, by code, when LinkedIn's
public job search API refused (jobs J22, owner 2026-11-18: "Yes, fall back to browser"). SPEC-jobs,
"LinkedIn's public job search", Fallback to the public page.

Evidence: on 2026-11-18 the guest API (linkedin_guest) answered 429 to the Jetson's home address
at its first request, twice, after its 30 minute cooldown, while the same searches through
`https://www.linkedin.com/jobs/search?keywords=<title>&location=<place>&f_TPR=r2592000` in a
normal browser on the owner's Mac listed every card (14 searches, 256 cards).

- When: LinkedIn's public job search ended `linkedin_rate_limited` (429 or 999, a sign in or
  security redirect, its cooldown) or failed (`page_failed`, `layout_changed`) before it searched
  every saved title and place pair (`sweep.linkedin_page_due`): the same sweep reads the page for
  the pairs it did not search (the entry's `not_searched`), after the websites read in the
  browser (sources.Runner.websites). Never after a search that stood in for the browser search
  (jobs J20, `fallback` "guest"), never twice a sweep, and it takes the guest search's one retry
  (B24): no chain, no loop.
- How: one `sweep` lease of the person's browser and a discovery on it (browser B20) whose scope
  never says `signed_in`, so browserd opens a fresh anonymous context: never the person's
  LinkedIn login. Code builds every address (`page_url`, the place as LinkedIn names it,
  linkedin_places) and reads every page with one script (CARDS_SCRIPT, POSTING_SCRIPT, run by
  browser_evaluate): no model, no token. A search reads `start=0`, then 25 and 50 while a page
  gives new cards, in rounds, breadth first as the guest search (the first page of every search,
  then the posting pages of that round's new cards taken in turn from each search, then the next
  pages). Each new card that passes J20's rules (the unwanted titles and places, the never apply
  rules, the saved places, the person's postings) has its `/jobs/view/<id>` page read for its
  text, then is stored through the run's commits as the guest search stores it.
- Pace and stops: LinkedIn's page pace (sources/linkedin.py PAUSE_S, 10 to 30 s after the
  previous page of LinkedIn's host ended, whoever opened it, the shared pacer), the permission
  checks before every call (platform_policy), at most PAGES_MAX pages. A sign in wall, a
  checkpoint or a 429 (999) page ends it at once `linkedin_page_refused`, with what was stored
  kept: no retry, no sign in.
- Summary: LinkedIn's guest entry says `fallback` "page" with the guest search's reason
  (`fallback_after`), its counters now count both, `not_searched` names the pairs neither read,
  and `page` holds the page's own: searches, result pages, cards, posting pages, postings.

BROWSER_SOURCES_BASE_URL (tests only) opens `<base>/linkedin/jobs/...` instead of LinkedIn.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import unicodedata
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode, urlsplit

from app.browser import finder
from app.browser.client import BrowserError
from app.browser.sources import SourceError, linkedin
from app.browser.sources import linkedin_guest as guest
from app.jobs import sweep
from app.jobs.schemas import SEARCHES_LISTED

if TYPE_CHECKING:
    from app.browser.sources import Pages, Runner
    from app.profile.schemas import InteractiveSite, SearchSection

logger = logging.getLogger("app.browser.sources.linkedin_page")

FALLBACK = "page"  # the `fallback` of LinkedIn's entry once this search starts
REFUSED = "linkedin_page_refused"
PAGE_SIZE = 25  # the cards a public search page lists before it scrolls
PAGES_PER_SEARCH = 3  # start=0, 25, 50
PAGES_MAX = 90  # LinkedIn pages one fallback opens, posting pages included (guest REQUESTS_MAX)
CARDS_MAX = 60  # cards read from one page, whatever it holds
CRITERIA_MAX = 8
PAGE_CAP = "page_cap"  # PAGES_MAX reached: the fallback ends, what it read kept
READ_THROUGH = "linkedin_public_page"

# One script per page (browserd's browser_evaluate on a discovery, line.SCRIPT_GUARD_JS): the
# result goes back as JSON text. Plain reads, no click, no request.
CARDS_SCRIPT = r"""() => {
  const text = (el) => (el ? (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 600) : "");
  const cards = [];
  for (const card of Array.from(document.querySelectorAll(".base-search-card")).slice(0, 60)) {
    const owner = card.closest("[data-entity-urn]");
    const link =
      card.querySelector("a.base-card__full-link") || card.closest("a.base-card__full-link");
    const time = card.querySelector("time[datetime]");
    cards.push({
      urn: (owner && owner.getAttribute("data-entity-urn")) || "",
      link: (link && link.href) || "",
      title: text(card.querySelector(".base-search-card__title")),
      company: text(card.querySelector(".base-search-card__subtitle")),
      place: text(card.querySelector(".job-search-card__location")),
      date: (time && time.getAttribute("datetime")) || "",
    });
  }
  return { cards };
}"""
POSTING_SCRIPT = r"""() => {
  const text = (el) => (el ? (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 600) : "");
  const markup = document.querySelector(".show-more-less-html__markup");
  const criteria = [];
  const items = document.querySelectorAll(".description__job-criteria-item");
  for (const item of Array.from(items).slice(0, 8)) {
    criteria.push([
      text(item.querySelector(".description__job-criteria-subheader")),
      text(item.querySelector(".description__job-criteria-text")),
    ]);
  }
  const place = Array.from(document.querySelectorAll(".topcard__flavor--bullet")).find(
    (el) => !el.classList.contains("num-applicants__caption"),
  );
  return {
    description: markup ? (markup.innerText || markup.textContent || "").slice(0, 200000) : "",
    criteria,
    title: text(document.querySelector(".top-card-layout__title")),
    company: text(document.querySelector(".topcard__org-name-link")),
    location: text(place),
  };
}"""


def page_url(base: str, query: guest.Query, days: int, start: int) -> str:
    """The public search page of `query` (the guest search's keywords, place as LinkedIn names
    it, its geoId and contract filter), `start` its first card."""
    params: dict[str, str] = {"keywords": query.keywords, "location": query.location}
    if query.geo_id:
        params["geoId"] = query.geo_id
    params["f_TPR"] = f"r{days * 86400}"
    if query.job_types:
        params["f_JT"] = query.job_types
    if start:
        params["start"] = str(start)
    return f"{base}/jobs/search?{urlencode(params, quote_via=quote)}"


def posting_page(base: str, job_id: str) -> str:
    return f"{base}/jobs/view/{job_id}"


# --- what the scripts read -----------------------------------------------------------------------


def _loads(result: dict[str, Any]) -> dict[str, Any]:
    """The script's JSON text as an object; StopError `layout_changed` for anything else."""
    raw = result.get("result")
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise guest.StopError("layout_changed")
    return value


def _text(value: Any) -> str:
    return guest._clean(value) if isinstance(value, str) else ""


def cards_of(value: dict[str, Any]) -> list[guest.Card]:
    """The cards CARDS_SCRIPT read, at most CARDS_MAX, each once: a card without its LinkedIn id
    (on the card, else in its link) or a title is left out."""
    raw = value.get("cards")
    cards: list[guest.Card] = []
    seen: set[str] = set()
    for item in raw[:CARDS_MAX] if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        job_id = _job_id(item.get("urn"), item.get("link"))
        title = _text(item.get("title"))
        if job_id is None or job_id in seen or not title:
            continue
        seen.add(job_id)
        published = item.get("date") if isinstance(item.get("date"), str) else ""
        cards.append(
            guest.Card(
                job_id,
                title,
                _text(item.get("company")),
                _text(item.get("place")),
                published if guest._valid_date(published) else "",
            )
        )
    return cards


def _job_id(urn: Any, link: Any) -> str | None:
    if isinstance(urn, str):
        found = guest._JOB_ID.match(urn)
        if found:
            return found.group(1)
    if isinstance(link, str) and len(link) <= 2000:
        try:
            found = guest._VIEW.search(urlsplit(link).path)
        except ValueError:
            return None
        if found:
            return found.group(1)
    return None


def posting_of(value: dict[str, Any]) -> guest.Detail | None:
    """The posting POSTING_SCRIPT read, as the guest search's Detail; None with no description."""
    raw = value.get("description")
    description = "".join(
        ch
        for ch in unicodedata.normalize("NFC", raw if isinstance(raw, str) else "")
        if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )[: guest.TEXT_MAX]
    if not description.strip():
        return None
    criteria: dict[str, str] = {}
    items = value.get("criteria")
    for item in items[:CRITERIA_MAX] if isinstance(items, list) else []:
        if isinstance(item, list) and len(item) == 2:
            name, said = _text(item[0]), _text(item[1])
            if name and said:
                criteria.setdefault(name, said)
    return guest.Detail(
        description,
        criteria,
        _text(value.get("title")),
        _text(value.get("company")),
        _text(value.get("location")),
    )


# --- the pages -----------------------------------------------------------------------------------


class _Reader:
    """The fallback's LinkedIn pages on one lease: each paced (Pages), its script run once,
    a refusal or a wall the end of the source (`REFUSED`)."""

    def __init__(self, pages: Pages, plan: Any, start: str, base_url: str | None) -> None:
        self.pages, self.plan, self.base_url = pages, plan, base_url
        self.marker = {"_discovery": "native_get", "_discovery_scope": {"start": start}}
        self.opened = 0

    async def read(self, url: str, script: str) -> dict[str, Any] | None:
        """The page at `url` read by `script`; None for a page gone (404)."""
        from app.browser import platform_policy

        if sweep.abandoned(self.plan):
            raise guest.StopError("store_failed")  # nothing read now could be stored
        if self.opened >= PAGES_MAX:
            raise guest.StopError(PAGE_CAP)
        result: dict[str, Any] = {}

        async def go() -> dict[str, Any]:
            nonlocal result
            result = await self.pages.tools.call("browser_navigate", {"url": url, **self.marker})
            return result

        self.opened += 1
        await self.pages._act(go)
        status = result.get("status")
        here = self.pages.last_url or str(result.get("url") or "")
        if status in guest.STOPPED or linkedin.wall_by_url(here, self.base_url):
            raise guest.StopError(REFUSED)
        if status == 404:
            return None
        if isinstance(status, int) and status != 200:
            raise guest.StopError("page_failed")
        try:
            done = await self.pages.tools.call(
                "browser_evaluate", {"function": script, **self.marker}
            )
        except BrowserError as exc:
            raise SourceError("lease_ended" if exc.code == "ended" else "page_failed") from None
        if not done.get("ok"):
            raise guest.StopError("page_failed")
        value = _loads(done)
        if not _read_anything(value):
            # The page asked for read nothing: a wall told by its headings ends the source.
            wall = platform_policy.wall(guest.KIND, here, self.pages.last_text, self.base_url)
            if wall is not None:
                raise guest.StopError(REFUSED)
        return value


def _read_anything(value: dict[str, Any]) -> bool:
    return bool(value.get("cards")) or bool(value.get("description"))


# --- the fallback --------------------------------------------------------------------------------


async def fall_back(
    runner: Runner,
    plan: Any,
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
    stop: str | None = None,
) -> None:
    """The public page for the pairs LinkedIn's public job search did not search, once a sweep
    (module docstring): for the first ticked LinkedIn address searched through the API whose
    entry `sweep.linkedin_page_due` says, unless the sweep stopped its sources or let go of
    its row."""
    search = getattr(plan, "search", None)
    if search is None or stop is not None or sweep.abandoned(plan):
        return
    commits = getattr(plan, "commits", None)
    if commits is not None and getattr(commits, "unsettled", False):
        return
    if any(entry.get("fallback") == FALLBACK for entry in stats.sources):
        return  # once a sweep
    for site in search.interactive_sites:
        if not (site.enabled and guest.by_api(plan, site)):
            continue
        entry = guest._guest_entry(stats, site)
        if entry is not None and sweep.linkedin_page_due(entry):
            await _one(runner, plan, search, site, entry, stats, progress, deadline)
        return


@dataclasses.dataclass
class _PageRun(guest._Run):
    """The guest search's counters, plus the cards the pages listed."""

    cards: int = 0


async def _one(
    runner: Runner,
    plan: Any,
    search: SearchSection,
    site: InteractiveSite,
    entry: dict[str, Any],
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
) -> None:
    pairs = [
        (one["title"], one.get("place"))
        for one in entry.get("not_searched") or []
        if isinstance(one, dict) and isinstance(one.get("title"), str)
    ]
    before = int(entry.get("postings") or 0)
    entry.update(
        phase="running",
        outcome=None,
        reason=None,
        error=None,
        fallback=FALLBACK,
        fallback_after=entry.get("reason"),
    )
    source_id = entry["source_id"]
    stats.current_board = source_id
    await progress()
    commits = getattr(plan, "commits", None)
    if commits is not None:
        commits.admit(source_id)
    done = _PageRun(pairs=dict.fromkeys(pairs))
    try:
        entry["error"] = await _search(runner, plan, search, entry, stats, progress, deadline, done)
    except Exception:
        logger.exception("linkedin public page search failed")
        entry["error"] = "page_failed"
    if commits is not None and not await commits.settle(source_id):
        entry["error"] = entry["error"] or "store_failed"
    _write(entry, done, plan, before)
    sweep.finish_source(entry)
    sweep.release_board(stats, source_id)
    await progress()


async def _search(
    runner: Runner,
    plan: Any,
    search: SearchSection,
    entry: dict[str, Any],
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    deadline: float,
    done: _PageRun,
) -> str | None:
    from app.browser import platform_policy
    from app.browser.sources import Pages
    from app.browser.sources.interactive import GuardedTools, SourceContext

    base = f"{runner.base_url.rstrip('/')}/linkedin" if runner.base_url else guest.BASE_URL
    host = linkedin.host(runner.base_url)
    start = f"{base}/jobs/search"
    user = str(plan.user_id)
    context = SourceContext(
        user,
        str(plan.sweep_id) if getattr(plan, "sweep_id", None) else None,
        entry["source_id"],
        guest.KIND,
        f"{base}/",
    )
    policy = runner.interactive_policy
    if policy is None or context.sweep_id is None:
        return "permission_unavailable"
    code = await policy.check(context)
    if code is not None:
        return guest.refused(code, False)
    try:
        every = guest.every_query(search)
    except ValueError:
        return "unsupported_criteria"
    queries = [q for q in every if (q.title, q.place) in done.pairs][: guest.SEARCHES_MAX]
    if not queries:
        return None
    got = await runner.leases.acquire(
        user,
        "sweep",
        automation=True,
        hosts=finder.lease_hosts(start),
        source_id=entry["source_id"],
    )
    if got.lease is None or got.token is None:
        await runner._give_back(user, None if got.lease is None else "", entry["source_id"])
        return "browser_busy"
    lease_id = got.lease.lease_id
    try:
        # Signed out: no `logins`, and a discovery scope without `signed_in` (a fresh context).
        pages = Pages(
            GuardedTools(runner.tools_for(user, lease_id, got.token), policy, context, None),
            deadline,
            sleep=runner.sleep,
            rng=runner.rng,
            monotonic=runner.monotonic,
            host=platform_policy.pace_key(guest.KIND, host, runner.base_url),
            pacer=runner.pacer,
            lease_until=math.inf,
            pause=linkedin.PAUSE_S,
            idle_s=got.lease.idle_s,
        )
        reader = _Reader(pages, plan, start, runner.base_url)
        try:
            await _rounds(reader, base, plan, search, queries, entry, stats, progress, done)
        except guest.StopError as stopped:
            return None if stopped.code == PAGE_CAP else stopped.code
        except SourceError as exc:
            return exc.code
    finally:
        await runner.leases.release(user, lease_id, reason="sweep_done")
    return "finding_persistence_pending" if done.found else None


async def _rounds(
    reader: _Reader,
    base: str,
    plan: Any,
    search: SearchSection,
    queries: list[guest.Query],
    entry: dict[str, Any],
    stats: sweep.Stats,
    progress: Callable[[], Awaitable[None]],
    done: _PageRun,
) -> None:
    """The pages in rounds, breadth first, as the guest search's (`guest._rounds`): a search
    goes on to its next page while a page gives new cards."""
    host = urlsplit(base).hostname or linkedin.HOST
    known = guest._known_ids(plan, host)
    seen: set[str] = set()
    wanted = guest._Wanted(search, getattr(plan, "rules", None))
    pending: deque[tuple[guest.Card, guest.Query]] = deque()
    active = list(queries)
    for page in range(PAGES_PER_SEARCH):
        still: list[guest.Query] = []
        for query in active:
            value = await reader.read(
                page_url(base, query, search.days, page * PAGE_SIZE), CARDS_SCRIPT
            )
            done.pages += 1
            done.searches.setdefault((query.title, query.place), None)
            cards = cards_of(value) if value is not None else []
            done.cards += len(cards)
            new = 0
            for card in cards:
                if card.job_id in seen:
                    continue
                seen.add(card.job_id)
                new += 1
                if not wanted.keep(card):
                    done.unwanted += 1
                elif not wanted.where.matches(card.location, card.title):
                    done.other_place += 1
                elif card.job_id in known:
                    guest._duplicate(stats, entry, done)
                else:
                    pending.append((card, query))
            if new:
                still.append(query)
            await progress()
        guest._in_turn(pending)
        await _details(reader, base, plan, search, pending, entry, done)
        await progress()
        active = still
        if not active:
            break


async def _details(
    reader: _Reader,
    base: str,
    plan: Any,
    search: SearchSection,
    pending: deque[tuple[guest.Card, guest.Query]],
    entry: dict[str, Any],
    done: _PageRun,
) -> None:
    """Each pending card's posting page, stored in batches (`guest._details`'s rules)."""
    batch: list[guest.GuestFinding] = []
    saved = set(search.contract_types)
    where = guest.wanted_places(search)
    try:
        while pending:
            card, query = pending.popleft()
            value = await reader.read(posting_page(base, card.job_id), POSTING_SCRIPT)
            detail = posting_of(value) if value is not None else None
            if detail is None:
                continue  # the posting is gone
            done.details += 1
            url = guest.view_url(base, card.job_id)
            location = card.location or detail.location
            if not card.location and not where.matches(location, card.title):
                done.other_place += 1
                continue
            contract = guest.contract_of(card.title, location, url, detail.criteria)
            if query.check_contract and saved and contract.value not in saved:
                done.other_contract += 1
                continue
            found = guest._finding(card, detail, url, location, contract)
            evidence = {**found.evidence, "read_through": READ_THROUGH}
            batch.append(dataclasses.replace(found, evidence=evidence))
            if len(batch) >= finder.VERIFIED_BATCH_MAX:
                await guest._store(plan, entry, batch, done)
                batch = []
    finally:
        if batch:
            await guest._store(plan, entry, batch, done)


def _write(entry: dict[str, Any], done: _PageRun, plan: Any, before: int) -> None:
    """LinkedIn's entry after the page: both searches counted, the pairs neither read named,
    and the page's own counters under `page`."""
    searched = [
        (one.get("title"), one.get("place"))
        for one in entry.get("searched") or []
        if isinstance(one, dict)
    ]
    for pair in done.searches:
        if pair not in searched:
            searched.append(pair)
    entry["searched"] = [{"title": t, "place": p} for t, p in searched[:SEARCHES_LISTED]]
    entry["searches"] = int(entry.get("searches") or 0) + len(done.searches)
    left = [pair for pair in done.pairs if pair not in done.searches]
    entry["not_searched"] = [{"title": t, "place": p} for t, p in left[:SEARCHES_LISTED]]
    for name in ("unwanted", "other_contract", "other_place"):
        count = getattr(done, name)
        if count:
            entry[name] = int(entry.get(name) or 0) + count
    if getattr(plan, "commits", None) is None:
        entry["found"] = entry.get("found", 0) + done.found
    entry["page"] = {
        "searches": len(done.searches),
        "result_pages": done.pages,
        "cards": done.cards,
        "details": done.details,
        "postings": max(0, int(entry.get("postings") or 0) - before),
    }
