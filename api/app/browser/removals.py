"""The drain of `browser_profile_removals` (browser B7): the profile directories to delete from
the browser's volume, queued by B3's trigger whenever a `browser_profiles` row is deleted ("Forget
all logins", an account deleted, any other path).

`drain` is a cron of the worker (app/worker.py, every minute): for each queued directory, oldest
first, it asks browserd to remove it (`DELETE /profiles/{profile_id}` with its uid) and deletes
the queue row only when browserd confirms the directory is gone. A row browserd keeps is skipped,
counted in the run's log line and moved behind the others (its `queued_at` set to now), so a row
that keeps failing never holds back a newer removal: a directory whose lease still runs
(`profile_in_use`) is done after that lease; one that is not owned by that uid
(`profile_uid_mismatch`) is never removed and stays queued; one browserd could not remove
(`not_removed`, or any other answer about it) is asked again. When browserd does not answer, the
rest of the queue waits for the next run. Every removal names one profile id and its uid, never a
path, and browserd removes it as that uid only, so one person's removal can never touch another
person's directory.

`drain_one` is the same for the one directory a "Forget all logins" just queued, so it goes at
once when browserd can do it; the cron covers whatever it could not.
"""

import asyncio
import logging
import uuid
from collections import Counter
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.browser.client import BrowserdClient, BrowserError
from app.browser.models import BrowserProfileRemoval
from app.browser.profiles import ProfileRef

logger = logging.getLogger("app.browser.removals")

BATCH = 20  # removals asked per run; the rest waits for the next minute


def _queued(sessions: sessionmaker[Session], limit: int) -> list[ProfileRef]:
    with sessions() as db:
        rows = db.execute(
            sa.select(BrowserProfileRemoval.profile_id, BrowserProfileRemoval.uid)
            .order_by(BrowserProfileRemoval.queued_at, BrowserProfileRemoval.profile_id)
            .limit(limit)
        ).all()
    return [ProfileRef(profile_id=str(row.profile_id), uid=row.uid) for row in rows]


def _row(profile: ProfileRef) -> sa.ColumnElement[bool]:
    return sa.and_(
        BrowserProfileRemoval.profile_id == uuid.UUID(profile.profile_id),
        BrowserProfileRemoval.uid == profile.uid,
    )


def _done(sessions: sessionmaker[Session], profile: ProfileRef) -> None:
    with sessions() as db:
        db.execute(sa.delete(BrowserProfileRemoval).where(_row(profile)))
        db.commit()


def _later(sessions: sessionmaker[Session], profile: ProfileRef) -> None:
    with sessions() as db:
        db.execute(
            sa.update(BrowserProfileRemoval)
            .where(_row(profile))
            .values(queued_at=sa.func.clock_timestamp())
        )
        db.commit()


async def drain_one(
    sessions: sessionmaker[Session], client: BrowserdClient, profile: ProfileRef
) -> bool:
    """Asks browserd to remove that directory; its queue row goes once browserd confirms. True
    when it is gone. Raises BrowserError when browserd does not answer."""
    outcome = await client.remove_profile(profile.profile_id, profile.uid)
    if outcome != "removed":
        logger.info("browser profile removal kept: %s", outcome)
        return False
    await asyncio.to_thread(_done, sessions, profile)
    return True


async def drain_with(
    sessions: sessionmaker[Session], client: BrowserdClient, limit: int = BATCH
) -> int:
    """One pass over the queue; how many directories were removed."""
    if not client.configured:
        return 0
    removed, kept = 0, Counter[str]()
    for profile in await asyncio.to_thread(_queued, sessions, limit):
        try:
            outcome = await client.remove_profile(profile.profile_id, profile.uid)
        except BrowserError as exc:
            logger.warning("browser profile removals wait: browserd %s", exc.code)
            break
        if outcome == "removed":
            await asyncio.to_thread(_done, sessions, profile)
            removed += 1
        else:
            kept[outcome] += 1
            await asyncio.to_thread(_later, sessions, profile)
    if removed:
        logger.info("browser profile directories removed: %d", removed)
    if kept:
        logger.warning("browser profile removals kept: %s", ", ".join(
            f"{outcome} {count}" for outcome, count in sorted(kept.items())))  # fmt: skip
    return removed


async def drain(ctx: dict[str, Any]) -> int:
    """The worker's cron: the client and the sessions of this worker (tests put a client in
    the context as `browserd_client`)."""
    client: BrowserdClient | None = ctx.get("browserd_client")
    if client is None:
        settings = ctx["settings"]
        secret = settings.browser_control_secret
        client = BrowserdClient(
            settings.browser_url, secret.get_secret_value() if secret is not None else None
        )
    sessions: sessionmaker[Session] = ctx["sessions"]
    return await drain_with(sessions, client)
