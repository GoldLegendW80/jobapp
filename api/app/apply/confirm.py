"""Review first with the apply agent (apply A20; SPEC-apply, "Review first with the apply agent").

The owner's words (2026-11-06): "when I'm not in full auto mode, I want to see all the input he
filled in but don't matter of the website or whatever, then a button to confirm." So the Apply
button with the review box ticked runs the same apply agent as One click Apply (`applier.py`) on
a lease of purpose `apply_review`, whose acts on a submit element are refused without the
capability (a wizard's Next aside, browserd's `detect.review_step`); the agent's session never
holds that capability. On the last step the agent names the control that sends the form
(`apply_ready`) instead of clicking it; the run then waits here, `ready`, the lease held:

- the list the person reads is the agent's own acts as the tools recorded them (the label its
  snapshot gave the control, the value it typed, chose, ticked or uploaded, the step), never a
  site specific reader, so it works on any website; it is stored as the answer row FIELDS_KEY
  with its hash (`answers_hash`);
- the person's Confirm (`confirm`, `POST /api/applications/{id}/confirm`) carries the hash of the
  list the page showed (409 `stale` on a difference), is refused once the held session stopped
  beating (`session_lost`, the page offers Try again: a new fill and a new list), writes a
  `person_approval` authorisation bound to that hash, and moves `ready` to `approved`;
- the waiting job (`hold`) sees it, reads the emergency stop again, reads the page again and
  refuses (`form_changed`, nothing sent) when it differs from the one the list was read from
  (refs aside), commits `submitting`, and the gate (this code, never a model) clicks the named
  control with the lease's capability; the agent then reads the page only (phase `confirm`) and
  reports the confirmation as One click Apply's recording does (`submitted`, else
  `submit_unknown`, never sent again);
- Cancel (`gate.cancel`) ends the wait, nothing sent; the wait's bound (READY_WAIT_S), a lost
  lease or a crash end it `failed` with `session_lost`, nothing sent.

Nothing of a value goes into a log or an event: they carry ids, codes and counts.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import states
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
from app.browser.client import BrowserError

if TYPE_CHECKING:
    from app.apply.applier import ApplySession

EVENT = "agent_review"  # written at the click on Apply with the box ticked (service.start)
FIELDS_KEY = "agent:filled"
READY_WAIT_S = 1800.0  # the held session waits this long for the person's Confirm
POLL_S = 2.0
KEEP_S = 60.0  # a call on the lease this often keeps browserd's idle timer (300 s) away
BEAT_S = 180  # a held session that wrote no beat for this long is gone (Confirm refused)
_REF = re.compile(r"\s*\[ref=e[0-9]+\]")


def review_agent(db: Session, application: Application) -> bool:
    """A Review first application of the Apply button with the box ticked (A20)."""
    if application.mode is not Mode.review or application.dry_run or application.ats == "email":
        return False
    return (
        db.scalar(
            sa.select(sa.func.count(ApplicationEvent.id)).where(
                ApplicationEvent.application_id == application.id,
                ApplicationEvent.user_id == application.user_id,
                ApplicationEvent.kind == EVENT,
            )
        )
        or 0
    ) > 0


def bare(page: str) -> str:
    """A snapshot without its refs: what the person saw, compared again before the click."""
    return _REF.sub("", page)


def fields_hash(fields: list[dict[str, Any]], submit: str, versions: dict[str, Any]) -> bytes:
    body = {"fields": fields, "submit": submit, "documents": versions}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).digest()


def fields_of(db: Session, application: Application) -> dict[str, Any] | None:
    row = db.get(ApplicationAnswer, (application.id, FIELDS_KEY))
    if row is None or row.user_id != application.user_id or not isinstance(row.value, dict):
        return None
    return row.value


def write_ready(db: Session, application: Application, session: ApplySession, now: datetime):
    """`filling` to `ready` with the list of what the agent filled (the caller commits)."""
    from app.apply import run

    ready = session.ready or {}
    fields = [
        {k: e[k] for k in ("step", "label", "kind", "value") if k in e}
        | ({"chosen": e["chosen"]} if e.get("chosen") else {})
        for e in session.filled
    ]
    submit = str(ready.get("label") or "Submit")
    row = db.get(ApplicationAnswer, (application.id, FIELDS_KEY))
    if row is None:
        row = ApplicationAnswer(
            application_id=application.id,
            user_id=application.user_id,
            field_key=FIELDS_KEY,
            label="What the assistant filled",
            kind="agent_fields",
            source=AnswerSource.agent,
        )
        db.add(row)
    row.value = {"fields": fields, "submit": submit}
    application.answers_hash = fields_hash(fields, submit, application.document_versions)
    states.move(application, State.ready, now)
    application.needs = None
    run._event(db, application, "agent_ready", now, fields=len(fields))


def confirm(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID, sent: bytes, now: datetime
) -> Application:
    """The person's Confirm and send (the caller commits). Raises gate.NotFoundError, or
    gate.RefusedError: `not_ready`, `stale` (the list changed since the page read it) or
    `session_lost` (the held session is gone: the application is `failed`, committed by the
    caller, and the page offers Try again)."""
    from app.apply import gate

    application, _ = gate.lock(db, user_id, application_id)
    if application.state is not State.ready or not review_agent(db, application):
        raise gate.RefusedError("not_ready")
    if application.answers_hash is None or sent != application.answers_hash:
        raise gate.RefusedError("stale")
    if (now - application.updated_at).total_seconds() > BEAT_S:
        lost(db, application, now)
        raise gate.RefusedError("session_lost")
    authorisation = Authorisation(
        id=uuid.uuid4(),
        user_id=user_id,
        application_id=application.id,
        kind=AuthorisationKind.person_approval,
        answers_hash=application.answers_hash,
        created_at=now,
        expires_at=now + timedelta(seconds=READY_WAIT_S),
    )
    db.add(authorisation)
    db.flush()
    states.move(application, State.approved, now, by_gate=True)
    gate._event(db, application, "person_confirmed", now, authorisation_id=str(authorisation.id))
    return application


def lost(db: Session, application: Application, now: datetime) -> None:
    """The held session is gone before the click: `failed`, nothing sent."""
    from app.apply import run

    if application.state in (State.ready, State.approved):
        run._end(db, application, State.failed, "session_lost", now)


# --- the waiting job ---------------------------------------------------------------------------


Db = Callable[[Callable[[Session], Any]], Awaitable[Any]]


def _state(db: Session, application_id: uuid.UUID) -> State | None:
    return db.scalar(sa.select(Application.state).where(Application.id == application_id))


def _beat(db: Session, application_id: uuid.UUID, now: datetime) -> None:
    from app.apply import run

    found = run._locked(db, application_id)
    if found is not None and found[0].state is State.ready:
        found[0].updated_at = now


def _end_lost(db: Session, application_id: uuid.UUID, now: datetime) -> str:
    from app.apply import run

    found = run._locked(db, application_id)
    if found is None:
        return "gone"
    lost(db, found[0], now)
    return found[0].state.value


def _arm(db: Session, application_id: uuid.UUID, now: datetime, code: str | None) -> str:
    """Under the lock, just before the click: `submitting` with the authorisation used, or the
    end the emergency stop gives (`blocked`), or what the person did meanwhile."""
    from app.apply import run

    found = run._locked(db, application_id)
    if found is None:
        return "gone"
    application = found[0]
    if application.state is not State.approved:
        return application.state.value
    if code is not None:
        run._end(db, application, State.blocked, code, now)
        return "blocked"
    authorisation = db.scalar(
        sa.select(Authorisation)
        .where(
            Authorisation.application_id == application.id,
            Authorisation.user_id == application.user_id,
            Authorisation.kind == AuthorisationKind.person_approval,
            Authorisation.used_at.is_(None),
            Authorisation.answers_hash == application.answers_hash,
            Authorisation.expires_at > now,
        )
        .with_for_update()
    )
    if authorisation is None:
        lost(db, application, now)
        return "failed"
    authorisation.used_at = now
    states.move(application, State.submitting, now)
    run._event(db, application, "gate_submit", now)
    return "submitting"


def _changed(db: Session, application_id: uuid.UUID, now: datetime) -> str:
    from app.apply import run

    found = run._locked(db, application_id)
    if found is not None and found[0].state is State.approved:
        run._end(db, found[0], State.failed, "form_changed", now)
    return "form_changed"


def find_again(page: str, role: str, label: str) -> str | None:
    """The ref of the one control of `page` with this role and name, None when not exactly one."""
    from app.apply.applier import control

    refs = re.findall(r"\[ref=(e[0-9]+)\]", page)
    found = [r for r in dict.fromkeys(refs) if control(page, r)[:2] == (role, label)]
    return found[0] if len(found) == 1 else None


async def hold(
    session: ApplySession,
    application_id: uuid.UUID,
    capability: str | None,
    db: Db,
    clock: Callable[[], datetime],
    stop: Callable[[], Awaitable[str | None]],
) -> str:
    """The held session after `apply_ready`: wait for the person's Confirm, Cancel or the bound,
    keeping the lease alive; on Confirm the gate clicks. Returns the state written (or seen)."""
    from app.browser.tools import gate_click

    tools, ready = session.tools, session.ready or {}
    start = last_keep = session.monotonic()
    while True:
        state = await db(lambda s: _state(s, application_id))
        if state is State.approved:
            break
        if state is not State.ready:
            return state.value if state else "gone"
        now_m = session.monotonic()
        if now_m - start >= READY_WAIT_S:
            return await db(lambda s: _end_lost(s, application_id, clock()))
        if now_m - last_keep >= KEEP_S:
            try:
                kept = await tools.call("browser_wait_for", {"time": 0.1})
            except BrowserError:
                kept = {"ok": False}
            if not kept.get("ok"):
                return await db(lambda s: _end_lost(s, application_id, clock()))
            last_keep = session.monotonic()
            await db(lambda s: _beat(s, application_id, clock()))
        await session.sleep(POLL_S)
    try:
        shot = await tools.call("browser_snapshot", {})
    except BrowserError:
        return await db(lambda s: _end_lost(s, application_id, clock()))
    page = shot.get("snapshot") if shot.get("ok") else None
    if not isinstance(page, str) or bare(page) != bare(str(ready.get("page") or "")):
        return await db(lambda s: _changed(s, application_id, clock()))
    ref = find_again(page, str(ready.get("role")), str(ready.get("label")))
    if ref is None or not capability:
        return await db(lambda s: _changed(s, application_id, clock()))
    code = await stop()
    armed = await db(lambda s: _arm(s, application_id, clock(), code))
    if armed != "submitting":
        return armed
    # From here a send may have happened: every end is `submitted` or `submit_unknown`.
    session.armed, session.phase, session.stopped, session.done = True, "confirm", "", None
    session.read_since_submit.clear()
    try:
        clicked = await gate_click(tools, ref, capability, str(ready.get("label") or ""))
    except BrowserError:
        clicked = {"ok": False}
    session.clicked = bool(clicked.get("ok"))
    return "clicked"
