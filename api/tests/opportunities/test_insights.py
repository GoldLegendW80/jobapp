"""Your search in numbers and the close of the silent applications (SPEC-opportunities.md,
"Your search in numbers"; owner, 2026-11-07). Invented rows of e2e people only."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.apply.models import Application, State
from app.jobs.models import Job, JobEvent, JobEventKind, JobStatus
from app.opportunities import insights
from tests.identity.api_helpers import Api
from tests.identity.conftest import Clock
from tests.opportunities.conftest import (
    Person,
    add_application,
    add_posting,
    pass_,
    status_of,
)


def read(api: Api, who: Person) -> dict[str, Any]:
    response = api.get("/api/insights", token=who.token)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def close(api: Api, who: Person, *ids: uuid.UUID) -> httpx.Response:
    return api.post(
        "/api/insights/silent/close", token=who.token, json={"ids": [str(i) for i in ids]}
    )


def event(
    factory: sessionmaker[Session],
    owner: Person,
    posting: uuid.UUID,
    on: date,
    data: dict[str, Any],
    kind: JobEventKind = JobEventKind.status,
) -> None:
    with factory() as session, session.begin():
        session.add(
            JobEvent(
                job_id=posting,
                user_id=owner.user_id,
                kind=kind,
                at=datetime.combine(on, time(9), tzinfo=UTC),
                data=data,
            )
        )


def applied(
    factory: sessionmaker[Session], owner: Person, on: date | None, **fields: Any
) -> uuid.UUID:
    """A posting the person applied to on `on`, as an imported row stores it (no event)."""
    values: dict[str, Any] = {"status": JobStatus.applied, "applied_on": on}
    values.update(fields)
    return add_posting(factory, owner, **values)


def answered(
    factory: sessionmaker[Session],
    owner: Person,
    on: date,
    after: int,
    to: str = "interview",
    **fields: Any,
) -> uuid.UUID:
    posting = applied(factory, owner, on, status=JobStatus(to), **fields)
    event(factory, owner, posting, on + timedelta(days=after), {"from": "applied", "to": to})
    return posting


# --- what a posting reached ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "moves", "expected"),
    [
        ("found", [], {"found"}),
        ("interview", [], {"interview"}),
        (
            "interview",
            [{"from": "found", "to": "applied"}, {"from": "applied", "to": "interview"}],
            {"found", "applied", "interview"},
        ),
        # An undo takes back the status it moved the posting out of.
        (
            "applied",
            [
                {"from": "applied", "to": "interview", "via": "agent"},
                {"from": "interview", "to": "applied", "via": "undo"},
            ],
            {"applied"},
        ),
        (
            "found",
            [
                {"from": "found", "to": "skipped", "via": "swipe"},
                {"from": "skipped", "to": "found", "via": "swipe_undo"},
            ],
            {"found"},
        ),
    ],
)
def test_what_a_posting_reached(status: str, moves: list[dict[str, Any]], expected: set) -> None:
    assert insights.reached(status, moves) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("linkedin:ai engineer/Paris", "linkedin"),
        ("ashby:example-robotics, linkedin:ai engineer", "ashby"),
        ("mail:indeed", "mail"),
        ("remoteok", "remoteok"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_the_channel_is_the_first_source_s_kind(source: str | None, expected: str) -> None:
    assert insights.channel(source) == expected


def test_a_rate_under_its_floor_is_none() -> None:
    assert insights.rate(3, 19, 20) is None
    assert insights.rate(0, 0, 0) is None
    assert insights.rate(5, 20, 20) == 25
    assert insights.rate(1, 3, 3) == 33


# --- the numbers --------------------------------------------------------------------------------


def test_the_funnel_counts_the_person_s_rows(
    api: Api, person_a: Person, person_b: Person, factory: sessionmaker[Session], clock: Clock
) -> None:
    today = clock().date()
    add_posting(factory, person_a)
    liked = add_posting(factory, person_a)
    add_application(factory, person_a, liked, State.queued)
    applied(factory, person_a, today - timedelta(days=3))
    answered(factory, person_a, today - timedelta(days=30), 4)
    answered(factory, person_a, today - timedelta(days=30), 9, to="rejected")
    passed = add_posting(factory, person_a)
    assert pass_(api, person_a, passed).status_code == 200
    applied(factory, person_b, today)
    funnel = read(api, person_a)["funnel"]
    assert funnel == {
        "found": 6,
        "liked": 4,
        "applied": 3,
        "answered": 2,
        "interview": 1,
        "rejected": 1,
        "passed": 1,
        "waiting": 1,
        "answer_rate": None,
        "interview_rate": None,
    }


def test_the_funnel_rates_show_from_20_applications(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Clock
) -> None:
    start = clock().date() - timedelta(days=20)
    for _ in range(15):
        applied(factory, person_a, start)
    for _ in range(4):
        answered(factory, person_a, start, 3)
    assert read(api, person_a)["funnel"]["interview_rate"] is None
    answered(factory, person_a, start, 3, to="rejected")
    funnel = read(api, person_a)["funnel"]
    assert (funnel["applied"], funnel["answer_rate"], funnel["interview_rate"]) == (20, 25, 20)


def test_interviews_by_fit_band_show_a_rate_from_5_applications(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Clock
) -> None:
    start = clock().date() - timedelta(days=20)
    for fit in (80, 95, 99):
        applied(factory, person_a, start, fit=fit)
    for fit in (85, 100):
        answered(factory, person_a, start, 2, fit=fit)
    for fit in (60, 79, 70, 61):
        applied(factory, person_a, start, fit=fit)
    applied(factory, person_a, start, fit=None)
    bands = {group["key"]: group for group in read(api, person_a)["by_fit"]}
    assert list(bands) == ["80_plus", "60_79", "under_60", "none"]
    assert bands["80_plus"] == {
        "key": "80_plus",
        "applied": 5,
        "answered": 2,
        "interviews": 2,
        "rate": 40,
    }
    assert (bands["60_79"]["applied"], bands["60_79"]["rate"]) == (4, None)
    assert (bands["under_60"]["applied"], bands["under_60"]["rate"]) == (0, None)
    assert bands["none"]["applied"] == 1


def test_the_applicant_tracking_system_and_the_source(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Clock
) -> None:
    start = clock().date() - timedelta(days=20)
    for _ in range(8):
        posting = applied(factory, person_a, start, source="linkedin:ai engineer/Paris")
        add_application(factory, person_a, posting, State.submitted)
    applied(
        factory,
        person_a,
        start,
        url="https://boards.greenhouse.io/example/jobs/1",
        source="greenhouse:example",
    )
    applied(factory, person_a, start, url=None, source=None)
    body = read(api, person_a)
    by_ats = {group["key"]: group for group in body["by_ats"]}
    assert (by_ats["ashby"]["applied"], by_ats["ashby"]["rate"]) == (8, 0)
    assert (by_ats["greenhouse"]["applied"], by_ats["greenhouse"]["rate"]) == (1, None)
    assert by_ats["unknown"]["applied"] == 1
    assert [group["key"] for group in body["by_source"]] == ["linkedin", "greenhouse", "unknown"]


def test_the_days_to_a_first_answer(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Clock
) -> None:
    start = clock().date() - timedelta(days=40)
    answered(factory, person_a, start, 5)
    answered(factory, person_a, start, 20, to="rejected")
    # An imported interview has no dated answer: counted apart.
    applied(factory, person_a, start, status=JobStatus.interview)
    assert read(api, person_a)["first_answer"] == {
        "median_days": None,
        "measured": 2,
        "undated": 1,
        "waiting": 0,
    }
    answered(factory, person_a, start, 10)
    applied(factory, person_a, start)
    assert read(api, person_a)["first_answer"] == {
        "median_days": 10.0,
        "measured": 3,
        "undated": 1,
        "waiting": 1,
    }


def test_the_reasons_of_the_passes_in_force(
    api: Api, person_a: Person, factory: sessionmaker[Session]
) -> None:
    postings = [add_posting(factory, person_a) for _ in range(5)]
    for posting in postings:
        pass_(api, person_a, posting)
    for posting, value in zip(postings[:3], ("pay", "pay", "remote"), strict=True):
        api.request(
            "PUT",
            f"/api/postings/{posting}/pass/reason",
            token=person_a.token,
            json={"reason": value},
        )
    # An undone Pass is no Pass, and a posting skipped otherwise has no Pass reason.
    api.request("DELETE", f"/api/postings/{postings[2]}/pass", token=person_a.token)
    add_posting(factory, person_a, status=JobStatus.skipped)
    body = read(api, person_a)
    assert body["pass_reasons"] == [{"reason": "pay", "count": 2}]
    assert body["passes_without_reason"] == 2
    assert body["funnel"]["passed"] == 4


# --- the silent applications --------------------------------------------------------------------


def test_the_silent_applications(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Clock
) -> None:
    today = clock().date()
    silent = applied(factory, person_a, today - timedelta(days=61), company="Example Robotics")
    edge = applied(factory, person_a, today - timedelta(days=60), company="Example Labs")
    applied(factory, person_a, today - timedelta(days=59))
    noted = applied(factory, person_a, today - timedelta(days=90))
    event(factory, person_a, noted, today - timedelta(days=10), {}, JobEventKind.note)
    clicked = applied(factory, person_a, today - timedelta(days=90))
    application = add_application(factory, person_a, clicked, State.submitted)
    with factory() as session, session.begin():
        session.get_one(Application, application).submitted_at = datetime.combine(
            today - timedelta(days=10), time(9), tzinfo=UTC
        )
    answered(factory, person_a, today - timedelta(days=90), 3)
    applied(factory, person_a, today - timedelta(days=90), status=JobStatus.closed)
    applied(factory, person_a, None)
    body = read(api, person_a)
    assert [row["id"] for row in body["silent"]] == [str(silent), str(edge)]
    first = dict(body["silent"][0])
    assert first.pop("title").startswith("Applied AI Engineer")
    assert first == {
        "id": str(silent),
        "company": "Example Robotics",
        "applied_on": (today - timedelta(days=61)).isoformat(),
        "last_signal": (today - timedelta(days=61)).isoformat(),
        "days": 61,
    }
    assert body["silent_days"] == 60
    assert body["silent_undated"] == 1


def test_close_closes_only_what_is_still_silent_with_an_undo(
    api: Api,
    person_a: Person,
    person_b: Person,
    factory: sessionmaker[Session],
    clock: Clock,
) -> None:
    long_ago = clock().date() - timedelta(days=90)
    first = applied(factory, person_a, long_ago)
    second = applied(factory, person_a, long_ago)
    answered_since = applied(factory, person_a, long_ago)
    recent = applied(factory, person_a, clock().date())
    theirs = applied(factory, person_b, long_ago)
    with factory() as session, session.begin():
        posting = session.get_one(Job, answered_since)
        posting.status = JobStatus.interview
    response = close(api, person_a, first, second, answered_since, recent, theirs, uuid.uuid4())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["closed"]) == {str(first), str(second)}
    assert len(body["skipped"]) == 4
    assert str(theirs) in body["skipped"]
    assert status_of(factory, first) is JobStatus.closed
    assert status_of(factory, answered_since) is JobStatus.interview
    assert status_of(factory, recent) is JobStatus.applied
    assert status_of(factory, theirs) is JobStatus.applied
    assert read(api, person_a)["silent"] == []
    undone = api.post(
        "/api/jobs/bulk/undo", token=person_a.token, json={"token": body["undo_token"]}
    )
    assert undone.status_code == 200, undone.text
    assert status_of(factory, first) is JobStatus.applied
    assert status_of(factory, second) is JobStatus.applied


def test_close_with_nothing_silent_changes_nothing(
    api: Api, person_a: Person, factory: sessionmaker[Session], clock: Clock
) -> None:
    recent = applied(factory, person_a, clock().date())
    response = close(api, person_a, recent)
    assert response.status_code == 200
    assert response.json() == {
        "closed": [],
        "skipped": [str(recent)],
        "undo_token": None,
        "undo_expires_at": None,
    }


@pytest.mark.parametrize("ids", [[], [str(uuid.uuid4())] * 501, ["nope"]])
def test_close_bounds_its_ids(api: Api, person_a: Person, ids: list[str]) -> None:
    response = api.post("/api/insights/silent/close", token=person_a.token, json={"ids": ids})
    assert response.status_code == 422


def test_both_routes_need_a_session(api: Api) -> None:
    assert api.get("/api/insights").status_code == 401
    assert (
        api.post("/api/insights/silent/close", json={"ids": [str(uuid.uuid4())]}).status_code == 401
    )
