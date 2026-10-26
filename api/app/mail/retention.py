"""Bounded send-audit retention, independent of a connection or sending authority."""

from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.mail import gate, locks
from app.mail.draft_models import MailSend


def prune(db: Session, now: datetime, *, keep_days: int, batch_size: int = 100) -> int:
    """Delete only audits strictly older than the cutoff. The caller commits.

    Lock order matches the send path: the person's mail lock, then the send and application.
    Resolve pending attempts conservatively before their audit disappears, preserving the
    application's uncertain state and spent authority even when its content was deleted.
    """
    if not 1 <= keep_days <= 3650 or not 1 <= batch_size <= 100:
        raise ValueError("Invalid send audit retention bounds.")
    cutoff = now - timedelta(days=keep_days)
    candidates = list(
        db.execute(
            sa.select(MailSend.user_id, MailSend.id)
            .where(MailSend.at < cutoff)
            .order_by(MailSend.user_id, MailSend.id)
            .limit(batch_size)
        )
    )
    removed = 0
    for user_id, send_id in candidates:
        locks.lock_send(db, user_id)
        row = db.scalar(
            sa.select(MailSend)
            .where(MailSend.id == send_id, MailSend.user_id == user_id, MailSend.at < cutoff)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            continue
        if row.result == "pending":
            gate._finish(db, row, "unknown", now)
        db.delete(row)
        removed += 1
    return removed
