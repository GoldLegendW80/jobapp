"""The sole mail send path. Audit commits before one provider call; uncertainty never retries.

Only a session route creates user_click. Application approvals are loaded from apply's durable
gate decision, never supplied by an agent. Their authority fits application drafts only.
"""

import base64
import dataclasses
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.apply import caps, full_auto, run, states, submit
from app.apply import email as application_email
from app.apply import gate as apply_gate
from app.apply.models import Application, Mode, State
from app.apply.models import Authorisation as ApplyAuthorisation
from app.config import Settings
from app.documents import freeze as documents
from app.documents.profile_view import build
from app.documents.sources import PostingView, sources_for
from app.jobs.models import Job
from app.mail import drafts, factcheck_rules, locks, send_key
from app.mail.draft_models import MailDraft, MailSend
from app.mail.mcp_client import GmailMcp, MailConnectionError, MailServerDownError
from app.mail.models import MailAccount, MailMessage, MailThread
from app.profile import service as profile_service

MAX_RAW_BYTES = 12 * 1024 * 1024

SENTENCE = {
    "message_too_large": (
        "The attachments are too large for email. "
        "Use the application form or smaller document versions."
    ),
    "not_authorised": "This message needs your approval.",
    "draft_changed": "The message changed. Review it again.",
    "not_open": "This message is already being sent or has been sent.",
    "not_connected": "Connect Gmail in Account to send this application.",
    "no_send_scope": "Reconnect Gmail in Account to allow sending.",
    "needs_reconnect": "Reconnect Gmail in Account to continue.",
    "mail_send_unavailable": "Email sending is not configured. Ask the administrator to enable it.",
    "unconfirmed_recipient": "Confirm every new recipient before sending.",
    "never_apply_role": (
        "Your rules exclude this role. Edit or write the message yourself to send it."
    ),
    "fact_check": "Review the message against your profile before sending.",
    "daily_limit": "Your email sending limit is reached.",
    "company_cap": "Your email sending limit for this company is reached.",
    "gmail_quota": (
        "Gmail refused this send: its sending quota is reached. "
        "Try again later, or send from Gmail."
    ),
    "gmail_error": "Gmail refused this message. Review it before trying again.",
    "mail_server_down": "The email service is unavailable. Try again later.",
    "unknown": "We could not confirm this was sent. Look in your Sent folder.",
}


@dataclasses.dataclass(frozen=True)
class Authorisation:
    kind: str
    user_id: uuid.UUID
    draft_id: uuid.UUID
    content_hash: str
    confirmed_new_recipients: tuple[str, ...]
    acknowledged_issues: tuple[str, ...]
    at: datetime
    apply_id: uuid.UUID | None = None


def authorise_click(
    user_id: uuid.UUID,
    draft_id: uuid.UUID,
    content_hash: str,
    confirmed: list[str],
    acknowledged: list[str],
    now: datetime,
) -> Authorisation:
    return Authorisation(
        "user_click", user_id, draft_id, content_hash, tuple(confirmed), tuple(acknowledged), now
    )


def authorise_application(
    db: Session, application: Application, now: datetime
) -> Authorisation | None:
    draft = db.scalar(
        sa.select(MailDraft).where(
            MailDraft.application_id == application.id, MailDraft.user_id == application.user_id
        )
    )
    spent = submit._spent(db, application)
    if draft is None or spent is None:
        return None
    application_email.sync_draft(db, application, draft, now)
    return Authorisation(
        spent.kind.value,
        application.user_id,
        draft.id,
        drafts.content_hash(draft),
        tuple(draft.to_addrs),
        tuple((draft.fact_check or {}).get("acknowledged", []))
        if (draft.fact_check or {}).get("hash") == drafts.content_hash(draft)
        else (),
        spent.created_at,
        spent.id,
    )


def issues(db: Session, draft: MailDraft, now: datetime) -> list[dict[str, Any]]:
    profile = profile_service.get_profile(db, draft.user_id, now)
    view = build(profile_service.sections_view(profile), profile.revision)
    posting = (
        db.get(Job, db.get(Application, draft.application_id).posting_id)
        if draft.application_id
        else None
    )
    sources = sources_for(
        view,
        None,
        None,
        PostingView(posting.company, posting.title) if posting else None,
        kind="letter",
    )
    sources = dataclasses.replace(
        sources,
        recipients=tuple([*draft.to_addrs, *draft.cc_addrs]),
        participants=(
            *sources.participants,
            *(v.rsplit("@", 1)[-1] for v in [*draft.to_addrs, *draft.cc_addrs]),
        ),
    )
    return [
        {**dataclasses.asdict(i), "id": f"{i.code}:{i.start}:{i.end}"}
        for i in factcheck_rules.check(draft.body, sources)
    ]


def _limits(
    db: Session, draft: MailDraft, company: str | None, now: datetime, settings: Settings
) -> str | None:
    for limit, code, extra in (
        (settings.mail_send_daily_limit, "daily_limit", []),
        (
            settings.mail_send_company_daily_limit if company else None,
            "company_cap",
            [MailSend.company_key == company],
        ),
    ):
        if limit is None:
            continue
        count = db.scalar(
            sa.select(sa.func.count())
            .select_from(MailSend)
            .where(
                MailSend.user_id == draft.user_id,
                MailSend.at > now - timedelta(days=1),
                MailSend.result.in_(["pending", "sent", "unknown"]),
                *extra,
            )
        )
        if count >= limit:
            return code
    return None


def expire_pending(db: Session, user_id: uuid.UUID, now: datetime) -> None:
    locks.lock_send(db, user_id)
    for row in db.scalars(
        sa.select(MailSend)
        .where(
            MailSend.user_id == user_id,
            MailSend.result == "pending",
            MailSend.at <= now - timedelta(seconds=120),
        )
        .with_for_update()
    ):
        _finish(db, row, "unknown", now)


def _application_for_send(
    db: Session, row: MailSend, draft: MailDraft | None
) -> tuple[Application, Job] | None:
    """Re-read the exact active application binding under apply's locks, after lock_send."""
    if draft is None:
        application_id = db.scalar(
            sa.select(Application.id).where(
                Application.user_id == row.user_id,
                Application.posting_id == row.posting_id,
                Application.mail_send_id == row.id,
                Application.ats == "email",
            )
        )
        if application_id is None:
            return None
    else:
        if (
            draft.id != row.draft_id
            or draft.user_id != row.user_id
            or draft.kind != "application"
            or not draft.application_id
        ):
            return None
        application_id = draft.application_id
    try:
        application, posting = apply_gate.lock(db, row.user_id, application_id)
    except apply_gate.NotFoundError:
        return None
    if (
        application.ats != "email"
        or application.mail_send_id != row.id
        or application.posting_id != row.posting_id
    ):
        return None
    return application, posting


def _finish(
    db: Session,
    row: MailSend,
    result: str,
    now: datetime,
    *,
    code: str | None = None,
    message_id: str | None = None,
    thread_id: str | None = None,
    resolved_by: str | None = None,
) -> None:
    draft = (
        db.scalar(
            sa.select(MailDraft)
            .where(MailDraft.id == row.draft_id, MailDraft.user_id == row.user_id)
            .execution_options(populate_existing=True)
        )
        if row.draft_id
        else None
    )
    application_binding = _application_for_send(db, row, draft)
    if draft is not None and draft.application_id and application_binding is None:
        # A provider may confirm a historical attempt, but it cannot settle a newer one.
        draft = None
    row.result, row.updated_at, row.refusal_code = result, now, code
    row.gmail_message_id, row.gmail_thread_id = message_id, thread_id
    row.resolved_by = resolved_by
    if draft is not None:
        draft.status = {"sent": "sent", "unknown": "send_unknown", "failed": "draft"}.get(
            result, draft.status
        )
        draft.updated_at = now
        if result == "sent":
            draft.gmail_message_id, draft.sent_at = message_id, now
    if application_binding is not None:
        application, posting = application_binding
        if result == "unknown" and application.state in states.DECIDED:
            # A lost process may have crossed the provider boundary. Keep uncertainty closed
            # even if its committed pending audit predates the submitting transaction.
            states.move(application, State.submitting, now)
            db.flush()
        if application.state is State.submitting:
            outcome = {
                "sent": State.submitted,
                "unknown": State.submit_unknown,
                "failed": State.refused_by_site,
            }[result]
            submit.record(db, application.id, outcome, now, code=code)
        elif result == "sent" and application.state is State.submit_unknown:
            apply_gate._confirm_submitted_locked(db, application, posting, now)


def _decide(
    db: Session,
    auth: Authorisation,
    now: datetime,
    settings: Settings,
    switches: apply_gate.Switches,
    *,
    available: bool = True,
) -> tuple[MailSend, MailDraft | None, MailAccount | None, bytes | None]:
    locks.lock_send(db, auth.user_id)
    account = db.scalar(
        sa.select(MailAccount)
        .where(MailAccount.user_id == auth.user_id)
        .options(undefer(MailAccount.refresh_token_encrypted))
        .with_for_update()
    )
    draft = db.scalar(
        sa.select(MailDraft).where(MailDraft.id == auth.draft_id, MailDraft.user_id == auth.user_id)
    )
    if draft is None:
        raise LookupError("No such draft.")
    previous = db.scalar(
        sa.select(MailSend)
        .where(
            MailSend.draft_id == draft.id,
            MailSend.user_id == auth.user_id,
            MailSend.result.in_(["pending", "unknown", "sent"]),
            sa.or_(MailSend.resolved_by.is_(None), MailSend.resolved_by != "user_retry"),
        )
        .order_by(MailSend.at.desc())
        .limit(1)
    )
    if previous is not None:
        if previous.result == "pending" and previous.at <= now - timedelta(seconds=120):
            _finish(db, previous, "unknown", now)
        return previous, None, None, None
    application, posting, plan = None, None, None
    code = None
    if draft.kind == "application":
        if auth.kind not in {"person_approval", "full_auto_like"} or auth.apply_id is None:
            code = "not_authorised"
        else:
            application, posting = apply_gate.lock(db, auth.user_id, draft.application_id)
            spent = db.get(ApplyAuthorisation, auth.apply_id)
            if (
                spent is None
                or spent.application_id != application.id
                or spent.user_id != auth.user_id
                or spent.kind.value != auth.kind
                or spent.used_at is None
                or (latest := submit._spent(db, application)) is None
                or latest.id != spent.id
            ):
                code = "not_authorised"
            elif application.mail_send_id is not None:
                code = "not_open"
            else:
                plan = submit.prepare(db, application.id, now)
                if isinstance(plan, str):
                    code = plan
                elif apply_gate.switch_code(switches):
                    code = apply_gate.switch_code(switches)
                elif application.mode.value == "full_auto" and not application_email.own_company(
                    posting, draft.to_addrs[0]
                ):
                    code = "email_company_domain"
    elif auth.kind != "user_click":
        code = "not_authorised"
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.id == draft.id).with_for_update())
    company = caps.company_key(posting.company) if posting else None
    row = MailSend(
        id=uuid.uuid4(),
        user_id=auth.user_id,
        draft_id=draft.id,
        posting_id=posting.id if posting else None,
        company_key=company,
        authorisation_kind=auth.kind
        if auth.kind in {"user_click", "person_approval", "full_auto_like"}
        else "user_click",
        authorised_at=auth.at,
        content_hash=auth.content_hash,
        recipients_count=len(draft.to_addrs) + len(draft.cc_addrs),
        new_recipients=True,
        result="refused",
        at=now,
        updated_at=now,
    )
    db.add(row)
    code = code or ("not_open" if draft.status != "draft" else None)
    code = code or ("draft_changed" if drafts.content_hash(draft) != auth.content_hash else None)
    code = code or application_email.account_code(db, auth.user_id)
    code = code or ("mail_send_unavailable" if not available else None)
    if not code and (
        not settings.mail_send_key or len(settings.mail_send_key.get_secret_value()) < 32
    ):
        code = "mail_send_unavailable"
    if not code and (set(auth.confirmed_new_recipients) != set([*draft.to_addrs, *draft.cc_addrs])):
        code = "unconfirmed_recipient"
    if not code:
        try:
            drafts.validate(draft)
        except ValueError:
            code = "draft_changed"
    if not code and draft.kind == "application":
        for record in draft.document_versions.values():
            pdf = documents.pdf(db, draft.user_id, uuid.UUID(record["version_id"]))
            if pdf is None or hashlib.sha256(pdf).hexdigest() != record.get("pdf_sha256"):
                code = "stale"
                break
    if not code:
        found = issues(db, draft, now)
        if (draft.written_by == "agent" and found) or any(
            i["id"] not in auth.acknowledged_issues for i in found
        ):
            code = "fact_check"
        draft.fact_check = {"hash": drafts.content_hash(draft), "issues": found}
    if not code:
        code = _limits(db, draft, company, now, settings)
    if code:
        row.refusal_code = code
        if (
            application
            and application.state in {State.approved, State.held}
            and code not in {"holding", "paced"}
        ):
            if code in {
                "not_connected",
                "no_send_scope",
                "needs_reconnect",
                "mail_send_unavailable",
            }:
                states.move(application, State.needs_person, now)
                application.needs, application.error_code = code, code
                full_auto.hand_over(db, application, code, now)
            else:
                full_auto.to_review(
                    db, application, code, now
                ) if application.mode.value == "full_auto" else states.move(
                    application, State.ready, now
                )
        return row, None, None, None
    row.message_id_header = f"<{uuid.uuid4().hex}@jobapp.invalid>"
    try:
        raw = _build_raw(db, row, draft)
        code = "message_too_large" if len(raw) > MAX_RAW_BYTES else None
    except ValueError:
        raw, code = b"", "stale"
    if code:
        row.refusal_code = code
        if application and application.state in {State.approved, State.held}:
            if application.mode.value == "full_auto":
                full_auto.to_review(db, application, code, now)
            else:
                states.move(application, State.ready, now)
        return row, None, None, None
    if application:
        if application.dry_run:
            states.move(application, State.rehearsed, now)
            apply_gate._event(db, application, "rehearsed", now)
            row.refusal_code = "dry_run"
            return row, None, None, None
        if (until := caps.reserve(db, application, now)) is not None:
            run._wait(db, application, "host_pace", now, until=until.isoformat())
            row.refusal_code = "paced"
            return row, None, None, None
        application.mail_send_id = row.id
    row.result = "pending"
    draft.status = "sending"
    return row, draft, account, raw


def _build_raw(db: Session, row: MailSend, draft: MailDraft) -> bytes:
    message = EmailMessage(policy=SMTP)
    message["To"] = ", ".join(draft.to_addrs)
    if draft.cc_addrs:
        message["Cc"] = ", ".join(draft.cc_addrs)
    message["Subject"], message["Message-ID"] = draft.subject, row.message_id_header
    message["X-Jobapp-Send"] = str(row.id)
    if draft.thread_id:
        thread = db.get(MailThread, draft.thread_id)
        latest = db.scalar(
            sa.select(MailMessage)
            .where(MailMessage.thread_id == thread.id, MailMessage.user_id == draft.user_id)
            .order_by(MailMessage.received_at.desc())
            .limit(1)
        )
        if latest and latest.message_id_header:
            message["In-Reply-To"] = latest.message_id_header
            message["References"] = " ".join(
                [(latest.references_header or ""), latest.message_id_header]
            )
    message.set_content(draft.body, charset="utf-8")
    for kind, record in sorted(draft.document_versions.items()):
        data = documents.pdf(db, draft.user_id, uuid.UUID(record["version_id"]))
        if data is None or (
            record.get("pdf_sha256") and hashlib.sha256(data).hexdigest() != record["pdf_sha256"]
        ):
            raise ValueError("stale")
        message.add_attachment(data, maintype="application", subtype="pdf", filename=f"{kind}.pdf")
    return message.as_bytes()


async def _call_send(
    db: Session,
    mcp: GmailMcp,
    settings: Settings,
    row: MailSend,
    draft: MailDraft,
    account: MailAccount,
    now: datetime,
    raw: bytes,
    switches_for: Callable[[], Awaitable[apply_gate.Switches]] | None = None,
    clock: Callable[[], datetime] | None = None,
    authorisation_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    db.commit()

    async def live_switches() -> apply_gate.Switches:
        if switches_for is None:
            return apply_gate.Switches(enabled=True, paused=False)
        try:
            return await switches_for()
        except Exception:
            return apply_gate.Switches(enabled=True, paused=True)

    # Re-read cancellation, the live pauses, caps, Like validity and exact approval after MIME
    # reads, immediately before the capability leaves the API process.
    fresh_switches = await live_switches()
    code = apply_gate.switch_code(fresh_switches)
    locks.lock_send(db, row.user_id)
    db.expire_all()
    if row.result != "pending":
        return {"result": row.result}
    code = code or application_email.account_code(db, row.user_id)
    application, stop = None, None
    if draft.application_id:
        application, posting = apply_gate.lock(db, row.user_id, draft.application_id)
        now = clock() if clock else now
        if application.state not in states.DECIDED or application.mail_send_id != row.id:
            code = code or "not_open"
        elif (spent := submit._spent(db, application)) is None or spent.id != authorisation_id:
            code = code or "not_authorised"
        elif apply_gate.current_hash(db, application) != bytes(application.answers_hash or b""):
            code = code or "stale"
        elif refused := submit.recheck(db, application, posting, now):
            code, stop = code or refused.code, refused
    else:
        now = clock() if clock else now
    if application and application.state in {
        State.submitting,
        State.submit_unknown,
        State.submitted,
    }:
        return {"result": "unknown"}
    until = None
    if application and not code:
        until = caps.reserve(db, application, now, clock=clock)
        # The shared host lock can wait after the person's locks. Recheck validity
        # under it before recording a click, even when the host is now paced.
        now = clock() if clock else now
        if refused := submit.recheck(db, application, posting, now):
            code, stop = refused.code, refused
    fresh_switches = await live_switches()
    code = apply_gate.switch_code(fresh_switches) or code
    now = clock() if clock else now
    if application and not code and (refused := submit.recheck(db, application, posting, now)):
        code, stop = refused.code, refused

    def local_stop(code: str, stop: Any = None) -> dict[str, Any]:
        if application and application.mail_send_id == row.id:
            draft.status, draft.updated_at = "draft", now
            application.mail_send_id = None
            if application.state is State.submitting:
                # No capability has been minted and the provider has not been called.
                # Keep the committed click audit as conservative pacing history.
                submit._stop(
                    db,
                    application,
                    submit.Stop(State.ready, code),
                    now,
                    click_refused=True,
                )
            elif application.state in states.DECIDED:
                if code in {"not_connected", "needs_reconnect", "no_send_scope"}:
                    stop = submit.Stop(State.needs_person, code, code)
                elif application.mode is Mode.full_auto:
                    stop = submit.Stop(State.ready, code)
                elif code in {"paused", "submit_disabled"}:
                    stop = submit.Stop(State.cancelled, code)
                submit._stop(db, application, stop or submit.Stop(State.ready, code), now)
            else:
                full_auto.hand_over(db, application, code, now)
        if application is None:
            draft.status, draft.updated_at = "draft", now
        # The proven non-send and its application handover are one transaction. A crash
        # cannot leave a pending audit blocking the next fresh approval after this stop.
        row.result, row.refusal_code, row.updated_at = "failed", code, now
        db.commit()
        return {"result": "not_sent", "code": code}

    if code:
        return local_stop(code, stop)
    if application and until is not None:
        run._wait(db, application, "host_pace", now, until=until.isoformat())
        draft.status, draft.updated_at = "draft", now
        application.mail_send_id = None
        row.result, row.refusal_code, row.updated_at = "failed", "paced", now
        db.commit()
        return {"result": "not_sent", "code": "paced"}
    thread_id = db.get(MailThread, draft.thread_id).gmail_thread_id if draft.thread_id else None
    if application:
        states.move(application, State.submitting, now)
        apply_gate._event(db, application, "click", now, send_id=str(row.id))
        db.commit()  # the irreversible state is visible before minting any provider capability
        locks.lock_send(db, row.user_id)
        db.expire_all()
        # Disconnect can win the lock after that commit. A revoked connection cannot call
        # the provider; uncertainty remains conservative once submitting was committed.
        if application_email.account_code(db, row.user_id):
            return {"result": "unknown"}
        application, posting = apply_gate.lock(db, row.user_id, draft.application_id)
        if row.result != "pending":
            return {"result": row.result}
    # This send lock can itself wait after the submitting commit. A pause or expiry
    # before minting is still a proven local non-send, settled in one transaction.
    fresh_switches = await live_switches()
    code = apply_gate.switch_code(fresh_switches)
    now = clock() if clock else now
    if application and (
        application.state is not State.submitting or application.mail_send_id != row.id
    ):
        code = code or "not_open"
    if application and not code:
        spent = submit._spent(db, application)
        if (
            spent is None
            or spent.id != authorisation_id
            or full_auto.expired(application, spent, now)
        ):
            code = "authorisation_expired"
        elif apply_gate.current_hash(db, application) != bytes(application.answers_hash or b""):
            code = "stale"
        elif refused := submit.recheck(db, application, posting, now):
            code = refused.code
    if code:
        return local_stop(code)
    key = send_key.mint(
        settings.mail_send_key.get_secret_value(),
        row.id,
        row.user_id,
        account.email,
        draft.id,
        thread_id,
        row.content_hash,
        raw,
        now,
    )
    # Keep the send lock through this bounded provider call. Disconnect waits for its result
    # before revoking or purging; the pending audit was committed in the earlier transaction.
    return await mcp.call(
        account,
        "send_message",
        {
            "send_key": key,
            "raw": base64.urlsafe_b64encode(raw).decode(),
            "thread_id": thread_id,
        },
    )


async def send(
    sessions: sessionmaker[Session],
    auth: Authorisation,
    now: datetime,
    *,
    settings: Settings,
    mcp: GmailMcp,
    switches: apply_gate.Switches,
    switches_for: Callable[[], Awaitable[apply_gate.Switches]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> MailSend:
    with sessions() as db:
        row, draft, account, raw = _decide(
            db, auth, now, settings, switches, available=mcp is not None
        )
        db.commit()  # irreversible boundary: another connection sees pending before Gmail.
        if draft is None:
            return row
        try:
            answer = await _call_send(
                db, mcp, settings, row, draft, account, now, raw, switches_for, clock, auth.apply_id
            )
        except MailConnectionError:
            answer = {"result": "not_sent", "code": "needs_reconnect"}
        except MailServerDownError:
            answer = {"result": "gmail_4xx", "status": 0}
        except Exception:
            answer = {"result": "unknown"}
        result = answer.get("result", "unknown")
        code = None
        if result == "not_sent":
            code, result = answer.get("code", "gmail_error"), "failed"
        if result == "gmail_4xx":
            code = answer.get("code") or (
                "mail_server_down"
                if answer.get("status") == 0
                else "gmail_quota"
                if answer.get("status") == 429
                else "gmail_error"
            )
            result = "failed"
        locks.lock_send(db, row.user_id)
        row = db.get(MailSend, row.id, populate_existing=True)
        if row.result == "pending":
            _finish(
                db,
                row,
                result,
                now,
                code=code,
                message_id=answer.get("id"),
                thread_id=answer.get("threadId"),
                resolved_by="gmail" if result == "sent" else None,
            )
        db.commit()
        if result == "sent" and row.gmail_message_id:
            try:
                headers = await mcp.get_message(
                    account,
                    row.gmail_message_id,
                    format="metadata",
                    metadata_headers=["Message-ID"],
                )
                kept = next(
                    (
                        h["value"]
                        for h in headers.get("payload", {}).get("headers", [])
                        if h.get("name", "").lower() == "message-id"
                    ),
                    None,
                )
                if kept:
                    row.message_id_header = kept
                    db.commit()
            except Exception:
                db.rollback()
        return row
