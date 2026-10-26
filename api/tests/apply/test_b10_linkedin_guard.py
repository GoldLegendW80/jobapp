"""Apply on a form on LinkedIn passes LinkedIn's guard (browser B10, review round 2, B2): until
A16 brings Easy Apply, an application whose form is on LinkedIn ends `blocked` with
`linkedin_off` or `linkedin_paused` before any lease when the person's switch is off, the
sentence is not accepted (or an older version is), or a checkpoint's pause is active; and the
gate and the submit lease's recheck refuse it the same way when that starts after the fill. A
form on a company site is unaffected."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import caps
from app.apply.models import Application, Mode, State
from app.browser import linkedin_guard as guard
from app.profile import service as profile_service
from tests.apply.a5_support import FakeLeases, run_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, fill, services, started
from tests.apply.test_a6_gate import approve, ready
from tests.profile.conftest import full

LINKEDIN_FORM = "https://www.linkedin.com/jobs/view/4100000001/"


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


def switch(factory: sessionmaker[Session], person: Person, **entry: Any) -> None:
    """The person's linkedin entry, written as PUT /api/browser/linkedin does (consent kept)."""
    base = {
        "kind": "linkedin",
        "query": "AI Engineer",
        "enabled": True,
        "accepted_at": (AT - timedelta(days=1)).isoformat(),
        "text_version": guard.TEXT_VERSION,
    }
    with factory() as db:
        profile = profile_service.get_profile(db, person.user_id, AT)
        search = {**full("search"), "browser_sources": [base | entry]}
        profile_service.save_section(
            db, person.user_id, "search", search, profile.revision, AT, linkedin_consent=True
        )
        db.commit()


def on_linkedin(db: Session, app_id: uuid.UUID) -> None:
    db.execute(
        sa.update(Application)
        .where(Application.id == app_id)
        .values(ats="linkedin", form_url=LINKEDIN_FORM)
    )
    db.commit()


def checkpoint(factory: sessionmaker[Session], person: Person) -> None:
    with factory() as db:
        guard.record_checkpoint(db, person.user_id, AT - timedelta(hours=1))
        db.commit()


CASES = {
    "no_entry": None,
    "switched_off": {"enabled": False},
    "not_accepted": {"accepted_at": None, "text_version": None},
    "old_version": {"text_version": guard.TEXT_VERSION + 1},  # another version than the current
    "paused": "pause",
}
EXPECTED = {
    "no_entry": "linkedin_off",
    "switched_off": "linkedin_off",
    "not_accepted": "linkedin_off",
    "old_version": "linkedin_off",
    "paused": "linkedin_paused",
}


def arrange(
    factory: sessionmaker[Session],
    person: Person,
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if case == "no_entry":
        return
    if case == "paused":
        switch(factory, person)
        checkpoint(factory, person)
        return
    if case == "old_version":
        switch(factory, person)  # accepted version 1, then the sentence moves to version 2
        monkeypatch.setattr(guard, "TEXT_VERSION", guard.TEXT_VERSION + 1)
        return
    switch(factory, person, **CASES[case])  # type: ignore[arg-type]


@pytest.mark.parametrize("case", list(CASES))
def test_a_linkedin_form_is_blocked_before_the_lease(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrange(factory, person_a, case, monkeypatch)
    app_id = started(db, person_a)
    on_linkedin(db, app_id)
    leases, form = FakeLeases(), run_ashby()
    assert fill(services(factory, form, leases, stub_base=stub), app_id) == "blocked"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.blocked, EXPECTED[case])
    assert leases.acquired == []
    assert form.calls == []


def test_a_company_site_form_is_unaffected_by_linkedin_off(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    checkpoint(factory, person_a)  # even a pause: it is LinkedIn's alone
    app_id = started(db, person_a)
    with factory() as s:
        found = s.get(Application, app_id)
        assert found is not None
        assert caps.linkedin_refusal(s, found, AT) is None
    leases = FakeLeases()
    assert fill(services(factory, run_ashby(), leases, stub_base=stub), app_id) != "blocked"
    assert leases.acquired != []


def test_an_allowed_person_passes_the_guard(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    switch(factory, person_a)
    app_id = started(db, person_a)
    on_linkedin(db, app_id)
    with factory() as s:
        found = s.get(Application, app_id)
        assert found is not None
        assert caps.linkedin_refusal(s, found, AT) is None


@pytest.mark.parametrize("case", list(CASES))
def test_the_gate_refuses_a_linkedin_form_when_the_guard_does(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id = ready(db, factory, person_a, stub)  # filled while the form was elsewhere
    on_linkedin(db, app_id)
    arrange(factory, person_a, case, monkeypatch)
    decision = approve(factory, person_a, app_id)
    assert (decision.passed, decision.code, decision.state) == (
        False,
        EXPECTED[case],
        State.blocked,
    )


def test_a_pause_after_the_decision_stops_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    from app.apply import submit
    from tests.apply.test_a6_gate import run_submit, submit_ashby, submit_services

    switch(factory, person_a)
    app_id = ready(db, factory, person_a, stub)
    on_linkedin(db, app_id)
    assert approve(factory, person_a, app_id).passed
    checkpoint(factory, person_a)
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, stub), app_id) == "linkedin_paused"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.blocked, "linkedin_paused")
    assert form.gate_clicks == []
    assert submit.recheck  # the check runs in the submit lease's recheck


# --- Full auto (apply A13a): every path of a LinkedIn item meets the guard ---------------------


def test_a_full_auto_like_on_linkedin_is_blocked_before_the_lease(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    from tests.apply.test_a13_full_auto import liked, turn_on

    turn_on(db, person_a)
    app_id = liked(db, person_a, stub)
    assert application(db, app_id).mode is Mode.full_auto
    on_linkedin(db, app_id)
    leases, form = FakeLeases(), run_ashby()
    assert fill(services(factory, form, leases, stub_base=stub), app_id) == "blocked"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.blocked, "linkedin_off")
    assert leases.acquired == []
    assert form.calls == []


@pytest.mark.parametrize("case", list(CASES))
def test_full_autos_decision_refuses_a_linkedin_item_when_the_guard_does(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.apply.test_a13_full_auto import HOUR, authorisations, filled, job, turn_on

    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)  # filled while the form was elsewhere
    on_linkedin(db, app_id)
    arrange(factory, person_a, case, monkeypatch)
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == (EXPECTED[case], [])
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.blocked, EXPECTED[case])
    [row] = authorisations(db, app_id)
    assert row.used_at is None  # refused: the Like was never spent


def test_a_held_full_auto_item_on_linkedin_is_stopped_before_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    from tests.apply.test_a13_full_auto import HOLD, held, job, turn_on

    turn_on(db, person_a)
    app_id = held(db, factory, person_a, stub)
    on_linkedin(db, app_id)
    switch(factory, person_a)
    checkpoint(factory, person_a)
    written, form = job(factory, stub, app_id, AT + HOLD)
    assert (written, form.gate_clicks) == ("linkedin_paused", [])
    found = application(db, app_id)
    assert (found.state, found.mode, found.error_code) == (
        State.ready,
        Mode.review,
        "linkedin_paused",
    )


def test_full_auto_on_allowed_linkedin_is_held_before_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """LinkedIn accepted and on, no pause: A16 supports the source with the normal hold."""
    from tests.apply.test_a13_full_auto import HOUR, filled, job, turn_on

    turn_on(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    on_linkedin(db, app_id)
    switch(factory, person_a)
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert form.gate_clicks == []
    assert written == "held"
    assert application(db, app_id).state is State.held


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("http://pages:8080/b8/linkedin/jobs/view/4100000201/", "linkedin"),
        ("http://pages:8080/b8/linkedin/", "linkedin"),
        ("http://pages:8080/b8/linkedinx/jobs/view/1/", "pages"),
        ("http://pages:8080/b8/google/jobs/results/1001/", "pages"),
        ("http://pages:8080/linkedin/jobs/view/1/", "pages"),
        ("https://jobs.example.org/fake-b10/1", "jobs.example.org"),
    ],
)
def test_the_fixture_pages_linkedin_is_linkedin_in_a_test_stack(
    url: str, host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests only: the journey's LinkedIn is the fixture server's /linkedin/ under
    BROWSER_SOURCES_BASE_URL (refused in production), so its rows meet the guard there too."""
    real = caps._setting
    base = "http://pages:8080/b8"
    monkeypatch.setattr(
        caps, "_setting", lambda name: base if name == "browser_sources_base_url" else real(name)
    )
    assert caps.host_of("other", url) == host
    monkeypatch.setattr(
        caps, "_setting", lambda name: None if name == "browser_sources_base_url" else real(name)
    )
    assert caps.host_of("other", url) != "linkedin"
