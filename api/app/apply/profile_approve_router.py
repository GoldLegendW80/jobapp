"""Explicit approval and skip actions for one owned profile section."""

import contextlib
import uuid

from fastapi import APIRouter, Depends, Request

from app.apply import profile_gate, site_profile
from app.apply.profile_router import Db, UserDep, detail, owned, private, refusal
from app.apply.profile_schemas import ApproveIn, RunDetail
from app.identity import deps
from app.queue import APPLY, QueueUnavailableError

router = APIRouter(tags=["site-profiles"], dependencies=[Depends(private)])


@router.post("/api/site-profiles/{run_id}/sections/{section}/approve", response_model=RunDetail)
async def approve(
    run_id: uuid.UUID, section: str, body: ApproveIn, request: Request, user: UserDep, db: Db
):
    run = owned(db, user.id, run_id)
    try:
        authorization = profile_gate.authorise_section(
            db, user.id, run_id, section, bytes.fromhex(body.values_hash), deps.clock(request)
        )
    except site_profile.NotFoundError:
        from fastapi import HTTPException

        raise HTTPException(404, "No such profile section.") from None
    except site_profile.RefusedError as exc:
        return refusal(exc)
    db.commit()
    with contextlib.suppress(QueueUnavailableError):
        await request.app.state.queue.enqueue(
            "site_profile_save",
            str(run_id),
            section,
            str(authorization.id),
            queue=APPLY,
            job_id=f"site-profile-save:{authorization.id}",
        )
    return detail(db, run)


@router.post("/api/site-profiles/{run_id}/sections/{section}/skip", response_model=RunDetail)
def skip(run_id: uuid.UUID, section: str, request: Request, user: UserDep, db: Db):
    run = owned(db, user.id, run_id)
    try:
        part = site_profile.section_of(db, run, section)
    except site_profile.NotFoundError:
        from fastapi import HTTPException

        raise HTTPException(404, "No such profile section.") from None
    if run.state not in (
        site_profile.SiteRunState.ready,
        site_profile.SiteRunState.done,
    ) or part.state not in ("ready", "left"):
        return refusal(site_profile.RefusedError("not_ready"))
    part.state = "skipped"
    site_profile.event(part, "skipped", deps.clock(request))
    site_profile.finish_state(db, run, deps.clock(request))
    db.commit()
    return detail(db, run)
