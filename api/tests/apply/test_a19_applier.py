"""One click Apply as on the owner's Mac (A19, owner 2026-11-05), on a real Postgres: the apply
agent's run (`applier.apply`) through a scripted agent on a fake job site that answers as
browserd's `apply_agent` lease does (a submit element refused without the capability with
`submit_permit`, a password box refused with `credential`). No browser, no model, no company:
every submission counted is one of the fake site's."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.apply import applier, service
from app.apply.models import AnswerBankEntry, ApplicationAnswer, Mode, State
from app.browser.leases import Acquired
from app.jobs.models import Job, JobStatus
from app.profile import service as profile_service
from tests.apply.a5_support import FakeLeases, Lease
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import (
    application,
    events,
    run_ashby,
    services,
    started,
    status,
)
from tests.profile.conftest import full

POSTING = "https://jobs.example.org/postings/42"
STEP_1 = "https://jobs.example.org/apply/42/step-1"
STEP_2 = "https://jobs.example.org/apply/42/step-2"
THANKS = "https://jobs.example.org/apply/42/thanks"
SUBMITS = {"e6", "e9"}  # Next and Submit application: buttons of the form, submits in HTML


class Client:
    def __init__(self, site: Site) -> None:
        self.site = site

    async def _tool(
        self, lease_id: str, token: str, name: str, args: dict[str, Any], capability: Any
    ) -> dict[str, Any]:
        return self.site.act(name, args, capability)


class Site:
    """A job site with an Apply leading to a two step form: a file upload, a password box, a
    required question the profile does not answer, a free text question, a confirmation."""

    def __init__(self, watched: bool = False) -> None:
        self.page, self.values, self.files = "about:blank", {}, {}
        self.uploaded: list[str] = []
        self.submissions: list[dict[str, Any]] = []
        self.calls: list[tuple[str, bool]] = []
        self.watched, self.user_id, self.lease_id, self._token = watched, "", "lease-1", "t"
        self.client = Client(self)

    async def _held(self) -> None:
        return None

    async def put_file(self, name: str, data: bytes) -> dict[str, Any]:
        self.files[name] = data
        return {"ok": True}

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.act(name, args or {}, None)

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        self.calls.append((name, capability is not None))
        ref = args.get("ref")
        if name == "browser_snapshot":
            return {"ok": True, "url": self.page, "title": "Example", "snapshot": self.text()}
        if name == "browser_navigate":
            self.page = POSTING
            return {"ok": True, "url": self.page}
        if name == "browser_ask_person":
            if self.watched:
                return {"ok": False, "error": "waiting_person", "left_s": 900}
            return {"ok": False, "error": "nobody_watching"}
        if name == "browser_type" and ref == "e4":
            return {"ok": False, "error": "credential", "message": "A password is the person's."}
        if ref in SUBMITS and capability is None:
            return {"ok": False, "error": "submit_permit", "message": "Refused."}
        if name == "browser_type":
            self.values[ref] = args["text"]
        elif name == "browser_file_upload":
            self.uploaded += args["paths"]
        elif name == "browser_click" and ref == "e1":
            self.page = STEP_1
        elif name == "browser_click" and ref == "e6" and self.values.get("e8"):
            self.page = STEP_2
        elif name == "browser_click" and ref == "e9" and self.values.get("e7"):
            self.submissions.append({"values": dict(self.values), "files": list(self.uploaded)})
            self.page = THANKS
        result: dict[str, Any] = {"ok": True, "url": self.page}
        return {**result, "submit_clicked": True} if ref in SUBMITS else result

    def text(self) -> str:
        return {
            POSTING: '- link "Apply" [ref=e1]',
            STEP_1: '- textbox "Full name" [ref=e2]\n- textbox "Email" [ref=e3]\n'
            '- textbox "Password" [ref=e4]\n- button "Resume" [ref=e5]\n'
            '- textbox "Notice period *" [ref=e8]\n- button "Next" [ref=e6]',
            STEP_2: '- textbox "Why this company?" [ref=e7]\n'
            '- button "Submit application" [ref=e9]',
            THANKS: "Thank you, your application was received.",
        }.get(self.page, "")


def agent(*, fail_after_submit: bool = False, try_password: bool = False) -> Any:
    """The scripted agent: what Claude does with the tools on the Mac, step by step."""

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        go.brief = brief  # type: ignore[attr-defined]
        await session.navigate(brief["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e2", "text": "Alex Example"})
        await session.interact("browser_type", {"ref": "e3", "text": "e2e-alex@example.org"})
        if try_password:
            go.password = await session.interact(  # type: ignore[attr-defined]
                "browser_type", {"ref": "e4", "text": "never-typed"}
            )
        await session.upload({"ref": "e5", "paths": brief["documents_to_upload"]})
        answers = {a["field"]: a["answer"] for a in brief["answers_to_your_questions"]}
        if "Notice period *" not in answers:
            await session.ask_person("I need the notice period.", "Notice period *", "fact")
            return applier.Ran(10, 2)
        await session.interact("browser_type", {"ref": "e8", "text": answers["Notice period *"]})
        await session.interact("browser_click", {"ref": "e6"})
        if fail_after_submit:
            raise RuntimeError("the model stream broke")
        text = "Your robots are the ones I want to build, as my letter says."
        await session.interact("browser_type", {"ref": "e7", "text": text})
        await session.interact("browser_click", {"ref": "e9"})
        await session.snapshot()
        session.finish("submitted", "Filled both steps, uploaded the CV and sent it.", session.url)
        return applier.Ran(1000, 200)

    return go


class Stop:
    def __init__(self, *codes: str | None) -> None:
        self.codes, self.reads = list(codes), 0

    async def __call__(self) -> str | None:
        self.reads += 1
        return self.codes.pop(0) if self.codes else None


class Leases(FakeLeases):
    async def acquire(self, user_id: str, purpose: str, automation: bool, **rest: Any) -> Any:
        self.acquired.append({"purpose": purpose, "hosts": list(rest.get("hosts", ()))})
        return Acquired(lease=Lease("lease-1"), token="t", capability="cap")


def svc(factory: sessionmaker[Session], site: Site, script: Any, stop: Stop | None = None) -> Any:
    fill = services(factory, run_ashby(), Leases())
    fill.tools_for = lambda user, lease, token: site
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    fill.sleep = sleep
    return applier.Services(
        fill=fill, agent=script, stop=stop or Stop(), monotonic=lambda: clock[0]
    )


def apply(services_: Any, app_id: uuid.UUID) -> str:
    return asyncio.run(applier.apply(services_, app_id))


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


def test_the_agent_fills_uploads_and_clicks_submit_itself(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    db.add(
        ApplicationAnswer(
            application_id=app_id, user_id=person_a.user_id, field_key="agent:question:001",
            label="Notice period *", kind="question:fact", value="Two months",
            options=["jobs.example.org"], source="person",
        )
    )  # fmt: skip
    db.commit()
    site, stop = Site(), Stop()
    services_ = svc(factory, site, agent(), stop)
    assert apply(services_, app_id) == "submitted"
    found = application(db, app_id)
    assert found.state is State.submitted
    assert found.submitted_at is not None
    assert status(db, person_a) is JobStatus.applied
    assert len(site.submissions) == 1
    assert site.submissions[0]["files"] == ["cv.pdf", "letter.pdf"]
    assert site.submissions[0]["values"]["e8"] == "Two months"
    assert services_.fill.leases.acquired == [{"purpose": "apply_agent", "hosts": []}]
    # The emergency stop was read before the run and before each of the two submit acts, and
    # `submitting` was committed before the first one.
    assert stop.reads == 3
    assert kinds(db, app_id).index("agent_submit") < kinds(db, app_id).index("submitted")
    assert ("browser_click", True) in site.calls
    assert ("browser_type", True) not in site.calls
    report = db.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
    assert report.value == {
        "summary": "Filled both steps, uploaded the CV and sent it.",
        "confirmation_url": THANKS,
    }
    frozen = next(e for e in events(db, app_id) if e.kind == "submitted")
    assert frozen.data["frozen"] >= 1


def test_a_missing_answer_waits_for_the_person_then_the_run_goes_on(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    site = Site()
    assert apply(svc(factory, site, agent()), app_id) == "needs_person"
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, applier.QUESTION)
    question = applier.open_question(db, found)
    assert question is not None
    assert question.label == "Notice period *"
    assert applier.sentence(question) == (
        'The assistant needs your answer for "Notice period *" on jobs.example.org.'
    )
    assert site.submissions == []
    assert ("browser_click", True) not in site.calls
    applier.answer_question(db, person_a.user_id, app_id, "Two months", True, AT)
    db.commit()
    saved = db.scalar(sa.select(AnswerBankEntry).where(AnswerBankEntry.user_id == person_a.user_id))
    assert (saved.question_key, saved.answer) == ("notice period", "Two months")
    script = agent()
    assert apply(svc(factory, Site(), script), app_id) == "submitted"
    assert script.brief["answers_to_your_questions"] == [
        {"field": "Notice period *", "host": "jobs.example.org", "answer": "Two months"}
    ]
    assert script.brief["saved_answers"] == {"notice period": "Two months"}


def test_an_answer_is_refused_when_nothing_waits_for_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    for user, code in ((person_a.user_id, 409), (person_b.user_id, 404)):
        with pytest.raises(applier.QuestionError) as raised:
            applier.answer_question(db, user, app_id, "Two months", False, AT)
        assert raised.value.status == code
        db.rollback()


def test_a_cybersecurity_posting_is_refused_before_the_run(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    profile = profile_service.get_profile(db, person_a.user_id, AT)
    search = {**full("search"), "never_apply": ["No security roles: Cybersecurity"]}
    profile_service.save_section(db, person_a.user_id, "search", search, profile.revision, AT)
    db.execute(
        sa.update(Job).where(Job.id == person_a.posting_id).values(title="Cybersecurity Engineer")
    )
    db.commit()
    site, services_ = Site(), None
    services_ = svc(factory, site, agent())
    assert apply(services_, app_id) == "blocked"
    found = application(db, app_id)
    assert found.state is State.blocked
    assert found.error_code.startswith("never_apply")
    assert services_.fill.leases.acquired == []
    assert site.calls == []


def test_the_emergency_stop_before_the_run_and_on_the_submit(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    site = Site()
    services_ = svc(factory, site, agent(), Stop("submit_disabled"))
    assert apply(services_, app_id) == "blocked"
    assert application(db, app_id).error_code == "submit_disabled"
    assert services_.fill.leases.acquired == []
    assert site.calls == []

    db.execute(sa.delete(applier.Application).where(applier.Application.id == app_id))
    db.commit()
    app_id = started(db, person_a, Mode.direct)
    db.add(
        ApplicationAnswer(
            application_id=app_id, user_id=person_a.user_id, field_key="agent:question:001",
            label="Notice period *", kind="question:fact", value="Two months",
            options=["jobs.example.org"], source="person",
        )
    )  # fmt: skip
    db.commit()
    site = Site()
    assert apply(svc(factory, site, agent(), Stop(None, "paused")), app_id) == "failed"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "paused")
    assert site.submissions == []
    assert ("browser_click", True) not in site.calls
    assert "agent_submit" not in kinds(db, app_id)


def test_after_a_submit_act_a_crash_is_submit_unknown_and_never_runs_again(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    db.add(
        ApplicationAnswer(
            application_id=app_id, user_id=person_a.user_id, field_key="agent:question:001",
            label="Notice period *", kind="question:fact", value="Two months",
            options=["jobs.example.org"], source="person",
        )
    )  # fmt: skip
    db.commit()
    assert apply(svc(factory, Site(), agent(fail_after_submit=True)), app_id) == "submit_unknown"
    assert application(db, app_id).state is State.submit_unknown
    site = Site()
    assert apply(svc(factory, site, agent()), app_id) == "not_queued"
    assert site.calls == []


def test_a_password_box_is_refused_to_the_agent(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    script, site = agent(try_password=True), Site()
    apply(svc(factory, site, script), app_id)
    answer, error = script.password
    assert error
    assert json.loads(answer)["error"] == "credential"
    assert "e4" not in site.values


def test_an_email_application_never_reaches_the_agent(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    db.execute(
        sa.update(Job)
        .where(Job.id == person_a.posting_id)
        .values(url=None, posting_text="Send your CV to jobs@example.org to apply.")
    )
    db.commit()
    app_id = service.start(
        db, person_a.user_id, person_a.posting_id, Mode.direct, False, now=AT
    ).application.id
    found = application(db, app_id)
    assert (found.mode, found.ats) == (Mode.review, "email")
    site = Site()
    assert apply(svc(factory, site, agent()), app_id) == "not_queued"
    assert site.calls == []
