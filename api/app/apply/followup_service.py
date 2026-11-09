"""Follow ups, the store (SPEC-apply.md, "Follow ups"): which of the person's postings are
followed, when each next follow up is due (followups.py on the dates read here), the open draft,
"I sent it", the person's switch, the start of the draft agent on a click, and the Gmail draft.

The dates of a posting: the application day is `jobs.applied_on`, else the day of its last
submitted application, else the day of its last move to applied (a posting moved by hand with no
date); the interview day is its last move to interview. With Gmail connected, the mails of the
threads linked to the posting count: the person's as follow ups, the company's as an answer
(their acknowledgement of the application left out). Days are the person's own, in the time
zone of their search schedule.

Nothing here sends anything (hard rule 3): the Gmail draft is a `mail_drafts` row like any other,
which only the person's click on Send through `mail.gate.send` can send, under every rule of that
gate. The caller commits.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.agents import credentials, runs
from app.agents.models import AgentRun, RunStatus
from app.apply import caps, followup_agent, followups, guardrails
from app.apply import email as apply_email
from app.apply.caps_models import ApplySettings
from app.apply.followup_models import FollowUp
from app.apply.models import Application, Mode, State
from app.documents import freeze as documents
from app.documents.profile_view import build
from app.documents.sources import PostingView, sources_for
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.mail import drafts as mail_drafts
from app.mail import factcheck_rules
from app.mail.draft_models import MailDraft
from app.mail.models import Direction, Label, MailClassification, MailMessage, MailThread
from app.profile import service as profile_service

AGENT, MODEL = followup_agent.NAME, followup_agent.MODEL
TOKEN_BUDGET = 150_000
TRACKED = (JobStatus.applied, JobStatus.interview)


class RefusedError(Exception):
    """What a click cannot do now; `code` is a key of the router's SENTENCES."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Entry:
    posting: Job
    applied_on: date
    # Where the application day comes from: `posting`, `application` or `status`.
    applied_from: str
    cadence: followups.Cadence
    sent: tuple[date, ...]
    draft: FollowUp | None


# --- the switch ---------------------------------------------------------------------------------


def enabled(db: Session, user_id: uuid.UUID) -> bool:
    row = db.get(ApplySettings, user_id)
    return True if row is None else bool(row.follow_ups)


def set_enabled(db: Session, user_id: uuid.UUID, on: bool, now: datetime) -> None:
    db.execute(
        insert(ApplySettings)
        .values(user_id=user_id, follow_ups=on, updated_at=now)
        .on_conflict_do_update(
            index_elements=[ApplySettings.user_id], set_={"follow_ups": on, "updated_at": now}
        )
    )


def today_of(db: Session, user_id: uuid.UUID, now: datetime) -> date:
    return _day(now, _zone(db, user_id))


# --- the list -----------------------------------------------------------------------------------


def _day(at: datetime, zone: ZoneInfo | None) -> date:
    return at.astimezone(zone).date() if zone is not None else at.date()


def _zone(db: Session, user_id: uuid.UUID) -> ZoneInfo | None:
    try:
        return ZoneInfo(caps.settings_of(db, user_id).timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _moves(
    db: Session, user_id: uuid.UUID, ids: list[uuid.UUID], to: str
) -> dict[uuid.UUID, datetime]:
    """The time of each posting's last move to `to`."""
    rows = db.execute(
        sa.select(JobEvent.job_id, sa.func.max(JobEvent.at))
        .where(
            JobEvent.user_id == user_id,
            JobEvent.job_id.in_(ids),
            JobEvent.kind == JobEventKind.status,
            JobEvent.data["to"].astext == to,
        )
        .group_by(JobEvent.job_id)
    ).all()
    return {job_id: at for job_id, at in rows}


def _submitted(db: Session, user_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
    rows = db.execute(
        sa.select(Application.posting_id, sa.func.max(Application.submitted_at))
        .where(
            Application.user_id == user_id,
            Application.posting_id.in_(ids),
            Application.state == State.submitted,
            Application.submitted_at.is_not(None),
        )
        .group_by(Application.posting_id)
    ).all()
    return {posting_id: at for posting_id, at in rows}


def _mails(
    db: Session, user_id: uuid.UUID, ids: list[uuid.UUID], zone: ZoneInfo | None
) -> tuple[dict[uuid.UUID, list[date]], dict[uuid.UUID, list[date]]]:
    """The days of the person's mails to the company and of the company's mails, by posting,
    from the threads linked to it; an acknowledgement of the application is no answer."""
    acknowledged = (
        sa.select(MailClassification.message_id)
        .where(
            MailClassification.user_id == user_id,
            MailClassification.label == Label.application_received,
            MailClassification.undone.is_(False),
        )
        .scalar_subquery()
    )
    rows = db.execute(
        sa.select(MailThread.posting_id, MailMessage.direction, MailMessage.received_at)
        .join(
            MailMessage,
            sa.and_(MailMessage.thread_id == MailThread.id, MailMessage.user_id == user_id),
        )
        .where(
            MailThread.user_id == user_id,
            MailThread.posting_id.in_(ids),
            MailThread.deleted_at.is_(None),
            sa.or_(
                MailMessage.direction == Direction.outbound,
                MailMessage.id.not_in(acknowledged),
            ),
        )
    ).all()
    out: dict[uuid.UUID, list[date]] = defaultdict(list)
    inbound: dict[uuid.UUID, list[date]] = defaultdict(list)
    for posting_id, direction, at in rows:
        (out if direction is Direction.outbound else inbound)[posting_id].append(_day(at, zone))
    return out, inbound


def _settle(db: Session, row: FollowUp, zone: ZoneInfo | None, now: datetime) -> None:
    """A draft the person sent with Gmail is a follow up sent that day."""
    if row.sent_on is not None or row.mail_draft_id is None:
        return
    draft = db.get(MailDraft, row.mail_draft_id)
    if draft is None or draft.user_id != row.user_id or draft.status != "sent":
        return
    row.sent_on = _day(draft.sent_at or now, zone)
    row.channel, row.updated_at = "gmail", now


def entries(
    db: Session, user_id: uuid.UUID, now: datetime, posting_id: uuid.UUID | None = None
) -> list[Entry]:
    """The person's followed postings (applied or in interview, with an application day), in the
    order of the list; one posting's only with `posting_id`."""
    query = sa.select(Job).where(Job.user_id == user_id, Job.status.in_(TRACKED))
    if posting_id is not None:
        query = query.where(Job.id == posting_id)
    postings = list(db.scalars(query))
    if not postings:
        return []
    ids = [posting.id for posting in postings]
    zone = _zone(db, user_id)
    today = _day(now, zone)
    applied_moves = _moves(db, user_id, ids, JobStatus.applied.value)
    interview_moves = _moves(db, user_id, ids, JobStatus.interview.value)
    submitted = _submitted(db, user_id, ids)
    mail_out, mail_in = _mails(db, user_id, ids, zone)
    rows: dict[uuid.UUID, list[FollowUp]] = defaultdict(list)
    for row in db.scalars(
        sa.select(FollowUp).where(FollowUp.user_id == user_id, FollowUp.posting_id.in_(ids))
    ):
        _settle(db, row, zone, now)
        rows[row.posting_id].append(row)
    found: list[Entry] = []
    for posting in postings:
        if posting.applied_on is not None:
            applied_on, source = posting.applied_on, "posting"
        elif posting.id in submitted:
            applied_on, source = _day(submitted[posting.id], zone), "application"
        elif posting.id in applied_moves:
            applied_on, source = _day(applied_moves[posting.id], zone), "status"
        else:
            continue
        mine = rows[posting.id]
        sent = followups.days(row.sent_on for row in mine)
        interview = interview_moves.get(posting.id)
        track = followups.Track(
            status="interview" if posting.status is JobStatus.interview else "applied",
            applied_on=applied_on,
            interview_on=_day(interview, zone) if interview is not None else None,
            sent=sent,
            mail_out=followups.days(mail_out.get(posting.id, ())),
            mail_in=followups.days(mail_in.get(posting.id, ())),
        )
        draft = next((row for row in mine if row.sent_on is None), None)
        found.append(
            Entry(posting, applied_on, source, followups.cadence(track, today), sent, draft)
        )
    found.sort(key=lambda entry: followups.sort_key(entry.cadence, entry.applied_on))
    return found


def entry(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, now: datetime) -> Entry:
    found = entries(db, user_id, now, posting_id)
    if not found:
        raise RefusedError("not_followed")
    return found[0]


# --- the fact check -----------------------------------------------------------------------------


def _dated(label: str, day: date) -> str:
    return f"{label} {day.day} {day:%B %Y} ({day.isoformat()})"


def _sources(
    db: Session, user_id: uuid.UUID, posting: Job, found: Entry | None, now: datetime
) -> factcheck_rules.Sources:
    """The profile, the posting's company and title, and the dates of this application."""
    profile = profile_service.get_profile(db, user_id, now)
    view = build(profile_service.sections_view(profile), profile.revision)
    sources = sources_for(
        view, None, None, PostingView(posting.company, posting.title), kind="letter"
    )
    if found is None:
        return sources
    known = [_dated("Applied on", found.applied_on)]
    known += [_dated("Followed up on", day) for day in found.sent]
    return dataclasses.replace(sources, instructions=(*sources.instructions, *known))


def issues(db: Session, user_id: uuid.UUID, found: Entry, body: str, now: datetime) -> list[dict]:
    """The fact check of mail M9 on a follow up's text: what it claims that neither the profile,
    the posting's company and title, nor the dates of this application state."""
    sources = _sources(db, user_id, found.posting, found, now)
    return [
        {**dataclasses.asdict(issue), "id": f"{issue.code}:{issue.start}:{issue.end}"}
        for issue in factcheck_rules.check(body, sources)
    ]


def draft_sources(db: Session, draft: MailDraft, now: datetime) -> factcheck_rules.Sources | None:
    """The sources of a Gmail draft made from a follow up (`to_gmail`), for the mail gate's fact
    check: the follow up's own, the dates left out once the posting is no longer followed. None
    for a draft no follow up of its person names."""
    posting = db.scalar(
        sa.select(Job)
        .join(FollowUp, sa.and_(FollowUp.posting_id == Job.id, FollowUp.user_id == Job.user_id))
        .where(FollowUp.user_id == draft.user_id, FollowUp.mail_draft_id == draft.id)
        .limit(1)
    )
    if posting is None:
        return None
    found = entries(db, draft.user_id, now, posting.id)
    return _sources(db, draft.user_id, posting, found[0] if found else None, now)


# --- the person's acts --------------------------------------------------------------------------


def _open_to_write(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, now: datetime) -> Entry:
    """The posting's entry while its draft may still be written: the switch on, the posting
    followed, a follow up due or waiting."""
    if not enabled(db, user_id):
        raise RefusedError("off")
    found = entry(db, user_id, posting_id, now)
    if not followups.can_draft(found.cadence):
        raise RefusedError(found.cadence.urgency)
    return found


def _open_row(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, now: datetime) -> FollowUp:
    """The posting's open draft, made empty when missing, locked FOR UPDATE."""
    db.execute(
        insert(FollowUp)
        .values(user_id=user_id, posting_id=posting_id, created_at=now, updated_at=now)
        .on_conflict_do_nothing(
            index_elements=[FollowUp.posting_id], index_where=FollowUp.sent_on.is_(None)
        )
    )
    return db.execute(
        sa.select(FollowUp)
        .where(
            FollowUp.user_id == user_id,
            FollowUp.posting_id == posting_id,
            FollowUp.sent_on.is_(None),
        )
        .with_for_update()
    ).scalar_one()


def _writing(db: Session, row: FollowUp) -> bool:
    if row.run_id is None:
        return False
    status = db.scalar(sa.select(AgentRun.status).where(AgentRun.id == row.run_id))
    return status in (RunStatus.queued, RunStatus.running)


def record_sent(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, now: datetime) -> Entry:
    """ "I sent it": the open draft, or an empty row, sent today from the person's own mail. A
    second click the same day records nothing more; a silent application takes two at most."""
    found = entry(db, user_id, posting_id, now)
    today = today_of(db, user_id, now)
    if today in found.sent:
        return found
    if not followups.can_draft(found.cadence):
        raise RefusedError(found.cadence.urgency)
    row = _open_row(db, user_id, posting_id, now)
    if _writing(db, row):
        raise RefusedError("running")
    row.sent_on, row.channel, row.updated_at = today, "own_mail", now
    db.flush()
    return entry(db, user_id, posting_id, now)


def save_text(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, subject: str, body: str, now: datetime
) -> FollowUp:
    """The person's own text for the draft: the dash rule and the fact check as for the agent's,
    shown, never a refusal of what they wrote (refused only as Write a draft is: the switch off,
    or no follow up to write)."""
    found = _open_to_write(db, user_id, posting_id, now)
    row = _open_row(db, user_id, posting_id, now)
    if _writing(db, row):
        raise RefusedError("running")
    row.subject, row.body = followups.undash(subject.strip()), followups.undash(body.strip())
    row.issues = issues(db, user_id, found, row.body, now)
    row.written_by, row.error, row.updated_at = "person", None, now
    db.flush()
    return row


def start_draft(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, now: datetime) -> uuid.UUID:
    """The person's click on Write a draft: a queued run of the follow up agent bound to the
    posting's open draft (D29: never without a click). The caller commits, then queues it."""
    found = _open_to_write(db, user_id, posting_id, now)
    rules = _rules(db, user_id)
    posting = found.posting
    swept = guardrails.swept_never_apply(db, user_id, posting.id)
    if guardrails.never_apply(posting, rules, swept=swept, mode=Mode.review) is not None:
        raise RefusedError("never_apply")
    row = _open_row(db, user_id, posting_id, now)
    if _writing(db, row):
        raise RefusedError("running")
    if credentials.get(db, user_id) is None:
        raise RefusedError("credential")
    data = {"follow_up_id": str(row.id), "token_budget": TOKEN_BUDGET}
    try:
        run = runs.create_run(
            db,
            user_id,
            AGENT,
            posting_id,
            data,
            MODEL,
            now,
            flow=runs.WorkflowRef("follow_up", trigger="draft"),
        )
    except runs.ActiveRunsError:
        raise RefusedError("busy") from None
    row.run_id, row.error, row.updated_at = run.id, None, now
    db.flush()
    return run.id


def not_queued(db: Session, user_id: uuid.UUID, run_id: uuid.UUID, now: datetime) -> None:
    runs.fail_queued(db, run_id, runs.QUEUE_DOWN, now)
    row = db.scalar(
        sa.select(FollowUp)
        .where(FollowUp.user_id == user_id, FollowUp.run_id == run_id)
        .with_for_update()
    )
    if row is not None:
        row.run_id, row.error = None, "queue_down"


def _rules(db: Session, user_id: uuid.UUID) -> Any:
    from app.apply import run as apply_run

    return apply_run._rules(db, user_id)


def letter_text(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> str:
    """The letter that was sent: the one of the posting's last submitted application, else the
    posting's current letter (an application made outside the app)."""
    from app.apply import run as apply_run

    record = db.scalar(
        sa.select(Application.document_versions)
        .where(
            Application.user_id == user_id,
            Application.posting_id == posting_id,
            Application.state == State.submitted,
        )
        .order_by(Application.submitted_at.desc().nulls_last())
        .limit(1)
    )
    version_id = ((record or {}).get("letter") or {}).get("version_id")
    if version_id is None:
        version = documents.current(db, user_id, posting_id).letter.version
        version_id = version.version_id if version is not None else None
    if version_id is None:
        return ""
    return apply_run._letter_text(db, user_id, uuid.UUID(str(version_id)))


# --- with Gmail ---------------------------------------------------------------------------------


def _thread(db: Session, user_id: uuid.UUID, posting_id: uuid.UUID) -> uuid.UUID | None:
    """The posting's latest linked thread, when no other draft is open on it."""
    thread = db.scalar(
        sa.select(MailThread.id)
        .where(
            MailThread.user_id == user_id,
            MailThread.posting_id == posting_id,
            MailThread.deleted_at.is_(None),
        )
        .order_by(MailThread.last_message_at.desc().nulls_last())
        .limit(1)
    )
    if thread is None:
        return None
    taken = db.scalar(
        sa.select(MailDraft.id).where(
            MailDraft.user_id == user_id, MailDraft.thread_id == thread, MailDraft.status != "sent"
        )
    )
    return None if taken is not None else thread


def to_gmail(
    db: Session, user_id: uuid.UUID, posting_id: uuid.UUID, to: str, now: datetime
) -> MailDraft:
    """The draft's text as a Gmail draft to `to`, in the posting's thread when there is one: a
    `reply` of the mail module, which the person sends by their own click on Send (the gate's
    `user_click`, its recipient, fact check and every other rule as for any draft). Refused as
    Write a draft is: the switch off, or no follow up to write."""
    _open_to_write(db, user_id, posting_id, now)
    code = apply_email.account_code(db, user_id)
    if code is not None:
        raise RefusedError(code)
    row = _open_row(db, user_id, posting_id, now)
    if _writing(db, row):
        raise RefusedError("running")
    if not row.body.strip():
        raise RefusedError("no_text")
    try:
        address = mail_drafts.address(to.strip())
    except ValueError:
        raise RefusedError("address") from None
    draft = (
        db.scalar(
            sa.select(MailDraft)
            .where(MailDraft.id == row.mail_draft_id, MailDraft.user_id == user_id)
            .with_for_update()
        )
        if row.mail_draft_id is not None
        else None
    )
    writer = "agent" if row.written_by == "agent" else "user"
    if draft is None:
        draft = MailDraft(
            id=uuid.uuid4(),
            user_id=user_id,
            thread_id=_thread(db, user_id, posting_id),
            kind="reply",
            to_addrs=[address],
            cc_addrs=[],
            subject=row.subject,
            body=row.body,
            written_by=writer,
            created_at=now,
            updated_at=now,
        )
        db.add(draft)
    elif draft.status == "draft":
        changed = (draft.to_addrs, draft.subject, draft.body) != ([address], row.subject, row.body)
        if changed:
            draft.to_addrs, draft.subject, draft.body = [address], row.subject, row.body
            draft.written_by, draft.fact_check = writer, None
            draft.version, draft.updated_at = draft.version + 1, now
    try:
        mail_drafts.validate(draft)
    except ValueError:
        raise RefusedError("address") from None
    db.flush()
    row.mail_draft_id, row.updated_at = draft.id, now
    return draft
