"""Jobs J29: the list's filters on the place, the work mode, the website, the published date, the
pay, the application and the sweep, each alone and combined, through GET /api/jobs, /places and
/facets; the sorts on the published date and the pay, page after page."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.apply.models import Application, Mode, State
from app.jobs import notices, service, sweep
from app.jobs.models import JobEvent, JobEventKind, PayPeriod, WorkMode
from tests.identity.api_helpers import Api
from tests.jobs.conftest import NOW
from tests.jobs.test_api import Signed, alice, bob, listing, ok, seed, slugs_of, walk

__all__ = ["alice", "bob"]  # the fixtures of test_api, used here by name

PLACES = {
    "reunion": "Saint-Denis, La Réunion",
    "paris8": "PARIS 8e",
    "parisot": "Parisot",
    "idf": "Île-de-France (Paris)",
    "montreal": "Montréal, QC",
    "zurich": "Zürich",
    "remote": "Remote",
    "empty": "",
}


def by_slug(api: Api, who: Signed, **params: Any) -> set[str]:
    return set(walk(api, who, 50, **params))


def seed_places(factory: sessionmaker[Session], who: Signed) -> None:
    seed(factory, who.id, [{"slug": key, "location": loc} for key, loc in PLACES.items()])


@pytest.mark.parametrize(
    ("places", "expected"),
    [
        (["reunion"], {"reunion"}),
        (["La Réunion"], {"reunion"}),
        (["RÉUNION"], {"reunion"}),
        (["saint denis"], {"reunion"}),
        (["Saint-Denis"], {"reunion"}),
        (["Saint-Denis, La Réunion"], {"reunion"}),
        (["paris"], {"paris8", "idf"}),
        (["Paris 8e"], {"paris8"}),
        (["ile de france"], {"idf"}),
        (["Île-de-France"], {"idf"}),
        (["montreal"], {"montreal"}),
        (["Montréal"], {"montreal"}),
        (["zurich"], {"zurich"}),
        (["Zurich", "paris"], {"zurich", "paris8", "idf"}),
        (["remote"], {"remote"}),
        (["pari"], set()),
        (["denis la"], {"reunion"}),
        (["la"], {"reunion"}),
        (["qc"], {"montreal"}),
        (["union"], set()),
        (["a_b"], set()),
        (["100%"], set()),
    ],
)
def test_place_holds_whole_words_case_and_accents_aside(
    api: Api, factory: sessionmaker[Session], alice: Signed, places: list[str], expected: set[str]
) -> None:
    seed_places(factory, alice)
    assert by_slug(api, alice, place=places) == expected


@pytest.mark.parametrize(
    "params",
    [
        {"place": ""},
        {"place": "  ,  "},
        {"place": "%"},
        {"place": "x" * 101},
        {"place": [f"p{i}" for i in range(21)]},
        {"source": " "},
        {"source": "x" * 201},
        {"source": [f"s{i}" for i in range(21)]},
        {"work_mode": "anywhere"},
        {"application": "maybe"},
        {"pay": "maybe"},
        {"pay_min": 100},
        {"pay_period": "year"},
        {"pay_min": 0, "pay_period": "year"},
        {"pay": "not_stated", "pay_min": 100, "pay_period": "year"},
        {"published_within": -1},
        {"published_within": 3651},
        {"published_from": "2026-02-31"},
        {"sweep": "not-an-id"},
    ],
)
def test_bad_parameters_are_422(api: Api, alice: Signed, params: dict[str, Any]) -> None:
    assert listing(api, alice, **params).status_code == 422
    assert api.get("/api/jobs/facets", token=alice.token, params=params).status_code == 422


def test_work_mode_and_not_stated(api: Api, factory: sessionmaker[Session], alice: Signed) -> None:
    seed(
        factory,
        alice.id,
        [{"slug": mode.value, "work_mode": mode} for mode in WorkMode] + [{"slug": "none"}],
    )
    assert by_slug(api, alice, work_mode="remote") == {"remote"}
    assert by_slug(api, alice, work_mode=["hybrid", "office"]) == {"hybrid", "office"}
    assert by_slug(api, alice, work_mode="not_stated") == {"none"}
    assert by_slug(api, alice, work_mode=["remote", "not_stated"]) == {"remote", "none"}


def test_source_matches_each_name_of_a_posting_found_twice(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    seed(
        factory,
        alice.id,
        [
            {"slug": "twice", "source": "linkedin:data scientist/Paris, web"},
            {"slug": "web", "source": "web"},
            {"slug": "board", "source": "ashby:example-robotics"},
            {"slug": "host", "source": "www.welcometothejungle.com"},
            {"slug": "none", "source": None},
        ],
    )
    assert by_slug(api, alice, source="web") == {"twice", "web"}
    assert by_slug(api, alice, source="linkedin") == {"twice"}
    assert by_slug(api, alice, source="linkedin:data scientist/Paris") == {"twice"}
    assert by_slug(api, alice, source=["ashby", "www.welcometothejungle.com"]) == {"board", "host"}
    assert by_slug(api, alice, source="ashby:example-robotics") == {"board"}
    assert by_slug(api, alice, source="linkedin:data scientist/Paris, web") == set()
    assert by_slug(api, alice, source="data scientist") == set()
    assert service.SOURCES_SEP == sweep.SOURCES_SEP


def test_published_within_from_and_undated(
    api: Api, factory: sessionmaker[Session], alice: Signed, clock: Any
) -> None:
    today = notices.today_of(clock())
    seed(
        factory,
        alice.id,
        [
            {"slug": "today", "published_on": today},
            {"slug": "week", "published_on": today - timedelta(days=7)},
            {"slug": "month", "published_on": today - timedelta(days=30)},
            {"slug": "undated", "published_on": None},
        ],
    )
    assert by_slug(api, alice, published_within=0) == {"today"}
    assert by_slug(api, alice, published_within=7) == {"today", "week"}
    assert by_slug(api, alice, published_within=7, published_undated="true") == {
        "today",
        "week",
        "undated",
    }
    week_ago = (today - timedelta(days=7)).isoformat()
    assert by_slug(api, alice, published_from=week_ago) == {"today", "week"}
    # Both: the later bound holds.
    assert by_slug(api, alice, published_from=week_ago, published_within=1) == {"today"}
    # Without a published bound, published_undated narrows nothing.
    assert len(by_slug(api, alice, published_undated="true")) == 4


def pay(low: int, high: int, period: PayPeriod) -> dict[str, Any]:
    return {"pay_min": low, "pay_max": high, "pay_currency": "EUR", "pay_period": period}


def test_pay_stated_and_minimum_per_period(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    seed(
        factory,
        alice.id,
        [
            {"slug": "y45-55", **pay(45_000, 55_000, PayPeriod.year)},
            {"slug": "y40", **pay(40_000, 40_000, PayPeriod.year)},
            {"slug": "d600", **pay(500, 600, PayPeriod.day)},
            {"slug": "none"},
        ],
    )
    assert by_slug(api, alice, pay="stated") == {"y45-55", "y40", "d600"}
    assert by_slug(api, alice, pay="not_stated") == {"none"}
    assert by_slug(api, alice, pay_min=50_000, pay_period="year") == {"y45-55"}
    assert by_slug(api, alice, pay_min=55_001, pay_period="year") == set()
    assert by_slug(api, alice, pay_min=550, pay_period="day") == {"d600"}
    assert by_slug(api, alice, pay_min=1, pay_period="month") == set()


def application(
    db: Session, who: Signed, job_id: uuid.UUID, state: State, minutes: int, mode: Mode
) -> None:
    submitted = NOW if state in (State.submitted, State.submit_unknown) else None
    db.add(
        Application(
            user_id=who.id,
            posting_id=job_id,
            mode=mode,
            state=state,
            ats="greenhouse",
            form_url=f"https://forms.example/{job_id}",
            canonical_key=f"key-{job_id}-{minutes}",
            created_at=NOW + timedelta(minutes=minutes),
            updated_at=NOW + timedelta(minutes=minutes),
            submitted_at=submitted,
        )
    )


# Each posting's applications, oldest first, and what its newest one reads as.
APPLICATIONS: dict[str, tuple[list[tuple[State, Mode]], str]] = {
    "never": ([], "none"),
    "cancelled": ([(State.cancelled, Mode.review)], "none"),
    "rehearsed": ([(State.rehearsed, Mode.full_auto)], "none"),
    "closed": ([(State.posting_closed, Mode.review)], "none"),
    "queued": ([(State.queued, Mode.review)], "under_way"),
    "filling": ([(State.filling, Mode.direct)], "under_way"),
    "ready-auto": ([(State.ready, Mode.full_auto)], "under_way"),
    "ready-direct": ([(State.ready, Mode.direct)], "under_way"),
    "submitting": ([(State.submitting, Mode.review)], "under_way"),
    "ready": ([(State.ready, Mode.review)], "waiting"),
    "needs": ([(State.needs_person, Mode.direct)], "waiting"),
    "unknown": ([(State.submit_unknown, Mode.review)], "waiting"),
    "sent": ([(State.submitted, Mode.review)], "sent"),
    "failed-then-sent": ([(State.failed, Mode.review), (State.submitted, Mode.direct)], "sent"),
    "sent-manual": ([(State.submitted, Mode.manual)], "sent"),
    "failed": ([(State.failed, Mode.review)], "failed"),
    "blocked": ([(State.blocked, Mode.full_auto)], "failed"),
    "refused": ([(State.refused_by_site, Mode.review)], "failed"),
    "cancelled-failed": ([(State.cancelled, Mode.review), (State.failed, Mode.review)], "failed"),
    "failed-cancelled": ([(State.failed, Mode.review), (State.cancelled, Mode.review)], "none"),
}  # fmt: skip


def test_application_reads_the_newest_one(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    jobs = seed(factory, alice.id, [{"slug": slug} for slug in APPLICATIONS])
    # Bob's application on his own posting changes nothing of Alice's list.
    bobs = seed(factory, bob.id, [{"slug": "never"}])
    with factory() as db, db.begin():
        for job in jobs:
            for minutes, (state, mode) in enumerate(APPLICATIONS[job.slug][0]):
                application(db, alice, job.id, state, minutes, mode)
        application(db, bob, bobs[0].id, State.submitted, 0, Mode.review)
    for choice in service.ApplicationChoice:
        wanted = {slug for slug, (_, reads) in APPLICATIONS.items() if reads == choice.value}
        assert by_slug(api, alice, application=choice.value) == wanted, choice
    assert by_slug(api, alice, application=["sent", "failed"]) == {
        slug for slug, (_, reads) in APPLICATIONS.items() if reads in ("sent", "failed")
    }
    assert by_slug(api, bob, application="none") == set()


def test_sweep_keeps_the_postings_it_added(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    jobs = seed(factory, alice.id, [{"slug": s} for s in ("first", "second", "other", "manual")])
    bobs = seed(factory, bob.id, [{"slug": "first"}])
    one, two = uuid.uuid4(), uuid.uuid4()
    with factory() as db, db.begin():
        for job, sweep_id in zip(jobs[:3], (one, one, two), strict=True):
            data = {"sweep_id": str(sweep_id)}
            db.add(JobEvent(job_id=job.id, user_id=alice.id, kind=JobEventKind.sweep, data=data))
        data = {"sweep_id": str(one)}
        db.add(JobEvent(job_id=bobs[0].id, user_id=bob.id, kind=JobEventKind.sweep, data=data))
    assert by_slug(api, alice, sweep=str(one)) == {"first", "second"}
    assert by_slug(api, alice, sweep=str(two)) == {"other"}
    assert by_slug(api, alice, sweep=str(uuid.uuid4())) == set()
    with factory() as db:
        added = sweep.new_jobs(db, alice.id, one, 10)
    assert {job.slug for job in added} == {"first", "second"}


def test_filters_combined_and_the_map_takes_them(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    seed(
        factory,
        alice.id,
        [
            {"slug": "match", "location": "Saint-Denis, La Réunion", "work_mode": WorkMode.hybrid,
             "source": "web, linkedin:ml/Paris", **pay(50_000, 60_000, PayPeriod.year),
             "published_on": date(2026, 11, 20)},
            {"slug": "office", "location": "Saint-Denis, La Réunion", "work_mode": WorkMode.office,
             "source": "web", **pay(50_000, 60_000, PayPeriod.year),
             "published_on": date(2026, 11, 20)},
            {"slug": "paris", "location": "Paris", "work_mode": WorkMode.hybrid, "source": "web",
             **pay(50_000, 60_000, PayPeriod.year), "published_on": date(2026, 11, 20)},
            {"slug": "old", "location": "La Réunion", "work_mode": WorkMode.hybrid,
             "source": "linkedin", **pay(50_000, 60_000, PayPeriod.year),
             "published_on": date(2026, 1, 2)},
        ],
    )  # fmt: skip
    params = {
        "place": "reunion",
        "work_mode": "hybrid",
        "source": ["linkedin", "indeed"],
        "published_from": "2026-11-01",
        "pay_min": 55_000,
        "pay_period": "year",
        "application": "none",
    }
    page = ok(api, alice, **params)
    assert slugs_of(page) == ["match"]
    assert page["total"] == 1
    places = api.get("/api/jobs/places", token=alice.token, params=params).json()
    assert places["total"] == 1


def test_facets_count_places_and_websites_over_the_other_filters(
    api: Api, factory: sessionmaker[Session], alice: Signed, bob: Signed
) -> None:
    seed(
        factory,
        alice.id,
        [
            {"slug": "a", "location": "Saint-Denis, La Réunion", "source": "web, linkedin:ml/X"},
            {"slug": "b", "location": "Saint-Denis (974)", "source": "web"},
            {"slug": "c", "location": "La Reunion", "source": "linkedin:data/Y"},
            {"slug": "d", "location": "Paris, Paris", "source": "ashby:acme",
             "work_mode": WorkMode.remote},
            {"slug": "e", "location": "", "source": None},
        ],
    )  # fmt: skip
    seed(factory, bob.id, [{"slug": "a", "location": "Bobville", "source": "bobsite"}])

    def facets(**params: Any) -> tuple[dict[str, int], dict[str, int]]:
        response = api.get("/api/jobs/facets", token=alice.token, params=params)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        return (
            {f["value"]: f["count"] for f in body["places"]},
            {f["value"]: f["count"] for f in body["sources"]},
        )

    places, websites = facets()
    assert places == {"Saint-Denis": 2, "La Réunion": 2, "974": 1, "Paris": 1}
    assert websites == {"web": 2, "linkedin": 2, "ashby": 1}
    assert list(places)[:2] == ["La Réunion", "Saint-Denis"]  # most first, then by name
    # Each facet follows every filter but its own.
    places, websites = facets(place="Paris", source="web")
    assert places == {"Saint-Denis": 2, "La Réunion": 1, "974": 1}
    assert websites == {"ashby": 1}
    places, websites = facets(work_mode="remote")
    assert (places, websites) == ({"Paris": 1}, {"ashby": 1})


def test_facets_are_bounded(api: Api, factory: sessionmaker[Session], alice: Signed) -> None:
    seed(factory, alice.id, [{"slug": f"j{i}", "location": f"Town {i:03d}"} for i in range(60)])
    body = api.get("/api/jobs/facets", token=alice.token).json()
    assert len(body["places"]) == service.FACETS_MAX


def test_sort_by_published_and_by_pay_page_after_page(
    api: Api, factory: sessionmaker[Session], alice: Signed
) -> None:
    rows = [
        {"slug": "p-new", "published_on": date(2026, 11, 20)},
        {"slug": "p-old", "published_on": date(2026, 1, 2)},
        {"slug": "p-mid-a", "published_on": date(2026, 6, 1), "found_on": date(2026, 10, 9)},
        {"slug": "p-mid-b", "published_on": date(2026, 6, 1)},
        {"slug": "y-high", **pay(80_000, 90_000, PayPeriod.year)},
        {"slug": "y-low", **pay(30_000, 35_000, PayPeriod.year)},
        {"slug": "m-high", **pay(9_000, 9_500, PayPeriod.month)},
        {"slug": "d-high", **pay(700, 900, PayPeriod.day)},
        {"slug": "h-high", **pay(90, 120, PayPeriod.hour)},
        {"slug": "none"},
    ]
    seed(factory, alice.id, rows)
    published = walk(api, alice, 2, sort="published")
    assert published[:4] == ["p-new", "p-mid-a", "p-mid-b", "p-old"]
    assert set(published[4:]) == {r["slug"] for r in rows[4:]}
    by_pay = walk(api, alice, 3, sort="pay")
    assert by_pay[:5] == ["y-high", "y-low", "m-high", "d-high", "h-high"]
    assert set(by_pay[5:]) == {r["slug"] for r in rows if "pay_period" not in r}


def test_application_filter_needs_its_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_application_condition", [])
    filters = service.JobFilters(applications=(service.ApplicationChoice.sent,))
    with pytest.raises(RuntimeError, match="apply registers"):
        service.filtered(uuid.uuid4(), filters)
