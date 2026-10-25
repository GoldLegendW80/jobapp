"""Owner-only draft review, the single explicit send click, and unknown-send decisions."""

import asyncio
import uuid
from contextlib import suppress
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.apply import email as apply_email
from app.apply import gate as apply_gate
from app.apply.models import Application, State
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.mail import dispatch, drafts, gate, reconciling
from app.mail.draft_models import MailDraft, MailSend

router = APIRouter(tags=["mail"])
Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_hash: str = Field(min_length=64, max_length=64)
    confirmed_new_recipients: list[Annotated[str, Field(max_length=254)]] = Field(
        default_factory=list, max_length=20
    )
    # IDs contain a fact-check rule code and two offsets into the 100000-character body.
    acknowledged_issues: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list, max_length=1000
    )


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["sent", "send_again"]


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(404, "No such message.") from None


def _draft(db: Session, user: User, wanted: uuid.UUID) -> MailDraft:
    draft = db.scalar(
        sa.select(MailDraft).where(MailDraft.id == wanted, MailDraft.user_id == user.id)
    )
    if draft is None:
        raise HTTPException(404, "No such draft.")
    return draft


def _out(db: Session, draft: MailDraft, now: Any) -> dict[str, Any]:
    send = db.scalar(
        sa.select(MailSend)
        .where(MailSend.user_id == draft.user_id, MailSend.draft_id == draft.id)
        .order_by(MailSend.at.desc())
        .limit(1)
    )
    return {
        "id": str(draft.id),
        "kind": draft.kind,
        "status": draft.status,
        "to": draft.to_addrs,
        "cc": draft.cc_addrs,
        "subject": draft.subject,
        "body": draft.body,
        "version": draft.version,
        "content_hash": drafts.content_hash(draft),
        "recipients": [
            {"address": address, "flags": ["new_address"]}
            for address in [*draft.to_addrs, *draft.cc_addrs]
        ],
        "issues": gate.issues(db, draft, now),
        "send_id": str(send.id) if send else None,
        "send_result": send.result if send else None,
        "error_code": send.refusal_code if send else None,
        "message": gate.SENTENCE.get(send.refusal_code or send.result) if send else None,
    }


@router.get("/api/applications/{application_id}/email")
def application_email(
    application_id: str, request: Request, user: CurrentUser, db: Db
) -> dict[str, Any]:
    application = db.scalar(
        sa.select(Application).where(
            Application.id == _id(application_id),
            Application.user_id == user.id,
            Application.ats == "email",
        )
    )
    if application is None:
        raise HTTPException(404, "No such email application.")
    apply_email.recover(
        db,
        application,
        deps.clock(request),
        configured=bool(request.app.state.settings.mail_send_key),
    )
    gate.expire_pending(db, user.id, deps.clock(request))
    draft = db.scalar(
        sa.select(MailDraft).where(
            MailDraft.application_id == application.id, MailDraft.user_id == user.id
        )
    )
    if draft is None:
        return {
            "id": str(application.id),
            "status": "preparing",
            "to": [],
            "subject": "",
            "body": "",
            "send_id": None,
            "message": None,
            "issues": [],
        }
    if application.state in {State.ready, State.approved, State.held} and draft.status == "draft":
        with suppress(ValueError):
            apply_email.sync_draft(db, application, draft, deps.clock(request))
    result = _out(db, draft, deps.clock(request))
    db.commit()
    return result


@router.get("/api/mail/drafts/{draft_id}")
def read(draft_id: str, request: Request, user: CurrentUser, db: Db) -> dict[str, Any]:
    gate.expire_pending(db, user.id, deps.clock(request))
    result = _out(db, _draft(db, user, _id(draft_id)), deps.clock(request))
    db.commit()
    return result


@router.post("/api/mail/drafts/{draft_id}/send")
async def send(draft_id: str, body: SendIn, request: Request, user: CurrentUser) -> JSONResponse:
    auth = gate.authorise_click(
        user.id,
        _id(draft_id),
        body.content_hash,
        body.confirmed_new_recipients,
        body.acknowledged_issues,
        deps.clock(request),
    )
    mcp = dispatch.client(request.app)
    try:
        row = await gate.send(
            request.app.state.sessionmaker,
            auth,
            deps.clock(request),
            settings=request.app.state.settings,
            mcp=mcp,
            switches=apply_gate.Switches(enabled=True, paused=False),
        )
    except LookupError:
        raise HTTPException(404, "No such draft.") from None
    return JSONResponse(
        status_code=409 if row.result == "refused" else 200,
        content={
            "id": str(row.id),
            "result": row.result,
            "code": row.refusal_code,
            "detail": gate.SENTENCE.get(row.refusal_code or row.result),
        },
    )


@router.post("/api/mail/sends/{send_id}/resolve")
def resolve(
    send_id: str, body: ResolveIn, request: Request, user: CurrentUser, db: Db
) -> dict[str, str]:
    try:
        row = reconciling.resolve(db, user.id, _id(send_id), body.action, deps.clock(request))
    except LookupError:
        raise HTTPException(404, "No such send.") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    db.commit()
    return {"id": str(row.id), "result": row.result}


@router.post("/api/mail/sends/{send_id}/reconcile")
async def reconcile(send_id: str, request: Request, user: CurrentUser) -> dict[str, int]:
    wanted = _id(send_id)

    def owned() -> bool:
        with request.app.state.sessionmaker() as db:
            return (
                db.scalar(
                    sa.select(MailSend.id).where(MailSend.id == wanted, MailSend.user_id == user.id)
                )
                is not None
            )

    if not await asyncio.to_thread(owned):
        raise HTTPException(404, "No such send.")
    mcp = dispatch.client(request.app)
    found = (
        await reconciling.reconcile(
            request.app.state.sessionmaker, user.id, mcp, deps.clock(request)
        )
        if mcp
        else 0
    )
    return {"resolved": found}
