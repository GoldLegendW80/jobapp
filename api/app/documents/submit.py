"""The submit step every writer shares (task D7; SPEC-documents.md, "Being careful, and the
automatic correction", steps 1 to 5). SDK free: the tool that calls it (`writer_tools`) lives in
the worker, and these functions are tested without a model.

One writer run submits its document through one tool (`submit_cv` for the CV writer). Each call
is `handle(...)`, which answers the writer with a text and keeps what the run has done so far in
memory (`_RUNS`, by run id), for the run's final hook (`generate.on_final`) to `take`:

1. **Structure.** The content is validated by D2's `checks.run` (the schema, the hard limits and
   the employer keys of the fixed facts). An invalid submit is answered with each field path and
   the code of the rule it breaks, never a value, and nothing is kept. At most
   DOCUMENTS_MAX_SUBMITS submits per run; the last one invalid ends the run's chances
   (`exhausted`), and the generation fails with `invalid_output`.
2. **Fixes code makes alone:** `corrections.fix_separators` (the person's dash rule), recorded.
3. **The fact check:** `checks.run` again on the fixed content, with the generation's date as
   `today`.
4. **The correction pass,** once: when the first valid submit holds claims, the writer is answered
   with each one (its field, its text, its rule, and whether it may only be rewritten or removed)
   and asked to submit again, changing nothing else. Nothing is kept yet.
5. **What remains:** the second valid submit (or a first one without claims) is accepted, never
   refused: what the writer changed is recorded by `diff_corrections` (`by: writer`), and each
   claim still open is removed by `remove_remaining` (`by: code`; a claim of `rules.POLICY`'s
   `needs_rewrite` class is cut only where the sentence stays whole, else kept and recorded for
   the person to see).

An outreach proposal (issue #485) goes through the same steps. It has no template: its context
is read here (`read_proposal`), with the posting's title and company as sources as for a letter,
no soft limits, and the fallbacks of `corrections.proposal_fallbacks`. Its `to`, the address it
goes to, is the app's (set when the proposal is created) and is put back on every submit:
the writer never chooses it.

A letter submitted with no recipient takes the default of the spec ("Hiring team, " and the
posting's company, in the document's language), before the checks: the company is a source of a
letter. The base CV a letter is written from is the generation's (`base_document_id`): the
letter document has none of its own.

The posting's title and company are not sources of a CV (`sources_for(..., kind="cv")`): a
claim copied from the posting is flagged like any invented one. Nothing here logs a text: the
answers go to the writer only, inside a private run whose tool results are never stored.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.documents import checks, corrections, profile_view, render_job, rules, store
from app.documents import content as c
from app.documents.corrections import Correction
from app.documents.models import Document, DocumentGeneration, Kind
from app.documents.settings import DocumentSettings
from app.documents.sources import DocumentSources, PostingView, sources_for
from app.ingestion.models import IngestionDocument
from app.jobs.models import Job
from app.profile import service as profile_service

STORED = "Stored. Your work is done: answer with one short line."
ALREADY = "Already stored. Do not submit again: answer with one short line."
NO_MORE = "No submit is left in this run. Stop and answer with one short line."
GONE = "This document can no longer be written. Stop and answer with one short line."
# The rules a writer is told, by issue code, in plain words (any other code: its own name).
RULES = {
    "unknown_employer": "not one of the person's employers: use only the keys of the profile",
    "duplicate_employer": "the same employer twice",
    "too_long": "over the hard limit of the field",
    "too_many": "too many entries",
    "unknown_field": "not a field of the schema",
    "required": "a required field is missing",
    "wrong_type": "the wrong type",
}
# The recipient of a letter that names none, by language.
RECIPIENT = {"en": "Hiring team, {company}", "fr": "Équipe de recrutement, {company}"}


@dataclass(frozen=True)
class WriterContext:
    """What one submit is checked against, read in one transaction."""

    kind: c.Kind
    sources: DocumentSources
    settings: DocumentSettings
    employer_keys: tuple[str, ...]
    soft_limits: rules.SoftLimits
    fallbacks: corrections.Fallbacks
    today: date
    recipient: str = ""  # a letter's default recipient; empty for a CV or a posting with no company
    contact: str = ""  # a proposal's `to`, the address the app set; never the writer's


@dataclass(frozen=True)
class Accepted:
    """What a run submitted for good: the content and every correction made to it."""

    content: c.Content
    corrections: tuple[Correction, ...]


@dataclass
class _Run:
    submits: int = 0
    first: c.Content | None = None  # before the correction pass
    first_claims: tuple[rules.Issue, ...] = ()
    accepted: Accepted | None = None
    exhausted: bool = False


@dataclass(frozen=True)
class Taken:
    """What `take` hands the final hook."""

    accepted: Accepted | None
    submits: int
    exhausted: bool


# A run whose final hook never takes its memory (its job cancelled under it by the queue's
# timeout, its end recorded by another worker) would stay forever: past RUNS_KEPT, the run that
# submitted least recently is forgotten. A worker runs MAX_AGENT_RUNS (2) runs at once.
RUNS_KEPT = 64
_RUNS: dict[uuid.UUID, _Run] = {}
_LOCK = threading.Lock()


def _run(run_id: uuid.UUID) -> _Run:
    with _LOCK:
        found = _RUNS.pop(run_id, None) or _Run()
        _RUNS[run_id] = found
        while len(_RUNS) > RUNS_KEPT:
            del _RUNS[next(iter(_RUNS))]
        return found


def take(run_id: uuid.UUID) -> Taken:
    """What the run submitted, forgotten here. A run that never submitted takes an empty one."""
    with _LOCK:
        found = _RUNS.pop(run_id, None) or _Run()
    return Taken(found.accepted, found.submits, found.exhausted)


def max_submits() -> int:
    from app.documents.writer_cv import setting

    return int(setting("documents_max_submits"))


# --- what a submit is checked against ---------------------------------------------------------


def _upload_text(db: Session, user_id: uuid.UUID, base: Document | None) -> str | None:
    if base is None or base.source_document_id is None:
        return None
    return db.execute(
        sa.select(IngestionDocument.text).where(
            IngestionDocument.id == base.source_document_id,
            IngestionDocument.user_id == user_id,
        )
    ).scalar_one_or_none()


# A proposal has no template, so no counter: the hard limits of its content are its limits.
NO_SOFT_LIMITS = rules.SoftLimits(
    headline=None, summary=None, highlight=None, main_bullet=None, bullet=None, paragraph=None
)


@dataclass(frozen=True)
class ProposalReading:
    """What a proposal is written and checked from: the profile seen with a base CV, that base
    CV and the posting's title and company (never its text)."""

    view: profile_view.ProfileView
    base: c.BaseCvContent | None
    posting: PostingView | None
    upload_text: str | None


def read_proposal(
    db: Session, document: Document, base_document_id: uuid.UUID | None, at: datetime
) -> ProposalReading:
    """The reading of a proposal, with the base CV named (a generation's), else none."""
    user_id = document.user_id
    base = store.get(db, user_id, base_document_id) if base_document_id is not None else None
    base_content = c.parse("base", base.draft) if base is not None else None
    if not isinstance(base_content, c.BaseCvContent):
        base_content = None
    profile = profile_service.get_profile(db, user_id, at)
    sections = profile_service.sections_view(profile)
    view = profile_view.build(sections, profile.revision, base_content)
    posting = None
    if document.posting_id is not None:
        row = db.execute(
            sa.select(Job.company, Job.title).where(
                Job.user_id == user_id, Job.id == document.posting_id
            )
        ).one_or_none()
        posting = PostingView(company=row[0] or "", title=row[1] or "") if row else None
    return ProposalReading(view, base_content, posting, _upload_text(db, user_id, base))


def proposal_sources(reading: ProposalReading) -> DocumentSources:
    return sources_for(
        reading.view, reading.base, reading.upload_text, reading.posting, kind="proposal"
    )


def proposal_edit_sources(db: Session, document: Document, at: datetime) -> DocumentSources:
    """The sources of a hand edit of a proposal (its notes): those of its last writing, read
    with the base CV that writing named (the default base CV before any writing)."""
    last = db.execute(
        sa.select(DocumentGeneration.base_document_id)
        .where(
            DocumentGeneration.user_id == document.user_id,
            DocumentGeneration.document_id == document.id,
        )
        .order_by(DocumentGeneration.created_at.desc(), DocumentGeneration.id.desc())
        .limit(1)
    ).one_or_none()
    if last is not None:
        base_id = last[0]
    else:
        default = store.default_base(db, document.user_id)
        base_id = default.id if default is not None else None
    return proposal_sources(read_proposal(db, document, base_id, at))


def _proposal_context(
    db: Session, document: Document, generation: DocumentGeneration
) -> WriterContext:
    started = generation.started_at or generation.created_at
    reading = read_proposal(db, document, generation.base_document_id, started)
    language = document.language.value
    return WriterContext(
        kind="proposal",
        sources=proposal_sources(reading),
        settings=store.settings(db, document.user_id),
        employer_keys=tuple(employer.key for employer in reading.view.employers),
        soft_limits=NO_SOFT_LIMITS,
        fallbacks=corrections.proposal_fallbacks(
            reading.view, reading.base, reading.posting, language
        ),
        today=started.date(),
        contact=str((document.draft or {}).get("to") or ""),
    )


def context_for(
    db: Session, user_id: uuid.UUID, binding: Mapping[str, Any], kind: c.Kind = "cv"
) -> WriterContext | None:
    """The sources, rules and limits of the bound document, None when it is gone."""
    generation = db.execute(
        sa.select(DocumentGeneration).where(
            DocumentGeneration.id == uuid.UUID(str(binding["generation_id"])),
            DocumentGeneration.user_id == user_id,
        )
    ).scalar_one_or_none()
    document = store.get(db, user_id, uuid.UUID(str(binding["document_id"])))
    if generation is None or document is None or generation.document_id != document.id:
        return None
    if kind == "proposal" or document.kind == Kind.proposal:
        if kind != "proposal" or document.kind != Kind.proposal:
            return None
        return _proposal_context(db, document, generation)
    started = generation.started_at or generation.created_at
    context = render_job.read_context(db, document, started)
    view, base_content = context.view, context.base
    base = store.get(db, user_id, document.base_document_id or generation.base_document_id)
    if kind == "letter" and base is not None and base_content is None:
        # A letter has no base CV of its own: the generation's is its source, as for a CV.
        base_content = c.parse("base", base.draft)
        profile = profile_service.get_profile(db, user_id, started)
        view = profile_view.build(
            profile_service.sections_view(profile), profile.revision, base_content
        )
    sources = sources_for(
        view, base_content, _upload_text(db, user_id, base), context.posting, kind=kind
    )
    template_kind = "letter" if kind == "letter" else "cv"
    company = context.posting.company.strip() if context.posting is not None else ""
    language = document.language.value
    return WriterContext(
        kind=kind,
        sources=sources,
        settings=store.settings(db, user_id),
        employer_keys=tuple(employer.key for employer in view.employers),
        soft_limits=context.template.kinds[template_kind].soft_limits,
        fallbacks=corrections.fallbacks_for(view, base_content, context.posting, language),
        today=started.date(),
        recipient=RECIPIENT[language].format(company=company)
        if kind == "letter" and company
        else "",
    )


# --- the answers to the writer ----------------------------------------------------------------


def _invalid_answer(found: checks.Checks, left: int) -> str:
    lines = [
        f"- {issue.field or '(the whole content)'}: {issue.code}"
        + (f" ({RULES[issue.code]})" if issue.code in RULES else "")
        for issue in found.invalid
    ]
    tail = (
        f"Fix each field and submit again ({left} submit(s) left)."
        if left > 0
        else "No submit is left in this run."
    )
    return "Not stored: the content breaks these rules.\n" + "\n".join(lines) + "\n" + tail


def _correction_answer(content: c.Content, found: checks.Checks) -> str:
    lines = []
    for issue in found.claims:
        how = (
            "rewrite it with what the profile or the base CV states, or remove it"
            if issue.rewrite
            else "remove it, or rewrite it with what the profile or the base CV states"
        )
        lines.append(f'- {issue.field}: "{checks.excerpt(content, issue)}" ({issue.code}): {how}')
    return (
        "Not stored yet: the fact check found claims that the profile and the base CV do not "
        "state. The posting is never a source. Fix each one below, change nothing else, and "
        "submit the whole document again once.\n" + "\n".join(lines)
    )


def handle(context: WriterContext | None, run_id: uuid.UUID, raw: object) -> tuple[str, bool]:
    """One submit of run `run_id`: `(answer, is_error)` (module docstring)."""
    state = _run(run_id)
    if state.accepted is not None:
        return ALREADY, False
    limit = max_submits()
    if state.submits >= limit:
        return NO_MORE, True
    if context is None:
        return GONE, True
    state.submits += 1
    kind = context.kind
    found = checks.run(
        kind,
        raw,
        context.sources,
        context.settings,
        employer_keys=context.employer_keys,
        soft_limits=context.soft_limits,
        today=context.today,
    )
    if found.content is None or found.invalid:
        left = limit - state.submits
        state.exhausted = left <= 0
        return _invalid_answer(found, left), True
    content, separators = corrections.fix_separators(kind, found.content, context.settings)
    if isinstance(content, c.LetterContent) and not content.recipient.strip() and context.recipient:
        content = content.model_copy(update={"recipient": context.recipient})
    if isinstance(content, c.ProposalContent) and content.to != context.contact:
        content = content.model_copy(update={"to": context.contact})
    found = checks.run(
        kind,
        content,
        context.sources,
        context.settings,
        employer_keys=context.employer_keys,
        soft_limits=context.soft_limits,
        today=context.today,
    )
    if state.first is None and found.claims:
        state.first, state.first_claims = content, found.claims
        return _correction_answer(content, found), True
    records: list[Correction] = list(separators)
    if state.first is not None:
        records += corrections.diff_corrections(state.first, content, state.first_claims)
    if found.claims:
        content, removed = corrections.remove_remaining(content, found, context.fallbacks)
        records += removed
    state.accepted = Accepted(content, tuple(records))
    return STORED, False
