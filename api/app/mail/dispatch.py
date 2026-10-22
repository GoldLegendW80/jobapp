"""API-owned dispatch: workers and model processes never receive the mail signing key."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import sqlalchemy as sa

from app.apply import admin, full_auto
from app.apply import email as apply_email
from app.apply import gate as apply_gate
from app.apply.models import Application, State
from app.mail import gate, log, reconciling
from app.mail.draft_models import MailSend
from app.mail.mcp_client import GmailMcp


def client(app: Any) -> GmailMcp | None:
    given = getattr(app.state, "mail_mcp", None)
    if given is not None:
        return given
    oauth, keyring = app.state.mail_google, app.state.credentials_keyring
    if oauth is None or keyring is None:
        return None
    return GmailMcp(app.state.settings, oauth, keyring)


async def application(app: Any, application_id: uuid.UUID) -> MailSend | None:
    sessions, now = app.state.sessionmaker, app.state.clock()
    with sessions() as db:
        row = db.get(Application, application_id)
        if row is None or row.ats != "email" or row.state not in {State.approved, State.held}:
            return None
        if row.state is State.held:
            until = full_auto.hold_until(db, row)
            if until is None or now < until:
                return None
        auth = gate.authorise_application(db, row, now)
        db.commit()
    if auth is None:
        return None
    settings = app.state.settings
    paused = await admin.paused(await app.state.queue.pool())
    mcp = client(app)

    # A missing connection/configuration is decided and shown by the same gate.
    async def current_switches() -> apply_gate.Switches:
        return apply_gate.Switches(
            enabled=app.state.settings.apply_submit_enabled,
            paused=await admin.paused(await app.state.queue.pool()),
        )

    return await gate.send(
        sessions,
        auth,
        now,
        settings=settings,
        mcp=mcp,
        switches=apply_gate.Switches(enabled=settings.apply_submit_enabled, paused=paused),
        switches_for=current_switches,
        clock=app.state.clock,
    )


async def once(app: Any) -> int:
    sessions = app.state.sessionmaker
    with sessions() as db:
        waiting = list(
            db.scalars(
                sa.select(Application)
                .where(
                    Application.ats == "email",
                    Application.state == State.needs_person,
                    Application.needs.in_(
                        [
                            "not_connected",
                            "no_send_scope",
                            "needs_reconnect",
                            "mail_send_unavailable",
                        ]
                    ),
                )
                .order_by(Application.updated_at, Application.id)
                .limit(5)
            )
        )
        for item in waiting:
            apply_email.recover(
                db, item, app.state.clock(), configured=bool(app.state.settings.mail_send_key)
            )
            item.updated_at = app.state.clock()
        due = list(
            db.scalars(
                sa.select(Application.id)
                .where(
                    Application.ats == "email",
                    sa.or_(
                        Application.state == State.approved,
                        sa.and_(
                            Application.state == State.held,
                            Application.id.in_(full_auto.held_over(app.state.clock())),
                        ),
                    ),
                )
                .order_by(Application.updated_at, Application.id)
                .limit(5)
            )
        )
        db.execute(
            sa.update(Application)
            .where(Application.id.in_(due))
            .values(updated_at=app.state.clock())
        )
        unknown_users = list(
            db.scalars(
                sa.select(MailSend.user_id)
                .where(MailSend.result.in_(["pending", "unknown"]))
                .where(MailSend.resolved_by.is_(None))
                .group_by(MailSend.user_id)
                .order_by(sa.func.min(MailSend.updated_at), MailSend.user_id)
                .limit(5)
            )
        )
        db.commit()
    for wanted in due:
        await application(app, wanted)
    mcp = client(app)
    if mcp:
        for user_id in unknown_users:
            await reconciling.reconcile(sessions, user_id, mcp, app.state.clock())
    return len(due)


@contextlib.asynccontextmanager
async def lifespan(app: Any) -> AsyncIterator[None]:
    async def loop() -> None:
        while True:
            try:
                await once(app)
            except Exception:
                log.event("mail.dispatch.failed", code="internal_error")
            await asyncio.sleep(15)

    task = asyncio.create_task(loop(), name="api-mail-dispatch")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
