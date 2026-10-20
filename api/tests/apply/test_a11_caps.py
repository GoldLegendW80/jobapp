"""Apply A11: company caps, the person's pause, Full auto's rules and the pace per ATS host, on a
real Postgres (tasks/apply/todo.md, "A11"). Times come from an injected clock; a race is played
with two sessions in a fixed order (the first holds its lock, the second gives up, the first
commits, the second tries again). Every company is invented."""

from __future__ import annotations

import itertools
import random
import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import bank, caps, gate
from app.apply.caps_models import ApplySettings, CompanyCap
from app.apply.models import AnswerBankEntry, Application, ApplicationEvent, Mode, State
from app.documents.models import Kind
from app.jobs import service as jobs_service
from app.jobs.models import Job
from app.main import create_app
from tests.apply import a11_support as a11
from tests.apply.a6_support import FakeQueue, submit_ashby
from tests.apply.conftest import AT, Person, add_posting
from tests.apply.test_a5_run import application
from tests.apply.test_a6_gate import ON, TTL, approve, ready, run_submit, submit_services
from tests.conftest import make_settings
from tests.identity.api_helpers import PASSWORD, Api

DAY = timedelta(days=1)


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


def mine(db: Session, person: Person, state: State = State.ready, **rest: Any) -> Application:
    """An application of a new posting of the person at Example Robotics, `ready` unless said."""
    where = a11.posting(db, person.user_id, title="Applied AI Engineer")
    return a11.application(db, person.user_id, where, state=state, **rest)


def second_posting(db: Session, person: Person, stub: str, title: str) -> Person:
    """Another posting of the person at Example Robotics, with its documents, on the fake form."""
    slug = title.lower().replace(" ", "-")
    url = f"{stub}/ashby/example-robotics/{slug}/application"
    where, versions = add_posting(db, person.user_id, url)
    db.execute(sa.update(Job).where(Job.id == where).values(title=title))
    db.commit()
    return Person(person.user_id, person.email, where, versions[Kind.cv], versions[Kind.letter])


# --- the company caps ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "counted"),
    [
        (State.submitted, True),
        (State.submit_unknown, True),
        (State.submitting, True),
        (State.refused_by_site, False),
        (State.cancelled, False),
        (State.blocked, False),
        (State.rehearsed, False),
        (State.ready, False),
    ],
)
def test_a_cap_counts_what_the_app_clicked(
    db: Session, person_a: Person, state: State, counted: bool
) -> None:
    a11.cap(db, person_a.user_id, max_count=1)
    a11.clicked(db, person_a.user_id, AT - DAY, state=state)
    found = mine(db, person_a)
    assert caps.check(db, found, AT) == ("company_cap" if counted else None)


@pytest.mark.parametrize(
    ("ago", "code"), [(90, "company_cap"), (90 + 1 / 86_400, None), (1, "company_cap")]
)
def test_the_window_of_a_cap_at_its_boundary(
    db: Session, person_a: Person, ago: float, code: str | None
) -> None:
    a11.cap(db, person_a.user_id, max_count=1, window_days=90)
    a11.clicked(db, person_a.user_id, AT - timedelta(days=ago))
    assert caps.check(db, mine(db, person_a), AT) == code


def test_a_company_under_two_spellings_and_its_ats_organisation(
    db: Session, person_a: Person
) -> None:
    a11.cap(db, person_a.user_id, label="EXAMPLE ROBOTICS SAS", max_count=2)
    a11.clicked(db, person_a.user_id, AT - DAY, company="Example Robotics, Inc.")
    assert caps.check(db, mine(db, person_a), AT) is None
    # a posting under another name, on a form of the company's Ashby organisation
    a11.clicked(
        db,
        person_a.user_id,
        AT - DAY,
        company="ER Warehouse Systems",
        form_url=a11.ASHBY.format(org="example-robotics", n=7),
    )
    assert caps.check(db, mine(db, person_a), AT) == "company_cap"
    # another company's organisation is not counted
    db.rollback()
    a11.cap(db, person_a.user_id, label="Example Robotics", max_count=1)
    a11.clicked(
        db,
        person_a.user_id,
        AT - DAY,
        company="Northwind Labs",
        form_url=a11.ASHBY.format(org="northwind-labs", n=8),
    )
    assert caps.check(db, mine(db, person_a), AT) is None


def test_the_same_role_cool_down(db: Session, person_a: Person) -> None:
    a11.cap(db, person_a.user_id, max_count=5, window_days=30, same_role_days=365)
    # 200 days ago: past the 180 days of the duplicates rule, inside the person's 365
    a11.clicked(db, person_a.user_id, AT - 200 * DAY, title="Applied AI Engineer (H/F)")
    assert caps.check(db, mine(db, person_a), AT) == "company_cap_role"
    db.rollback()
    a11.cap(db, person_a.user_id, max_count=5, window_days=30, same_role_days=365)
    a11.clicked(db, person_a.user_id, AT - 200 * DAY, title="Robotics Engineer")
    assert caps.check(db, mine(db, person_a), AT) is None


def test_a_cap_edited_down_refuses_new_ones_and_undoes_nothing(
    db: Session, person_a: Person
) -> None:
    one = a11.clicked(db, person_a.user_id, AT - DAY)
    two = a11.clicked(db, person_a.user_id, AT - DAY, state=State.submit_unknown)
    db.commit()
    shown = caps.revision([])
    caps.replace_caps(db, person_a.user_id, [caps.CapIn("Example Robotics", 3, 90)], shown, AT)
    db.commit()
    assert caps.check(db, mine(db, person_a), AT) is None
    db.rollback()
    shown = caps.revision(caps.caps_of(db, person_a.user_id))
    caps.replace_caps(db, person_a.user_id, [caps.CapIn("Example Robotics", 1, 90)], shown, AT)
    db.commit()
    assert caps.used(db, person_a.user_id, caps.caps_of(db, person_a.user_id), AT) == [2]
    assert caps.check(db, mine(db, person_a), AT) == "company_cap"
    assert application(db, one.id).state is State.submitted
    assert application(db, two.id).state is State.submit_unknown


def test_a_stale_list_of_caps_is_refused(db: Session, person_a: Person) -> None:
    shown = caps.revision([])
    caps.replace_caps(db, person_a.user_id, [caps.CapIn("Example Robotics", 3, 90)], shown, AT)
    db.commit()
    with pytest.raises(caps.CapsError, match="stale"):
        caps.replace_caps(db, person_a.user_id, [], shown, AT)
    db.rollback()
    for items, code in (
        (
            [caps.CapIn("Example Robotics", 1, 9), caps.CapIn("example-robotics inc", 2, 9)],
            "same_company",
        ),
        ([caps.CapIn("!!!", 1, 9)], "no_company"),
        ([caps.CapIn("Example Robotics", 0, 9)], "out_of_range"),
    ):
        seen = caps.revision(caps.caps_of(db, person_a.user_id))
        with pytest.raises(caps.CapsError) as refused:
            caps.replace_caps(db, person_a.user_id, items, seen, AT)
        assert refused.value.code == code
        db.rollback()
    assert [c.max_count for c in caps.caps_of(db, person_a.user_id)] == [3]


def test_one_persons_caps_and_clicks_never_count_for_another(
    db: Session, person_a: Person, person_b: Person
) -> None:
    a11.cap(db, person_b.user_id, max_count=1)
    a11.clicked(db, person_b.user_id, AT - DAY)
    assert caps.check(db, mine(db, person_a), AT) is None
    db.rollback()
    a11.cap(db, person_b.user_id, max_count=1)
    a11.clicked(db, person_a.user_id, AT - DAY)
    assert caps.check(db, mine(db, person_b), AT) is None


def test_the_pause_switch_refuses_and_keeps_the_form(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    caps.save_settings(db, person_a.user_id, AT, paused=True, min_fit=None)
    db.commit()
    decision = approve(factory, person_a, app_id)
    assert (decision.passed, decision.code) == (False, "paused_by_you")
    assert application(db, app_id).state is State.ready
    caps.save_settings(db, person_a.user_id, AT, paused=False, min_fit=None)
    db.commit()
    assert approve(factory, person_a, app_id).passed


def test_the_pause_switch_stops_a_pending_approval_before_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = ready(db, factory, person_a, stub)
    assert approve(factory, person_a, app_id).passed
    caps.save_settings(db, person_a.user_id, AT, paused=True, min_fit=None)
    db.commit()
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub), app_id) == "paused_by_you"
    assert form.gate_clicks == []
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "paused_by_you")


def test_two_approvals_racing_for_the_last_slot_one_passes(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = second_posting(db, person_a, stub, "Robotics Engineer")
    first, second = ready(db, factory, person_a, stub), ready(db, factory, other, stub)
    a11.cap(db, person_a.user_id, max_count=1)
    db.commit()
    with factory() as s:
        auths = []
        for app_id in (first, second):
            found = s.get(Application, app_id)
            assert found is not None
            auths.append(
                gate.authorise_approval(
                    s, person_a.user_id, app_id, gate.current_hash(s, found), AT, TTL
                )
            )
        s.commit()
        auth_ids = [a.id for a in auths]
    monkeypatch.setattr(jobs_service, "LOCK_TIMEOUT_MS", 50)
    with factory() as one, factory() as two:
        assert gate.decide(one, person_a.user_id, auth_ids[0], AT, switches=ON).passed
        # the first holds the person's apply lock: the second gives up instead of reading old counts
        with pytest.raises(jobs_service.JobBusyError):
            gate.decide(two, person_a.user_id, auth_ids[1], AT, switches=ON)
        two.rollback()
        one.commit()
        decision = gate.decide(two, person_a.user_id, auth_ids[1], AT, switches=ON)
        two.commit()
    assert (decision.passed, decision.code) == (False, "company_cap")
    assert application(db, first).state is State.approved
    assert application(db, second).state is State.ready


def test_a_cap_set_after_two_approvals_lets_only_the_first_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    other = second_posting(db, person_a, stub, "Robotics Engineer")
    first, second = ready(db, factory, person_a, stub), ready(db, factory, other, stub)
    assert approve(factory, person_a, first).passed
    assert approve(factory, other, second).passed
    a11.cap(db, person_a.user_id, max_count=1)
    db.commit()
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub), first) == "submitted"
    later = submit_ashby()
    assert run_submit(submit_services(factory, later, stub), second) == "company_cap"
    assert application(db, second).state is State.ready
    assert later.gate_clicks == []


# --- Full auto -----------------------------------------------------------------------------------


def test_full_auto_one_application_per_company_unless_a_cap_says_otherwise(
    db: Session, person_a: Person
) -> None:
    a11.clicked(db, person_a.user_id, AT - 29 * DAY)
    assert caps.check(db, mine(db, person_a, mode=Mode.full_auto), AT) == "full_auto_company"
    assert caps.check(db, mine(db, person_a, mode=Mode.review), AT) is None
    db.rollback()
    a11.clicked(db, person_a.user_id, AT - 31 * DAY)
    assert caps.check(db, mine(db, person_a, mode=Mode.full_auto), AT) is None
    db.rollback()
    a11.clicked(db, person_a.user_id, AT - DAY)
    a11.cap(db, person_a.user_id, max_count=2, window_days=30)
    assert caps.check(db, mine(db, person_a, mode=Mode.full_auto), AT) is None


@pytest.mark.parametrize(
    ("times", "code"),
    [
        # 01:00 in Los Angeles: two clicks since midnight there reach a ceiling of 2
        ([datetime(2026, 10, 15, 7, 30), datetime(2026, 10, 15, 7, 0)], "safety_ceiling"),
        # 23:59 the day before in Los Angeles is not today
        ([datetime(2026, 10, 15, 7, 30), datetime(2026, 10, 15, 6, 59)], None),
        ([datetime(2026, 10, 15, 7, 30)], None),
    ],
)
def test_the_safety_ceiling_counts_the_persons_day(
    db: Session, person_a: Person, times: list[datetime], code: str | None
) -> None:
    a11.search(db, person_a.user_id, {"sweep_schedule": {"timezone": "America/Los_Angeles"}})
    db.add(ApplySettings(user_id=person_a.user_id, safety_ceiling=2))
    for n, at in enumerate(times):
        a11.clicked(db, person_a.user_id, at.replace(tzinfo=AT.tzinfo), company=f"Company {n}")
    now = datetime(2026, 10, 15, 8, 0, tzinfo=AT.tzinfo)
    assert caps.check(db, mine(db, person_a, mode=Mode.full_auto), now) == code


# --- the pace per ATS host -----------------------------------------------------------------------


def test_the_host_pace_across_two_people_the_second_waits(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    app_a, app_b = ready(db, factory, person_a, stub), ready(db, factory, person_b, stub)
    assert approve(factory, person_a, app_a).passed
    assert approve(factory, person_b, app_b).passed
    clicked_at = AT + timedelta(minutes=5)
    svc = submit_services(factory, submit_ashby(), stub)
    svc.clock = a11.Fixed(clicked_at)
    assert run_submit(svc, app_a) == "submitted"

    goes = clicked_at + timedelta(seconds=180)
    for now in (clicked_at + timedelta(seconds=10), goes - timedelta(seconds=1)):
        form = submit_ashby()
        svc = submit_services(factory, form, stub)
        svc.clock = a11.Fixed(now)
        assert run_submit(svc, app_b) == "paced"
        assert form.gate_clicks == []
        found = application(db, app_b)
        assert (found.state, found.needs) == (State.approved, "host_pace")
    waits = db.execute(
        sa.select(ApplicationEvent.data).where(
            ApplicationEvent.application_id == app_b, ApplicationEvent.kind == "waiting"
        )
    ).scalars()
    assert [w for w in waits if w.get("needs") == "host_pace"] == [
        {"needs": "host_pace", "until": goes.isoformat()}
    ]
    svc = submit_services(factory, submit_ashby(), stub)
    svc.clock = a11.Fixed(goes)
    assert run_submit(svc, app_b) == "submitted"
    assert application(db, app_b).needs is None


def test_two_sessions_reserve_one_slot_of_a_host(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    app_a = mine(db, person_a, state=State.approved)
    app_b = mine(db, person_b, state=State.approved)
    db.commit()
    with factory() as one, factory() as two:
        found_a, found_b = one.get(Application, app_a.id), two.get(Application, app_b.id)
        assert found_a is not None
        assert found_b is not None
        assert caps.reserve(one, found_a, AT) is None
        a11.click(one, found_a, AT)
        # the first holds the host's lock until it commits: the second waits an interval
        two.execute(sa.text("select set_config('lock_timeout', '50ms', true)"))
        assert caps.reserve(two, found_b, AT + timedelta(seconds=1)) == AT + timedelta(seconds=181)
        one.commit()
        # once the first committed, the second sees its click
        assert caps.reserve(two, found_b, AT + timedelta(seconds=2)) == AT + timedelta(seconds=180)
        two.rollback()


def test_the_daily_count_of_a_host_is_any_24_hours(db: Session, person_a: Person) -> None:
    other = a11.clicked(db, person_a.user_id, AT - DAY)
    first = AT - timedelta(hours=20)
    for n in range(20):
        a11.click(db, other, first + timedelta(minutes=10 * n))
    found = mine(db, person_a, state=State.approved)
    assert caps.next_slot(db, found, AT) == first + DAY
    assert caps.next_slot(db, found, first + DAY - timedelta(seconds=1)) == first + DAY
    assert caps.next_slot(db, found, first + DAY) is None
    # another host is not paced by these clicks
    lever = mine(
        db, person_a, state=State.approved, ats="lever", form_url="https://jobs.lever.co/x/1"
    )
    assert caps.next_slot(db, lever, AT) is None


def test_the_pace_holds_for_any_sequence_of_attempts(db: Session, person_a: Person) -> None:
    """The property: whatever the times people try, two clicks of one host are never closer than
    the interval and never more than the daily count in any 24 hours, and an attempt is refused
    only until a time after it."""
    pace = caps.pace_of("ashby")
    other = a11.clicked(db, person_a.user_id, AT - DAY)
    found = mine(db, person_a, state=State.approved)
    draw = random.Random(248)  # noqa: S311 (test data, not a secret)
    now, clicks = AT, []
    for _ in range(300):
        now += timedelta(seconds=draw.choice([1, 30, 90, 179, 180, 181, 600, 3600, 4 * 3600]))
        until = caps.next_slot(db, found, now)
        if until is None:
            a11.click(db, other, now)
            clicks.append(now)
        else:
            assert until > now
    assert len(clicks) > pace.daily_max
    assert all(b - a >= pace.interval for a, b in itertools.pairwise(clicks))
    assert all(sum(t <= c < t + DAY for c in clicks) <= pace.daily_max for t in clicks)


def test_a_rehearsal_is_never_paced(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, stub: str
) -> None:
    app_a = ready(db, factory, person_a, stub)
    rehearsal = ready(db, factory, person_b, stub, dry_run=True)
    assert approve(factory, person_a, app_a).passed
    assert approve(factory, person_b, rehearsal).passed
    svc = submit_services(factory, submit_ashby(), stub)
    svc.clock = a11.Fixed(AT + timedelta(minutes=5))
    assert run_submit(svc, app_a) == "submitted"
    svc = submit_services(factory, submit_ashby(), stub)
    svc.clock = a11.Fixed(AT + timedelta(minutes=5, seconds=10))
    assert run_submit(svc, rehearsal) == "rehearsed"


# --- the routes ----------------------------------------------------------------------------------


SECRET_KEY = "e2e-only-not-a-secret-" + "0" * 24


@pytest.fixture
def api(
    migrated_url: str, stub: str, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> Iterator[Api]:
    from app.identity import security

    with factory() as s:
        for person in (person_a, person_b):
            s.execute(
                sa.text("UPDATE users SET password_hash = :h WHERE id = :u"),
                {"h": security.hash_password(PASSWORD), "u": person.user_id},
            )
        s.commit()
    app: FastAPI = create_app(
        make_settings(migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=stub),
        clock=lambda: AT,
    )
    app.state.queue = FakeQueue()
    with TestClient(app, follow_redirects=False) as client:
        yield Api(client)


def test_the_settings_routes(api: Api, db: Session, person_a: Person, person_b: Person) -> None:
    token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
    shown = api.get("/api/apply/settings", token=token_a)
    assert shown.status_code == 200
    assert shown.headers["cache-control"] == "no-store"
    assert shown.json() == {
        "mode_after_like": "review",
        "full_auto_available": True,
        "paused": False,
        "min_fit": 80,
        "min_fit_chosen": False,
        "safety_ceiling": 30,
        "hold_s": 900,
        "company_days": 30,
        "show_postings_from": None,
        "write_documents_from": None,
    }
    a11.search(db, person_a.user_id, {"min_fit": 60, "documents": {"min_fit": 65}})
    db.commit()
    body = api.get("/api/apply/settings", token=token_a).json()
    assert (body["show_postings_from"], body["write_documents_from"], body["min_fit"]) == (
        60,
        65,
        65,
    )

    changed = api.request(
        "PUT", "/api/apply/settings", token=token_a, json={"min_fit": 85, "paused": True}
    )
    assert changed.status_code == 200
    assert (
        changed.json()["min_fit"],
        changed.json()["min_fit_chosen"],
        changed.json()["paused"],
    ) == (85, True, True)
    for refused in (
        {"mode_after_like": "manual"},
        {"safety_ceiling": 100},
        {"min_fit": 101},
        {"min_fit": "85"},
    ):
        assert (
            api.request("PUT", "/api/apply/settings", token=token_a, json=refused).status_code
            == 422
        )
    assert api.request("PUT", "/api/apply/settings", json={"paused": True}).status_code == 401
    assert (
        api.request(
            "PUT", "/api/apply/settings", token=token_a, csrf=False, json={"paused": True}
        ).status_code
        == 403
    )
    # B's settings are B's own
    assert api.get("/api/apply/settings", token=token_b).json()["paused"] is False
    assert db.get(ApplySettings, person_b.user_id) is None


@pytest.mark.parametrize(
    "lax",
    ["no", "yes", "false", "true", "off", "on", "0", "1", "", 0, 1, 0.0, 1.0, [], {}],
)
def test_the_pause_switch_takes_a_json_boolean_only(
    api: Api, db: Session, person_a: Person, lax: object
) -> None:
    token = api.login_token(person_a.email)
    on = api.request("PUT", "/api/apply/settings", token=token, json={"paused": True})
    assert on.status_code == 200
    refused = api.request("PUT", "/api/apply/settings", token=token, json={"paused": lax})
    assert refused.status_code == 422, refused.text
    assert api.get("/api/apply/settings", token=token).json()["paused"] is True
    off = api.request("PUT", "/api/apply/settings", token=token, json={"paused": False})
    assert (off.status_code, off.json()["paused"]) == (200, False)


def test_the_caps_routes(api: Api, db: Session, person_a: Person, person_b: Person) -> None:
    token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
    empty = api.get("/api/apply/caps", token=token_a).json()
    assert empty["items"] == []
    cap = {"label": "Example Robotics", "max_count": 3, "window_days": 90, "same_role_days": 180}
    saved = api.request(
        "PUT",
        "/api/apply/caps",
        token=token_a,
        json={"items": [cap], "revision": empty["revision"]},
    )
    assert saved.status_code == 200, saved.text
    (item,) = saved.json()["items"]
    assert {k: item[k] for k in cap} == cap
    assert item["used"] == 0
    # what the page showed moved: 409, nothing written
    stale = api.request(
        "PUT", "/api/apply/caps", token=token_a, json={"items": [], "revision": empty["revision"]}
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "stale")
    revision = saved.json()["revision"]
    canary = secrets.token_hex(8)
    two = [
        {**cap, "label": f"Example Robotics {canary}"},
        {**cap, "label": f"example-robotics-{canary} inc"},
    ]
    clash = api.request(
        "PUT", "/api/apply/caps", token=token_a, json={"items": two, "revision": revision}
    )
    assert (clash.status_code, clash.json()["code"]) == (422, "same_company")
    assert canary not in clash.text
    nothing = [{**cap, "label": "!!!"}]
    refused = api.request(
        "PUT", "/api/apply/caps", token=token_a, json={"items": nothing, "revision": revision}
    )
    assert (refused.status_code, refused.json()["code"]) == (422, "no_company")
    # B sees none of A's caps, and B's change never touches them
    shown_b = api.get("/api/apply/caps", token=token_b).json()
    assert shown_b["items"] == []
    other = {**cap, "label": "Northwind Labs"}
    assert (
        api.request(
            "PUT",
            "/api/apply/caps",
            token=token_b,
            json={"items": [other], "revision": shown_b["revision"]},
        ).status_code
        == 200
    )
    labels = db.execute(sa.select(CompanyCap.user_id, CompanyCap.label)).all()
    assert sorted((str(u), label) for u, label in labels) == sorted(
        [(str(person_a.user_id), "Example Robotics"), (str(person_b.user_id), "Northwind Labs")]
    )


def test_the_answer_bank_routes(api: Api, db: Session, person_a: Person, person_b: Person) -> None:
    token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
    db.add(
        AnswerBankEntry(
            user_id=person_b.user_id,
            question_key="notice period",
            question="Notice period",
            answer="One month",
        )
    )
    db.add(
        AnswerBankEntry(
            user_id=person_a.user_id, question_key="pronouns", question="Pronouns", answer="they"
        )
    )
    db.commit()
    assert [
        e["question"] for e in api.get("/api/apply/answer-bank", token=token_a).json()["items"]
    ] == ["Pronouns"]
    # B's saved answer is not A's to change: 404, as a missing one
    put = api.request(
        "PUT",
        "/api/apply/answer-bank",
        token=token_a,
        json={"question_key": "notice period", "answer": "Two months"},
    )
    assert put.status_code == 404
    changed = api.request(
        "PUT",
        "/api/apply/answer-bank",
        token=token_a,
        json={"question_key": "pronouns", "answer": "she"},
    )
    assert [e["answer"] for e in changed.json()["items"]] == ["she"]
    forgot = api.request(
        "PUT",
        "/api/apply/answer-bank",
        token=token_a,
        json={"question_key": "pronouns", "answer": None},
    )
    assert forgot.json()["items"] == []
    kept = api.get("/api/apply/answer-bank", token=token_b).json()["items"]
    assert [(e["question"], e["answer"]) for e in kept] == [("Notice period", "One month")]


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n \r\n", "\u00a0", "\u3000 "])
def test_a_blank_saved_answer_is_forgotten_as_the_bank_does(
    api: Api, db: Session, person_a: Person, blank: str
) -> None:
    token = api.login_token(person_a.email)
    for key, answer in (("pronouns", "they"), ("notice period", "One month")):
        db.add(
            AnswerBankEntry(
                user_id=person_a.user_id, question_key=key, question=key.title(), answer=answer
            )
        )
    db.commit()
    put = api.request(
        "PUT",
        "/api/apply/answer-bank",
        token=token,
        json={"question_key": "pronouns", "answer": blank},
    )
    assert put.status_code == 200, put.text
    assert [e["question_key"] for e in put.json()["items"]] == ["notice period"]
    assert (
        bank.AnswerBank(person_a.user_id, {"pronouns": "they"})
        .with_answer("Pronouns", blank)
        .entries
        == {}
    )
    # an answer with spaces around it is kept, without them
    kept = api.request(
        "PUT",
        "/api/apply/answer-bank",
        token=token,
        json={"question_key": "notice period", "answer": f"{blank}Two months{blank}"},
    )
    assert [e["answer"] for e in kept.json()["items"]] == ["Two months"]


def test_every_refusal_keeps_the_form_and_says_its_sentence() -> None:
    for code in caps.KEEP:
        assert code in gate.KEEP
        assert gate.SENTENCES[code] == caps.SENTENCES[code]
        assert not any(dash in caps.SENTENCES[code] for dash in (" - ", "\u2014", "\u2013"))
