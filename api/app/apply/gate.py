"""The gate (SPEC-apply, "Guardrails, checked by the gate in every mode where the app clicks"):
the person's approval, and the decision that lets the submit lease (`submit.py`) click.

`answers_hash` binds an approval to what the person was shown: every field of the filled form
(key, label, kind, value, source, required) and the version id of each document filled. The page
shows it with the fields (`approval_view`); the approve route sends it back.

`authorise_approval` (only the approve route calls it, after the session and the CSRF header)
writes a `person_approval` authorisation, valid `APPLY_APPROVAL_TTL_S`, for a `ready`
application whose current hash is the one the person sent (else `StaleError`, a 409 with the
current fields). `decide` then runs, in one transaction under the posting's row lock, the
person's apply lock and the application's row lock, the checks of the spec in order:

1. the kill switches: `APPLY_SUBMIT_ENABLED` and the administrator's pause (`admin.py`);
2. the authorisation: its kind for the mode, owner, application, hash, unused, not expired; and
   the application `ready`;
3. never apply (`guardrails.never_apply`, the sweep's event included), then LinkedIn's guard
   on a form on LinkedIn (`caps.linkedin_refusal`: `linkedin_off`, `linkedin_paused`);
4. duplicates (`guardrails.duplicates`, this application excluded);
5. `caps.check`: the person's pause, company caps, and Full auto's one per company rule and
   safety ceiling (A11; each refusal leaves the application `ready`);
6. documents: `documents.current` returns the versions the hash covers, else `documents_changed`
   (a newer version written since the fill: nothing frozen, nothing refused on content). In Full
   auto, apply's own rule (`documents_need_review`);
7. Full auto only: `full_auto.checks` (A13a: the fit threshold, a supported source, nothing left,
   no captcha, never apply in the posting's text; A13c: the check by model passed on the agent's
   free text, `model_check.code`).

Its audit event (`decision`, with the outcome and the code) is written whatever the outcome, and
the caller commits before any click. A pass spends the authorisation (`used_at`), marks every
field approved, stores the hash on the application and moves it to `approved` (the only path into
`approved`: `states.move(..., by_gate=True)`). A rehearsal (`dry_run`) passes the same checks and
is approved the same way; the submit lease records `rehearsed` before the click. The gate never
freezes a document: the freeze is in the transaction that records the click (`submit.record`).

A refusal by a kill switch, the authorisation, the caps or the documents leaves the application
`ready`; a refusal by never apply or a duplicate ends it `blocked` with the code
(`posting_closed` for a closed posting), as at the start (A2). In Full auto (`full_auto_like`,
written by `full_auto.decide`) a pass moves the item to `held` with the end of its hold, and a
refusal that leaves it `ready` sends it to the review queue (`full_auto.to_review`).

The decision is not the last word: the submit lease runs the checks that can change after it
again under the same locks, when it starts and just before the click (`submit.recheck`: the
approval's expiry, never apply, duplicates, caps, and Full auto's `full_auto.recheck`), so an
approval past its validity, a posting applied or closed since, or the same role clicked since on
another posting is never clicked.
"""

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import caps, full_auto, guardrails, service, states
from app.apply.answer_limits import ANSWER_MAX, LETTER_MAX, letter_issue
from app.apply.ats import base as ats_base
from app.apply.models import (
    AnswerSource,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    Authorisation,
    AuthorisationKind,
    Mode,
    State,
)
from app.documents import freeze as documents
from app.documents import store as documents_store
from app.documents.models import FreezeReason
from app.jobs import service as jobs_service
from app.jobs import sweep
from app.jobs.models import Job, JobStatus

logger = logging.getLogger(__name__)

HASH_HEX = 64

SENTENCES = {
    "unconfirmed_recipient": "Confirm the new email recipient before approving.",
    "email_company_domain": (
        "The recipient is not on a confirmed company domain. Review this application."
    ),
    "not_connected": "Connect Gmail in Account to send this application.",
    "no_send_scope": "Reconnect Gmail in Account to allow sending.",
    "needs_reconnect": "Reconnect Gmail in Account to continue.",
    "mail_send_unavailable": "Email sending is not configured. Ask the administrator to enable it.",
    "submit_disabled": "Submissions are switched off on this server for now. Nothing was sent.",
    "paused": "Submissions are paused by the administrator for now. Nothing was sent.",
    "not_ready": "This application is not waiting for your approval.",
    "stale": (
        "The form or your documents changed since you looked. Check the answers shown now and "
        "approve again."
    ),
    "authorisation_invalid": "This approval does not match this application.",
    "authorisation_used": "This approval was already used.",
    "authorisation_expired": "This approval expired. Check the answers and approve again.",
    "documents_changed": (
        "A newer version of your documents was written after this form was filled, so nothing "
        "was sent. Cancel this application and apply again to send the new version."
    ),
    "documents_review": "Your documents need a look before they are sent.",
    "manual": "You apply yourself to this one: the app does not click Submit.",
    # LinkedIn's guard on a form on LinkedIn (browser B10, caps.linkedin_refusal).
    "linkedin_off": "LinkedIn is off for you: turn it on in Find jobs to apply there.",
    "linkedin_paused": "LinkedIn is paused for a week after a security check. Nothing was sent.",
    "not_cancellable": "This application can no longer be stopped or rejected.",
    "not_unknown": "This application is not waiting for you to confirm it.",
    "not_retryable": "Only an unconfirmed or refused application can be tried again.",
    "busy": "This posting is busy with another change. Try again in a moment.",
    "not_editable": "The answers of this application can no longer be changed.",
    "signin_first": (
        "Sign in to the site first. The form is filled again after that, and its answers can "
        "then be changed."
    ),
    "unknown_field": "This form has no such question to answer.",
    "wrong_type": "This answer does not fit the question.",
    "email_subject": "Use an email subject of at most 998 characters on one line.",
    "email_recipient": "Choose an email recipient from the posting.",
    "email_message": "Review the email message before approving it.",
    "letter_too_long": "Shorten the letter to at most 10000 characters before saving.",
    "letter_control_character": (
        "Remove tabs, carriage returns and other control characters before saving the letter."
    ),
    "not_an_option": "This form does not offer that choice.",
    # Not a refusal: the error code of a `submitted` application whose posting kept its status.
    "status_not_set": (
        "This application was sent, but the posting could not be marked as applied. Mark it "
        "yourself."
    ),
    **guardrails.SENTENCES,
    **caps.SENTENCES,
    **full_auto.SENTENCES,
}
# Refusals that leave the application as it is (`ready`): the person may approve again later.
KEEP = frozenset(
    {
        "submit_disabled",
        "paused",
        "not_ready",
        "stale",
        "authorisation_invalid",
        "authorisation_used",
        "authorisation_expired",
        "documents_changed",
        "documents_review",
        "manual",
    }
).union(caps.KEEP, full_auto.SENTENCES)
# The authorisation each mode needs.
KIND_OF_MODE = {
    Mode.review: AuthorisationKind.person_approval,
    Mode.full_auto: AuthorisationKind.full_auto_like,
}


@dataclass(frozen=True)
class Switches:
    """The kill switches as read by the caller: the setting and the administrator's pause."""

    enabled: bool
    paused: bool


def switch_code(switches: Switches) -> str | None:
    if not switches.enabled:
        return "submit_disabled"
    return "paused" if switches.paused else None


async def _click(tools: Any, ref: str, capability: str, element: str) -> dict[str, Any]:
    """The application gate's privileged browser click, wired only into code's submit lease."""
    from app.browser.tools import gate_click

    return await gate_click(tools, ref, capability, element)


@dataclass(frozen=True)
class Decision:
    passed: bool
    code: str | None
    state: State

    @property
    def sentence(self) -> str | None:
        return SENTENCES.get(self.code) if self.code else None


class NotFoundError(LookupError):
    """No such application for this person (a 404 that says nothing more)."""


class RefusedError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StaleError(RefusedError):
    """The hash sent is not the current one: the current one goes back with the fields."""

    def __init__(self, current: bytes) -> None:
        super().__init__("stale")
        self.current = current


# --- the hash ------------------------------------------------------------------------------------


def answers_hash(rows: list[ApplicationAnswer], document_versions: dict[str, Any]) -> bytes:
    """SHA-256 of the fields and the document version ids, in a canonical form (sorted keys, no
    spaces): the same form and documents give the same hash, any change of a value, a source, a
    field or a version gives another."""
    fields = sorted(
        [
            row.field_key,
            row.label,
            row.kind,
            row.options,
            row.value,
            str(getattr(row.source, "value", row.source)),
            bool(row.required),
            bool(row.document_letter),
        ]
        for row in rows
    )
    versions = {
        kind: (
            [str(record.get("version_id")), record["pdf_sha256"]]
            if "pdf_sha256" in record
            else str(record.get("version_id"))
        )
        for kind, record in sorted((document_versions or {}).items())
        if isinstance(record, dict)
    }
    payload = json.dumps(
        {"fields": fields, "documents": versions},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).digest()


def rows_of(db: Session, application: Application) -> list[ApplicationAnswer]:
    """The form's fields, as shown, hashed, checked and refilled; never a credential's box (a
    row of an older fill of a field that takes typed text, labelled as one: a fill records none).
    A check box, a choice, a file field or a skipped widget so labelled is a row as any other."""
    rows = db.execute(
        sa.select(ApplicationAnswer)
        .where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
        )
        .order_by(ApplicationAnswer.field_key)
    ).scalars()
    return [
        row
        for row in rows
        if row.field_key.startswith("skipped:") or not ats_base.typed_secret(row.kind, row.label)
    ]


def current_hash(db: Session, application: Application) -> bytes:
    return answers_hash(rows_of(db, application), application.document_versions)


def parse_hash(text: str) -> bytes | None:
    if len(text) != HASH_HEX:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


# --- locks and events ----------------------------------------------------------------------------


def _event(db: Session, application: Application, kind: str, at: datetime, /, **data: Any) -> None:
    db.add(
        ApplicationEvent(
            application_id=application.id,
            user_id=application.user_id,
            at=at,
            kind=kind,
            data=data,
        )
    )


def lock(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> tuple[Application, Job]:
    """The application of `user_id` and its posting under the gate's locks, in the order of
    `service.start`: the posting's row, the person's apply lock, the application's row. Raises
    NotFoundError for an application that is not this person's, and JobBusyError past the lock
    timeout."""
    posting_id = db.execute(
        sa.select(Application.posting_id).where(
            Application.id == application_id, Application.user_id == user_id
        )
    ).scalar_one_or_none()
    if posting_id is None:
        raise NotFoundError(str(application_id))
    posting = jobs_service.lock_job(db, user_id, posting_id)
    if posting is None:
        raise NotFoundError(str(application_id))
    service._lock_person(db, user_id)
    application = db.execute(
        sa.select(Application)
        .where(Application.id == application_id, Application.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if application is None:
        raise NotFoundError(str(application_id))
    return application, posting


# --- the approval and the decision ---------------------------------------------------------------


def authorise_approval(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    answers_hash_sent: bytes,
    now: datetime,
    ttl_s: int,
) -> Authorisation:
    """The person's approval of the form they were shown, as an authorisation the gate accepts.
    Raises NotFoundError, RefusedError (`not_ready`, `manual`) or StaleError. The caller commits
    after `decide`."""
    application, _ = lock(db, user_id, application_id)
    if application.mode is Mode.manual:
        raise RefusedError("manual")
    if application.state is not State.ready:
        raise RefusedError("not_ready")
    current = current_hash(db, application)
    if answers_hash_sent != current:
        raise StaleError(current)
    authorisation = Authorisation(
        id=uuid.uuid4(),
        user_id=user_id,
        application_id=application.id,
        kind=AuthorisationKind.person_approval,
        answers_hash=current,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_s),
    )
    db.add(authorisation)
    db.flush()
    _event(
        db,
        application,
        "authorisation",
        now,
        authorisation_id=str(authorisation.id),
        kind=authorisation.kind.value,
        valid_s=ttl_s,
    )
    return authorisation


def _rules(db: Session, user_id: uuid.UUID) -> list[sweep.NeverApplyRule] | None:
    try:
        search = sweep.load_search(db, user_id)
    except sweep.InvalidSearchError:
        return None
    return sweep.never_apply_rules(search) if search is not None else []


def _authorisation_code(
    authorisation: Authorisation, application: Application, current: bytes, now: datetime
) -> str | None:
    if application.mode is Mode.manual:
        return "manual"
    if (
        authorisation.user_id != application.user_id
        or authorisation.application_id != application.id
        or authorisation.kind is not KIND_OF_MODE.get(application.mode)
    ):
        return "authorisation_invalid"
    if authorisation.used_at is not None:
        return "authorisation_used"
    if now >= authorisation.expires_at:
        return "authorisation_expired"
    if application.state is not State.ready:
        return "not_ready"
    if authorisation.answers_hash != current:
        return "stale"
    return None


def _version_ids(versions: dict[str, Any]) -> dict[str, str]:
    return {
        kind: str(record.get("version_id"))
        for kind, record in (versions or {}).items()
        if isinstance(record, dict)
    }


def documents_code(db: Session, application: Application) -> str | None:
    """`documents_changed` when `documents.current` no longer returns the versions filled (and
    covered by the hash), or while an approved version is still rendering (the one filled is
    then an older one), else None. Nothing is refused on content."""
    found = documents.current(db, application.user_id, application.posting_id)
    if found.cv.awaits_render or found.letter.awaits_render:
        return "documents_changed"
    now_ids = {
        kind: str(slot.version.version_id)
        for kind, slot in (("cv", found.cv), ("letter", found.letter))
        if slot.version is not None
    }
    return None if now_ids == _version_ids(application.document_versions) else "documents_changed"


def documents_need_review(versions: dict[str, Any]) -> bool:
    """Full auto's own rule on the documents (SPEC-apply, guardrail 6): a version that overflows
    its page or carries corrections goes to the person. Unknown fit counts as an overflow."""
    for record in (versions or {}).values():
        if not isinstance(record, dict):
            return True
        if record.get("fit") != "one_page" or record.get("corrections"):
            return True
    return False


def decide(
    db: Session,
    user_id: uuid.UUID,
    authorisation_id: uuid.UUID,
    now: datetime,
    *,
    switches: Switches,
) -> Decision:
    """The gate's decision on the application the authorisation names. Raises NotFoundError for
    an authorisation that is not this person's. Writes the `decision` event whatever the
    outcome; the caller commits before any click."""
    subject = db.execute(
        sa.select(Authorisation.application_id).where(
            Authorisation.id == authorisation_id, Authorisation.user_id == user_id
        )
    ).scalar_one_or_none()
    if subject is None:
        raise NotFoundError(str(authorisation_id))
    # The gate's lock order first (posting, person, application), then the authorisation's row.
    application, posting = lock(db, user_id, subject)
    authorisation = db.execute(
        sa.select(Authorisation)
        .where(Authorisation.id == authorisation_id, Authorisation.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()

    def refused(code: str) -> Decision:
        if code not in KEEP:
            to = State.posting_closed if code == "posting_closed" else State.blocked
            states.move(application, to, now)
            application.error_code = code
        elif (
            authorisation.kind is AuthorisationKind.full_auto_like
            and application.mode is Mode.full_auto
            and application.state is State.ready
        ):
            full_auto.to_review(db, application, code, now)
        _event(
            db,
            application,
            "decision",
            now,
            outcome="refused",
            code=code,
            authorisation_id=str(authorisation.id),
        )
        return Decision(False, code, State(application.state))

    if code := switch_code(switches):
        return refused(code)
    if code := _authorisation_code(authorisation, application, current_hash(db, application), now):
        return refused(code)
    code = guardrails.never_apply(
        posting,
        _rules(db, user_id),
        swept=guardrails.swept_never_apply(db, user_id, posting.id),
        mode=full_auto.blocking_mode(application.mode),
    )
    if code is None:
        code = caps.linkedin_refusal(db, application, now)
    if code is not None:
        return refused(code)
    duplicate = guardrails.duplicates(
        db, user_id, posting, application.canonical_key, now, exclude=application.id
    )
    if duplicate is not None:
        return refused(duplicate.code)
    if code := caps.check(db, application, now):
        return refused(code)
    if code := documents_code(db, application):
        return refused(code)
    if application.mode is Mode.full_auto:
        if documents_need_review(application.document_versions):
            return refused("documents_review")
        if code := full_auto.checks(db, application, posting, now):
            return refused(code)

    authorisation.used_at = now
    db.execute(
        sa.update(ApplicationAnswer)
        .where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
        )
        .values(approved=True)
    )
    application.answers_hash = authorisation.answers_hash
    application.error_code = None
    to = State.held if application.mode is Mode.full_auto else State.approved
    held: dict[str, Any] = {}
    if to is State.held:
        values = caps.settings_of(db, user_id)
        held["until"] = full_auto.hold_end(values, application, now).isoformat()
    states.move(application, to, now, by_gate=True)
    _event(
        db,
        application,
        "decision",
        now,
        outcome=to.value,
        code=None,
        authorisation_id=str(authorisation.id),
        dry_run=application.dry_run,
        **held,
    )
    return Decision(True, None, to)


# --- the freeze and the person's other decisions ------------------------------------------------


def freeze_sent(db: Session, application: Application, at: datetime) -> int:
    """Every version sent with this application frozen (documents D14), in the caller's
    transaction. A version that is gone (its document deleted) is skipped in its own savepoint;
    nothing else refuses. Returns how many were frozen."""
    frozen = 0
    for record in (application.document_versions or {}).values():
        if not isinstance(record, dict):
            continue
        try:
            with db.begin_nested():
                documents.freeze(
                    db,
                    application.user_id,
                    uuid.UUID(str(record.get("version_id"))),
                    FreezeReason.apply,
                    at,
                )
            frozen += 1
        except (documents_store.NotFoundError, ValueError):
            logger.warning("apply.freeze_skipped", extra={"application_id": str(application.id)})
    return frozen


# Before the click, and after a refusal by the site: the person may stop it. Never once the app
# clicked (`submitting`, `submitted`, `submit_unknown`) and never after an end.
CANCELLABLE = frozenset(
    {
        State.queued,
        State.filling,
        State.needs_person,
        State.ready,
        State.approved,
        State.held,
        State.refused_by_site,
    }
)
REJECTABLE = frozenset({State.needs_person, State.ready})


def cancel(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime, *, reject: bool
) -> Application:
    """Reject (the form as filled, before an approval) or Cancel (anything before the click).
    A submit lease under way sees the state changed under its lock and does not click."""
    application, _ = lock(db, user_id, application_id)
    if application.state not in (REJECTABLE if reject else CANCELLABLE):
        raise RefusedError("not_cancellable")
    code = "rejected" if reject else "cancelled_by_person"
    states.move(application, State.cancelled, now)
    application.error_code = code
    _event(db, application, "cancelled", now, code=code)
    return application


def confirm_submitted(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime
) -> Application:
    """ "It was submitted" on a `submit_unknown`: the versions frozen (already, at the click; a
    frozen version keeps its first reason and time), the posting to `applied`."""
    if (
        db.scalar(
            sa.select(Application.ats).where(
                Application.id == application_id, Application.user_id == user_id
            )
        )
        == "email"
    ):
        from app.mail.locks import lock_send

        lock_send(db, user_id)
    application, posting = lock(db, user_id, application_id)
    if application.ats == "email":
        from app.mail import reconciling
        from app.mail.draft_models import MailDraft, MailSend

        send_id = db.scalar(
            sa.select(MailSend.id)
            .join(MailDraft, MailDraft.id == MailSend.draft_id)
            .where(
                MailSend.id == application.mail_send_id,
                MailSend.user_id == user_id,
                MailSend.posting_id == application.posting_id,
                MailDraft.user_id == user_id,
                MailDraft.application_id == application.id,
                MailDraft.kind == "application",
            )
        )
        if send_id is None or application.state not in (State.submit_unknown, State.submitted):
            raise RefusedError("not_unknown")
        try:
            reconciling.resolve(db, user_id, send_id, "sent", now)
        except (LookupError, ValueError):
            raise RefusedError("not_unknown") from None
        return application
    return _confirm_submitted_locked(db, application, posting, now)


def _confirm_submitted_locked(
    db: Session, application: Application, posting: Job, now: datetime
) -> Application:
    """Record the confirmation while the caller holds the application and posting locks."""
    if application.state is not State.submit_unknown:
        raise RefusedError("not_unknown")
    freeze_sent(db, application, now)
    if JobStatus(posting.status) not in guardrails.APPLIED_STATUSES:
        jobs_service.set_status(
            db,
            posting,
            JobStatus.applied,
            now,
            via="apply",
            extra={"application_id": str(application.id)},
        )
    states.move(application, State.submitted, now)
    application.error_code = None
    _event(db, application, "person_confirmed", now)
    return application


# The person's own answer to a field (`set_answer`): before the click only, and never a file (the
# documents) or a widget the app cannot fill (`skipped:`).
EDITABLE = frozenset({State.needs_person, State.ready, State.approved})


def answer_max(row: ApplicationAnswer, *, email: bool = False) -> int:
    if email and row.field_key == "email_subject":
        return 998
    return LETTER_MAX if row.document_letter and row.kind in {"text", "textarea"} else ANSWER_MAX


TEXT_KINDS = frozenset({"text", "textarea", "select", "combobox", "single", "date"})
CHOICE_KINDS = frozenset({"select", "combobox", "single"})  # the value is one of the options
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class InvalidAnswerError(ValueError):
    """A 422: `unknown_field` (no such field, a file or a skipped widget), `wrong_type` or
    `not_an_option` (a choice the form does not offer)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


REQUIRED_LEFT = "required_left"  # the `needs` of a fill that left a required field (run._needs)


def _required_left(db: Session, application: Application) -> bool:
    """Whether a required field of the form is still left: what the run left (`left`, no value)
    and no one answered since. Flushes first: the answer just set is in the session only."""
    db.flush()
    found = db.scalar(
        sa.select(ApplicationAnswer.field_key)
        .where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
            ApplicationAnswer.required.is_(True),
            ApplicationAnswer.source == AnswerSource.left,
        )
        .limit(1)
    )
    return found is not None


def _is_date(value: str) -> bool:
    """An ISO calendar date, `YYYY-MM-DD`, and a real one."""
    if not ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def set_answer(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    field_key: str,
    value: str | bool,
    now: datetime,
) -> Application:
    """The person's own value for one field of the form (one left for them, or one they
    override): stored with the source `person`, so the hash changes and any approval no longer
    matches; an `approved` application goes back to `ready` (its approval spent, a new one
    needed); a `needs_person` one that waited only for required fields goes to `ready` once the
    last of them is answered. The submit lease refills and reads back this value as any other;
    a refill by the run keeps it (`run.finish`). A credential's box is no field (`unknown_field`),
    and an application waiting for a sign in takes no answer until the fill after it
    (`signin_first`). Raises NotFoundError, RefusedError (`not_editable`, `signin_first`) or
    InvalidAnswerError. The caller commits. No value goes into the event."""
    application, _ = lock(db, user_id, application_id)
    if application.state not in EDITABLE:
        raise RefusedError("not_editable")
    row = db.execute(
        sa.select(ApplicationAnswer)
        .where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == user_id,
            ApplicationAnswer.field_key == field_key,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        row is None
        or row.field_key.startswith(("skipped:", "designated:"))
        or row.kind in ("file", "designated")
        or ats_base.typed_secret(row.kind, row.label)
    ):
        raise InvalidAnswerError("unknown_field")
    if application.state is State.needs_person and application.needs == "signin":
        raise RefusedError("signin_first")
    if application.ats == "email":
        from app.apply.email import field_issue

        if issue := field_issue(field_key, value):
            raise InvalidAnswerError(issue)
    wanted: type = bool if row.kind in ("checkbox", "yes_no") else str
    if not isinstance(value, wanted) or (isinstance(value, str) and not value.strip()):
        raise InvalidAnswerError("wrong_type")
    if isinstance(value, str) and row.document_letter and (issue := letter_issue(value)):
        raise InvalidAnswerError(issue)
    if isinstance(value, str) and (
        row.kind not in TEXT_KINDS or len(value) > answer_max(row, email=application.ats == "email")
    ):
        raise InvalidAnswerError("wrong_type")
    if isinstance(value, str) and row.kind == "date" and not _is_date(value):
        raise InvalidAnswerError("wrong_type")
    if (
        isinstance(value, str)
        and row.kind in CHOICE_KINDS
        and row.options
        and value not in row.options
    ):
        raise InvalidAnswerError("not_an_option")
    row.value = value
    row.source = AnswerSource.person
    row.approved = False
    if application.state is State.approved:
        states.move(application, State.ready, now)
        application.error_code = "stale"
    elif (
        application.state is State.needs_person
        and application.needs == REQUIRED_LEFT
        and not _required_left(db, application)
    ):
        # The fill stopped only for required fields left to the person (run._needs); the last
        # of them is answered, so the form is complete and waits for the approval like any
        # other. A required file is never answered here and keeps it waiting; a skipped widget
        # is never required (run._answer_rows), so it never held it.
        incomplete = db.scalar(
            sa.select(ApplicationAnswer.field_key).where(
                ApplicationAnswer.application_id == application.id,
                ApplicationAnswer.user_id == user_id,
                ApplicationAnswer.field_key == "designated:incomplete",
            )
        )
        states.move(application, State.queued if incomplete else State.ready, now)
        application.needs = None
    application.updated_at = now
    _event(db, application, "answer_set", now, field_key=field_key)
    return application


def retry(db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime) -> Application:
    """ "Try again" after `submit_unknown` or `refused_by_site`: back to `ready`, where only a new
    approval (a new authorisation through `decide`) leads to a click again. A Full auto item
    becomes a review item in the same transaction: its Like was spent on the first click, so
    only the person's Approve clicks again."""
    if (
        db.scalar(
            sa.select(Application.ats).where(
                Application.id == application_id, Application.user_id == user_id
            )
        )
        == "email"
    ):
        from app.mail.locks import lock_send

        lock_send(db, user_id)
    application, _ = lock(db, user_id, application_id)
    if application.state not in (State.submit_unknown, State.refused_by_site):
        raise RefusedError("not_retryable")
    previous = application.state.value
    if application.ats == "email" and application.mail_send_id is not None:
        from app.mail.draft_models import MailDraft, MailSend

        row = db.get(MailSend, application.mail_send_id)
        if row and row.result == "unknown":
            row.resolved_by = "user_retry"
            row.updated_at = now
        draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == application.id))
        if draft:
            draft.status, draft.updated_at = "draft", now
        application.mail_send_id = None
    states.move(application, State.ready, now)
    application.error_code = None
    _event(db, application, "retry", now, previous=previous)
    full_auto.hand_over(db, application, "authorisation_used", now)
    return application
