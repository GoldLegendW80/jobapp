"""The clicks that write a posting's documents, "Write" and "Make it fit" (tasks D10, D18 and
D29; SPEC-documents.md, "API", "Writing on a click", "Templates"). Included by `routers.py`
before D6's `/api/documents/{document_id}`. Nothing in the app writes a CV or a letter without
one of these clicks, an Apply or a Like (owner, 2026-11-06).

- `POST /api/jobs/{id}/documents/write {kinds, base_document_id?}`: a click. It writes the
  posting's CV, its letter, or both, whatever the fit. A generation of trigger `auto` of the
  same document left from before D29 that has not started gives way to it (cancelled). Started
  at once when the consent and a credential are there, else `waiting` with `consent` or
  `credential` (nothing spends without both; the tick starts it once they exist). A person with
  no base CV gets one built from the profile first (`writer_base.click_wait`): the CV waits with
  `base_cv` and the tick starts it once the base CV is written. With both kinds, the CV is
  written now and the letter follows it once the new CV and its hiring panel are done (D8's
  chain, `generate.start_letter`, D29's `panel`), even when the letter has a version: it is
  never written from the old CV. `base_document_id` names another of the person's base CVs to
  write the CV from. 202 with the generations; 404 for another person's posting or base CV;
  409 while one is running, and 409 with the page's sentence for a closed posting, the
  posting's own address read first (jobs/liveness_check.py, SPEC-jobs "Liveness").
- `POST /api/documents/{id}/fit`: "Make it fit", a click. The `shorten` generation of a posting's
  CV whose latest version overflows, whoever made that version (a template switch included),
  started at once (`generate.make_fit`). It needs the consent, a credential, a write of the CV
  to draw on and a known ceiling left; each missing one is a 409 with the sentence of
  `FIT_REFUSED`, nothing created. A frozen latest version stays as it is: the shorter one is a
  new version on top. 202 with the generation; 404 for another person's document; 409 while one
  is open; 422 for a letter or a base CV.
Every route is the signed in person's. Every answer is `Cache-Control: private, no-store`.
Logs carry ids and codes, never a text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio.from_thread
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import queue as queues
from app.agents import runs as agent_runs
from app.db import get_session
from app.documents import estimate, generate, store, writer_base
from app.documents.models import (
    Document,
    DocumentGeneration,
    DocumentVersion,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    GenerationWait,
    Kind,
)
from app.identity import deps
from app.identity.models import User
from app.jobs import liveness_check

logger = logging.getLogger("app.documents")

PRIVATE = {"Cache-Control": "private, no-store"}
POSTING_NOT_FOUND = "Posting not found."
BASE_NOT_FOUND = "CV not found."
RUNNING = "This document is being written. Wait for it to finish."
BUSY = "This posting is busy. Try again in a moment."
DOCUMENT_NOT_FOUND = "Document not found."
EDIT_INSTEAD = "Shorten the longest fields in Edit, or choose a more compact template."
WRITE_AGAIN = "Write it again, or shorten the longest fields in Edit."
# What "Make it fit" answers for each reason of generate.FIT_REFUSALS.
FIT_REFUSED = {
    "not_shortened": (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        f"Only a posting's CV has a shortening pass. {EDIT_INSTEAD}",
    ),
    "not_drawn": (
        status.HTTP_409_CONFLICT,
        "The page of this version is not drawn yet. Wait for the preview, then try again.",
    ),
    "fits": (status.HTTP_409_CONFLICT, "This CV already fits on one page."),
    "consent": (
        status.HTTP_409_CONFLICT,
        "Confirm what is sent to the writer first, in the CV tab of your profile.",
    ),
    "credential": (status.HTTP_409_CONFLICT, "Add a model credential in your account first."),
    "not_written": (
        status.HTTP_409_CONFLICT,
        f"The writer has not written this CV, so it has no budget to shorten it. {EDIT_INSTEAD}",
    ),
    "spend_unknown": (
        status.HTTP_409_CONFLICT,
        "What the writer spent on this CV is not known, so it has no limit to shorten it within."
        f" {WRITE_AGAIN}",
    ),
    "token_ceiling": (
        status.HTTP_409_CONFLICT,
        f"The writing of this CV has used its token limit. {WRITE_AGAIN}",
    ),
    "runs_busy": (
        status.HTTP_409_CONFLICT,
        "The assistant is busy with other documents. Try again in a moment.",
    ),
}


def _private(response: Response) -> None:
    response.headers.update(PRIVATE)


Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]
router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=PRIVATE)


# --- Write now ----------------------------------------------------------------------------------


class WriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[Literal["cv", "letter"]] = Field(min_length=1, max_length=2)
    base_document_id: uuid.UUID | None = None
    # The fake backend's recorded streams (tests and journeys), named by what follows
    # `documents_<kind>_`; ignored on the real model.
    script: Annotated[str, Field(max_length=30, pattern=r"^[a-z0-9_]{1,30}$")] | None = None


class WrittenOut(BaseModel):
    id: uuid.UUID
    kind: Kind
    status: GenerationStatus
    wait_reason: GenerationWait | None


class WriteOut(BaseModel):
    generations: list[WrittenOut]


def _has_version(db: Session, document_id: uuid.UUID) -> bool:
    found = db.execute(
        sa.select(DocumentVersion.id).where(DocumentVersion.document_id == document_id).limit(1)
    ).scalar_one_or_none()
    return found is not None


def _give_way(db: Session, document: Document, at: datetime, *, follows: bool = False) -> None:
    """`generate.give_way`; a running generation, or another click, is a 409, unless the document
    `follows` the CV (a letter of both kinds: then it is left as it is)."""
    if not generate.give_way(db, document, at) and not follows:
        raise _error(status.HTTP_409_CONFLICT, RUNNING)


@router.post(
    "/api/jobs/{job_id}/documents/write",
    response_model=WriteOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"description": "Not the person's posting or base CV"},
        409: {"description": "A generation of this document is running"},
    },
)
def write_now(
    job_id: uuid.UUID, body: WriteIn, user: CurrentUser, request: Request, db: Db
) -> WriteOut:
    if (closed := liveness_check.click_refusal(request, user.id, job_id)) is not None:
        raise _error(status.HTTP_409_CONFLICT, closed)
    at = deps.clock(request)
    kinds = [Kind(kind) for kind in dict.fromkeys(body.kinds)]
    made: list[tuple[Kind, DocumentGeneration]] = []
    runs: list[uuid.UUID] = []
    both = Kind.cv in kinds and Kind.letter in kinds
    try:
        # Lock 1 before lock 2 (the base CV a click may build, `writer_base.click_wait`), lock 2
        # before lock 3 (store.py), as a render storing this CV and a move to applied.
        store.lock_person(db, user.id)
        if store.lock_posting(db, user.id, job_id) is None:
            raise _error(status.HTTP_404_NOT_FOUND, POSTING_NOT_FOUND)
        # No base CV yet: one is built from the profile first, and the CV waits for it (D29).
        reason = writer_base.click_wait(db, user.id, at)
        cv_base = body.base_document_id
        for kind in kinds:
            document = store.get_or_create(
                db,
                user.id,
                job_id,
                kind,
                at=at,
                base_document_id=cv_base if kind == Kind.cv else None,
            )
            if kind == Kind.letter and both:
                _give_way(db, document, at, follows=True)
                continue  # the letter follows the new CV (generate.start_letter)
            _give_way(db, document, at)
            base_id = cv_base if kind == Kind.cv else None
            if kind == Kind.letter:
                cv = next(
                    (
                        found
                        for found in store.posting_documents(db, user.id, job_id)
                        if found.kind == Kind.cv
                    ),
                    None,
                )
                base_id = cv.base_document_id if cv is not None else None
            figures = estimate.for_posting(db, user.id, job_id, base_id)
            stored: dict[str, Any] = dict(estimate.as_json(figures))
            if kind == Kind.cv and both:
                stored[generate.LETTER_FOLLOWS] = True
            if kind == Kind.cv and body.script:
                stored[generate.LETTER_SCRIPT] = f"documents_letter_{body.script}"
            generation = generate.create(
                db,
                document,
                GenerationMode.posting,
                GenerationTrigger.click,
                base_id,
                stored,
                figures.ceiling,
                at=at,
                status=GenerationStatus.waiting if reason else GenerationStatus.queued,
                wait_reason=reason,
            )
            made.append((kind, generation))
        if reason is None:
            for kind, generation in made:
                script = f"documents_{kind.value}_{body.script}" if body.script else None
                try:
                    runs.append(generate.start(db, generation, at=at, script=script).id)
                except agent_runs.ActiveRunsError:
                    break  # stays queued: the tick starts it
        out = WriteOut(
            generations=[
                WrittenOut(id=g.id, kind=k, status=g.status, wait_reason=g.wait_reason)
                for k, g in made
            ]
        )
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, BASE_NOT_FOUND) from None
    except store.GenerationOpenError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, RUNNING) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, BUSY) from None
    except HTTPException:
        db.rollback()
        raise
    for run_id in runs:
        try:
            anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, run_id)
        except queues.QueueUnavailableError:
            logger.warning("documents.write.run_not_queued", extra={"run_id": str(run_id)})
    logger.info(
        "documents.write",
        extra={"job_id": str(job_id), "generations": len(made), "started": len(runs)},
    )
    return out


# --- Make it fit --------------------------------------------------------------------------------


class FitOut(BaseModel):
    id: uuid.UUID
    status: GenerationStatus
    wait_reason: GenerationWait | None
    error: str | None


@router.post(
    "/api/documents/{document_id}/fit",
    response_model=FitOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"description": "Not the person's document"},
        409: {"description": "A generation is open, or a check refuses: the sentence says which"},
        422: {"description": "A letter or a base CV: no shortening pass"},
    },
)
def make_it_fit(document_id: uuid.UUID, user: CurrentUser, request: Request, db: Db) -> FitOut:
    try:
        generation, run = generate.make_fit(db, user.id, document_id, at=deps.clock(request))
        out = FitOut(
            id=generation.id,
            status=generation.status,
            wait_reason=generation.wait_reason,
            error=generation.error,
        )
        store.commit(db)
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, DOCUMENT_NOT_FOUND) from None
    except store.GenerationOpenError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, RUNNING) from None
    except store.DocumentBusyError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, BUSY) from None
    except generate.FitRefusedError as refused:
        db.rollback()
        code, sentence = FIT_REFUSED[refused.reason]
        logger.info(
            "documents.fit.refused",
            extra={"document_id": str(document_id), "reason": refused.reason},
        )
        raise _error(code, sentence) from None
    try:
        anyio.from_thread.run(generate.enqueue_run, request.app.state.queue, run.id)
    except queues.QueueUnavailableError:
        logger.warning("documents.fit.run_not_queued", extra={"run_id": str(run.id)})
    return out
