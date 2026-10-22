"""Unknown sends can be found in Sent, never sent again by a background task."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.mail import gate, locks
from app.mail.draft_models import MailDraft, MailSend
from app.mail.mcp_client import GmailMcp
from app.mail.models import MailAccount


async def reconcile(
    sessions: sessionmaker[Session],
    user_id: uuid.UUID,
    mcp: GmailMcp,
    now: datetime,
) -> int:
    with sessions() as db:
        gate.expire_pending(db, user_id, now)
        rows = list(
            db.scalars(
                sa.select(MailSend)
                .where(
                    MailSend.user_id == user_id,
                    MailSend.result == "unknown",
                    MailSend.resolved_by.is_(None),
                )
                .order_by(MailSend.updated_at, MailSend.id)
                .limit(5)
            )
        )
        account = db.scalar(
            sa.select(MailAccount)
            .where(MailAccount.user_id == user_id)
            .options(undefer(MailAccount.refresh_token_encrypted))
        )
        db.commit()
        if account is None or account.disconnecting_at is not None:
            return 0
        found = 0
        for row in rows:
            row.updated_at = now
            db.commit()
            try:
                query = (
                    f"in:sent rfc822msgid:{row.message_id_header}"
                    if row.message_id_header
                    else "in:sent newer_than:2d"
                )
                page = await mcp.list_messages(account, q=query, max_results=50)
                refs = page.get("messages", [])
                if not refs:
                    page = await mcp.list_messages(
                        account, q="in:sent newer_than:2d", max_results=50
                    )
                    refs = page.get("messages", [])
                match = None
                for ref in refs:
                    message = await mcp.get_message(
                        account,
                        ref["id"],
                        format="metadata",
                        metadata_headers=["Message-ID", "X-Jobapp-Send"],
                    )
                    headers = {
                        h["name"].lower(): h["value"]
                        for h in message.get("payload", {}).get("headers", [])
                    }
                    if headers.get("x-jobapp-send") == str(row.id) or (
                        row.message_id_header and headers.get("message-id") == row.message_id_header
                    ):
                        match = message
                        break
                if match is None:
                    continue
                locks.lock_send(db, user_id)
                current = db.get(MailSend, row.id, populate_existing=True)
                if current and current.result == "unknown":
                    gate._finish(
                        db,
                        current,
                        "sent",
                        now,
                        message_id=match["id"],
                        thread_id=match.get("threadId"),
                        resolved_by="reconcile",
                    )
                    found += 1
                db.commit()
            except Exception:
                db.rollback()
                break
        return found


def resolve(
    db: Session, user_id: uuid.UUID, send_id: uuid.UUID, action: str, now: datetime
) -> MailSend:
    locks.lock_send(db, user_id)
    row = db.scalar(
        sa.select(MailSend)
        .where(MailSend.id == send_id, MailSend.user_id == user_id)
        .with_for_update()
    )
    if row is None:
        raise LookupError("No such send.")
    if row.result != "unknown" or row.resolved_by == "user_retry":
        raise ValueError("This send is not waiting for a decision.")
    draft = db.get(MailDraft, row.draft_id) if row.draft_id else None
    if action == "sent":
        gate._finish(db, row, "sent", now, resolved_by="user")
    elif action == "send_again" and draft:
        if draft.application_id:
            from app.apply import gate as apply_gate

            application = apply_gate.retry(db, user_id, draft.application_id, now)
            application.mail_send_id = None
        draft.status, draft.updated_at = "draft", now
        row.resolved_by, row.updated_at = "user_retry", now
    else:
        raise ValueError("Choose whether the message was sent or needs a new approval.")
    return row
