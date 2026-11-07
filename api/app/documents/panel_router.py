"""The hiring panel of a document, as the person sees it (task D29; SPEC-documents.md, "The
hiring panel", "Pages").

- `GET /api/documents/{id}/panel`: the latest panel of one of the person's documents
  (`panel.view`): one line per reviewer (its verdict, the line that hurt most), the numbered
  mistakes with their fixes, the screen before and after, the questions with the profile section
  that would hold each fact, the reviewer closest to rejecting it, what failed, and the tokens its
  runs spent. `panel` is null when no panel ran for it. 404 for another person's document.

The answer is the person's own text (their document's quoted lines): `Cache-Control: private,
no-store`, like every route of the module. Logs carry ids only.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.documents import panel, store
from app.documents.router import NOT_FOUND, CurrentUser, Db, _error, _private

router = APIRouter(tags=["documents"], dependencies=[Depends(_private)])


class PanelOut(BaseModel):
    panel: dict[str, Any] | None
    input_tokens: int = 0
    output_tokens: int = 0


@router.get("/api/documents/{document_id}/panel", response_model=PanelOut)
def get_panel(document_id: uuid.UUID, user: CurrentUser, db: Db) -> PanelOut:
    if store.get(db, user.id, document_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    found = panel.latest(db, user.id, document_id)
    if found is None:
        return PanelOut(panel=None)
    spent_in, spent_out = panel.tokens(db, found)
    return PanelOut(panel=panel.view(found), input_tokens=spent_in, output_tokens=spent_out)
