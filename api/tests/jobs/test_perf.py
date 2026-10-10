"""GET /api/jobs over 5,000 rows of one user (and 5,000 of another): the page, the counts and the
histogram in one request, under LIST_BUDGET_MS (150 ms on the Mac, the median of 7 runs after
a warm-up, through the whole app). CI machines are slower: CI=1 widens the budget to 1 second.
GET /api/jobs/places over the same volume, with thousands of distinct free-text locations: every
matching row goes through the gazetteer, under PLACES_BUDGET_MS (the same 150 ms on the Mac,
1 second on CI; about 30 ms measured), and the answer stays bounded (UNPLACED_MAX locations,
JOB_IDS_MAX ids per pin)."""

import os
import random
import statistics
import time
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.identity.models import User
from app.jobs import gazetteer, places
from app.jobs.models import ContractType, Job, JobStatus
from tests.identity.api_helpers import Api, make_user
from tests.jobs.conftest import NOW

ROWS = 5_000
LIST_BUDGET_MS = 1000.0 if os.environ.get("CI") else 150.0
PLACES_BUDGET_MS = 1000.0 if os.environ.get("CI") else 150.0
WORDS = ["agent", "engineer", "platform", "data", "ml", "applied", "ai", "backend", "forward"]


def _bulk(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    seed: int,
    location: Callable[[int, random.Random], str] | None = None,
) -> None:
    rng = random.Random(seed)  # noqa: S311, synthetic test data
    statuses = list(JobStatus)
    types = list(ContractType)
    rows: list[dict[str, Any]] = []
    for i in range(ROWS):
        title = " ".join(rng.sample(WORDS, 3)).title()
        rows.append(
            {
                "user_id": user,
                "slug": f"company-{i % 300}-{i}",
                "company": f"Company {i % 300}",
                "title": title,
                "location": (
                    location(i, rng)
                    if location
                    else rng.choice(["Paris", "London", "Remote", "Bordeaux", "Berlin"])
                ),
                "contract_type": rng.choice(types),
                "url": f"https://jobs.example/{user}/{i}",
                "status": rng.choice(statuses),
                "fit": None if i % 13 == 0 else rng.randint(0, 100),
                "competencies": ["agents", "python"],
                "gaps": ["rag"],
                "description": "x" * 280,
                "posting_text": "Posting text. " * 300,
                "found_on": date(2026, 10, 7) - timedelta(days=i % 60),
                "notes": "note" if i % 5 == 0 else "",
                "created_at": NOW,
                "updated_at": NOW + timedelta(seconds=i % 500),
            }
        )
    with factory() as db, db.begin():
        db.execute(sa.insert(Job), rows)
        db.execute(sa.text("ANALYZE jobs"))


def _median_ms(api: Api, token: str, params: dict[str, Any]) -> float:
    api.get("/api/jobs", token=token, params=params)  # warm-up
    times = []
    for _ in range(7):
        start = time.perf_counter()
        response = api.get("/api/jobs", token=token, params=params)
        times.append((time.perf_counter() - start) * 1000)
        assert response.status_code == 200, response.text
    return statistics.median(times)


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"sort": "fit", "limit": 200},
        {"sort": "company"},
        {"sort": "status", "status": ["found", "shortlisted"]},
        {"sort": "updated"},
        {"q": "agent engineer"},
        {"company": "company 1", "fit_min": 50, "type": ["CDI", "VIE"]},
        {"found_from": "2026-09-01", "found_to": "2026-09-30", "fit_max": 80},
    ],
    ids=["default", "fit-200", "company", "status", "updated", "q", "company-fit-type", "dates"],
)
def test_list_over_5000_rows(
    api: Api, factory: sessionmaker[Session], params: dict[str, Any]
) -> None:
    make_user(factory, email="perf@example.org", password_hash=None)
    make_user(factory, email="other@example.org", password_hash="h")
    with factory() as db:
        users = dict(db.execute(sa.select(User.email, User.id)).all())
    _bulk(factory, users["perf@example.org"], 1)
    _bulk(factory, users["other@example.org"], 2)
    token = api.login_token("perf@example.org")
    first = api.get("/api/jobs", token=token, params=params).json()
    assert sum(first["counts_by_status"].values()) <= ROWS
    if not params:
        assert first["total"] == ROWS
    # The second page through the cursor costs the same.
    cursor = first["next_cursor"]
    median = _median_ms(api, token, params)
    paged = _median_ms(api, token, {**params, "cursor": cursor}) if cursor else 0.0
    print(f"GET /api/jobs {params}: {median:.1f} ms, next page {paged:.1f} ms")
    assert median < LIST_BUDGET_MS
    assert paged < LIST_BUDGET_MS


PLACED = ["Paris, France", "London", "Berlin, Germany", "Remote, United States", "Zürich",
          "Paris, London", "New York", "Bordeaux", "Remote (EMEA), Spain", "Singapore"]  # fmt: skip


def _varied_location(i: int, rng: random.Random) -> str:
    """Two thirds placed (one or two places), one third free text the gazetteer cannot place:
    about 1,600 distinct texts, a few of them frequent."""
    if i % 3:
        return rng.choice(PLACED)
    if i % 10 == 0:
        return rng.choice(["Remote", "", "EMEA", "Hybrid"])
    return f"Somewhere {i:05d}"


def test_places_over_5000_rows(api: Api, factory: sessionmaker[Session]) -> None:
    make_user(factory, email="perf@example.org", password_hash=None)
    make_user(factory, email="other@example.org", password_hash="h")
    with factory() as db:
        users = dict(db.execute(sa.select(User.email, User.id)).all())
    _bulk(factory, users["perf@example.org"], 1, _varied_location)
    _bulk(factory, users["other@example.org"], 2, _varied_location)
    token = api.login_token("perf@example.org")

    response = api.get("/api/jobs/places", token=token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == ROWS
    assert body["placed"] + body["unplaced_total"] == ROWS
    assert body["unplaced_locations"] > places.UNPLACED_MAX
    assert len(body["unplaced"]) == places.UNPLACED_MAX
    counts = [u["count"] for u in body["unplaced"]]
    assert counts == sorted(counts, reverse=True)
    assert all(len(p["job_ids"]) <= gazetteer.JOB_IDS_MAX for p in body["places"])
    assert {"Paris", "London", "Berlin", "Singapore", "United States"} <= {
        p["name"] for p in body["places"]
    }
    # Bounded: the pins, their ids and 200 locations, not one entry per posting.
    print(f"GET /api/jobs/places: {len(response.content) / 1024:.0f} KiB")
    assert len(response.content) < 1_000_000

    times = []
    for _ in range(7):
        start = time.perf_counter()
        response = api.get("/api/jobs/places", token=token, params={"status": list(JobStatus)})
        times.append((time.perf_counter() - start) * 1000)
        assert response.status_code == 200, response.text
    median = statistics.median(times)
    filtered = api.get("/api/jobs/places", token=token, params={"q": "agent", "fit_min": 50})
    assert filtered.status_code == 200
    print(f"GET /api/jobs/places over {ROWS} rows: {median:.1f} ms")
    assert median < PLACES_BUDGET_MS
