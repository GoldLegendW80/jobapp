"""Follow ups of sent applications (SPEC-apply.md, "Follow ups"; followup_service.py).

- GET /api/apply/followups: the person's switch, their day, and while it is on, each followed
  posting with its cadence, the follow ups sent and the open draft, due ones first.
- PUT /api/apply/followups/settings `{enabled}`: the switch, on by default.
- POST /api/apply/followups/{posting_id}/draft: Write a draft, the person's click (D29): 202
  once the follow up agent is queued.
- PUT /api/apply/followups/{posting_id}/draft `{subject, body}`: the person's own text, refused
  as Write a draft is (the switch off, `cold`, `answered`, `done`), as is Gmail below.
- POST /api/apply/followups/{posting_id}/sent: "I sent it", from the person's own mail.
- POST /api/apply/followups/{posting_id}/gmail `{to}`: the draft as a Gmail draft, which the
  person sends with POST /api/mail/drafts/{id}/send, the mail gate's own route and rules.

Every route reads and writes the session's person only; another person's posting is 404. CSRF
on every write by the middleware; every answer is `Cache-Control: no-store` (apply/privacy.py).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

import anyio.from_thread
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session

from app import queue as queues
from app.apply import followup_agent, followups
from app.apply import followup_service as service
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.mail import gate as mail_gate

router = APIRouter(tags=["apply"])
logger = logging.getLogger(__name__)
Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
NO_STORE = {"Cache-Control": "no-store"}

# Each refusal: its status and the sentence the person reads.
SENTENCES: dict[str, tuple[int, str]] = {
    "not_followed": (404, "This posting has no follow up: it is not applied or in interview."),
    "off": (409, "Follow ups are off. Turn them on first."),
    "cold": (409, "Two follow ups got no answer. The app writes no third one."),
    "answered": (409, "The company wrote back. Answer their mail instead."),
    "done": (409, "The thank you note is sent already."),
    "never_apply": (409, "Your rules exclude this role, so the assistant writes nothing for it."),
    "running": (409, "The assistant is writing this draft. Wait for it to finish."),
    "consent": (409, "Confirm what is sent to the writer first, in the CV tab of your profile."),
    "credential": (409, "Add a model credential in your account first."),
    "busy": (409, "Your assistant is busy with other work. Try again when it is done."),
    "queue_down": (503, followup_agent.ERRORS["queue_down"]),
    "no_text": (409, "Write the draft first."),
    "address": (422, "Use one complete email address."),
    "not_connected": (409, "Connect Gmail in Account first."),
    "no_send_scope": (409, mail_gate.SENTENCE["no_send_scope"]),
    "needs_reconnect": (409, mail_gate.SENTENCE["needs_reconnect"]),
}


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class TextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=followups.SUBJECT_MAX, pattern=r"^[^\r\n]*$")
    body: str = Field(min_length=1, max_length=20_000)


class GmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=3, max_length=254)


def _refusal(code: str) -> JSONResponse:
    status, detail = SENTENCES[code]
    return JSONResponse(
        status_code=status, content={"detail": detail, "code": code}, headers=NO_STORE
    )


def _posting(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _draft(found: service.Entry, db: Session) -> dict[str, Any] | None:
    row = found.draft
    if row is None:
        return None
    writing = service._writing(db, row)
    if not (row.body or writing or row.error):
        return None
    return {
        "subject": row.subject,
        "body": row.body,
        "issues": row.issues,
        "written_by": row.written_by,
        "writing": writing,
        "error": followup_agent.ERRORS.get(row.error or "") if row.error else None,
        "mail_draft_id": str(row.mail_draft_id) if row.mail_draft_id else None,
    }


def _item(found: service.Entry, db: Session) -> dict[str, Any]:
    cadence = found.cadence
    return {
        "posting_id": str(found.posting.id),
        "company": found.posting.company,
        "title": found.posting.title,
        "status": found.posting.status.value,
        "applied_on": found.applied_on.isoformat(),
        "applied_from": found.applied_from,
        "kind": cadence.kind,
        "urgency": cadence.urgency,
        "next_on": cadence.next_on.isoformat() if cadence.next_on else None,
        "count": cadence.count,
        "sent": [day.isoformat() for day in found.sent],
        "can_draft": followups.can_draft(cadence),
        "draft": _draft(found, db),
    }


def _now(request: Request) -> datetime:
    return deps.clock(request)


@router.get("/api/apply/followups")
def read(request: Request, user: CurrentUser, db: Db) -> JSONResponse:
    now = _now(request)
    on = service.enabled(db, user.id)
    items = [_item(found, db) for found in service.entries(db, user.id, now)] if on else []
    content = {
        "enabled": on,
        "today": service.today_of(db, user.id, now).isoformat(),
        "items": items,
    }
    db.commit()  # a draft sent with Gmail is recorded as sent (service._settle)
    return JSONResponse(content=content, headers=NO_STORE)


@router.put("/api/apply/followups/settings")
def put_settings(body: SettingsIn, request: Request, user: CurrentUser, db: Db) -> JSONResponse:
    service.set_enabled(db, user.id, body.enabled, _now(request))
    db.commit()
    return JSONResponse(content={"enabled": body.enabled}, headers=NO_STORE)


@router.post("/api/apply/followups/{posting_id}/draft")
def write(posting_id: str, request: Request, user: CurrentUser, db: Db) -> JSONResponse:
    wanted, now = _posting(posting_id), _now(request)
    if wanted is None:
        return _refusal("not_followed")
    try:
        run_id = service.start_draft(db, user.id, wanted, now)
    except service.RefusedError as refused:
        db.rollback()
        return _refusal(refused.code)
    db.commit()
    queue = request.app.state.queue
    try:
        anyio.from_thread.run(
            lambda: queue.enqueue(
                "run_agent",
                str(run_id),
                queue=queues.AGENTS,
                job_id=queues.agent_run_job_id(run_id),
            )
        )
    except queues.QueueUnavailableError:
        logger.warning("apply.followup.run_not_queued", extra={"run_id": str(run_id)})
        service.not_queued(db, user.id, run_id, now)
        db.commit()
        return _refusal("queue_down")
    found = service.entry(db, user.id, wanted, now)
    return JSONResponse(status_code=202, content=_item(found, db), headers=NO_STORE)


@router.put("/api/apply/followups/{posting_id}/draft")
def save(
    posting_id: str, body: TextIn, request: Request, user: CurrentUser, db: Db
) -> JSONResponse:
    wanted, now = _posting(posting_id), _now(request)
    if wanted is None:
        return _refusal("not_followed")
    try:
        service.save_text(db, user.id, wanted, body.subject, body.body, now)
    except service.RefusedError as refused:
        db.rollback()
        return _refusal(refused.code)
    found = service.entry(db, user.id, wanted, now)
    content = _item(found, db)
    db.commit()
    return JSONResponse(content=content, headers=NO_STORE)


@router.post("/api/apply/followups/{posting_id}/sent")
def sent(posting_id: str, request: Request, user: CurrentUser, db: Db) -> JSONResponse:
    wanted, now = _posting(posting_id), _now(request)
    if wanted is None:
        return _refusal("not_followed")
    try:
        found = service.record_sent(db, user.id, wanted, now)
    except service.RefusedError as refused:
        db.rollback()
        return _refusal(refused.code)
    content = _item(found, db)
    db.commit()
    return JSONResponse(content=content, headers=NO_STORE)


@router.post("/api/apply/followups/{posting_id}/gmail")
def gmail(
    posting_id: str, body: GmailIn, request: Request, user: CurrentUser, db: Db
) -> JSONResponse:
    wanted = _posting(posting_id)
    if wanted is None:
        return _refusal("not_followed")
    try:
        draft = service.to_gmail(db, user.id, wanted, body.to, _now(request))
    except service.RefusedError as refused:
        db.rollback()
        return _refusal(refused.code)
    content = {"draft_id": str(draft.id)}
    db.commit()
    return JSONResponse(content=content, headers=NO_STORE)
