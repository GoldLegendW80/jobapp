"""What a disconnect deletes, in one place (mail M4; tasks/mail/todo.md, "Privacy and
isolation"). Complete for the tables of migration 0013; M8 adds those of 0014 (drafts,
instructions, and the emptied `message_id_header` of the send log, which stays).

`purge_user(db, user_id)` runs inside the caller's transaction, under `lock_sync` then
`lock_send` (accounts.py takes them). It:

- deletes the `mail_link` proposals of the user,
- reduces the payload of his `mail_status` proposals to `{from, to, mail_deleted: true}`,
- removes `message_id` from his job events, which keep `{from, to, via, mail_deleted: true}`:
  the history of a posting still says that a status changed because of a mail,
- deletes his classifications, messages, threads, confirmed domains, assist batches and, last,
  his account row with its sealed token.

It is idempotent: a second run finds nothing. The proposal rows are locked in id order before
they change, last of all locks, as everywhere in the app ("Locks").
"""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.models import Proposal
from app.mail import models

KIND_LINK = "mail_link"
KIND_STATUS = "mail_status"

# Every mail table of 0013, children first. A test counts the rows of each after a purge.
TABLES: tuple[type, ...] = (
    models.MailClassification,
    models.MailMessage,
    models.MailThread,
    models.MailConfirmedDomain,
    models.MailAssistBatch,
    models.MailAccount,
)

_STRIP_STATUS = sa.text(
    "UPDATE proposals SET payload = jsonb_strip_nulls(jsonb_build_object("
    "'from', payload -> 'from', 'to', payload -> 'to')) || '{\"mail_deleted\": true}'::jsonb "
    "WHERE user_id = :user_id AND kind = :kind AND NOT jsonb_exists(payload, 'mail_deleted')"
)
_STRIP_EVENTS = sa.text(
    "UPDATE job_events SET data = jsonb_strip_nulls(jsonb_build_object("
    "'from', data -> 'from', 'to', data -> 'to', 'via', data -> 'via')) "
    "|| '{\"mail_deleted\": true}'::jsonb "
    "WHERE user_id = :user_id AND jsonb_exists(data, 'message_id')"
)


@dataclass(frozen=True)
class Purged:
    """How many rows went, for the log line: counts only."""

    rows: int
    proposals: int
    events: int


def purge_user(db: Session, user_id: uuid.UUID) -> Purged:
    from app.mail.draft_models import MailDraft, MailSend

    rows = _count(db.execute(sa.delete(MailDraft).where(MailDraft.user_id == user_id)))
    db.execute(
        sa.update(MailSend).where(MailSend.user_id == user_id).values(message_id_header=None)
    )
    for table in TABLES[:-1]:
        rows += _count(db.execute(sa.delete(table).where(table.user_id == user_id)))  # type: ignore[attr-defined]
    events = _count(db.execute(_STRIP_EVENTS, {"user_id": user_id}))
    db.scalars(
        sa.select(Proposal.id)
        .where(Proposal.user_id == user_id, Proposal.kind.in_((KIND_LINK, KIND_STATUS)))
        .order_by(Proposal.id)
        .with_for_update()
    ).all()
    proposals = _count(
        db.execute(
            sa.delete(Proposal).where(Proposal.user_id == user_id, Proposal.kind == KIND_LINK)
        )
    )
    proposals += _count(db.execute(_STRIP_STATUS, {"user_id": user_id, "kind": KIND_STATUS}))
    account = TABLES[-1]
    rows += _count(db.execute(sa.delete(account).where(account.user_id == user_id)))  # type: ignore[attr-defined]
    db.flush()
    return Purged(rows=rows, proposals=proposals, events=events)


def _count(result: sa.Result[object] | sa.CursorResult[object]) -> int:
    found = getattr(result, "rowcount", 0)
    return found if isinstance(found, int) and found > 0 else 0
