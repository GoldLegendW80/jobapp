"""The rehearsal's answer (apply A13a; SPEC-apply, "Modes", Dry run).

- POST /api/applications/{application_id}/rehearsal `{answer: "yes" | "no"}`: the person's
  answer to "Does this look right?" on a Full auto rehearsal (`rehearsed`). Yes moves it to the
  review queue, `ready` for a one click Approve (`full_auto.confirm_rehearsal`); No keeps it
  `rehearsed` and Full auto in rehearsal. 409 `not_rehearsal` for anything else, 409
  `rehearsal_taken` when another live application took the posting or its form since, 409 `busy`
  past a lock timeout; 404 for an application that is not the person's.

The user always comes from the session (CSRF on the write, by the middleware);
`Cache-Control: no-store`.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.apply import approval_router, full_auto
from app.apply.router import NO_STORE, ApplicationOut
from app.db import get_session
from app.identity import deps
from app.identity.models import User


def _no_store(response: Response) -> None:
    response.headers.update(NO_STORE)


router = APIRouter(tags=["apply"], dependencies=[Depends(_no_store)])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]


class RehearsalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Literal["yes", "no"]


@router.post(
    "/api/applications/{application_id}/rehearsal",
    response_model=ApplicationOut,
    responses={404: {}, 409: {}},
)
def answer_rehearsal(
    application_id: str, body: RehearsalIn, request: Request, user: CurrentUser, db: Db
) -> ApplicationOut | JSONResponse:
    """Does this look right? Yes sends it to the review queue; No keeps Full auto rehearsing."""
    act = full_auto.confirm_rehearsal if body.answer == "yes" else full_auto.decline_rehearsal
    return approval_router._person(request, user, db, application_id, act)
