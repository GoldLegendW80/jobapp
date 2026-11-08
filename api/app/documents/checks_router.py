"""The keyword coverage of a document and the writing rules its chat keeps (owner, 2026-11-07,
"build all" of the career-ops analysis; SPEC-documents.md, "Keyword coverage" and "The chat, and
keeping a rule"). Included by `routers.py` before D6's `/api/documents/{document_id}`.

- `GET /api/documents/{id}/coverage`: the requirement terms of the posting that the CV draft
  names and those it does not (`coverage.for_document`), at the draft's revision; empty lists
  for a letter, a base CV, a posting with no requirement line or a person with no words to match.
- `POST /api/documents/{id}/messages/{message_id}/rule {text}`: keep a rule from one of the
  person's own messages of this document's chat (`writing_rules.keep`); `kept` is false when the
  writing rules held it already. 404 for another person's document or message, or an answer of
  the assistant; 422 for an empty or long rule, or one that states a fact; 409 when the facts
  section cannot take it.

Every answer is `Cache-Control: private, no-store`. Logs carry nothing of a text.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.documents import coverage, store, writing_rules
from app.documents.models import DocumentMessage, Role
from app.identity import deps
from app.identity.models import User

PRIVATE = {"Cache-Control": "private, no-store"}
NOT_FOUND = "Document not found."
MESSAGE_NOT_FOUND = "Message not found."
REFUSED = {
    "empty": "Write the rule first.",
    "long": f"A rule is at most {writing_rules.RULE_MAX} characters.",
    "fact": (
        "A writing rule says how to write, not what is true of you. "
        "Put facts about you in your profile."
    ),
}
FACTS_FULL = "Your profile's facts cannot take this rule: {reason}"


def _private(response: Response) -> None:
    response.headers.update(PRIVATE)


Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail, headers=PRIVATE)


class CoverageOut(BaseModel):
    draft_revision: int
    terms: list[str]
    missing: list[str]


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, Field(max_length=2_000)]


class RuleOut(BaseModel):
    rule: str
    kept: bool


@router.get("/api/documents/{document_id}/coverage", response_model=CoverageOut)
def read_coverage(document_id: uuid.UUID, user: CurrentUser, db: Db) -> CoverageOut:
    document = store.get(db, user.id, document_id)
    if document is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    found = coverage.for_document(db, document)
    return CoverageOut(
        draft_revision=document.draft_revision, terms=list(found.terms), missing=list(found.missing)
    )


@router.post(
    "/api/documents/{document_id}/messages/{message_id}/rule",
    response_model=RuleOut,
    responses={
        404: {"description": "Not the person's document, or not one of their messages"},
        409: {"description": "The facts section cannot take the rule"},
        422: {"description": "Empty, too long, or a fact"},
    },
)
def keep_rule(
    document_id: uuid.UUID,
    message_id: int,
    body: RuleIn,
    user: CurrentUser,
    request: Request,
    db: Db,
) -> RuleOut:
    if store.get(db, user.id, document_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    message = db.scalar(
        sa.select(DocumentMessage.id).where(
            DocumentMessage.id == message_id,
            DocumentMessage.document_id == document_id,
            DocumentMessage.user_id == user.id,
            DocumentMessage.role == Role.user,
        )
    )
    if message is None:
        raise _error(status.HTTP_404_NOT_FOUND, MESSAGE_NOT_FOUND)
    try:
        kept = writing_rules.keep(db, user.id, body.text, deps.clock(request))
    except writing_rules.RuleRefusedError as refused:
        db.rollback()
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, REFUSED[refused.reason]) from None
    except writing_rules.FactsFullError as full:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, FACTS_FULL.format(reason=full.message)) from None
    db.commit()
    return RuleOut(rule=kept.rule, kept=kept.proposal_id is not None)
