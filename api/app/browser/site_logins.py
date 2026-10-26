"""The job websites a person signed in to (browser B17): one row per profile and host.

The person signs in through a website's Sign in action (B7's sign in session, the person at the
keyboard, no debugging port) and clicks Done: the api ends that session, asks browserd whether the
profile now holds an unexpired cookie of that host (`client.site_session`: the cookie store's host
and expiry columns only, read by the profile's uid, never a name nor a value), and records the
login only then. Job website discovery reads a website signed in when its host has a row that has
not expired (`signed_in`), in that person's own profile; a search that meets the site's sign in
page sets `expired_at` (`expire`), so the card asks to sign in again and the next search reads the
site anonymously. Rows belong to the profile row: "Forget all logins" (B7) deletes them with it.

Every function takes the person's user id and reaches the row through that person's profile, so
nobody reads or changes another person's logins. The functions are synchronous (the app's
sessions are); `SiteLogins` runs them in a thread for the sweep.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Session, sessionmaker

from app.browser.address import check_address
from app.browser.models import BrowserProfile, BrowserSiteLogin

State = Literal["signed_in", "expired"]


@dataclass(frozen=True)
class SiteLogin:
    host: str
    state: State
    signed_in_at: datetime


def host_of(address: Any) -> str | None:
    """The lower case host of an http or https address the sign in rule accepts, else None."""
    checked = check_address(address) if isinstance(address, str) else None
    if checked is None:
        return None
    try:
        return urlsplit(checked).hostname
    except ValueError:
        return None


def _profile(user_id: uuid.UUID) -> Any:
    return sa.select(BrowserProfile.profile_id).where(BrowserProfile.user_id == user_id)


def record(sessions: sessionmaker[Session], user_id: uuid.UUID, host: str, at: datetime) -> bool:
    """The website's login saved now in the person's profile; False when they have none."""
    with sessions() as db:
        profile_id = db.execute(_profile(user_id)).scalar_one_or_none()
        if profile_id is None:
            return False
        db.execute(
            pg.insert(BrowserSiteLogin)
            .values(profile_id=profile_id, host=host, signed_in_at=at, expired_at=None)
            .on_conflict_do_update(
                index_elements=["profile_id", "host"],
                set_={"signed_in_at": at, "expired_at": None},
            )
        )
        db.commit()
    return True


def expire(sessions: sessionmaker[Session], user_id: uuid.UUID, host: str, at: datetime) -> None:
    """A search met the website's sign in page: its login is no longer there."""
    with sessions() as db:
        db.execute(
            sa.update(BrowserSiteLogin)
            .where(
                BrowserSiteLogin.profile_id.in_(_profile(user_id).scalar_subquery()),
                BrowserSiteLogin.host == host,
                BrowserSiteLogin.expired_at.is_(None),
            )
            .values(expired_at=at)
        )
        db.commit()


def signed_in(sessions: sessionmaker[Session], user_id: uuid.UUID, host: str) -> bool:
    with sessions() as db:
        found = db.execute(
            sa.select(BrowserSiteLogin.host).where(
                BrowserSiteLogin.profile_id.in_(_profile(user_id).scalar_subquery()),
                BrowserSiteLogin.host == host,
                BrowserSiteLogin.expired_at.is_(None),
            )
        ).first()
    return found is not None


def listing(sessions: sessionmaker[Session], user_id: uuid.UUID) -> list[SiteLogin]:
    with sessions() as db:
        rows = db.execute(
            sa.select(
                BrowserSiteLogin.host, BrowserSiteLogin.signed_in_at, BrowserSiteLogin.expired_at
            )
            .where(BrowserSiteLogin.profile_id.in_(_profile(user_id).scalar_subquery()))
            .order_by(BrowserSiteLogin.host)
        ).all()
    return [
        SiteLogin(r.host, "expired" if r.expired_at else "signed_in", r.signed_in_at) for r in rows
    ]


class SiteLogins:
    """The sweep's view (app/browser/sources/interactive.py): code passes the user id."""

    def __init__(self, sessions: sessionmaker[Session], clock: Any = None) -> None:
        self.sessions = sessions
        self.clock = clock or (lambda: datetime.now(UTC))

    async def signed_in(self, user_id: str, host: str) -> bool:
        return await asyncio.to_thread(signed_in, self.sessions, uuid.UUID(user_id), host)

    async def expire(self, user_id: str, host: str) -> None:
        await asyncio.to_thread(expire, self.sessions, uuid.UUID(user_id), host, self.clock())
