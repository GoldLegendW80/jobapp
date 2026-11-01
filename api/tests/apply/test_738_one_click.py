"""One click Apply (issue #738, owner 2026-11-01), on a real Postgres, A5's fill on A4's modelled
Ashby form and A6's fake submit form: the Apply button with "Let me review each filled form
before it is sent" unticked fills and the gate's code sends, with the person's click as the
approval (`apply_click`); ticked, it is Review first, unchanged. Every submission counted here is
a call of the fake form's `gate_click`; no browser, no model, no company."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import direct, email, full_auto, gate, model_check, signin, submit
from app.apply.caps_models import ApplySettings
from app.apply.models import Application, ApplicationAnswer, AuthorisationKind, Mode, State
from app.jobs.models import Job
from tests.apply import a11_support as a11
from tests.apply import test_a14_email as a14
from tests.apply.a5_support import FakeAgent
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import WHY, application, events, fill, run_ashby, services, started
from tests.apply.test_a6_gate import run_submit, stub
from tests.apply.test_a13_full_auto import (
    HOUR,
    WEEK,
    WHY_TEXT,
    answered,
    api,
    authorisations,
    job,
    reviewed,
)
from tests.identity.api_helpers import Api
from tests.mail import canary
from tests.mail.fake_gmail import FakeGoogle

__all__ = ["api", "stub"]


def settings(db: Session, person: Person, **values: object) -> None:
    db.add(ApplySettings(user_id=person.user_id, updated_at=AT - HOUR, **values))
    db.commit()


def low_fit(db: Session, person: Person) -> None:
    """A fit far below any threshold: one click Apply never reads "Apply automatically from"."""
    db.execute(sa.update(Job).where(Job.user_id == person.user_id).values(fit=10))
    db.commit()


def filled(
    db: Session,
    factory: sessionmaker[Session],
    person: Person,
    stub: str,
    *,
    mode: Mode = Mode.direct,
    clean: bool = True,
) -> uuid.UUID:
    """The Apply button clicked at `AT`, the form filled by A5 and `ready`."""
    app_id = started(db, person, mode)
    agent = FakeAgent(lambda q: (WHY_TEXT, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stub), app_id) == "ready"
    if clean:
        answered(db, app_id)
    return app_id


def agent_wrote(db: Session, app_id: uuid.UUID) -> None:
    """The free text question answered by the agent: the check by model must pass first."""
    db.execute(
        sa.update(ApplicationAnswer)
        .where(ApplicationAnswer.application_id == app_id, ApplicationAnswer.label == WHY)
        .values(source="agent")
    )
    db.commit()


# --- the setting and the button ----------------------------------------------------------------


def test_the_review_box_is_off_by_default_and_put_changes_it(api: Api, person_a: Person) -> None:
    token = api.login_token(person_a.email)
    read = api.request("GET", "/api/apply/settings", token=token)
    assert (read.status_code, read.json()["review_before_send"]) == (200, False)
    on = api.request("PUT", "/api/apply/settings", token=token, json={"review_before_send": True})
    assert (on.status_code, on.json()["review_before_send"]) == (200, True)
    assert on.json()["mode_after_like"] == "review"  # Likes are not touched
    wrong = api.request("PUT", "/api/apply/settings", token=token, json={"review_before_send": 1})
    assert wrong.status_code == 422
    extra = api.request("PUT", "/api/apply/settings", token=token, json={"review_first": True})
    assert extra.status_code == 422


@pytest.mark.parametrize(("ticked", "mode"), [(False, "direct"), (True, "review")])
def test_the_apply_button_follows_the_box(
    api: Api, db: Session, person_a: Person, ticked: bool, mode: str
) -> None:
    token = api.login_token(person_a.email)
    put = {"review_before_send": ticked}
    assert api.request("PUT", "/api/apply/settings", token=token, json=put).status_code == 200
    path = f"/api/postings/{person_a.posting_id}/applications"
    started_ = api.post(path, token=token, json={"mode": "apply"})
    assert (started_.status_code, started_.json()["mode"]) == (201, mode)
    app_id = uuid.UUID(started_.json()["id"])
    kinds = [e.kind for e in events(db, app_id)]
    assert ("apply_click" in kinds) is (not ticked)


def test_a_rehearsal_from_the_apply_button_is_review_first(api: Api, person_a: Person) -> None:
    token = api.login_token(person_a.email)
    path = f"/api/postings/{person_a.posting_id}/applications"
    body = {"mode": "apply", "dry_run": True}
    assert api.post(path, token=token, json=body).json()["mode"] == "review"


# --- the click is the approval -----------------------------------------------------------------


def test_the_fill_then_the_gate_sends_with_the_click_and_no_approve(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    low_fit(db, person_a)  # no fit threshold, no rehearsal (none done), no hold
    app_id = filled(db, factory, person_a, stub)
    found = application(db, app_id)
    assert (found.mode, found.state, found.created_at) == (Mode.direct, State.ready, AT)
    assert app_id in submit.due(db, AT + HOUR)
    assert app_id in submit.undecided_of(db, [app_id])

    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == ("approved", [])
    [row] = authorisations(db, app_id)
    assert row.kind is AuthorisationKind.apply_click
    assert (row.created_at, row.expires_at, row.used_at) == (AT + HOUR, AT + WEEK, AT + HOUR)
    assert bytes(row.answers_hash) == bytes(application(db, app_id).answers_hash)

    written, form = job(factory, stub, app_id, AT + HOUR)
    assert written == "submitted"
    assert [c["what"] for c in form.gate_clicks] == ["submit"]
    kinds = [e.kind for e in events(db, app_id)]
    assert "held" not in [e.data.get("outcome") for e in events(db, app_id)]
    order = [kinds.index(k) for k in ("apply_click", "authorisation", "decision", "click")]
    assert order == sorted(order)
    assert application(db, app_id).dry_run is False


def test_one_click_gives_one_decision(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    assert job(factory, stub, app_id, AT + HOUR)[0] == "approved"
    # The same application back at `ready` in mode direct: the click was spent already.
    db.execute(
        sa.update(Application).where(Application.id == app_id).values(state=State.ready.value)
    )
    db.commit()
    written, form = job(factory, stub, app_id, AT + 2 * HOUR)
    assert (written, form.gate_clicks) == ("authorisation_used", [])
    reviewed(db, app_id, "authorisation_used")
    assert len(authorisations(db, app_id)) == 1


def test_a_click_older_than_seven_days_is_not_sent(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    written, form = job(factory, stub, app_id, AT + WEEK)
    assert (written, form.gate_clicks) == ("authorisation_expired", [])
    reviewed(db, app_id, "authorisation_expired")
    assert authorisations(db, app_id) == []


def test_the_ticked_box_is_review_first_unchanged(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub, mode=Mode.review)
    assert app_id not in submit.due(db, AT + HOUR)
    assert submit.undecided_of(db, [app_id]) == set()
    svc_written, form = job(factory, stub, app_id, AT + HOUR)
    assert form.gate_clicks == []
    found = application(db, app_id)
    assert (found.state, found.mode) == (State.ready, Mode.review)
    assert authorisations(db, app_id) == []
    assert svc_written != "submitted"


# --- the guards kept ---------------------------------------------------------------------------


def test_the_persons_pause_sends_it_to_review(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    settings(db, person_a, paused=True)
    app_id = filled(db, factory, person_a, stub)
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == ("paused_by_you", [])
    reviewed(db, app_id, "paused_by_you")


@pytest.mark.parametrize(
    ("rest", "code"), [({"paused": True}, "paused"), ({"enabled": False}, "submit_disabled")]
)
def test_the_kill_switches_send_it_to_review(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    stub: str,
    rest: dict[str, bool],
    code: str,
) -> None:
    app_id = filled(db, factory, person_a, stub)
    written, form = job(factory, stub, app_id, AT + HOUR, **rest)
    assert (written, form.gate_clicks) == (code, [])
    reviewed(db, app_id, code)


def test_the_safety_ceiling_holds_for_one_click_apply(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    settings(db, person_a, safety_ceiling=2)
    for company in ("Northwind Labs", "Contoso Robotics"):
        a11.clicked(db, person_a.user_id, AT, company=company)
    db.commit()
    app_id = filled(db, factory, person_a, stub)
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == ("safety_ceiling", [])
    reviewed(db, app_id, "safety_ceiling")


def test_full_autos_one_per_company_rule_is_dropped(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    posting = db.get(Job, person_a.posting_id)
    assert posting is not None
    a11.clicked(db, person_a.user_id, AT - 2 * HOUR, company=posting.company, title="Other Role")
    db.commit()
    app_id = filled(db, factory, person_a, stub)
    assert job(factory, stub, app_id, AT + HOUR)[0] == "approved"


def test_a_duplicate_role_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    posting = db.get(Job, person_a.posting_id)
    assert posting is not None
    a11.clicked(db, person_a.user_id, AT, company=posting.company, title=posting.title)
    db.commit()
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert form.gate_clicks == []
    found = application(db, app_id)
    assert found.state is State.blocked
    assert found.error_code == written


def test_the_host_pace_waits_and_keeps_the_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    form_url = application(db, app_id).form_url.replace("example-robotics", "northwind")
    other = a11.clicked(
        db, person_a.user_id, AT, company="Northwind Labs", ats="other", form_url=form_url
    )
    for i in range(20):
        a11.click(db, other, AT + i * timedelta(seconds=1))
    db.commit()
    assert job(factory, stub, app_id, AT + HOUR)[0] == "approved"
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == ("paced", [])
    found = application(db, app_id)
    assert (found.state, found.needs, found.mode) == (State.approved, "host_pace", Mode.direct)
    written, form = job(factory, stub, app_id, AT + timedelta(days=1, hours=1))
    assert (written, len(form.gate_clicks)) == ("submitted", 1)
    assert len(authorisations(db, app_id)) == 1


def test_free_text_without_a_passed_model_check_goes_to_review(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    agent_wrote(db, app_id)
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == (model_check.NOT_RUN_CODE, [])
    reviewed(db, app_id, model_check.NOT_RUN_CODE)


def test_free_text_with_a_passed_model_check_is_sent(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    agent_wrote(db, app_id)
    calls: list[model_check.Request] = []

    async def passing(request: model_check.Request) -> model_check.Reply:
        calls.append(request)
        return model_check.Reply(model_check.Reported.model_validate({"passed": True}))

    from tests.apply.test_a6_gate import submit_ashby, submit_services
    from tests.apply.test_a6_recheck import at

    svc = submit_services(factory, submit_ashby(), stub)
    svc.clock = at(AT + HOUR)
    svc.check = passing
    written = __import__("asyncio").run(submit.decision(svc, app_id))
    assert written == "approved", written
    assert len(calls) == 1
    assert run_submit(svc, app_id) == "submitted"


def test_the_company_site_and_the_persons_stops(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """A stop the person clears keeps the item direct; any other hand over makes it review."""
    app_id = filled(db, factory, person_a, stub)
    found = application(db, app_id)
    full_auto.hand_over(db, found, "signin", AT, keep_direct=True)
    assert found.mode is Mode.direct
    full_auto.hand_over(db, found, "company_site_unverified", AT)
    assert (found.mode, found.dry_run) == (Mode.review, False)
    db.rollback()


def test_a_sign_in_then_the_application_goes_on_and_is_sent(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = filled(db, factory, person_a, stub)
    db.execute(
        sa.update(Application)
        .where(Application.id == app_id)
        .values(state=State.needs_person.value, needs="signin")
    )
    db.commit()
    assert app_id not in submit.due(db, AT + HOUR)
    assert signin.signed_in(db, person_a.user_id, app_id, AT + HOUR)
    db.commit()
    found = application(db, app_id)
    assert (found.state, found.mode) == (State.queued, Mode.direct)
    agent = FakeAgent(lambda q: (WHY_TEXT, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stub), app_id) == "ready"
    answered(db, app_id)
    assert job(factory, stub, app_id, AT + 2 * HOUR)[0] == "approved"
    written, form = job(factory, stub, app_id, AT + 2 * HOUR)
    assert (written, len(form.gate_clicks)) == ("submitted", 1)


def test_the_decision_refuses_another_mode() -> None:
    assert direct.CLICK_VALID == WEEK
    assert full_auto.gate_kind(Mode.direct) is AuthorisationKind.apply_click


# --- the documents and the reasons the job page gives (review of #739) -------------------------


def _overflowing(db: Session, app_id: uuid.UUID) -> None:
    found = application(db, app_id)
    found.document_versions = {
        k: {**v, "fit": "overflow"} for k, v in found.document_versions.items()
    }
    db.commit()


def _corrected(db: Session, app_id: uuid.UUID) -> None:
    found = application(db, app_id)
    found.document_versions = {
        k: {**v, "corrections": ["A claim the fact check changed"]}
        for k, v in found.document_versions.items()
    }
    db.commit()


@pytest.mark.parametrize("spoil", [_overflowing, _corrected])
def test_documents_that_need_a_look_are_never_sent_by_one_click(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str, spoil: Any
) -> None:
    """Guardrail 6 holds for `direct`: the item goes to review with its reason, no click."""
    low_fit(db, person_a)
    app_id = filled(db, factory, person_a, stub)
    spoil(db, app_id)
    assert model_check.request_for(db, app_id, AT + HOUR) == "refused_anyway"
    db.rollback()
    written, form = job(factory, stub, app_id, AT + HOUR)
    assert (written, form.gate_clicks) == ("documents_review", [])
    reviewed(db, app_id, "documents_review")
    assert gate.SENTENCES["documents_review"]
    written, form = job(factory, stub, app_id, AT + 2 * HOUR)
    assert form.gate_clicks == []
    assert written != "submitted"


def test_a_review_only_site_says_why_once_the_form_is_filled(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    """The hand over before the fill (run.py, `review_only_site`) survives the fill's own write."""
    app_id = started(db, person_a, Mode.direct)
    full_auto.hand_over(db, application(db, app_id), "review_only_site", AT)
    db.commit()
    agent = FakeAgent(lambda q: (WHY_TEXT, "motivation") if q.label == WHY else ("leave", ""))
    assert fill(services(factory, run_ashby(), None, agent, stub_base=stub), app_id) == "ready"
    reviewed(db, app_id, "review_only_site")
    assert direct.SENTENCES["review_only_site"]


def test_an_email_posting_says_why_after_the_connection(db: Session, person_a: Person) -> None:
    restore = canary.block_google()
    try:
        found = a14.prepare(db, person_a, mode=Mode.direct)
        assert (found.mode, found.state) == (Mode.review, State.needs_person)
        fake = FakeGoogle()
        fake.seed("alice")
        a14.connect(db, person_a, fake)
        assert email.recover(db, found, AT, configured=True)
        db.commit()
        reviewed(db, found.id, "email_review")
        assert direct.SENTENCES["email_review"]
    finally:
        restore()


def test_the_apply_mode_is_the_only_way_to_direct(api: Api, person_a: Person) -> None:
    token = api.login_token(person_a.email)
    path = f"/api/postings/{person_a.posting_id}/applications"
    assert api.post(path, token=token, json={"mode": "direct"}).status_code == 422
