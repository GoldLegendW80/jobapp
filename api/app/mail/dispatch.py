"""API-owned dispatch: workers and model processes never receive the mail signing key."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from app.apply import admin, full_auto, submit
from app.apply import email as apply_email
from app.apply import gate as apply_gate
from app.apply.models import Application, State
from app.mail import gate, log, reconciling, retention
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


def _authorise(app: Any, application_id: uuid.UUID, now: datetime) -> gate.Authorisation | None:
    """The send authority of an approved or held over application, in one transaction. A draft
    the approval no longer fits (the posting's address edited after the Approve, so its
    recipient is gone) is a visible stop: `ready` with the code and a sentence in the review
    queue, never an approved item left without a message."""
    with app.state.sessionmaker() as db:
        row = db.get(Application, application_id)
        if row is None or row.ats != "email" or row.state not in {State.approved, State.held}:
            return None
        if row.state is State.held:
            until = full_auto.hold_until(db, row)
            if until is None or now < until:
                return None
        user_id = row.user_id
        try:
            auth = gate.authorise_application(db, row, now)
        except apply_gate.InvalidAnswerError as exc:
            db.rollback()
            application, _ = apply_gate.lock(db, user_id, application_id)
            if application.state in {State.approved, State.held}:
                submit._stop(db, application, submit.Stop(State.ready, exc.code), now)
            db.commit()
            log.event("mail.dispatch.refused", user_id=user_id, code=exc.code)
            return None
        db.commit()
        return auth


async def application(app: Any, application_id: uuid.UUID) -> MailSend | None:
    now = app.state.clock()
    # Database work runs in a thread: the event loop never waits on a lock (gate.send). A
    # stop of the loop waits for the step, so no session outlives the shutdown.
    auth = await gate._blocking(lambda: _authorise(app, application_id, now))
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
        app.state.sessionmaker,
        auth,
        now,
        settings=settings,
        mcp=mcp,
        switches=apply_gate.Switches(enabled=settings.apply_submit_enabled, paused=paused),
        switches_for=current_switches,
        clock=app.state.clock,
    )


def _batch(app: Any) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """Recover what a connection fixed, then pick the due applications and the users with an
    unresolved send, each bounded and rotated by `updated_at`."""
    clock = app.state.clock
    with app.state.sessionmaker() as db:
        retention.prune(db, clock(), keep_days=app.state.settings.mail_sends_keep_days)
        db.commit()
        waiting = list(
            db.scalars(
                sa.select(Application.id)
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
        for wanted in waiting:
            try:
                item = db.get(Application, wanted)
                apply_email.recover(
                    db, item, clock(), configured=bool(app.state.settings.mail_send_key)
                )
                item.updated_at = clock()
                db.commit()
            except Exception:
                db.rollback()
                log.event("mail.dispatch.failed", code="recover")
                db.execute(
                    sa.update(Application)
                    .where(Application.id == wanted)
                    .values(updated_at=clock())
                )
                db.commit()
        due = list(
            db.scalars(
                sa.select(Application.id)
                .where(
                    Application.ats == "email",
                    sa.or_(
                        Application.state == State.approved,
                        sa.and_(
                            Application.state == State.held,
                            Application.id.in_(full_auto.held_over(clock())),
                        ),
                    ),
                )
                .order_by(Application.updated_at, Application.id)
                .limit(5)
            )
        )
        db.execute(sa.update(Application).where(Application.id.in_(due)).values(updated_at=clock()))
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
    return due, unknown_users


async def once(app: Any) -> int:
    """One tick. Each item stands alone: one that fails is logged and the rest of the batch,
    any user's, and every reconciliation still run."""
    due, unknown_users = await gate._blocking(lambda: _batch(app))
    for wanted in due:
        try:
            await application(app, wanted)
        except Exception:
            log.event("mail.dispatch.failed", code="application")
    mcp = client(app)
    if mcp:
        for user_id in unknown_users:
            try:
                await reconciling.reconcile(app.state.sessionmaker, user_id, mcp, app.state.clock())
            except Exception:
                log.event("mail.dispatch.failed", user_id=user_id, code="reconcile")
    return len(due)


TICK_S = 15


@contextlib.asynccontextmanager
async def lifespan(app: Any) -> AsyncIterator[None]:
    """The dispatch loop of the api. The first tick waits one interval: starting the app opens
    no database connection (tests/test_app.py, `test_create_app_opens_no_connection`)."""

    async def loop() -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await once(app)
            except Exception:
                log.event("mail.dispatch.failed", code="internal_error")

    task = asyncio.create_task(loop(), name="api-mail-dispatch")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
