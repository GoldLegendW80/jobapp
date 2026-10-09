"""GET /api/jobs/places: the caller's jobs, under the list filters, grouped by the places their
location names (see gazetteer.py for the matching rules), plus the locations the gazetteer cannot
place, so the map can show pins and say what to add to the gazetteer.

Answer:
    {"places": [{"name", "kind": "city"|"country", "lat", "lon", "count",
                 "counts_by_status": {status: n, ...}, "job_ids": [...], "job_ids_truncated"}],
     "jobs": {id: {"title", "company", "location", "status", "fit"}},
     "unplaced": [{"location", "count"}], "total", "placed", "unplaced_total"}

Places come most postings first (ties by name); `job_ids` follow the engine's side panel order
(pipeline order, then best fit) and stop at JOB_IDS_MAX; `jobs` describes every id listed. A job
naming two cities counts in both pins, and once in `placed`. Unplaced locations come with their
counts, most first; an absent location is "".
"""

import uuid
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.jobs import gazetteer
from app.jobs.models import Job, JobStatus
from app.jobs.service import JobFilters, filtered


class PlaceOut(BaseModel):
    name: str
    kind: Literal["city", "country"]
    lat: float
    lon: float
    count: int  # postings naming this place
    counts_by_status: dict[JobStatus, int]  # only the statuses present
    job_ids: list[uuid.UUID]  # at most JOB_IDS_MAX, side panel order
    job_ids_truncated: bool


class MapJobOut(BaseModel):
    title: str
    company: str
    location: str
    status: JobStatus
    fit: int | None


class UnplacedOut(BaseModel):
    location: str  # "" when the posting states none
    count: int


class PlacesOut(BaseModel):
    places: list[PlaceOut]
    jobs: dict[uuid.UUID, MapJobOut]
    unplaced: list[UnplacedOut]
    total: int  # postings matching the filters
    placed: int  # of which naming at least one place
    unplaced_total: int


def places_map(db: Session, user_id: uuid.UUID, filters: JobFilters) -> PlacesOut:
    """The map of the user's jobs matching `filters`."""
    query = filtered(user_id, filters).with_only_columns(
        Job.id, Job.location, Job.status, Job.fit, Job.title, Job.company
    )
    rows = db.execute(query).all()
    summary = gazetteer.summarize(
        gazetteer.MapJob(
            id=row.id, location=row.location, status=row.status, fit=row.fit, title=row.title
        )
        for row in rows
    )
    by_id = {row.id: row for row in rows}
    places: list[PlaceOut] = []
    listed: dict[uuid.UUID, MapJobOut] = {}
    for group in summary.groups:
        ids = group.job_ids()
        for job_id in ids:
            if job_id not in listed:
                row = by_id[job_id]
                listed[job_id] = MapJobOut(
                    title=row.title,
                    company=row.company,
                    location=row.location,
                    status=row.status,
                    fit=row.fit,
                )
        places.append(
            PlaceOut(
                name=group.place.name,
                kind=group.place.kind,
                lat=group.place.lat,
                lon=group.place.lon,
                count=group.count,
                counts_by_status=group.counts_by_status(),
                job_ids=ids,
                job_ids_truncated=group.count > len(ids),
            )
        )
    return PlacesOut(
        places=places,
        jobs=listed,
        unplaced=[UnplacedOut(location=loc, count=n) for loc, n in summary.unplaced],
        total=summary.total,
        placed=summary.placed,
        unplaced_total=summary.total - summary.placed,
    )
