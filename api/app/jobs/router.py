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

GET /api/jobs/{id}/files/{file_id}: the bytes with their stored type, `inline` for a PDF (the
browser shows it) and `attachment` for anything else, `X-Content-Type-Options: nosniff`,
`Cache-Control: private, no-cache` and a strong ETag (the SHA-256), 304 on If-None-Match.

POST /api/jobs/import: the import document as the JSON body, IMPORT_HTTP_MAX_BYTES (10 MiB, as
Caddy) at most, 413 above. Larger imports (Harald's ~18 MB) go through the CLI inside the api
container: `./deploy.sh jobs import <email>`.
"""

import re
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import get_session
from app.identity import deps
from app.identity.models import User
from app.identity.schemas import ErrorOut
from app.jobs import service
from app.jobs.models import ContractType, Job, JobFile, JobStatus
from app.jobs.schemas import (
    IMPORT_HTTP_MAX_BYTES,
    DocumentError,
    EventOut,
    FileOut,
    FitBucket,
    FitHistogram,
    ImportSummaryOut,
    JobDetail,
    JobListItem,
    JobListPage,
    JobsImportDocument,
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


@router.get(
    "", response_model=JobListPage, responses={**_401, **_422}, dependencies=[Depends(_no_store)]
)
def list_jobs(
    user: CurrentUser,
    db: Db,
    status_: Annotated[list[JobStatus] | None, Query(alias="status")] = None,
    type_: Annotated[list[ContractType] | None, Query(alias="type")] = None,
    company: Annotated[str | None, Query(max_length=Q_MAX)] = None,
    q: Annotated[str | None, Query(max_length=Q_MAX)] = None,
    fit_min: Annotated[int | None, Query(ge=0, le=100)] = None,
    fit_max: Annotated[int | None, Query(ge=0, le=100)] = None,
    found_from: date | None = None,
    found_to: date | None = None,
    sort: JobSort = JobSort.found_on,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = 50,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
) -> JobListPage:
    """One page of the caller's jobs, with the counts and the histogram of the filtered set."""
    if fit_min is not None and fit_max is not None and fit_min > fit_max:
        raise _invalid("fit_min", "fit_min must not be above fit_max")
    if found_from is not None and found_to is not None and found_from > found_to:
        raise _invalid("found_from", "found_from must not be after found_to")
    filters = JobFilters(
        statuses=tuple(dict.fromkeys(status_ or ())),
        contract_types=tuple(dict.fromkeys(type_ or ())),
        company=company,
        q=q,
        fit_min=fit_min,
        fit_max=fit_max,
        found_from=found_from,
        found_to=found_to,
    )
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
    job = _job_or_404(db, user, job_id)
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
