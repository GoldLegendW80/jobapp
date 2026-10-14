"""The person's "Mark as sent" (task D14; spec, "API"), `POST .../versions/{n}/mark-sent`.

For an application made outside the app (an email, a form the app never saw). It freezes the
version with the reason `marked_sent`, whatever the posting's status is, and moves nothing else.
Marking a frozen version again answers 200 with the version as it is: the first reason and time
are the fact kept. A version without a PDF is refused (409): nothing was sent that a person
could have received, and a frozen version can never receive its PDF. The posting's other
version, the document's draft and the status are untouched.

Same rules as the rest of the module's api: another person's document or version, and a base
CV, answer 404; the answers carry `Cache-Control: private, no-store`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from app.documents import freeze, store
from app.documents.models import FreezeReason, Scope
from app.documents.router import (
    NOT_FOUND,
    VERSION_NOT_FOUND,
    CurrentUser,
    Db,
    VersionSummary,
    _error,
    _private,
    _summary,
)
from app.identity import deps

NOT_RENDERED = "This version has no PDF yet, so it cannot have been sent."

router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])


@router.post(
    "/api/documents/{document_id}/versions/{number}/mark-sent", response_model=VersionSummary
)
def mark_sent(
    document_id: uuid.UUID,
    number: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    request: Request,
    db: Db,
) -> VersionSummary:
    """Freeze the version as sent by the person."""
    document = store.get(db, user.id, document_id)
    if document is None or document.scope is not Scope.posting:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    found = store.version(db, user.id, document_id, number)
    if found is None:
        raise _error(status.HTTP_404_NOT_FOUND, VERSION_NOT_FOUND)
    if not freeze.rendered(found):
        raise _error(status.HTTP_409_CONFLICT, NOT_RENDERED)
    try:
        freeze.freeze(db, user.id, found.id, FreezeReason.marked_sent, deps.clock(request))
    except store.NotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, VERSION_NOT_FOUND) from None
    summary = VersionSummary(**_summary(found))
    store.commit(db)
    return summary
