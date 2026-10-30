"""/api/jobs/boards: what Find jobs' "Company job boards" needs besides the saved list, which is
the search section's `company_boards` (PUT /api/profile/search).

- GET /api/jobs/boards/starter: the starter list (catalog.seed), offered ticked while the person
  has never saved a list.
- POST /api/jobs/boards/detect `{url}`: the ATS and board id a pasted board address names, and a
  company name made from the id; 422 with a sentence when it names none. Nothing is fetched.
- GET /api/jobs/boards/suggestions: boards the caller's own postings point to and their list
  does not hold (suggest.py). Nothing is fetched.

Behind the app's sign in and CSRF checks like every /api route; mounted before the jobs router so
/api/jobs/boards is never read as a job id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import sweep
from app.jobs.boards import ats, catalog, suggest
from app.profile.schemas import BoardAts

router = APIRouter(prefix="/api/jobs/boards", tags=["jobs"])
CurrentUser = Annotated[User, Depends(deps.current_user)]
_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
NOT_A_BOARD = (
    "This address is not a company job board the app reads. Paste the board of an Ashby, "
    "Greenhouse, Lever, SmartRecruiters, Workable or Recruitee page, such as "
    "https://jobs.ashbyhq.com/example."
)
EU_BOARD = (
    "Boards on the European hosts of Greenhouse and Lever are not read yet. Add a board on "
    "another ATS, or this company's board on the main host if it has one."
)


class BoardOut(BaseModel):
    company: str
    ats: BoardAts
    board: str
    enabled: bool = True
    url: str  # the public board, for a link


class StarterOut(BaseModel):
    checked_on: str
    boards: list[BoardOut]


class DetectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: Annotated[str, Field(min_length=1, max_length=2000)]


class SuggestionOut(BoardOut):
    postings: int


class SuggestionsOut(BaseModel):
    boards: list[SuggestionOut]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _out(company: str, kind: str, board: str, enabled: bool = True) -> BoardOut:
    return BoardOut(
        company=company,
        ats=kind,  # type: ignore[arg-type]  # one of BOARD_ATS, checked by the model
        board=board,
        enabled=enabled,
        url=ats.board_page(kind, board),
    )


@router.get("/starter", response_model=StarterOut, responses=_401)
async def starter(user: CurrentUser) -> StarterOut:
    del user
    return StarterOut(
        checked_on="2026-10-30",
        boards=[_out(b.company, b.ats, b.board) for b in catalog.seed()],
    )


@router.post("/detect", response_model=BoardOut, responses=_401)
async def detect(body: DetectIn, user: CurrentUser) -> BoardOut:
    del user
    found = ats.detect(body.url)
    if found is None:
        host = body.url.strip().split("://")[-1].split("/")[0].lower()
        detail = EU_BOARD if host in ats.EU_HOSTS else NOT_A_BOARD
        raise HTTPException(status_code=422, detail=detail)
    return _out(found.company, found.ats, found.board)


def _suggestions(sessions: sessionmaker[Session], user: User) -> SuggestionsOut:
    with sessions() as db:
        search = sweep.user_search(db, user.id)
        listed = (search.company_boards or []) if search is not None else []
        found = suggest.of_user(db, user.id, listed)
        db.rollback()
    return SuggestionsOut(
        boards=[
            SuggestionOut(**_out(company, b.ats, b.board).model_dump(), postings=count)
            for b, company, count in found
        ]
    )


@router.get(
    "/suggestions",
    response_model=SuggestionsOut,
    responses=_401,
    dependencies=[Depends(_no_store)],
)
async def suggestions(user: CurrentUser, request: Request) -> SuggestionsOut:
    sessions: sessionmaker[Session] = request.app.state.sessionmaker
    return await run_in_threadpool(_suggestions, sessions, user)
