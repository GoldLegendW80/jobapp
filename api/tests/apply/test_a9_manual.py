"""Apply myself (apply A9, app/apply/manual.py): the session on an `apply_manual` lease, the
panel's commands, the watch, and the record, on a real Postgres with the fake lease store and
A4's modelled Ashby form. The person's own clicks in the live view are steps of the fake panel;
nothing here reaches a browser, a model or a company."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import gate, manual, service, states
from app.apply.models import Application, ApplicationEvent, Mode, State
from app.documents.models import DocumentVersion
from app.identity import security
from app.jobs.models import Job, JobStatus
from app.main import create_app
from tests.apply.a5_support import FakeLeases, RunForm, run_ashby
from tests.apply.a6_support import FakeQueue
from tests.apply.conftest import AT, Person, add_posting
from tests.apply.test_a5_run import Clock, application, events, located, status
from tests.apply.test_start import SECURITY_RULE, rules
from tests.conftest import make_settings
from tests.identity.api_helpers import PASSWORD, Api

CONFIRMED = '- heading "Thank you for applying to Example Robotics" [level=1]'
Step = str | Callable[[], None] | None


@pytest.fixture
def stub(forms_base_url: str) -> str:
    return forms_base_url


class ManualForm(RunForm):
    """The modelled form, plus what the person does in the live view: open a file chooser,
    click the site's own Submit (`sent`, the page then confirms)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.chooser = False
        self.sent = False
        self.answered: list[list[str]] = []

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        if name == "browser_snapshot":
            self._log("watch", passive=args.get("passive"))
            if self.sent:
                return {"ok": True, "url": self.url, "snapshot": CONFIRMED}
            return {"ok": True, "url": self.url, "snapshot": self.render(), "chooser": self.chooser}
        if name == "browser_file_upload" and "ref" not in args:
            self._log("chooser", paths=args["paths"])
            if not self.chooser:
                return {"ok": False, "error": "no_chooser"}
            self.chooser = False
            self.answered.append(list(args["paths"]))
            return {"ok": True}
        raise AssertionError(name)


def manual_ashby(**rest: Any) -> ManualForm:
    form = run_ashby(**rest)
    return ManualForm(form.fields, submit=form.submit, title=form.title)


@dataclass
class FakeChannel:
    """The panel: each pop takes the next step, a command or the person's action (a callable,
    run then answered as no command). Past the script, the panel closes the session."""

    script: list[Step] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)

    async def push(self, application_id: uuid.UUID, command: str) -> bool:
        self.pushed.append(command)
        return True

    async def pop(self, application_id: uuid.UUID, timeout_s: float) -> str | None:
        if not self.script:
            return "close"
        step = self.script.pop(0)
        if callable(step):
            step()
            return None
        return step

    async def put_status(self, application_id: uuid.UUID, status: dict[str, Any]) -> None:
        self.statuses.append(status)

    async def status(self, application_id: uuid.UUID) -> dict[str, Any]:
        return self.statuses[-1] if self.statuses else {}


@dataclass
class FakeBrowser:
    """browserd's lease and the live view's input: who holds it, in order."""

    idle_left_s: int = 240
    gone: bool = False
    inputs: list[str] = field(default_factory=list)
    lease_id: str = "lease-1"

    async def current(self) -> dict[str, Any]:
        if self.gone:
            return {"lease": None}
        return {"lease": {"lease_id": self.lease_id, "idle_left_s": self.idle_left_s}}

    async def live_input(self, lease_id: str, viewer: str | None, giver: str | None = None) -> str:
        self.inputs.append("app" if viewer is None else "person")
        return "agent" if viewer is None else "person"


class Ticks:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 5.0
        return self.now


def manual_services(
    factory: sessionmaker[Session],
    form: ManualForm,
    channel: FakeChannel,
    browser: FakeBrowser | None = None,
    leases: FakeLeases | None = None,
    stub: str | None = None,
) -> manual.Services:
    from app.apply import run

    fill = run.Services(
        sessions=factory,
        clock=Clock(),
        leases=leases or FakeLeases(),
        tools_for=lambda user, lease, token: form,
        stub_base=stub,
    )
    return manual.Services(fill, channel, browser or FakeBrowser())


def start_manual(db: Session, person: Person) -> uuid.UUID:
    located(db, person)
    return service.start(
        db, person.user_id, person.posting_id, Mode.manual, False, now=AT
    ).application.id


def session(svc: manual.Services, application_id: uuid.UUID) -> str:
    return asyncio.run(manual.session(svc, application_id, monotonic=Ticks()))


def frozen(db: Session, *version_ids: uuid.UUID) -> list[str | None]:
    db.expire_all()
    rows = dict(
        db.execute(
            sa.select(DocumentVersion.id, DocumentVersion.frozen_reason).where(
                DocumentVersion.id.in_(version_ids)
            )
        ).all()
    )
    return [getattr(rows.get(v), "value", rows.get(v)) for v in version_ids]


def record(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID, seen: bool) -> None:
    with factory() as s:
        manual.record(s, person.user_id, app_id, AT, seen=seen)
        s.commit()


# --- the session ---------------------------------------------------------------------------------


def test_the_person_attaches_the_cv_submits_and_records_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    form, leases = manual_ashby(), FakeLeases()

    def sent() -> None:
        form.sent = True

    channel = FakeChannel(
        ["attach_cv", sent, None, lambda: record(factory, person_a, app_id, True)]
    )
    assert session(manual_services(factory, form, channel, leases=leases, stub=stub), app_id) == (
        "submitted"
    )

    assert leases.acquired[0]["purpose"] == "apply_manual"
    assert leases.acquired[0]["automation"] is True
    assert leases.released[-1][2] == "apply_manual_done"
    assert form.placed.keys() == {"cv.pdf", "letter.pdf"}
    assert form.spec("Resume").value == "cv.pdf"
    assert form.spec("Cover letter").value == ""
    assert form.submit_clicks == 0
    assert not [c for c in form.calls if c[0] == "click" and "Submit" in str(c[1])]
    assert any(s.get("confirmation_seen") for s in channel.statuses)
    found = application(db, app_id)
    assert found.state is State.submitted
    assert found.submitted_at == AT
    assert status(db, person_a) is JobStatus.applied
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == ["apply", "apply"]
    kinds = [e.kind for e in events(db, app_id)]
    assert kinds[:3] == ["created", "lease", "placed"]
    assert "recorded" in kinds
    recorded = next(e for e in events(db, app_id) if e.kind == "recorded")
    assert recorded.data == {"by": "confirmation", "frozen": 2}


def test_closing_without_submitting_cancels_and_freezes_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    channel = FakeChannel([None, "close"])
    assert session(manual_services(factory, manual_ashby(), channel, stub=stub), app_id) == (
        "cancelled"
    )
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.cancelled, "closed")
    assert frozen(db, person_a.cv_version_id) == [None]
    assert status(db, person_a) is not JobStatus.applied
    assert channel.statuses[-1]["phase"] == "ended"


def test_the_person_closing_from_the_page_ends_the_session(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)

    def closed() -> None:
        with factory() as s:
            manual.cancel(s, person_a.user_id, app_id, AT)
            s.commit()

    channel = FakeChannel([closed, None, None, None])
    session(manual_services(factory, manual_ashby(), channel, stub=stub), app_id)
    assert application(db, app_id).state is State.cancelled
    assert channel.script == [None, None, None]  # the loop saw the state, and stopped


def test_a_lease_that_ends_under_the_session_cancels_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    browser = FakeBrowser(gone=True)
    channel = FakeChannel([None, None])
    assert session(
        manual_services(factory, manual_ashby(), channel, browser, stub=stub), app_id
    ) == ("cancelled")
    assert application(db, app_id).error_code == "lease_ended"


def test_the_idle_warning_shows_a_minute_before_the_timeout(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    browser = FakeBrowser(idle_left_s=61)

    def later() -> None:
        browser.idle_left_s = 60

    channel = FakeChannel([None, later, None])
    session(manual_services(factory, manual_ashby(), channel, browser, stub=stub), app_id)
    shown = [s["idle_warning"] for s in channel.statuses if s["phase"] == "open"]
    assert shown[0] is False
    assert shown[-1] is True


def test_fill_my_details_types_identity_fields_only_with_the_input_to_the_app(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    form, browser = manual_ashby(), FakeBrowser()
    form.spec("Phone").value = "+33 1 00 00 00 00"  # the person typed it already
    channel = FakeChannel(["fill_details"])
    session(manual_services(factory, form, channel, browser, stub=stub), app_id)

    identity = full("identity")
    assert form.spec("Full name").value == identity["full_name"]
    assert form.spec("Email").value == identity["email"]
    assert form.spec("Phone").value == "+33 1 00 00 00 00"
    assert form.spec("Why Example Robotics?").value == ""
    assert form.spec("Resume").value == ""
    assert not form.spec("I agree to the processing of my data for this application.").checked
    assert form.spec("Will you now or in the future require visa sponsorship?").value == ""
    assert browser.inputs == ["app", "person"]
    note = next(e for e in events(db, app_id) if e.kind == "manual")
    assert note.data["action"] == "fill_details"
    assert note.data["outcome"] == "filled"


def test_a_chooser_the_person_opened_is_answered_with_the_document_named(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    form = manual_ashby()

    def opened() -> None:
        form.chooser = True

    channel = FakeChannel([opened, None, "attach_letter"])
    session(manual_services(factory, form, channel, stub=stub), app_id)
    assert form.answered == [["letter.pdf"]]
    assert any(s.get("chooser") for s in channel.statuses)
    assert form.spec("Cover letter").value == ""  # through the chooser, not an input of the form


def test_a_chooser_with_no_document_named_sets_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    form = manual_ashby()

    def opened() -> None:
        form.chooser = True

    channel = FakeChannel([opened, None, None])
    session(manual_services(factory, form, channel, stub=stub), app_id)
    assert form.answered == []
    assert not [c for c in form.calls if c[0] in ("upload", "chooser")]
    assert channel.statuses[-2]["chooser"] is True


def test_two_inputs_read_as_the_cv_ask_the_person_to_pick(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    form = manual_ashby()
    form.spec("Cover letter").label = "Resume"
    channel = FakeChannel(["attach_cv"])
    session(manual_services(factory, form, channel, stub=stub), app_id)
    assert not [c for c in form.calls if c[0] == "upload"]
    assert any(
        s.get("last") == {"action": "attach_cv", "outcome": "pick"} for s in channel.statuses
    )


def test_waiting_behind_another_lease_keeps_the_application_queued(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    app_id = start_manual(db, person_a)
    channel = FakeChannel()
    svc = manual_services(factory, manual_ashby(), channel, leases=FakeLeases(waiting=2), stub=stub)
    assert session(svc, app_id) == "waiting_lease"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.queued, "browser_queue")
    assert channel.statuses == [{"phase": "waiting", "position": 2}]


def test_a_never_apply_posting_opens_with_its_warning(
    db: Session, factory: sessionmaker[Session], person_a: Person, stub: str
) -> None:
    db.execute(
        sa.update(Job).where(Job.id == person_a.posting_id).values(title="Cybersecurity Engineer")
    )
    rules(db, person_a.user_id, SECURITY_RULE)
    app_id = start_manual(db, person_a)
    created = next(e for e in events(db, app_id) if e.kind == "created")
    assert created.data["warnings"] == ["never_apply_role"]
    form = manual_ashby()
    channel = FakeChannel([None, "close"])
    assert session(manual_services(factory, form, channel, stub=stub), app_id) == "cancelled"
    assert ("navigate", {"url": form.url}) in form.calls  # the form opened for the person


# --- the record ----------------------------------------------------------------------------------


def test_recording_twice_records_once_and_freezes_once(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = start_manual(db, person_a)
    record(factory, person_a, app_id, False)
    with factory() as s, pytest.raises(gate.RefusedError) as again:
        manual.record(s, person_a.user_id, app_id, AT, seen=False)
    assert again.value.code == "already_recorded"
    recorded = [e for e in events(db, app_id) if e.kind == "recorded"]
    assert [e.data for e in recorded] == [{"by": "person", "frozen": 0}]
    applied = db.execute(
        sa.select(sa.func.count())
        .select_from(ApplicationEvent)
        .where(ApplicationEvent.application_id == app_id)
    ).scalar_one()
    assert applied == 2  # created, recorded


def test_the_manual_move_is_for_manual_applications_only(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    located(db, person_a)
    review = service.start(db, person_a.user_id, person_a.posting_id, Mode.review, False, now=AT)
    with factory() as s, pytest.raises(gate.RefusedError) as refused:
        manual.record(s, person_a.user_id, review.application.id, AT, seen=False)
    assert refused.value.code == "not_manual"
    for mode in (Mode.review, Mode.full_auto):
        for current in states.MANUAL_SENT_FROM:
            row = Application(mode=mode, state=current, submitted_at=None)
            with pytest.raises(states.TransitionError):
                states.move(row, State.submitted, AT)
    for current in states.MANUAL_SENT_FROM:
        row = Application(mode=Mode.manual, state=current, submitted_at=None)
        states.move(row, State.submitted, AT)
        assert row.state is State.submitted
    for current in (State.ready, State.cancelled, State.blocked, State.failed):
        row = Application(mode=Mode.manual, state=current, submitted_at=None)
        with pytest.raises(states.TransitionError):
            states.move(row, State.submitted, AT)


def test_another_person_never_records_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    app_id = start_manual(db, person_a)
    with factory() as s, pytest.raises(gate.NotFoundError):
        manual.record(s, person_b.user_id, app_id, AT, seen=False)
    with factory() as s, pytest.raises(gate.NotFoundError):
        manual.cancel(s, person_b.user_id, app_id, AT)
    assert application(db, app_id).state is State.queued


def test_a_closed_application_cannot_be_recorded(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = start_manual(db, person_a)
    with factory() as s:
        manual.cancel(s, person_a.user_id, app_id, AT)
        s.commit()
    with factory() as s, pytest.raises(gate.RefusedError) as refused:
        manual.record(s, person_a.user_id, app_id, AT, seen=False)
    assert refused.value.code == "not_open"


# --- the routes ----------------------------------------------------------------------------------


SECRET_KEY = "e2e-only-not-a-secret-" + "1" * 24


@pytest.fixture
def api_app(migrated_url: str, stub: str) -> FastAPI:
    settings = make_settings(migrated_url, secret_key=SECRET_KEY, apply_stub_base_url=stub)
    app = create_app(settings, clock=lambda: AT)
    app.state.queue = FakeQueue()
    return app


def test_the_routes(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    api_app: FastAPI,
) -> None:
    with factory() as s:
        for person in (person_a, person_b):
            s.execute(
                sa.text("UPDATE users SET password_hash = :h WHERE id = :u"),
                {"h": security.hash_password(PASSWORD), "u": person.user_id},
            )
        s.commit()
    with TestClient(api_app, follow_redirects=False) as client:
        api_app.state.queue = queue = FakeQueue()
        api = Api(client)
        token_a, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
        located(db, person_a)
        started_ = api.post(
            f"/api/postings/{person_a.posting_id}/applications",
            token=token_a,
            json={"mode": "manual"},
        )
        assert started_.status_code == 201
        base = f"/api/applications/{started_.json()['id']}/manual"

        assert api.get(base, token=token_b).status_code == 404
        assert api.post(f"{base}/open", token=token_b).status_code == 404
        assert api.post(f"{base}/record", token=token_b, json={}).status_code == 404
        assert api.post(f"{base}/close", token=token_b).status_code == 404

        opened = api.post(f"{base}/open", token=token_a)
        assert opened.status_code == 202
        assert opened.headers["Cache-Control"] == "no-store"
        assert [e[0] for e in queue.enqueued] == ["apply_manual"]
        assert queue.enqueued[0][3] == manual.job_id(started_.json()["id"])
        api.post(f"{base}/open", token=token_a)
        assert len(queue.enqueued) == 1

        not_open = api.post(f"{base}/actions", token=token_a, json={"action": "attach_cv"})
        assert not_open.status_code == 409
        assert not_open.json()["code"] == "no_session"
        bad = api.post(f"{base}/actions", token=token_a, json={"action": "click_submit"})
        assert bad.status_code == 422
        assert api.post(f"{base}/record", token=token_a, json={"seen": "yes"}).status_code == 422
        assert api.post(f"{base}/record", token=token_a, json={}, csrf=False).status_code == 403

        view = api.get(base, token=token_a).json()
        assert view["state"] == "queued"
        assert view["confirmation_seen"] is False

        done = api.post(f"{base}/record", token=token_a, json={"seen": False})
        assert done.status_code == 200
        assert done.json()["state"] == "submitted"
        twice = api.post(f"{base}/record", token=token_a, json={"seen": False})
        assert twice.status_code == 409
        assert twice.json()["code"] == "already_recorded"
        closing = api.post(f"{base}/open", token=token_a)
        assert closing.json()["code"] == "not_open"

        posting, _ = add_posting(db, person_a.user_id, person_a_url(db, person_a))
        db.commit()
        review = api.post(f"/api/postings/{posting}/applications", token=token_a, json={})
        if review.status_code == 201:
            other = f"/api/applications/{review.json()['id']}/manual"
            assert (
                api.post(f"{other}/record", token=token_a, json={}).json()["code"] == "not_manual"
            )
            assert api.post(f"{other}/open", token=token_a).json()["code"] == "not_manual"


def person_a_url(db: Session, person: Person) -> str:
    url = db.execute(sa.select(Job.url).where(Job.id == person.posting_id)).scalar_one()
    return url.replace("applied-ai-engineer", "robotics-engineer")


def full(section: str) -> dict[str, Any]:
    from tests.profile.conftest import full as made

    return made(section)


def test_the_session_job_outlives_the_leases_maximum() -> None:
    from app import worker

    functions = {f.name: f for f in worker.apply_queue().functions}
    assert functions["apply_manual"].timeout_s == 1800 + worker.APPLY_JOB_MARGIN_S
    assert functions["apply_manual"].keep_result_s == 0
    assert manual.job_id("x") == "apply-manual:x"
