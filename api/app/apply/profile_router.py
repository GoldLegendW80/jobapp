"""Owned site profile runs, field choices and private screenshot evidence."""

import contextlib
import importlib.util
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.apply import profile_sources, site_profile
from app.apply.models import SiteProfileRun, SiteProfileShot, SiteRunState
from app.apply.profile_schemas import (
    FieldIn,
    FieldOut,
    RunDetail,
    RunList,
    RunOut,
    SectionOut,
    ShotOut,
    StartIn,
)
from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.queue import APPLY, QueueUnavailableError

Db = Annotated[Session, Depends(get_session)]
UserDep = Annotated[User, Depends(deps.current_user)]


def private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(tags=["site-profiles"], dependencies=[Depends(private)])


def owned(db: Session, user_id: uuid.UUID, run_id: uuid.UUID) -> SiteProfileRun:
    try:
        return site_profile.lock(db, user_id, run_id)
    except site_profile.NotFoundError:
        raise HTTPException(404, "No such profile run.") from None


def refusal(exc: site_profile.RefusedError) -> JSONResponse:
    return JSONResponse(
        {"code": exc.code, "detail": str(exc)},
        status_code=409,
        headers={"Cache-Control": "no-store"},
    )


def detail(db: Session, run: SiteProfileRun) -> RunDetail:
    parts = []
    for section in site_profile.sections(db, run):
        fields = site_profile.fields(db, run, section.section)
        screenshots = list(
            db.scalars(
                sa.select(SiteProfileShot)
                .where(
                    SiteProfileShot.run_id == run.id,
                    SiteProfileShot.user_id == run.user_id,
                    SiteProfileShot.section == section.section,
                )
                .order_by(SiteProfileShot.created_at)
            )
        )
        parts.append(
            SectionOut(
                section=section.section,
                label=section.label,
                position=section.position,
                state=section.state,
                error_code=section.error_code,
                values_hash=profile_sources.value_hash(section, fields).hex(),
                fields=[FieldOut.model_validate(f) for f in fields],
                screenshots=[ShotOut.model_validate(s) for s in screenshots],
                events=section.events,
            )
        )
    return RunDetail(**RunOut.model_validate(run).model_dump(), sections=parts)


async def enqueue(request: Request, run_id: uuid.UUID) -> None:
    with contextlib.suppress(QueueUnavailableError):
        await request.app.state.queue.enqueue(
            "site_profile_run", str(run_id), queue=APPLY, job_id=site_profile.job_id(run_id)
        )


@router.post("/api/site-profiles", response_model=RunDetail, status_code=201)
async def start(body: StartIn, request: Request, user: UserDep, db: Db):
    try:
        run = site_profile.start(
            db,
            user.id,
            body.url,
            body.autosave_confirmed,
            now=deps.clock(request),
            stub_base=request.app.state.settings.apply_stub_base_url,
        )
    except site_profile.RefusedError as exc:
        return refusal(exc)
    db.commit()
    await enqueue(request, run.id)
    return detail(db, run)


@router.get("/api/site-profiles", response_model=RunList)
def listing(
    user: UserDep,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    runs = list(
        db.scalars(
            sa.select(SiteProfileRun)
            .where(SiteProfileRun.user_id == user.id)
            .order_by(SiteProfileRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return RunList(items=[RunOut.model_validate(r) for r in runs])


@router.get("/api/site-profiles/{run_id}", response_model=RunDetail)
def get(run_id: uuid.UUID, user: UserDep, db: Db):
    return detail(db, owned(db, user.id, run_id))


@router.put("/api/site-profiles/{run_id}/sections/{section}/fields/{key}", response_model=RunDetail)
def edit(
    run_id: uuid.UUID,
    section: str,
    key: str,
    body: FieldIn,
    request: Request,
    user: UserDep,
    db: Db,
):
    owned(db, user.id, run_id)
    try:
        site_profile.edit(
            db,
            user.id,
            run_id,
            section,
            key,
            body.model_dump(exclude_unset=True),
            deps.clock(request),
        )
    except site_profile.NotFoundError:
        raise HTTPException(404, "No such profile field.") from None
    except site_profile.RefusedError as exc:
        return refusal(exc)
    db.flush()
    return detail(db, owned(db, user.id, run_id))


@router.post("/api/site-profiles/{run_id}/confirm-autosave", response_model=RunDetail)
async def confirm_autosave(run_id: uuid.UUID, request: Request, user: UserDep, db: Db):
    run = owned(db, user.id, run_id)
    if run.state is not SiteRunState.needs_person or run.needs != "autosave_confirmation":
        return refusal(site_profile.RefusedError("not_ready"))
    run.autosave_confirmed, run.state, run.needs = True, SiteRunState.queued, None
    run.updated_at = deps.clock(request)
    db.commit()
    await enqueue(request, run.id)
    return detail(db, run)


@router.post("/api/site-profiles/{run_id}/cancel", response_model=RunDetail)
def cancel(run_id: uuid.UUID, request: Request, user: UserDep, db: Db):
    run = owned(db, user.id, run_id)
    run.state, run.updated_at = SiteRunState.cancelled, deps.clock(request)
    for part in site_profile.sections(db, run):
        site_profile.event(part, "cancelled", run.updated_at)
    return detail(db, run)


@router.get("/api/site-profiles/{run_id}/sections/{section}/screenshots/{shot_id}")
def image(run_id: uuid.UUID, section: str, shot_id: uuid.UUID, user: UserDep, db: Db):
    owned(db, user.id, run_id)
    jpeg = db.scalar(
        sa.select(SiteProfileShot.jpeg).where(
            SiteProfileShot.id == shot_id,
            SiteProfileShot.run_id == run_id,
            SiteProfileShot.user_id == user.id,
            SiteProfileShot.section == section,
        )
    )
    if jpeg is None:
        raise HTTPException(404, "No such screenshot.")
    return Response(bytes(jpeg), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


if importlib.util.find_spec("app.browser.later_handoff") is not None:
    from app.browser.later_handoff import register

    register(
        "site_profile",
        site_profile.address,
        site_profile.resume,
        job_name="site_profile_run",
        job_id=site_profile.job_id,
    )
