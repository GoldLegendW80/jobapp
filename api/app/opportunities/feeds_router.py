"""/api/opportunities/feeds: the catalogue of freelance and project sources (opportunities O7).

GET answers `{supported: [{kind, name, description, attribution, terms_url, checked_on}],
not_supported: [{kind, name, reason, terms_url, checked_on}]}` (sources/catalogue.py). It is the
same for everyone and holds nothing of the caller's; it still needs a session, like every route
of the app, and the person's own choice of feeds is `search.feeds` of the profile.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.opportunities.sources import catalogue

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])

CurrentUser = Annotated[User, Depends(deps.current_user)]


class SupportedOut(BaseModel):
    kind: str
    name: str
    description: str
    attribution: str
    terms_url: str
    checked_on: str


class NotSupportedOut(BaseModel):
    kind: str
    name: str
    reason: str
    terms_url: str
    checked_on: str


class CatalogueOut(BaseModel):
    supported: list[SupportedOut]
    not_supported: list[NotSupportedOut]


@router.get(
    "/feeds",
    response_model=CatalogueOut,
    responses={401: {"model": ErrorOut}},
)
def feeds(_: CurrentUser, response: Response) -> CatalogueOut:
    response.headers["Cache-Control"] = "private, max-age=300"
    return CatalogueOut.model_validate(catalogue.as_json())
