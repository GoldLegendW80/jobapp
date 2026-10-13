"""/api/jobs: the caller's job list with its counts and fit histogram, one job in detail, a file
download, and the import of a jobs document.

Every route needs a session (the app-wide default deny) and acts on the caller's rows only: the
user id comes from `current_user`, never from the request, and another user's job or file
answers the same 404 as one that does not exist.

GET /api/jobs, query parameters (all optional, any combination):
- status, type: repeat the parameter for several values (`?status=found&status=applied`); an
  unknown value is a 422.
- company: case-insensitive substring of the company.
- q: full text over company, title, location and notes, in web search syntax (words are ANDed,
  "quoted phrase", `or`, `-word`). Postgres 'simple' configuration: case is ignored, accents
  are not ("developpeur" does not find "développeur"), words are matched whole (no stemming,
  no prefix: "engineer" does not find "engineering").
- fit_min, fit_max: 0 to 100, inclusive; either one leaves out unscored rows. fit_min above
  fit_max is a 422.
- found_from, found_to: ISO dates, inclusive; from after to is a 422.
- sort: found_on (default: newest found first), fit (best first, unscored last), company (A to
  Z), status (pipeline order), updated (last changed first). Ties are broken by fit, then id.
- limit: 1 to 200 (default 50); cursor: the next_cursor of the previous page, with the same
  sort (a cursor of another sort, or a damaged one, is a 422).
The answer holds the page, next_cursor, total (rows matching every filter), counts_by_status
(every filter but status, so tabs can show their counts) and fit_histogram (every filter).

GET /api/jobs/places: the same filters (not sort, limit, cursor); the filtered rows grouped by
the places their location names, for the map, with the locations no place matches (places.py).

GET /api/jobs/{id}/files/{file_id}: the bytes with their stored type, `inline` for a PDF (the
browser shows it) and `attachment` for anything else, `X-Content-Type-Options: nosniff`,
`Cache-Control: private, no-cache` and a strong ETag (the SHA-256), 304 on If-None-Match.

POST /api/jobs/import: the import document as the JSON body, IMPORT_HTTP_MAX_BYTES (10 MiB, as
Caddy) at most, 413 above. Larger imports (Harald's ~18 MB) go through the CLI inside the api
container: `./deploy.sh jobs import <email>`.
"""

import re
import secrets
import time
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session, read_snapshot
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import places, service
from app.jobs import undo as undo_tokens
from app.jobs.importer import kind
from app.jobs.models import ContractType, Job, JobFile, JobStatus
from app.jobs.schemas import (
    IMPORT_HTTP_MAX_BYTES,
    BulkIn,
    BulkItemOut,
    BulkOut,
    DocumentError,
    DuplicateOut,
    EventOut,
    FileOut,
    FitBucket,
    FitHistogram,
    ImportSummaryOut,
    JobCreateIn,
    JobDetail,
    JobListItem,
    JobListPage,
    JobPatchIn,
    JobsImportDocument,
    MissingOut,
    RescoreIn,
    RescoreOut,
    StaleOut,
    UndoIn,
    UndoOut,
    parse_document,
)
from app.jobs.service import InvalidCursorError, JobFilters, JobSort

NO_STORE = {"Cache-Control": "no-store"}
FILE_CACHE = "private, no-cache"
LIST_LIMIT_MAX = 200
EVENTS_IN_DETAIL = 50
Q_MAX = 200
NOT_FOUND = "Job not found."
FILE_NOT_FOUND = "File not found."
TOO_LARGE = (
    "The import file is larger than 10 MiB. Import it with the CLI instead "
    "(./deploy.sh jobs import <email>)."
)


def _no_store(response: Response) -> None:
    """Job data is private: no browser or proxy keeps a copy."""
    response.headers.update(NO_STORE)


router = APIRouter(prefix="/api/jobs", tags=["jobs"])

Db = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User, Depends(deps.current_user)]

_401 = {401: {"model": ErrorOut, "description": "Not signed in"}}
_404 = {404: {"model": ErrorOut, "description": "No such job (or not yours)"}}
_422 = {422: {"model": ErrorOut, "description": "Invalid parameters"}}


def _invalid(field: str, message: str) -> RequestValidationError:
    """A 422 formatted by the app's handler like every other one: `field: message`."""
    return RequestValidationError(
        [{"type": "value_error", "loc": ("query", field), "msg": message}]
    )


def _item(job: Job, file_count: int) -> JobListItem:
    data = {name: getattr(job, name) for name in JobListItem.model_fields if name != "file_count"}
    return JobListItem(**data, file_count=file_count)


def _filters(
    status_: Annotated[list[JobStatus] | None, Query(alias="status")] = None,
    type_: Annotated[list[ContractType] | None, Query(alias="type")] = None,
    company: Annotated[str | None, Query(max_length=Q_MAX)] = None,
    q: Annotated[str | None, Query(max_length=Q_MAX)] = None,
    fit_min: Annotated[int | None, Query(ge=0, le=100)] = None,
    fit_max: Annotated[int | None, Query(ge=0, le=100)] = None,
    found_from: date | None = None,
    found_to: date | None = None,
) -> JobFilters:
    """The list filters of the query, shared by GET /api/jobs and GET /api/jobs/places."""
    if fit_min is not None and fit_max is not None and fit_min > fit_max:
        raise _invalid("fit_min", "fit_min must not be above fit_max")
    if found_from is not None and found_to is not None and found_from > found_to:
        raise _invalid("found_from", "found_from must not be after found_to")
    return JobFilters(
        statuses=tuple(dict.fromkeys(status_ or ())),
        contract_types=tuple(dict.fromkeys(type_ or ())),
        company=company,
        q=q,
        fit_min=fit_min,
        fit_max=fit_max,
        found_from=found_from,
        found_to=found_to,
    )


Filters = Annotated[JobFilters, Depends(_filters)]


@router.get(
    "", response_model=JobListPage, responses={**_401, **_422}, dependencies=[Depends(_no_store)]
)
def list_jobs(
    user: CurrentUser,
    db: Db,
    filters: Filters,
    sort: JobSort = JobSort.found_on,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = 50,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
) -> JobListPage:
    """One page of the caller's jobs, with the counts and the histogram of the filtered set."""
    read_snapshot(db)  # the page, the counts and the histogram are one moment of the table
    try:
        page = service.list_page(db, user.id, filters, sort, limit, cursor)
    except InvalidCursorError as exc:
        raise _invalid("cursor", str(exc)) from None
    counts = service.status_counts(db, user.id, filters)
    buckets, unscored = service.fit_histogram(db, user.id, filters)
    selected = filters.statuses or tuple(JobStatus)
    return JobListPage(
        items=[_item(job, files) for job, files in page.rows],
        next_cursor=page.next_cursor,
        total=sum(counts[s] for s in selected),
        counts_by_status=counts,
        fit_histogram=FitHistogram(
            buckets=[
                FitBucket(low=i * 10, high=100 if i == len(buckets) - 1 else i * 10 + 9, count=n)
                for i, n in enumerate(buckets)
            ],
            unscored=unscored,
        ),
    )


@router.get(
    "/places",
    response_model=places.PlacesOut,
    responses={**_401, **_422},
    dependencies=[Depends(_no_store)],
)
def jobs_places(user: CurrentUser, db: Db, filters: Filters) -> places.PlacesOut:
    """The caller's jobs under the list filters, grouped by place for the map (see places.py)."""
    return places.places_map(db, user.id, filters)


async def _import_body(request: Request) -> bytes:
    """The body, refused with 413 as soon as it is known to exceed the limit: from
    Content-Length when declared, else while it streams in (nothing larger is buffered)."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > IMPORT_HTTP_MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > IMPORT_HTTP_MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def _validation_error(exc: ValidationError) -> RequestValidationError:
    errors = []
    for error in exc.errors(include_input=False, include_url=False):
        message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
        errors.append({**error, "loc": ("body", *error.get("loc", ())), "msg": message})
    return RequestValidationError(errors)


@router.post(
    "/import",
    response_model=ImportSummaryOut,
    responses={
        **_401,
        413: {"model": ErrorOut, "description": "The document is larger than 10 MiB"},
        **_422,
    },
    dependencies=[Depends(_no_store)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": JobsImportDocument.model_json_schema()}},
        }
    },
)
def import_jobs(
    raw: Annotated[bytes, Depends(_import_body)], user: CurrentUser, request: Request, db: Db
) -> ImportSummaryOut:
    """Load a jobs import document into the caller's jobs (idempotent, see service.import_jobs),
    in one transaction."""
    try:
        document = parse_document(raw, IMPORT_HTTP_MAX_BYTES)
    except DocumentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    except ValidationError as exc:
        raise _validation_error(exc) from None
    summary = service.import_jobs(db, user.id, document.jobs, deps.clock(request))
    db.commit()
    return ImportSummaryOut(
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        skipped=summary.skipped,
        files_added=summary.files_added,
        files_replaced=summary.files_replaced,
        files_unchanged=summary.files_unchanged,
        files_kept=summary.files_kept,
        fits_manual=summary.fits_manual,
        fits_checked=summary.fits_checked,
        warnings=[*document.warnings, *summary.warnings],
    )


@router.get(
    "/{job_id}",
    response_model=JobDetail,
    responses={**_401, **_404},
    dependencies=[Depends(_no_store)],
)
def read_job(job_id: str, user: CurrentUser, db: Db) -> JobDetail:
    """One of the caller's jobs: every field, the posting text, the files (without their bytes)
    and the newest events."""
    read_snapshot(db)  # the events and their count are one moment, with the job
    return _detail(db, user, _job_or_404(db, user, job_id))


def _detail(db: Session, user: User, job: Job) -> JobDetail:
    files = service.list_files(db, user.id, job.id)
    events, events_total = service.recent_events(db, user.id, job.id, EVENTS_IN_DETAIL)
    item = _item(job, len(files))
    return JobDetail(
        **item.model_dump(),
        posting_text=job.posting_text,
        legacy=job.legacy,
        files=[FileOut.model_validate(f) for f in files],
        events=[EventOut.model_validate(e) for e in events],
        events_total=events_total,
    )


_UUID = re.compile(r"^[0-9a-fA-F]{8}-?([0-9a-fA-F]{4}-?){3}[0-9a-fA-F]{12}$")


def _uuid_or_404(value: str, detail: str) -> uuid.UUID:
    """A path id that is not a UUID names nothing: 404, as a missing row."""
    if not _UUID.match(value):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return uuid.UUID(value)


def _job_or_404(db: Session, user: User, job_id: str) -> Job:
    job = service.get_job(db, user.id, _uuid_or_404(job_id, NOT_FOUND))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return job


def _matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison of If-None-Match with the current ETag (`*` matches any)."""
    if if_none_match is None:
        return False
    return any(
        tag.strip() == "*" or tag.strip().removeprefix("W/") == etag
        for tag in if_none_match.split(",")
    )


def _disposition(file: JobFile, job: Job) -> str:
    kind = "inline" if file.mime == "application/pdf" else "attachment"
    return f'{kind}; filename="{job.slug}-{file.name}"'


@router.get(
    "/{job_id}/files/{file_id}",
    responses={
        **_401,
        200: {"content": {"application/pdf": {}, "application/json": {}}, "description": "File"},
        304: {"description": "If-None-Match matches the file"},
        404: {"model": ErrorOut, "description": "No such job or file (or not yours)"},
    },
    response_class=Response,
)
def download_file(job_id: str, file_id: str, user: CurrentUser, request: Request, db: Db) -> Any:
    """The file's bytes with its stored type."""
    job = _job_or_404(db, user, job_id)
    file = service.get_file(db, user.id, job.id, _uuid_or_404(file_id, FILE_NOT_FOUND))
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=FILE_NOT_FOUND)
    etag = f'"{bytes(file.sha256).hex()}"'
    headers = {
        "Cache-Control": FILE_CACHE,
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    media_type = file.mime
    if media_type.startswith("text/") or media_type == "application/json":
        media_type = f"{media_type}; charset=utf-8"
    return Response(
        content=bytes(file.content),
        media_type=media_type,
        headers={**headers, "Content-Disposition": _disposition(file, job)},
    )


# --- J4: add, edit, bulk, undo, rescore ---------------------------------------------------------

RESCORE_BATCH = 100
RESCORE_MAX_ROWS = 2_000  # per call; the client calls again with `after` for the rest
RESCORE_BUDGET_S = 20.0  # per call, checked between batches
NO_SKILLS = "Your profile has no skills to score with yet. Add them in Profile, then rescore."
DUPLICATE = "This posting is already in your list."
STALE = "This job changed since you loaded it. Reload it and try again."
_409 = {
    409: {
        "model": DuplicateOut,
        "description": "Duplicate posting, stale version, or the job is being changed",
    }
}


def _duplicate(job: Job) -> JSONResponse:
    body = DuplicateOut(detail=DUPLICATE, existing_id=job.id, existing_slug=job.slug)
    return JSONResponse(status_code=409, content=body.model_dump(mode="json"), headers=NO_STORE)


def _missing(missing: list[uuid.UUID]) -> JSONResponse:
    shown = ", ".join(str(m) for m in missing[:5])
    more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
    noun = "job was" if len(missing) == 1 else "jobs were"
    body = MissingOut(detail=f"{len(missing)} {noun} not found: {shown}{more}.", missing=missing)
    return JSONResponse(status_code=404, content=body.model_dump(mode="json"), headers=NO_STORE)


def _undo_key(request: Request) -> bytes:
    """The undo signing key, from SECRET_KEY (required in production). Without one (development
    and tests) a random key of this process: its tokens die with it, which is harmless."""
    settings = request.app.state.settings
    if settings.secret_key is not None and settings.secret_key.get_secret_value():
        return undo_tokens.derive_key(settings.secret_key.get_secret_value().encode())
    state = request.app.state
    if not hasattr(state, "undo_secret"):
        state.undo_secret = secrets.token_bytes(32)
    return undo_tokens.derive_key(state.undo_secret)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=JobDetail,
    responses={**_401, **_409, **_422},
    dependencies=[Depends(_no_store)],
)
def add_job(body: JobCreateIn, user: CurrentUser, request: Request, db: Db) -> Any:
    """Add a posting by hand. Scored at once when it has a posting text and the profile has
    skills. 409 with the existing job when it is already in the list (same URL, tracking
    parameters and scheme aside, or same company, title and location)."""
    now = deps.clock(request)
    new = service.NewJob(
        company=body.company,
        title=body.title,
        location=body.location,
        url=body.url,
        contract_type=body.contract_type or kind(body.title, body.location, body.url or ""),
        source="manual",
        posting_text=body.posting_text,
    )
    try:
        job, _ = service.add_manual_job(db, user.id, new, now)
        db.commit()
    except service.DuplicateJobError as exc:
        db.rollback()
        return _duplicate(exc.job)
    except IntegrityError:
        # The same posting added at the same moment by another request: report that one.
        db.rollback()
        existing = service.find_duplicate(
            db, user.id, company=new.company, title=new.title, location=new.location, url=new.url
        )
        if existing is None:
            raise
        return _duplicate(existing)
    return _detail(db, user, job)


@router.patch(
    "/{job_id}",
    response_model=JobDetail,
    responses={**_401, **_404, **_409, **_422},
    dependencies=[Depends(_no_store)],
)
def edit_job(job_id: str, body: JobPatchIn, user: CurrentUser, request: Request, db: Db) -> Any:
    """Change a job's status, notes, fit (a number sets it by hand, null goes back to the
    computed one) or details. Each change is recorded as an event with its before and after.
    With `updated_at`, the edit applies only to that version of the job (409 otherwise)."""
    target = _uuid_or_404(job_id, NOT_FOUND)
    job = service.lock_job(db, user.id, target)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    sent = body.model_fields_set
    changes = service.JobChanges(
        fields={name: getattr(body, name) for name in service.EDITABLE_FIELDS if name in sent},
        status=body.status,
        notes=body.notes,
        set_fit="fit" in sent,
        fit=body.fit,
    )
    try:
        service.edit_job(
            db,
            job,
            changes,
            deps.clock(request),
            expected_updated_at=body.updated_at,
            skills=lambda: service.user_skills(db, user.id),
        )
        db.commit()
    except service.StaleJobError as exc:
        current = exc.job.updated_at
        db.rollback()
        content = StaleOut(detail=STALE, updated_at=current).model_dump(mode="json")
        return JSONResponse(status_code=409, content=content, headers=NO_STORE)
    except service.DuplicateJobError as exc:
        existing = exc.job
        db.rollback()
        return _duplicate(existing)
    except service.NothingToComputeError as exc:
        db.rollback()
        raise _invalid("fit", exc.reason) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE) from None
    return _detail(db, user, job)


@router.post(
    "/bulk",
    response_model=BulkOut,
    responses={
        **_401,
        404: {"model": MissingOut, "description": "Ids not found"},
        409: {"model": ErrorOut, "description": "A job is being changed"},
        **_422,
    },
    dependencies=[Depends(_no_store)],
)
def bulk(body: BulkIn, user: CurrentUser, request: Request, db: Db) -> Any:
    """Set the status of and/or append a note to up to 500 jobs, in one transaction: when one
    id is not the caller's, nothing changes and the 404 lists the missing ids. A status change
    returns an undo token, valid for 10 minutes."""
    now = deps.clock(request)
    try:
        outcome = service.bulk_update(
            db, user.id, body.ids, now, status=body.set_.status, notes_append=body.set_.notes_append
        )
    except service.MissingJobsError as exc:
        db.rollback()
        return _missing(exc.missing)
    token = (
        undo_tokens.sign(_undo_key(request), user.id, outcome.undo, now) if outcome.undo else None
    )
    results = [
        BulkItemOut(
            id=r.job.id, changed=r.changed, status=r.job.status, previous_status=r.previous_status
        )
        for r in outcome.results
    ]
    db.commit()
    changed = sum(r.changed for r in results)
    return BulkOut(
        results=results,
        changed=changed,
        unchanged=len(results) - changed,
        undo_token=token,
        undo_expires_at=undo_tokens.expires_at(now) if token else None,
    )


@router.post(
    "/bulk/undo",
    response_model=UndoOut,
    responses={
        **_401,
        400: {"model": ErrorOut, "description": "Invalid token"},
        409: {"model": ErrorOut, "description": "Nothing left to undo, or a job is being changed"},
        410: {"model": ErrorOut, "description": "The undo window has passed"},
    },
    dependencies=[Depends(_no_store)],
)
def bulk_undo(body: UndoIn, user: CurrentUser, request: Request, db: Db) -> UndoOut:
    """Put back the statuses a bulk change replaced, for the jobs not changed since. A token
    works once."""
    now = deps.clock(request)
    try:
        previous = undo_tokens.verify(_undo_key(request), body.token, user.id, now)
    except undo_tokens.ExpiredTokenError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This undo has expired: an undo is possible for 10 minutes.",
        ) from None
    except undo_tokens.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This undo token is not valid."
        ) from None
    outcome = service.undo_bulk(db, user.id, previous, now)
    if not outcome.restored:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nothing to undo: these jobs were already restored or changed since.",
        )
    db.commit()
    return UndoOut(restored=outcome.restored, skipped=outcome.skipped)


@router.post(
    "/rescore",
    response_model=RescoreOut,
    responses={
        **_401,
        404: {"model": MissingOut, "description": "Ids not found"},
        409: {"model": ErrorOut, "description": "The profile has no skills"},
        **_422,
    },
    dependencies=[Depends(_no_store)],
)
def rescore(body: RescoreIn, user: CurrentUser, request: Request, db: Db) -> Any:
    """Score the caller's jobs again against the profile's skills: all of them, or `ids`. A fit
    set by hand is kept unless `overwrite_manual`. Runs in batches of 100, each committed, and
    stops after 2,000 jobs or 20 seconds: `remaining` and `next_after` say where to resume."""
    skills = service.user_skills(db, user.id)
    if skills is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_SKILLS)
    ids = list(dict.fromkeys(body.ids)) if body.ids is not None else None
    if ids is not None:
        missing = service.missing_ids(db, user.id, ids)
        if missing:
            return _missing(missing)
    now = deps.clock(request)
    outcome = service.RescoreOutcome()
    started = time.monotonic()
    after = body.after
    looked = 0
    while looked < RESCORE_MAX_ROWS:
        count = service.rescore_batch(
            db,
            user.id,
            skills,
            now,
            ids=ids,
            after=after,
            limit=min(RESCORE_BATCH, RESCORE_MAX_ROWS - looked),
            overwrite_manual=body.overwrite_manual,
            outcome=outcome,
        )
        db.commit()
        looked += count
        after = outcome.last_id
        if count == 0 or time.monotonic() - started > RESCORE_BUDGET_S:
            break
    remaining = service.count_after(db, user.id, ids, after) if after is not None else 0
    if after is None and looked == 0:
        remaining = 0
    return RescoreOut(
        scored=outcome.scored,
        changed=outcome.changed,
        unchanged=outcome.unchanged,
        skipped_manual=outcome.skipped_manual,
        skipped_no_text=outcome.skipped_no_text,
        remaining=remaining,
        next_after=after if remaining else None,
    )
