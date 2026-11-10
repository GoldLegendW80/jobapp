"""The applications the old document rule stopped go back to sending by themselves (apply A22,
issue #777; owner, 2026-11-10, asked whether they should: "yes").

Until A21 (2026-11-09) the gate refused a Full auto or one click Apply item whose documents
overflowed their page or carried corrections with `documents_review`, and handed it to Review
first (`full_auto.to_review`), where it waits for the person's Approve. `run`, a step of the apply
tick, finds each such item still waiting (`ready` in Review first, its last decision that
refusal, no earlier `run` written on it) and, under the gate's locks, read again:

- while the authorisation that decision named (the Like's `full_auto_like`, the click's
  `apply_click`) is unspent, not expired, bound to the current answers and documents
  (`gate.current_hash`, `gate.documents_code`), and for a Like while Full auto stays on since
  before it: back to its own mode, `dry_run` as at its start, with a `MOVED` event naming the
  authorisation. The tick's decision job then decides it as any other (the check by model,
  `full_auto.decide` or `direct.decide`, which take that authorisation once, `reusable`), so
  every check of the gate runs again and Full auto's hold applies;
- otherwise it stays waiting for the person's Approve, with a `KEPT` event and the reason as
  its error code, whose sentence the card shows.

Either event marks the item done: a second run finds nothing to move. Hard rule 4 holds: the
Like or the click on Apply is still the one approval, used for at most one click.
"""

import logging
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.apply.models import (
    Application,
    ApplicationEvent,
    Authorisation,
    AuthorisationKind,
    Mode,
    State,
)
from app.jobs import service as jobs_service

logger = logging.getLogger(__name__)

STOPPED = "documents_review"
MOVED = "back_to_auto"
KEPT = "kept_for_approval"
MODE_OF_KIND = {
    AuthorisationKind.full_auto_like: Mode.full_auto,
    AuthorisationKind.apply_click: Mode.direct,
}

SENTENCES = {
    "resend_expired": (
        "The document check that stopped this application is gone, but your Like or your click "
        "on Apply is more than 7 days old, so it waits for your approval."
    ),
    "resend_changed": (
        "The document check that stopped this application is gone, but its answers or your "
        "documents changed since your Like or your click on Apply, so it waits for your approval."
    ),
    "resend_full_auto_off": (
        "The document check that stopped this application is gone, but Full auto was turned off "
        "after this Like, so it waits for your approval."
    ),
    "resend_used": (
        "The document check that stopped this application is gone, but your Like or your click "
        "on Apply was already used, so it waits for your approval."
    ),
}


def _last_decisions() -> sa.Select[tuple[int]]:
    """The id of the last `decision` event of every application waiting in Review first."""
    waiting = sa.select(Application.id).where(
        Application.state == State.ready, Application.mode == Mode.review
    )
    return (
        sa.select(sa.func.max(ApplicationEvent.id))
        .where(ApplicationEvent.kind == "decision", ApplicationEvent.application_id.in_(waiting))
        .group_by(ApplicationEvent.application_id)
    )


def stopped() -> sa.Select[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """(application, person, authorisation) of every item `run` has still to look at."""
    marker = aliased(ApplicationEvent)
    marked = sa.exists().where(
        marker.application_id == Application.id, marker.kind.in_((MOVED, KEPT))
    )
    named = sa.cast(ApplicationEvent.data["authorisation_id"].astext, sa.Uuid)
    return (
        sa.select(Application.id, Application.user_id, Authorisation.id)
        .join(
            ApplicationEvent,
            sa.and_(
                ApplicationEvent.application_id == Application.id,
                ApplicationEvent.user_id == Application.user_id,
            ),
        )
        .join(
            Authorisation,
            sa.and_(
                Authorisation.id == named,
                Authorisation.application_id == Application.id,
                Authorisation.user_id == Application.user_id,
            ),
        )
        .where(
            ApplicationEvent.id.in_(_last_decisions()),
            ApplicationEvent.data["outcome"].astext == "refused",
            ApplicationEvent.data["code"].astext == STOPPED,
            Authorisation.kind.in_(tuple(MODE_OF_KIND)),
            Application.state == State.ready,
            Application.mode == Mode.review,
            ~marked,
        )
        .order_by(Application.updated_at, Application.id)
    )


def _why_not(
    db: Session, application: Application, authorisation: Authorisation, now: datetime
) -> str | None:
    """Why the authorisation can no longer send the item by itself, or None."""
    from app.apply import caps, full_auto, gate  # gate imports this module

    if authorisation.used_at is not None:
        return "resend_used"
    if now >= authorisation.expires_at:
        return "resend_expired"
    if authorisation.answers_hash != gate.current_hash(db, application):
        return "resend_changed"
    if gate.documents_code(db, application) is not None:
        return "resend_changed"
    if authorisation.kind is AuthorisationKind.full_auto_like and not full_auto.is_on(
        caps.settings_of(db, application.user_id), application.created_at
    ):
        return "resend_full_auto_off"
    return None


def _dry_run_at_start(db: Session, application: Application) -> bool:
    """`dry_run` as the application started (a rehearsal stays one); unknown is a rehearsal."""
    found = db.scalar(
        sa.select(ApplicationEvent.data["dry_run"].astext)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.kind == "created",
        )
        .order_by(ApplicationEvent.id)
        .limit(1)
    )
    return found != "false"


def move(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    authorisation_id: uuid.UUID,
    now: datetime,
) -> str | None:
    """One item, in the caller's transaction: `MOVED`, `KEPT`, or None when it no longer waits
    as the old rule left it (the person acted since the list was read)."""
    from app.apply import gate  # gate imports this module

    try:
        application, _ = gate.lock(db, user_id, application_id)
    except gate.NotFoundError:
        return None
    again = db.execute(
        stopped().where(Application.id == application_id, Authorisation.id == authorisation_id)
    ).first()
    if again is None:
        return None
    authorisation = db.execute(
        sa.select(Authorisation).where(Authorisation.id == authorisation_id).with_for_update()
    ).scalar_one()
    if (code := _why_not(db, application, authorisation, now)) is not None:
        application.error_code = code
        application.updated_at = now
        gate._event(db, application, KEPT, now, code=code, authorisation_id=str(authorisation.id))
        return KEPT
    mode = MODE_OF_KIND[authorisation.kind]
    application.mode = mode
    application.dry_run = _dry_run_at_start(db, application)
    application.error_code = None
    application.updated_at = now
    gate._event(
        db, application, MOVED, now, mode=mode.value, authorisation_id=str(authorisation.id)
    )
    return MOVED


def run(sessions: sessionmaker[Session], now: datetime) -> int:
    """Every item the old rule stopped, each in its own transaction; one whose posting is busy is
    left to the next tick, and one that raises is logged and skipped: this never fails the tick.
    Returns how many were moved back or kept."""
    with sessions() as db:
        found = [tuple(row) for row in db.execute(stopped())]
    done = 0
    for application_id, user_id, authorisation_id in found:
        try:
            with sessions() as db:
                try:
                    written = move(db, user_id, application_id, authorisation_id, now)
                    db.commit()
                except jobs_service.JobBusyError:
                    db.rollback()
                    continue
        except Exception as error:
            logger.warning(
                "apply.resend.failed",
                extra={"application_id": str(application_id), "error": type(error).__name__},
            )
            continue
        done += written is not None
        if written is not None:
            logger.info(
                "apply.resend", extra={"application_id": str(application_id), "outcome": written}
            )
    return done


def reusable(
    db: Session, application: Application, kind: AuthorisationKind
) -> Authorisation | None:
    """The authorisation the move back named, while unspent and not yet decided since it: the
    decision of the item's mode takes it instead of writing a new one, once."""
    moved = db.execute(
        sa.select(ApplicationEvent.id, ApplicationEvent.data["authorisation_id"].astext)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.kind == MOVED,
        )
        .order_by(ApplicationEvent.id.desc())
        .limit(1)
    ).first()
    if moved is None:
        return None
    event_id, named = moved
    decided = db.scalar(
        sa.select(ApplicationEvent.id)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.kind == "decision",
            ApplicationEvent.id > event_id,
        )
        .limit(1)
    )
    if decided is not None:
        return None
    return db.execute(
        sa.select(Authorisation).where(
            Authorisation.id == uuid.UUID(named),
            Authorisation.application_id == application.id,
            Authorisation.user_id == application.user_id,
            Authorisation.kind == kind,
            Authorisation.used_at.is_(None),
        )
    ).scalar_one_or_none()
