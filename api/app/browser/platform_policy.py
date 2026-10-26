"""The search's permission check of the job websites read in the person's browser (browser B14).

`PlatformPolicy` is the `InteractivePolicy` of app/browser/sources/interactive.py: code builds
it in `sources.runner_for`, and the model never sees or supplies any of its inputs. For each
selected website it reads, from the database and in a session of its own:

- check, before the lease and again after the page pacing, before every automatic browser call:
  the person's current consent for the platform (LinkedIn: the `linkedin` entry of
  `browser_sources`, linkedin_guard; Free-Work: its `browser_sources` entry; Indeed and Welcome
  to the Jungle: `search.platform_consents`, opportunities/platforms/guard.py), at the version
  of the risk sentence now shown, the 7 day pause of a checkpoint, and one sweep a day. A
  platform without consent ends `permission_unavailable`; consent removed after the first page
  ends `permission_revoked`; a pause ends `permission_checkpoint` (LinkedIn's own:
  `linkedin_paused` reads the same); a sweep that already opened it today ends `linkedin_daily`
  or `platform_daily`. The other selected websites go on.
- reserve, once, after the lease and the fresh check, before the first navigation: records
  `opened_at` and `platform` in every entry of the source in the sweep row (guard.reserve, under
  the person's platform lock and the row's lock) and commits, so a busy browser never spends
  the day's sweep and a kill after the first page keeps it.
- observe, on each page code read (the snapshot's address and text, bounded by the caller):
  a checkpoint or block page records `checkpoint_at` (LinkedIn: also its `linkedin_checkpoints`
  row) and a sign in page records its code, both committed before the page reaches the model.

Caps that are tighter than the interactive source's own win, per platform and per sweep, so two
addresses of the same platform share them: LinkedIn reads at most RESULT_PAGES pages a sweep,
paced 10 to 30 seconds (sources/linkedin.py, SPEC-browser decision 2); the other platforms
share the finder's PAGES_MAX. A page is counted when code reads a new address on it.

The platform is classified from the address by code (`platform_of`), whatever label the
person gave it: any host label equal to a platform's name counts, so `indeed.fr`, `fr.indeed.com`
or `linkedin.com.example.test` all need that platform's consent. When unsure, the address is the
platform's (fail closed). A write that fails ends the source (`permission_unavailable`): no page
opens without its durable record.

Declared open points: a page is counted per new address read, so a search that changes no
address counts once; the wall headings are an allow list of known wording (English and French),
a block page worded otherwise is not recognised by its text (its address may still be).
"""

from __future__ import annotations

import asyncio
import logging
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.browser import finder, linkedin_guard
from app.browser.sources import PAGE_PAUSE_S, linkedin, parse_snapshot
from app.opportunities.platforms import guard

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from app.browser.sources.interactive import SourceContext

logger = logging.getLogger("app.browser.platform_policy")

CUSTOM = "custom"
# Host labels that name a platform, whatever the rest of the host (`fr.indeed.com`, `indeed.fr`).
BRANDS = {
    "linkedin": "linkedin",
    "lnkd": "linkedin",
    "indeed": "indeed",
    "free-work": "freework",
    "freework": "freework",
    "welcometothejungle": "welcome_to_the_jungle",
    "wttj": "welcome_to_the_jungle",
}
PLATFORMS = frozenset(BRANDS.values())
PAGES = {"linkedin": linkedin.RESULT_PAGES}  # tighter than finder.PAGES_MAX, which the rest share
PAUSES = {"linkedin": linkedin.PAUSE_S}

CHECKPOINT_SEGMENTS = frozenset(
    {"checkpoint", "captcha", "challenge", "challenges", "challenge-platform", "blocked"}
)
SIGNIN_SEGMENTS = frozenset(
    {
        "login",
        "log-in",
        "signin",
        "sign-in",
        "signup",
        "sign-up",
        "authwall",
        "auth",
        "uas",
        "connexion",
        "se-connecter",
        "inscription",
    }
)
CHECKPOINT_WORDS = (
    *linkedin.CHECKPOINT_WORDS,
    "verify you are human",
    "verify you are a human",
    "are you a robot",
    "are you human",
    "captcha",
    "access denied",
    "just a moment",
    "vérifiez que vous êtes humain",
    "êtes-vous un robot",
    "accès refusé",
    "accès bloqué",
)
SIGNIN_HEADINGS = linkedin.SIGNIN_HEADINGS | {
    "log in",
    "login",
    "sign in to continue",
    "se connecter",
    "identifiez-vous",
    "connectez-vous",
}
SIGNIN = guard.SIGNIN
SWEEPS_KEPT = 64  # sweeps whose page counts this process keeps (one runs per person at a time)


def platform_of(url: str) -> str:
    """The platform of an address by its host alone, `custom` for any other site."""
    raw = finder.canonical(url) or url
    try:
        host = urlsplit(raw).hostname or ""
    except ValueError:
        return CUSTOM
    host = unicodedata.normalize("NFKC", host).casefold().rstrip(".")
    labels = host.split(".")
    for label in labels[:-1] if len(labels) > 1 else labels:
        if label in BRANDS:
            return BRANDS[label]
    return CUSTOM


def platform_for(context: SourceContext) -> str:
    """The context's platform, or the one its address names when it was saved as custom."""
    return context.platform if context.platform in PLATFORMS else platform_of(context.url)


def pause_for(platform: str) -> tuple[float, float]:
    return PAUSES.get(platform, PAGE_PAUSE_S)


def pace_key(platform: str, host: str, base_url: str | None = None) -> str:
    """The pacer's key of a source's pages. Every address of a protected platform shares one,
    whatever its host (`www.linkedin.com`, `fr.linkedin.com`), so two addresses of the platform
    in one sweep keep its pause between them; LinkedIn's is the legacy reader's own
    (sources/linkedin.py), so both readers share LinkedIn's slot. Any other website: its host."""
    if platform == "linkedin":
        return linkedin.READER.host(base_url)
    if platform in PLATFORMS:
        return f"platform:{platform}"
    return host


def test_start(base_url: str | None, platform: str, url: str) -> str:
    """BROWSER_SOURCES_BASE_URL (tests only, refused in production): a protected platform's
    search opens the fixture server's `<base>/<platform>/` instead of the real host, so no
    journey ever reaches a real job site."""
    if not base_url or platform not in PLATFORMS:
        return url
    return finder.canonical(f"{base_url.rstrip('/')}/{platform}/") or url


def wall(platform: str, url: str, snapshot: str, base_url: str | None = None) -> str | None:
    """The code of a checkpoint or sign in page told by its address or its headings."""
    if platform == "linkedin":
        code = linkedin.wall_by_url(url, base_url)
        code = code or linkedin.wall_by_heading(parse_snapshot(snapshot))
        generic = None if code else wall("", url, snapshot)
        if generic is not None:  # the wording of any platform's wall counts on LinkedIn too
            code = (
                "linkedin_checkpoint" if generic == "permission_checkpoint" else "linkedin_signin"
            )
        return code
    try:
        path = urlsplit(url).path
    except ValueError:
        return "permission_checkpoint"
    segments = [linkedin.norm(s) for s in path.split("/") if s]
    if any(s in CHECKPOINT_SEGMENTS for s in segments):
        return "permission_checkpoint"
    if any(s in SIGNIN_SEGMENTS for s in segments):
        return SIGNIN
    headings = [
        linkedin.norm(n.label())
        for n in parse_snapshot(snapshot).walk()
        if n.role == "heading" and not n.hidden
    ]
    if any(word in h for h in headings for word in CHECKPOINT_WORDS):
        return "permission_checkpoint"
    if any(h in SIGNIN_HEADINGS for h in headings):
        return SIGNIN
    return None


def source_code(platform: str, refused: str, reserved: bool) -> str:
    """The sources' code (sources.ERRORS) of a guard's refusal."""
    if refused.endswith("_paused") or refused == "permission_checkpoint":
        return "permission_checkpoint"
    if refused.endswith("_daily"):
        return "linkedin_daily" if platform == "linkedin" else "platform_daily"
    return "permission_revoked" if reserved else "permission_unavailable"


def refusal(
    db: Session, user_id: uuid.UUID, platform: str, sweep_id: uuid.UUID | None, now: datetime
) -> str | None:
    """The guard's refusal of the platform for this sweep now, None when it may open."""
    if platform == "linkedin":
        refused = linkedin_guard.sweep_refusal(db, user_id, sweep_id, now)
        if refused is None and guard.swept_today(db, user_id, platform, now, sweep_id):
            refused = "linkedin_daily"
        return refused
    return guard.sweep_refusal(db, user_id, platform, sweep_id, now)


@dataclass
class _Run:
    """What this process knows of one sweep: the sources reserved, the pages per platform."""

    reserved: set[str] = field(default_factory=set)
    pages: dict[str, int] = field(default_factory=dict)
    last_url: dict[str, str] = field(default_factory=dict)


class PlatformPolicy:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        base_url: str | None = None,
    ) -> None:
        self.sessions, self.clock, self.base_url = sessions, clock, base_url
        self._runs: OrderedDict[str, _Run] = OrderedDict()

    def _run(self, context: SourceContext) -> _Run:
        key = context.sweep_id or ""
        run = self._runs.pop(key, None) or _Run()
        self._runs[key] = run
        while len(self._runs) > SWEEPS_KEPT:
            self._runs.popitem(last=False)
        return run

    async def check(self, context: SourceContext) -> str | None:
        platform = platform_for(context)
        if platform == CUSTOM:
            return None
        if context.sweep_id is None:
            return "permission_unavailable"
        run = self._run(context)
        if run.pages.get(platform, 0) >= PAGES.get(platform, finder.PAGES_MAX):
            return "page_budget"
        reserved = context.source_id in run.reserved
        try:
            refused = await asyncio.to_thread(self._refusal, context, platform)
        except Exception as exc:
            logger.warning("platform check failed: error=%s", type(exc).__name__)
            return "permission_unavailable"
        return None if refused is None else source_code(platform, refused, reserved)

    def _refusal(self, context: SourceContext, platform: str) -> str | None:
        with self.sessions() as db:
            return refusal(
                db,
                uuid.UUID(context.user_id),
                platform,
                uuid.UUID(str(context.sweep_id)),
                self.clock(),
            )

    async def reserve(self, context: SourceContext) -> str | None:
        platform = platform_for(context)
        if platform == CUSTOM:
            return None
        if context.sweep_id is None:
            return "permission_unavailable"
        try:
            refused = await asyncio.to_thread(self._reserve, context, platform)
        except Exception as exc:
            logger.warning("platform reservation failed: error=%s", type(exc).__name__)
            return "permission_unavailable"
        if refused is not None:
            return source_code(platform, refused, False)
        self._run(context).reserved.add(context.source_id)
        return None

    def _reserve(self, context: SourceContext, platform: str) -> str | None:
        user, sweep_id, now = (
            uuid.UUID(context.user_id),
            uuid.UUID(str(context.sweep_id)),
            self.clock(),
        )
        with self.sessions() as db:
            try:
                refused = guard.reserve(
                    db,
                    user,
                    sweep_id,
                    context.source_id,
                    platform,
                    now,
                    lambda locked: refusal(locked, user, platform, sweep_id, now),
                )
                if refused is None:
                    db.commit()
                else:
                    db.rollback()
                return refused
            except BaseException:
                db.rollback()
                raise

    async def observe(self, context: SourceContext, url: str, snapshot: str) -> str | None:
        platform = platform_for(context)
        if platform == CUSTOM:
            return None
        run = self._run(context)
        if run.last_url.get(context.source_id) != url:
            run.last_url[context.source_id] = url
            run.pages[platform] = run.pages.get(platform, 0) + 1
        code = wall(platform, url, snapshot, self.base_url)
        if code is None:
            return None
        try:
            await asyncio.to_thread(self._record, context, platform, code)
        except Exception as exc:
            logger.warning("platform wall record failed: error=%s", type(exc).__name__)
            return "permission_unavailable"
        return code

    def _record(self, context: SourceContext, platform: str, code: str) -> None:
        user, now = uuid.UUID(context.user_id), self.clock()
        with self.sessions() as db:
            try:
                if code == "linkedin_checkpoint":
                    linkedin_guard.record_checkpoint(db, user, now, "sweep")
                if context.sweep_id is not None:
                    guard.record_wall(
                        db,
                        user,
                        uuid.UUID(str(context.sweep_id)),
                        context.source_id,
                        platform,
                        code,
                        now,
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise


def runtime_policy(settings: Any, sessions: Any) -> PlatformPolicy:
    return PlatformPolicy(sessions, base_url=settings.browser_sources_base_url)
