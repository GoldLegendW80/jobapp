"""Full auto (apply A13a; SPEC-apply, "Modes", "Guardrails" 7 and the flow's step 4).

The Like the person gives while Full auto is on is the person's approval (owner, 2026-10-10;
CLAUDE.md rule 4). `likes.on_like` creates the application with the mode `full_auto` at the Like,
in the Like's own transaction, so the Like's time is the application's `created_at`; its `liked`
event holds the settings in force. The first `APPLY_REHEARSALS` Likes (fewer Yes answers than
that) are rehearsals (`dry_run`).

The order, after the documents and the fill (A5), every step through the gate:

1. `decide`, which the submit job runs for a `ready` Full auto item (`submit.due` lists them):
   under the gate's locks, the Like still valid (`LIKE_VALID` after it, and Full auto on since
   before it, else the review queue with `authorisation_expired` or `full_auto_off` and no
   authorisation row), then the `full_auto_like` authorisation written, bound to the hash of the
   filled answers and the documents, expiring 7 days after the Like, and `gate.decide` with it:
   the gate's checks, then `checks` (Full auto's own). A pass spends the row and moves the item
   to `held`, the end of its hold in the `decision` event (`hold_s`, never below
   `APPLY_HOLD_S`; a rehearsal has none).
2. The hold: `submit.due` asks the submit lease only once it ended; Cancel (`gate.cancel`), the
   person's pause and the administrator's pause stop it.
3. The submit lease: `submit.recheck` with Full auto's lines (`recheck`: Full auto still on, the
   Like valid, the documents, `checks` again) beside the kill switches, never apply, duplicates,
   the caps and the pace, then the read back, then the click. A rehearsal ends `rehearsed`
   before it.

Every refusal sends the item to the review queue (`to_review`): `ready` with its code (a stop for
the person keeps `needs_person`), `mode` `review` and `dry_run` false in the same transaction, so
the person's Approve writes a `person_approval` the gate accepts and is clicked for real. Codes
that end an application in every mode (never apply on the company or the title, duplicates) still
end it `blocked`. A never apply term found only in the posting's text sends it to the queue
(`never_apply_text`).

Rehearsals: the Yes (`confirm_rehearsal`) is the one way out of `rehearsed`, to `ready` with
`mode` `review` and `dry_run` false, under the gate's locks, 409 when another live application
took the posting or the form address since; it counts one rehearsal done. The No records itself
and changes nothing: Full auto stays in rehearsal.

Declared open points (each toward the review queue):
- until the check by model (A13c) lands, any free text the agent wrote (an answer of source
  `agent` and kind text or textarea) goes to the queue; the fact check of that text is the
  answer agent's own (`agent.judge` keeps only what passes it), so this rule covers it;
- a submit element the generic form agent designated (#343's hand over on `form_changed`) goes to
  the queue as well (`agent_designated`): no person saw the highlighted screenshot;
- any field left, optional ones and widgets the app cannot fill included, goes to the queue.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.apply import caps, guardrails, service, states
from app.apply.caps_models import ApplySettings, LikeMode
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
from app.jobs.models import Job

FULL_AUTO_SOURCES = frozenset({"ashby", "greenhouse", "lever"})
LIKE_VALID = timedelta(days=7)
FREE_TEXT = frozenset({"text", "textarea"})
DESIGNATED = "designated"

SENTENCES = {
    "below_min_fit": (
        "This posting's fit is below your threshold for Full auto, so it waits for you."
    ),
    "unsupported_source": "Full auto does not apply on this site yet, so it waits for you.",
    "fields_left": "Some questions of this form were left, so it waits for you.",
    "captcha_seen": "A captcha showed up while filling this form, so it waits for you.",
    "agent_text": "The app wrote a free text answer on this form, so you check it first.",
    "agent_designated": (
        "The app chose the submit button of this form itself, so you check it first."
    ),
    "never_apply_text": (
        "The posting's text mentions a term your rules exclude, so it waits for you."
    ),
    "full_auto_off": "Full auto was turned off after this Like, so it waits for you.",
    "not_rehearsal": "This application is not a rehearsal waiting for your answer.",
    "rehearsal_taken": (
        "Another application to this posting or this form started since the rehearsal."
    ),
    "hold_too_short": "The hold cannot be shorter than the server's minimum.",
}


def _setting(name: str) -> Any:
    return caps._setting(name)


# --- the Like ------------------------------------------------------------------------------------


def is_on(values: caps.Values, liked_at: datetime) -> bool:
    """Full auto is on and was already on at the Like."""
    return (
        values.mode_after_like is LikeMode.full_auto
        and values.full_auto_since is not None
        and values.full_auto_since <= liked_at
    )


def mode_of_like(
    db: Session, user_id: uuid.UUID, posting: Job, values: caps.Values, now: datetime
) -> tuple[Mode, str | None]:
    """The mode of the application a Like creates, and why it is not Full auto when Full auto is
    on: a never apply term in the posting's text alone (the company and the title refuse the
    Like itself, in every mode)."""
    if not is_on(values, now):
        return Mode.review, None
    rules = service._rules(db, user_id)
    found = guardrails.never_apply(posting, rules, swept=False, mode=Mode.full_auto)
    return (Mode.review, "never_apply_text") if found else (Mode.full_auto, None)


def is_rehearsal(values: caps.Values) -> bool:
    return values.rehearsals_done < int(_setting("apply_rehearsals"))


def settings_in_force(values: caps.Values) -> dict[str, Any]:
    """What the `liked` event of a Full auto Like records."""
    return {
        "min_fit": values.min_fit,
        "hold_s": hold_s(values),
        "company_days": values.company_days,
        "safety_ceiling": values.safety_ceiling,
        "full_auto_since": values.full_auto_since.isoformat() if values.full_auto_since else None,
        "rehearsals_done": values.rehearsals_done,
    }


def blocking_mode(mode: Mode) -> Mode:
    """The mode `guardrails.never_apply` refuses with: in Full auto the posting's text sends the
    item to the review queue (`checks`), it does not end it."""
    return Mode.review if mode is Mode.full_auto else mode


def hold_s(values: caps.Values) -> int:
    return max(values.hold_s, int(_setting("apply_hold_s")))


# --- the review queue ----------------------------------------------------------------------------


def hand_over(db: Session, application: Application, code: str | None, now: datetime) -> None:
    """A Full auto item becomes a review item (module docstring), in the caller's transaction."""
    if application.mode is not Mode.full_auto:
        return
    application.mode = Mode.review
    application.dry_run = False
    application.updated_at = now
    service._event(db, application, "sent_to_review", now, code=code)


def to_review(db: Session, application: Application, code: str, now: datetime) -> None:
    """`ready` with `code`, then `hand_over`. The caller holds the gate's locks."""
    if application.state is not State.ready:
        states.move(application, State.ready, now)
    application.error_code = code
    hand_over(db, application, code, now)


# --- the checks ----------------------------------------------------------------------------------


def _captcha_seen(db: Session, application: Application) -> bool:
    found = db.scalar(
        sa.select(ApplicationEvent.id)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.data["needs"].astext == "captcha",
        )
        .limit(1)
    )
    return found is not None


def checks(db: Session, application: Application, posting: Job) -> str | None:
    """Full auto's own checks (SPEC-apply, "Guardrails" 6 and 7), the code of the first that
    fails, or None. The documents' rule is the gate's (`gate.documents_need_review`)."""
    if application.ats not in FULL_AUTO_SOURCES:
        return "unsupported_source"
    values = caps.settings_of(db, application.user_id)
    if posting.fit is None or posting.fit < values.min_fit:
        return "below_min_fit"
    rows = db.execute(
        sa.select(ApplicationAnswer.kind, ApplicationAnswer.source).where(
            ApplicationAnswer.application_id == application.id,
            ApplicationAnswer.user_id == application.user_id,
        )
    ).all()
    if any(source is AnswerSource.left for _, source in rows):
        return "fields_left"
    if any(source is AnswerSource.agent and kind in FREE_TEXT for kind, source in rows):
        return "agent_text"
    if any(source is AnswerSource.agent and kind == DESIGNATED for kind, source in rows):
        return "agent_designated"
    if _captcha_seen(db, application):
        return "captcha_seen"
    rules = service._rules(db, application.user_id)
    if guardrails.never_apply(posting, rules, swept=False, mode=Mode.full_auto) is not None:
        return "never_apply_text"
    return None


def recheck(db: Session, application: Application, posting: Job, now: datetime) -> str | None:
    """Before the click of a `held` item, beside the gate's own checks again: Full auto still on
    since before the Like, the documents still the ones filled, and `checks` again."""
    from app.apply import gate  # gate imports this module

    if not is_on(caps.settings_of(db, application.user_id), application.created_at):
        return "full_auto_off"
    return gate.documents_code(db, application) or checks(db, application, posting)


def expired(application: Application, spent: Authorisation, now: datetime) -> bool:
    """Whether the authorisation the gate spent no longer lets the submit lease click. A Review
    first approval that only the pace per host holds back (`needs` is `host_pace`) stays valid
    past its own expiry, at most `LIKE_VALID` after it was given (planner default, 2026-10-19);
    a Full auto Like's row already expires `LIKE_VALID` after the Like."""
    if spent.kind is not gate_kind(application.mode):
        return True
    if now < spent.expires_at:
        return False
    paced = spent.kind is AuthorisationKind.person_approval and application.needs == "host_pace"
    return not (paced and now < spent.created_at + LIKE_VALID)


def gate_kind(mode: Mode) -> AuthorisationKind | None:
    return {
        Mode.review: AuthorisationKind.person_approval,
        Mode.full_auto: AuthorisationKind.full_auto_like,
    }.get(mode)


# --- the decision and the hold -------------------------------------------------------------------


def undecided(db: Session, application_id: uuid.UUID) -> bool:
    """A `ready` Full auto item: the submit job runs its decision first."""
    found = db.execute(
        sa.select(Application.state, Application.mode).where(Application.id == application_id)
    ).one_or_none()
    return found is not None and found[0] is State.ready and found[1] is Mode.full_auto


def hold_end(values: caps.Values, application: Application, now: datetime) -> datetime:
    return now if application.dry_run else now + timedelta(seconds=hold_s(values))


def decide(db: Session, application_id: uuid.UUID, now: datetime, switches: Any) -> str:
    """Step 1 of the module docstring. Returns what was written (a code, no value); the caller
    commits."""
    from app.apply import gate  # gate imports this module

    user_id = db.scalar(sa.select(Application.user_id).where(Application.id == application_id))
    if user_id is None:
        return "gone"
    try:
        application, _ = gate.lock(db, user_id, application_id)
    except gate.NotFoundError:
        return "gone"
    if application.state is not State.ready or application.mode is not Mode.full_auto:
        return "superseded"
    liked_at = application.created_at
    expires = liked_at + LIKE_VALID
    code = None
    if now >= expires:
        code = "authorisation_expired"
    elif not is_on(caps.settings_of(db, user_id), liked_at):
        code = "full_auto_off"
    if code is not None:
        to_review(db, application, code, now)
        gate._event(db, application, "decision", now, outcome="refused", code=code)
        return code
    authorisation = Authorisation(
        id=uuid.uuid4(),
        user_id=user_id,
        application_id=application.id,
        kind=AuthorisationKind.full_auto_like,
        answers_hash=gate.current_hash(db, application),
        created_at=now,
        expires_at=expires,
    )
    db.add(authorisation)
    db.flush()
    gate._event(
        db,
        application,
        "authorisation",
        now,
        authorisation_id=str(authorisation.id),
        kind=authorisation.kind.value,
        liked_at=liked_at.isoformat(),
    )
    decision = gate.decide(db, user_id, authorisation.id, now, switches=switches)
    return decision.state.value if decision.passed else (decision.code or "refused")


def hold_until(db: Session, application: Application) -> datetime | None:
    """The end of the hold the gate's decision wrote, None when there is none."""
    found = db.scalar(
        sa.select(ApplicationEvent.data["until"].astext)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.user_id == application.user_id,
            ApplicationEvent.kind == "decision",
            ApplicationEvent.data["outcome"].astext == State.held.value,
        )
        .order_by(ApplicationEvent.id.desc())
        .limit(1)
    )
    return datetime.fromisoformat(found) if found else None


def held_over(now: datetime | None) -> sa.Select[Any]:
    """The ids of the `held` items whose hold ended at `now` (the database's clock when None)."""
    until = sa.cast(ApplicationEvent.data["until"].astext, sa.DateTime(timezone=True))
    return (
        sa.select(Application.id)
        .join(
            ApplicationEvent,
            sa.and_(
                ApplicationEvent.application_id == Application.id,
                ApplicationEvent.user_id == Application.user_id,
            ),
        )
        .where(
            Application.state == State.held,
            ApplicationEvent.kind == "decision",
            ApplicationEvent.data["outcome"].astext == State.held.value,
            until <= (now if now is not None else sa.func.now()),
        )
    )


# --- the person's switch and rehearsals ----------------------------------------------------------


def turn(db: Session, user_id: uuid.UUID, on: bool, now: datetime) -> list[uuid.UUID]:
    """Full auto on or off, in the caller's transaction; `full_auto_since` is set when it turns
    on, so a Like given before it never counts. Returns the person's `held` items when it turns
    off (`release` sends each to the review queue)."""
    row = db.get(ApplySettings, user_id, with_for_update=True)
    was_on = row is not None and row.mode_after_like is LikeMode.full_auto
    values: dict[str, Any] = {
        "updated_at": now,
        "mode_after_like": LikeMode.full_auto if on else LikeMode.review,
    }
    if on and not was_on:
        values["full_auto_since"] = now
    stmt = insert(ApplySettings).values(user_id=user_id, **values)
    db.execute(stmt.on_conflict_do_update(index_elements=["user_id"], set_=values))
    db.expire_all()
    if on:
        return []
    return list(
        db.execute(
            sa.select(Application.id)
            .where(
                Application.user_id == user_id,
                Application.state == State.held,
                Application.mode == Mode.full_auto,
            )
            .order_by(Application.created_at, Application.id)
        ).scalars()
    )


def release(db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime) -> bool:
    """A `held` item of a Full auto turned off, to the review queue under the gate's locks."""
    from app.apply import gate  # gate imports this module

    application, _ = gate.lock(db, user_id, application_id)
    if application.state is not State.held or application.mode is not Mode.full_auto:
        return False
    to_review(db, application, "full_auto_off", now)
    return True


def confirm_rehearsal(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime
) -> Application:
    """The Yes to "Does this look right?" (module docstring). Raises gate.NotFoundError and
    gate.RefusedError (`not_rehearsal`, `rehearsal_taken`). The caller commits."""
    from app.apply import gate  # gate imports this module

    application, _ = gate.lock(db, user_id, application_id)
    if application.state is not State.rehearsed or application.mode is not Mode.full_auto:
        raise gate.RefusedError("not_rehearsal")
    try:
        with db.begin_nested():
            states.move(application, State.ready, now)
            application.mode = Mode.review
            application.dry_run = False
            application.error_code = None
            db.flush()
    except IntegrityError:
        raise gate.RefusedError("rehearsal_taken") from None
    stmt = insert(ApplySettings).values(user_id=user_id, rehearsals_done=1, updated_at=now)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={"rehearsals_done": ApplySettings.rehearsals_done + 1, "updated_at": now},
        )
    )
    gate._event(db, application, "rehearsal_yes", now)
    return application


def decline_rehearsal(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, now: datetime
) -> Application:
    """The No: recorded, the rehearsal stays `rehearsed` and nothing is counted."""
    from app.apply import gate  # gate imports this module

    application, _ = gate.lock(db, user_id, application_id)
    if application.state is not State.rehearsed or application.mode is not Mode.full_auto:
        raise gate.RefusedError("not_rehearsal")
    gate._event(db, application, "rehearsal_no", now)
    return application
