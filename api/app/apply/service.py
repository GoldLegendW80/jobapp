"""Starting an application, and reading them (A2; SPEC-apply, "The flow of one application", 1).

`start` runs the refusals before anything is spent, in one transaction under the posting's row
lock (`jobs.service.lock_job`) and then the person's apply lock (a transaction advisory lock), the
order of the gate (SPEC-apply, "Guardrails"), so two starts at once make one row:

1. the posting is the person's (else `PostingNotFoundError`, a 404 that says nothing more);
2. never apply (`guardrails.never_apply`): in Apply myself a warning the page shows, in Review
   first and Full auto a refusal; the same for rules that cannot be read;
3. the form address: none (`no_address`) refuses in every mode;
4. duplicates (`guardrails.duplicates`): every code refuses in every mode, except `same_role` and
   `same_address`, and a passed posting (`passed`), which Apply myself shows as warnings (the
   person decides; the app never clicks there).

Then the `applications` row (`queued`, attempt 0) and its `created` event, which holds the mode,
whether it is a rehearsal and the warning codes: ids and codes only. Full auto's own checks (the
fit threshold, the supported ATS, the hold, the rehearsals, the caps) are the gate's (A6, A11, A13);
the lease of the fill is A5's.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.apply import guardrails
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.jobs import service as jobs_service
from app.jobs import sweep

logger = logging.getLogger(__name__)

LIST_LIMIT_MAX = 200
# What turns into a warning instead of a refusal when the person applies himself.
MANUAL_WARNS = frozenset(
    {"never_apply_role", "rules_unreadable", "same_address", "same_role", "passed"}
)


class PostingNotFoundError(LookupError):
    """No such posting for this person."""


class RefusedError(Exception):
    """A refusal before anything is spent: its code, its sentence, and the application it
    names when there is one (the live one of the posting, the one on the same form)."""

    def __init__(self, refusal: guardrails.Refusal) -> None:
        super().__init__(refusal.code)
        self.refusal = refusal


@dataclass(frozen=True)
class Started:
    application: Application
    warnings: tuple[str, ...]


def _lock_person(db: Session, user_id: uuid.UUID) -> None:
    """The person's apply lock, until the transaction ends. Taken after the posting's row lock
    (whose lock timeout this transaction now has), so a wait past it is a 409, not a hang."""
    key = f"apply:{user_id}"
    try:
        db.execute(sa.select(sa.func.pg_advisory_xact_lock(sa.func.hashtextextended(key, 0))))
    except DBAPIError as exc:
        if jobs_service.is_lock_timeout(exc):
            raise jobs_service.JobBusyError(jobs_service.BUSY) from exc
        raise


def _rules(db: Session, user_id: uuid.UUID) -> list[sweep.NeverApplyRule] | None:
    try:
        search = sweep.load_search(db, user_id)
    except sweep.InvalidSearchError:
        return None
    return sweep.never_apply_rules(search) if search is not None else []


def _event(db: Session, application: Application, kind: str, at: datetime, **data: object) -> None:
    db.add(
        ApplicationEvent(
            application_id=application.id,
            user_id=application.user_id,
            at=at,
            kind=kind,
            data=data,
        )
    )


def _refuse(
    code: str, mode: Mode, warnings: list[str], application_id: uuid.UUID | None = None
) -> None:
    """Raise the refusal, or keep it as a warning in Apply myself when it is one of those."""
    if mode is Mode.manual and code in MANUAL_WARNS:
        warnings.append(code)
        return
    raise RefusedError(guardrails.Refusal(code, application_id))


def start(
    db: Session,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    mode: Mode,
    dry_run: bool,
    *,
    now: datetime,
    stub_base: str | None = None,
) -> Started:
    """Create the application, or raise RefusedError or PostingNotFoundError. Commits on success;
    the caller rolls back on an error. Raises ValueError for a rehearsal in Apply myself."""
    if dry_run and mode is Mode.manual:
        raise ValueError("Apply myself is never a rehearsal")
    posting = jobs_service.lock_job(db, user_id, posting_id)
    if posting is None:
        raise PostingNotFoundError(str(posting_id))
    _lock_person(db, user_id)
    warnings: list[str] = []

    code = guardrails.never_apply(
        posting,
        _rules(db, user_id),
        swept=guardrails.swept_never_apply(db, user_id, posting.id),
        mode=Mode.review if mode is Mode.direct else mode,
    )
    if code is not None:
        _refuse(code, mode, warnings)

    from app.apply.email import addresses, unsure

    form_url = (posting.url or "").strip()
    read_as = guardrails.unstub(form_url, stub_base)
    # An application by email only when the posting's address shows no form of its own
    # (email.addresses): an ATS page never becomes one. An address the text may say not to use
    # (email.unsure) is never one in Apply myself, which keeps its form; in Review first and
    # Full auto it is left for the person to choose or not, and Full auto hands it over.
    recipients = addresses(posting, read_as)
    if recipients and mode is Mode.manual:
        raise RefusedError(guardrails.Refusal("email_manual"))
    if not recipients and mode is not Mode.manual:
        recipients = unsure(posting, read_as)
    email_application = bool(recipients)
    sent_to_review = None
    if email_application and mode is Mode.direct:
        # The app never sends mail by itself (CLAUDE.md rule 3): an e-mail application from the
        # Apply button is Review first, and the job page says why.
        mode, sent_to_review = Mode.review, "email_review"
    key = f"email:{posting.id}" if email_application else guardrails.canonical_key(read_as)
    if key is None:
        raise RefusedError(guardrails.Refusal("no_address"))

    refusal = guardrails.duplicates(db, user_id, posting, key, now)
    if refusal is not None:
        _refuse(refusal.code, mode, warnings, refusal.application_id)
        for code in refusal.also:
            _refuse(code, mode, warnings)

    application = Application(
        id=uuid.uuid4(),
        user_id=user_id,
        posting_id=posting.id,
        mode=mode,
        dry_run=dry_run,
        state=State.queued,
        ats="email" if email_application else guardrails.ats_of(read_as),
        form_url=form_url or f"mailto:{recipients[0]}",
        canonical_key=key,
        created_at=now,
        updated_at=now,
        error_code=sent_to_review,
    )
    db.add(application)
    try:
        db.flush()
    except IntegrityError:
        # Only a writer that skipped the locks can get here; the index still holds.
        db.rollback()
        raise RefusedError(guardrails.Refusal("in_progress")) from None
    _event(
        db,
        application,
        "created",
        now,
        mode=mode.value,
        dry_run=dry_run,
        warnings=warnings,
    )
    if mode is Mode.direct:
        # The person's click on Apply is the approval (owner, 2026-11-01): `created_at` is its
        # time; `direct.decide` writes its `apply_click` authorisation once the fill is done.
        _event(db, application, "apply_click", now)
    elif sent_to_review:
        _event(db, application, "sent_to_review", now, code=sent_to_review)
    db.commit()
    logger.info(
        "apply.started",
        extra={"application_id": str(application.id), "mode": mode.value, "warnings": warnings},
    )
    return Started(application, tuple(warnings))


def list_applications(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID | None, limit: int
) -> list[Application]:
    """The person's applications, newest first."""
    query = sa.select(Application).where(Application.user_id == user_id)
    if posting_id is not None:
        query = query.where(Application.posting_id == posting_id)
    query = query.order_by(Application.created_at.desc(), Application.id).limit(limit)
    return list(db.execute(query).scalars())


def get_application(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID
) -> Application | None:
    return db.execute(
        sa.select(Application).where(
            Application.user_id == user_id, Application.id == application_id
        )
    ).scalar_one_or_none()


def events(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> list[ApplicationEvent]:
    return list(
        db.execute(
            sa.select(ApplicationEvent)
            .where(
                ApplicationEvent.user_id == user_id,
                ApplicationEvent.application_id == application_id,
            )
            .order_by(ApplicationEvent.at, ApplicationEvent.id)
        ).scalars()
    )
