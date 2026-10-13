"""GET /api/jobs over 5,000 rows of one user (and 5,000 of another): the page, the counts and the
histogram in one request. GET /api/jobs/places over the same volume, with thousands of distinct
free-text locations: every matching row goes through the gazetteer, and the answer stays bounded
(UNPLACED_MAX locations, JOB_IDS_MAX ids per pin).

What is measured is what the volume costs, not how fast the machine is: the request over 5,000
rows against the same request of a user who has BASELINE_ROWS, each the best of ROUNDS, taken
in turn in the same seconds. A bound in milliseconds (150 ms, then 1 s on CI) failed on a busy
machine with nothing wrong in the code (issue #104); a load slows both requests alike, and the
best of several rounds is the one the load touched least. What the ratio still catches is what
the guard is for: a list whose cost follows the size of the table (a lost index, every row
loaded or sent, a query per row). It does not catch a cost added to every request whatever the
volume, which the milliseconds printed with `-s` show (about 30 ms each on the Mac)."""

import math
import random
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
BASELINE_ROWS = 200  # a full page of every case below, and nothing more
ROUNDS = 7
# What 25 times the rows may cost, in times the request over BASELINE_ROWS. Measured on the Mac,
# alone and beside four busy processes, with the same figures: the list from 1.1 (a filter on
# dates) to 1.7 (a sort on the company), which is the page read through an index plus the
# counts and the histogram over every row; the places 4.3, since every matching row goes
# through the gazetteer. The bounds are a little over twice what was seen. In milliseconds
# on that machine: about 40 for the list and 80 for the places, where the budget was 150.
LIST_RATIO = 4.0
PLACES_RATIO = 10.0
# The ratio is blind to a cost added to every request: it raises the baseline with the rest,
# and with 130 ms of fixed cost the list would pass until about 600 ms. So an absolute
# ceiling stands beside it, on the same best of ROUNDS: ten times the old budget of 150 ms,
# far above what a loaded machine adds to the 40 and 80 ms measured, and still under a
# request that takes seconds (PR #156, N3).
CEILING_MS = 1_500.0
WORDS = ["agent", "engineer", "platform", "data", "ml", "applied", "ai", "backend", "forward"]


def _bulk(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    seed: int,
    location: Callable[[int, random.Random], str] | None = None,
    count: int = ROWS,
) -> None:
    rng = random.Random(seed)  # noqa: S311, synthetic test data
    statuses = list(JobStatus)
    types = list(ContractType)
    rows: list[dict[str, Any]] = []
    for i in range(count):
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


def _best_ms(api: Api, path: str, requests: list[tuple[str, dict[str, Any]]]) -> list[float]:
    """The best time of each request over ROUNDS, after a warm-up. The requests are taken in
    turn, so that each round gives all of them the same machine."""

    def once(token: str, params: dict[str, Any]) -> float:
        start = time.perf_counter()
        response = api.get(path, token=token, params=params)
        elapsed = (time.perf_counter() - start) * 1000
        assert response.status_code == 200, response.text
        return elapsed

    for token, params in requests:
        once(token, params)
    best = [math.inf] * len(requests)
    for _ in range(ROUNDS):
        for index, (token, params) in enumerate(requests):
            best[index] = min(best[index], once(token, params))
    return best


def _users(
    factory: sessionmaker[Session],
    api: Api,
    location: Callable[[int, random.Random], str] | None = None,
) -> tuple[str, str]:
    """The tokens of a user with ROWS postings (beside another user's ROWS) and of the user
    the baseline is measured on, who has BASELINE_ROWS of the same making."""
    make_user(factory, email="perf@example.org", password_hash=None)
    make_user(factory, email="other@example.org", password_hash="h")
    make_user(factory, email="small@example.org", password_hash=None)
    with factory() as db:
        users = dict(db.execute(sa.select(User.email, User.id)).all())
    _bulk(factory, users["perf@example.org"], 1, location)
    _bulk(factory, users["other@example.org"], 2, location)
    _bulk(factory, users["small@example.org"], 3, location, count=BASELINE_ROWS)
    return api.login_token("perf@example.org"), api.login_token("small@example.org")


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
    token, small = _users(factory, api)
    first = api.get("/api/jobs", token=token, params=params).json()
    assert sum(first["counts_by_status"].values()) <= ROWS
    if not params:
        assert first["total"] == ROWS
    # The second page through the cursor costs the same.
    cursor = first["next_cursor"]
    requests = [(small, params), (token, params)]
    if cursor:
        requests.append((token, {**params, "cursor": cursor}))
    baseline, *over_the_volume = _best_ms(api, "/api/jobs", requests)
    ratios = [cost / baseline for cost in over_the_volume]
    print(
        f"GET /api/jobs {params}: {baseline:.1f} ms over {BASELINE_ROWS} rows, over {ROWS}: "
        + ", ".join(f"{cost:.1f} ms" for cost in over_the_volume)
        + " (the page, the next page), ratios "
        + ", ".join(f"{ratio:.2f}" for ratio in ratios)
    )
    assert max(ratios) < LIST_RATIO, (baseline, over_the_volume)
    assert max(baseline, *over_the_volume) < CEILING_MS, (baseline, over_the_volume)


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
    token, small = _users(factory, api, _varied_location)

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

    everything = {"status": list(JobStatus)}
    baseline, cost = _best_ms(api, "/api/jobs/places", [(small, everything), (token, everything)])
    filtered = api.get("/api/jobs/places", token=token, params={"q": "agent", "fit_min": 50})
    assert filtered.status_code == 200
    print(
        f"GET /api/jobs/places: {baseline:.1f} ms over {BASELINE_ROWS} rows, {cost:.1f} ms "
        f"over {ROWS}, ratio {cost / baseline:.2f}"
    )
    assert cost / baseline < PLACES_RATIO, (baseline, cost)
    assert max(baseline, cost) < CEILING_MS, (baseline, cost)
