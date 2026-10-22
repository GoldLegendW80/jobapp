"""Applications by email: posting addresses only, fixed document versions, no send authority."""

import hashlib
import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.apply import full_auto, gate, run, states
from app.apply.models import AnswerSource, Application, ApplicationAnswer, Mode, State
from app.jobs.models import Job, JobStatus
from app.mail import domains, drafts, hosts
from app.mail.draft_models import MailDraft
from app.mail.google import SCOPE_SEND
from app.mail.models import AccountStatus, MailAccount

_ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")
_INSTRUCTION = re.compile(
    r"(?:\b(?:send|email|submit|forward|envoyez|envoyer|adresser|adressez)\b"
    r"[^\n.!?]{0,100}\b(?:cv|resume|résumé|application|candidature)\b"
    r"|\b(?:apply|postulez|postuler)\s+(?:by\s+email\s+)?(?:to|at|à|par))",
    re.IGNORECASE,
)
_RECIPIENT_PREFIX = re.compile(r"\s*(?:(?:to|at|à|par)\s+)?<?", re.IGNORECASE)
_RECIPIENT_JOIN = re.compile(r"\s*>?\s*(?:[,;/]|\b(?:or|and|ou|et)\b)\s*<?", re.IGNORECASE)


def recipients(text: str) -> list[str]:
    """Read only the address list immediately following application instructions."""
    found: list[str] = []
    for line in text[:100_000].splitlines():
        line = line[:2000]
        for instruction in _INSTRUCTION.finditer(line):
            prefix = _RECIPIENT_PREFIX.match(line, instruction.end())
            offset = prefix.end()
            while match := _ADDRESS.match(line, offset):
                try:
                    value = drafts.address(match.group().rstrip("."))
                except ValueError:
                    break
                if value not in found:
                    found.append(value)
                join = _RECIPIENT_JOIN.match(line, match.end())
                if join is None:
                    break
                offset = join.end()
    return found[:20]


def addresses(posting: Job) -> list[str]:
    value = (posting.url or "").strip()
    if value.lower().startswith("mailto:"):
        try:
            return [drafts.address(value[7:])]
        except ValueError:
            return []
    return recipients(posting.posting_text or "")


def own_company(posting: Job, recipient: str) -> bool:
    domain = recipient.rsplit("@", 1)[-1]
    host = urlsplit(posting.url or "").hostname
    return (
        host is not None
        and hosts.is_company_own(host)
        and not hosts.is_public_mailbox(domain)
        and domains.same_organisation(host, domain)
    )


def account_code(db: Session, user_id: uuid.UUID) -> str | None:
    account = db.get(MailAccount, user_id)
    if account is None or account.disconnecting_at is not None:
        return "not_connected"
    if account.status is not AccountStatus.ok:
        return "needs_reconnect"
    return None if SCOPE_SEND in account.scopes else "no_send_scope"


def recover(db: Session, application: Application, now: datetime, *, configured: bool) -> bool:
    from app.mail.locks import lock_send

    if application.ats != "email" or application.state is not State.needs_person:
        return False
    if application.needs not in {
        "not_connected",
        "no_send_scope",
        "needs_reconnect",
        "mail_send_unavailable",
    }:
        return False
    if account_code(db, application.user_id) or not configured:
        return False
    lock_send(db, application.user_id)
    application, _ = gate.lock(db, application.user_id, application.id)
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == application.id))
    if draft is None or draft.status != "draft":
        return False
    states.move(application, State.ready, now)
    application.needs = application.error_code = None
    gate._event(db, application, "person_done", now, needs="email_connection")
    return True


def prepare(db: Session, application_id: uuid.UUID, now: datetime) -> str:
    plan = run.prepare(db, application_id, now)
    if isinstance(plan, str):
        return plan
    application = db.get(Application, application_id)
    posting = db.get(Job, application.posting_id)
    chosen = addresses(posting)
    states.move(application, State.filling, now)
    application.ats = "email"
    application.document_versions = {
        kind: {
            **record,
            "pdf_sha256": hashlib.sha256(
                plan.pdfs[run.CV_NAME if kind == "cv" else run.LETTER_NAME]
            ).hexdigest(),
        }
        for kind, record in application.document_versions.items()
        if (run.CV_NAME if kind == "cv" else run.LETTER_NAME) in plan.pdfs
    }

    if len(plan.pdfs) != 2 or not plan.letter_text:
        states.move(application, State.needs_person, now)
        run._wait(db, application, "no_documents", now)
        return "no_documents"
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == application_id))
    if draft is None:
        draft = MailDraft(
            id=uuid.uuid4(),
            user_id=application.user_id,
            application_id=application.id,
            kind="application",
            to_addrs=chosen if len(chosen) == 1 else [],
            cc_addrs=[],
            subject=posting.title.replace("\r", " ").replace("\n", " ")[:998],
            body=plan.letter_text,
            written_by="agent",
            version=1,
            status="draft",
            document_versions=application.document_versions,
            created_at=now,
            updated_at=now,
        )
        db.add(draft)
    for field, label, kind, value, source, options in (
        (
            "email_to",
            "Email recipient, new address from the posting",
            "select",
            chosen[0] if len(chosen) == 1 else None,
            AnswerSource.document if len(chosen) == 1 else AnswerSource.left,
            chosen,
        ),
        ("email_subject", "Email subject", "text", draft.subject, AnswerSource.document, []),
        ("email_body", "Email message", "textarea", draft.body, AnswerSource.agent, []),
    ):
        db.add(
            ApplicationAnswer(
                application_id=application.id,
                user_id=application.user_id,
                field_key=field,
                label=label,
                kind=kind,
                value=value,
                source=source,
                options=options,
                required=True,
                approved=False,
            )
        )
    if JobStatus(posting.status) is JobStatus.found:
        from app.jobs import service as jobs_service

        jobs_service.set_status(db, posting, JobStatus.staged, now, via="apply")
    code = account_code(db, application.user_id)
    if code or len(chosen) != 1:
        states.move(application, State.needs_person, now)
        run._wait(db, application, code or "required_left", now)
    else:
        states.move(application, State.ready, now)
    if application.mode is Mode.full_auto and (
        len(chosen) != 1 or not own_company(posting, chosen[0])
    ):
        if application.state is State.needs_person:
            full_auto.hand_over(db, application, "email_company_domain", now)
        else:
            full_auto.to_review(db, application, "email_company_domain", now)
    gate._event(db, application, "email_prepared", now, recipients_count=len(chosen))
    return application.state.value


def sync_draft(db: Session, application: Application, draft: MailDraft, now: datetime) -> None:
    values = {r.field_key: r for r in gate.rows_of(db, application)}
    recipient = values.get("email_to")
    allowed = addresses(db.get(Job, application.posting_id))
    if recipient is None or recipient.value not in allowed:
        raise ValueError("Choose a recipient from the posting.")
    draft.to_addrs, draft.cc_addrs = [drafts.address(recipient.value)], []
    draft.subject = values["email_subject"].value
    draft.body = values["email_body"].value
    draft.written_by = "user" if values["email_body"].source is AnswerSource.person else "agent"
    draft.document_versions = application.document_versions
    draft.updated_at = now
    drafts.validate(draft)
