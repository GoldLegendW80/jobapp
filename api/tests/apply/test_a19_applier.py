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


CHOSEN = {
    "field": "Notice period *",
    "value": "One month",
    "why": "The profile names no notice period, and one month is the usual one.",
}


def plant_question(db: Session, app_id: uuid.UUID, user_id: uuid.UUID, *, sent: bool) -> None:
    """A question an A19 run asked before A20 (owner, 2026-11-06) and nobody answered yet: the
    application waits `needs_person`, or `submit_unknown` when it came after a possible send."""
    from app.apply import run

    found = application(db, app_id)
    db.add(
        ApplicationAnswer(
            application_id=app_id, user_id=user_id, field_key="agent:question:001",
            label="Notice period *", kind="question:fact", value=None,
            options=["jobs.example.org"], source="left", required=True,
            left_reason=applier.QUESTION,
        )
    )  # fmt: skip
    if sent:
        found.state, found.submitted_at = State.submit_unknown, AT
        run._event(db, found, "agent_submit", AT)
    else:
        found.state, found.needs = State.needs_person, applier.QUESTION
    db.commit()


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
        # A20: a required field the profile does not answer is the agent's to fill, never a
        # question; the answer it chose is named in its report.
        chosen = [] if "Notice period *" in answers else [CHOSEN]
        notice = answers.get("Notice period *", "One month")
        await session.interact("browser_type", {"ref": "e8", "text": notice})
        await session.interact("browser_click", {"ref": "e6"})
        if fail_after_submit:
            raise RuntimeError("the model stream broke")
        text = "Your robots are the ones I want to build, as my letter says."
        await session.interact("browser_type", {"ref": "e7", "text": text})
        await session.interact("browser_click", {"ref": "e9"})
        await session.snapshot()
        session.finish(
            "submitted", "Filled both steps, uploaded the CV and sent it.", session.url, chosen
        )
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
    # The emergency stop was read before the run, before each of the nine acts and again before
    # each of the two submit acts, and `submitting` was committed before the first one.
    assert stop.reads == 1 + 9 + 2
    assert kinds(db, app_id).index("agent_submit") < kinds(db, app_id).index("submitted")
    assert ("browser_click", True) in site.calls
    assert ("browser_type", True) not in site.calls
    report = db.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
    assert report.value == {
        "summary": "Filled both steps, uploaded the CV and sent it.",
        "confirmation_url": THANKS,
        "chosen": [],
    }
    frozen = next(e for e in events(db, app_id) if e.kind == "submitted")
    assert frozen.data["frozen"] >= 1


def test_a_required_field_the_profile_does_not_answer_is_filled_never_asked(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    """A20 (owner, 2026-11-06: "he never ask me a question"): the box unticked, the agent fills
    the field itself, the run ends `submitted`, and its chosen answer is in the run report."""
    app_id = started(db, person_a, Mode.direct)
    site = Site()
    assert apply(svc(factory, site, agent()), app_id) == "submitted"
    found = application(db, app_id)
    assert found.state is State.submitted
    assert found.needs is None
    assert applier.open_question(db, found) is None
    assert applier._questions(db, found) == []
    assert "needs_person" not in kinds(db, app_id)
    assert len(site.submissions) == 1
    assert site.submissions[0]["values"]["e8"] == "One month"
    assert ("browser_ask_person", False) not in site.calls
    report = db.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
    assert report.value["chosen"] == [
        {"label": "Notice period *", "value": "One month", "why": CHOSEN["why"]}
    ]


def test_an_a19_question_left_from_before_still_answers_and_resumes(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    plant_question(db, app_id, person_a.user_id, sent=False)
    question = applier.open_question(db, application(db, app_id))
    assert question is not None
    assert applier.sentence(question) == (
        'The assistant needs your answer for "Notice period *" on jobs.example.org.'
    )
    applier.answer_question(db, person_a.user_id, app_id, "Two months", True, AT)
    db.commit()
    assert application(db, app_id).state is State.queued
    saved = db.scalar(sa.select(AnswerBankEntry).where(AnswerBankEntry.user_id == person_a.user_id))
    assert (saved.question_key, saved.answer) == ("notice period", "Two months")
    script, site = agent(), Site()
    assert apply(svc(factory, site, script), app_id) == "submitted"
    assert script.brief["answers_to_your_questions"] == [
        {"field": "Notice period *", "host": "jobs.example.org", "answer": "Two months"}
    ]
    assert script.brief["saved_answers"] == {"notice period": "Two months"}
    assert site.submissions[0]["values"]["e8"] == "Two months"


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
    # Off before the run and the first eight acts (the first submit act's own read), then on
    # at the read just before that act is sent with the capability.
    site = Site()
    assert apply(svc(factory, site, agent(), Stop(*[None] * 8, "paused")), app_id) == "failed"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "paused")
    assert site.submissions == []
    assert ("browser_click", False) in site.calls  # the submit act was tried, refused
    assert ("browser_click", True) not in site.calls
    assert "agent_submit" not in kinds(db, app_id)


def test_the_emergency_stop_is_read_before_every_act_and_withdraws_the_capability(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    site, script = Site(), agent()
    # Off before the run and the navigation, on before the click on Apply: nothing more acts.
    session_ = svc(factory, site, script, Stop(None, None, "submit_disabled"))
    assert apply(session_, app_id) == "failed"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "submit_disabled")
    assert [name for name, _ in site.calls if name != "browser_snapshot"] == ["browser_navigate"]


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


# --- round 1 of the review: a question after a control that may have sent the form ------------

COOKIES = "c1"  # an untyped <button> (a submit in HTML), as most cookie banners' Accept


class WizardSite(Site):
    """A site as most are: a cookie banner whose Accept is an untyped button (browserd marks it
    a submit, so the run is `submitting` from there), and the required question the profile
    does not answer on step 2, behind Next."""

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        ref = args.get("ref")
        if name == "browser_click" and ref == COOKIES:
            self.calls.append((name, capability is not None))
            if capability is None:
                return {"ok": False, "error": "submit_permit", "message": "Refused."}
            return {"ok": True, "url": self.page, "submit_clicked": True}
        if name == "browser_click" and ref == "e6":
            self.calls.append((name, capability is not None))
            if capability is None:
                return {"ok": False, "error": "submit_permit", "message": "Refused."}
            self.page = STEP_2
            return {"ok": True, "url": self.page, "submit_clicked": True}
        if name == "browser_click" and ref == "e9" and capability is not None:
            self.calls.append((name, True))
            if self.values.get("e7") and self.values.get("e8"):
                self.submissions.append({"values": dict(self.values), "files": self.uploaded})
                self.page = THANKS
            return {"ok": True, "url": self.page, "submit_clicked": True}
        return super().act(name, args, capability)

    def text(self) -> str:
        return {
            POSTING: '- button "Accept all cookies" [ref=c1]\n- link "Apply" [ref=e1]',
            STEP_1: '- textbox "Full name" [ref=e2]\n- textbox "Email" [ref=e3]\n'
            '- button "Resume" [ref=e5]\n- button "Next" [ref=e6]',
            STEP_2: '- textbox "Notice period *" [ref=e8]\n- textbox "Why this company?" [ref=e7]'
            '\n- button "Submit application" [ref=e9]',
            THANKS: "Thank you, your application was received.",
        }.get(self.page, "")


def wizard_agent() -> Any:
    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        go.brief = brief  # type: ignore[attr-defined]
        await session.navigate(brief["posting"]["url"])
        await session.interact("browser_click", {"ref": COOKIES})
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e2", "text": "Alex Example"})
        await session.interact("browser_type", {"ref": "e3", "text": "e2e-alex@example.org"})
        await session.upload({"ref": "e5", "paths": brief["documents_to_upload"]})
        await session.interact("browser_click", {"ref": "e6"})
        answers = {a["field"]: a["answer"] for a in brief["answers_to_your_questions"]}
        notice = answers.get("Notice period *", "One month")
        await session.interact("browser_type", {"ref": "e8", "text": notice})
        await session.interact("browser_type", {"ref": "e7", "text": "Your robots, my letter."})
        await session.interact("browser_click", {"ref": "e9"})
        await session.snapshot()
        session.finish("submitted", "Filled both steps and sent it.", session.url)
        return applier.Ran(1000, 200)

    return go


def test_a_question_after_a_possible_send_is_shown_and_the_answer_is_the_person_s_try_again(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    from app.apply import run
    from app.apply.router import _question

    app_id = started(db, person_a, Mode.direct)
    site = WizardSite()
    # An A19 run asked after a control that may have sent the form, before A20 (planted).
    plant_question(db, app_id, person_a.user_id, sent=True)
    found = application(db, app_id)
    assert found.state is State.submit_unknown
    # Shown on the job page with the warning, and answerable.
    shown = _question(db, found)
    assert shown is not None
    assert (shown.label, shown.kind, shown.after_send) == ("Notice period *", "fact", True)
    assert site.submissions == []
    # Nobody runs it again without the person: neither the tick nor a stray job.
    assert app_id not in run.due(db)
    assert apply(svc(factory, site, wizard_agent()), app_id) == "not_queued"
    with pytest.raises(applier.QuestionError) as raised:
        applier.answer_question(db, person_b.user_id, app_id, "Two months", False, AT)
    assert raised.value.status == 404
    db.rollback()
    # The answer is the person's Try again.
    applier.answer_question(db, person_a.user_id, app_id, "Two months", False, AT)
    db.commit()
    found = application(db, app_id)
    assert found.state is State.queued
    retry = [e for e in events(db, app_id) if e.kind == "retry"]
    assert [e.data.get("previous") for e in retry] == ["submit_unknown"]
    assert app_id in run.due(db)
    script = wizard_agent()
    assert apply(svc(factory, site, script), app_id) == "submitted"
    assert script.brief["earlier_send"] is True
    assert script.brief["answers_to_your_questions"][-1]["answer"] == "Two months"
    assert len(site.submissions) == 1
    assert application(db, app_id).state is State.submitted
    assert apply(svc(factory, site, wizard_agent()), app_id) == "not_queued"
    assert len(site.submissions) == 1


def test_try_again_on_an_a19_application_queues_one_run_of_the_agent(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from app.apply import gate

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
    retried = gate.retry(db, person_a.user_id, app_id, AT)
    db.commit()
    # Never an empty Review first item: the same mode, queued for one more run of the agent.
    assert (retried.state, retried.mode) == (State.queued, Mode.direct)
    site, script = Site(), agent()
    assert apply(svc(factory, site, script), app_id) == "submitted"
    assert script.brief["earlier_send"] is True
    assert len(site.submissions) == 1
    assert apply(svc(factory, Site(), agent()), app_id) == "not_queued"


def test_a_confirmation_read_before_the_last_submit_act_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        await session.navigate(brief["posting"]["url"])
        before = session.url
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e8", "text": "Two months"})
        await session.interact("browser_click", {"ref": "e6"})  # a submit act
        go.refused = session.finish("submitted", "Sent.", before)  # type: ignore[attr-defined]
        return applier.Ran(1, 1)

    assert apply(svc(factory, Site(), go), app_id) == "submit_unknown"
    text, error = go.refused  # type: ignore[attr-defined]
    assert error
    assert text.startswith("Refused: read the confirmation page")
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, "no_confirmation")


@pytest.mark.parametrize(
    ("label", "secret"),
    [
        ("Password", True),
        ("Confirm your password *", True),
        ("Passwort", True),
        ("Kennwort wiederholen", True),
        ("Mot de passe", True),
        ("Mot de passe (8 caractères)", True),
        ("PIN", True),
        ("Verification code", True),
        ("Enter the code we sent to your e-mail", True),
        ("One-time code", True),
        ("Code de vérification", True),
        ("Bestätigungscode", True),
        ("Sicherheitscode", True),
        ("SMS code", True),
        ("OTP", True),
        ("2FA token", True),
        ("Captcha", True),
        ("Card number", True),
        ("Credit card", True),
        ("Numéro de carte bancaire", True),
        ("CVC", True),
        ("IBAN", True),
        ("Payment method", True),
        ("Moyen de paiement", True),
        ("Promo code", True),
        ("Programming languages you code in", True),  # "code" alone: fail closed, an action
        # Facts the person answers in the app's box.
        ("Postal code", False),
        ("ZIP code", False),
        ("Code postal", False),
        ("Postcode", False),
        ("Country code", False),
        ("Area code", False),
        ("Notice period *", False),
        ("What is your notice period?", False),
        ("Expected salary", False),
        ("Desired pay rate", False),
        ("Do you hold a security clearance?", False),
        ("Work permit expiry date", False),
        ("Residence permit (carte de séjour)", False),
        ("Années d'expérience", False),
        ("", False),
    ],
)
def test_a_label_that_reads_as_a_password_a_code_or_a_payment_is_an_action(
    label: str, secret: bool
) -> None:
    assert applier.secret_label(label) is secret


def test_the_agent_has_no_ask_tool_and_an_ask_that_reaches_the_session_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from app.agents import definitions

    assert "browser_ask_person" not in applier.TOOLS
    assert "browser_ask_person" not in definitions.get(applier.NAME, "test").tools
    app_id = started(db, person_a, Mode.direct)

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        await session.navigate(brief["posting"]["url"])
        go.asked = await session.ask_person("I need it.", "Apply", "fact")  # type: ignore[attr-defined]
        return applier.Ran(1, 1)

    site = Site()
    assert apply(svc(factory, site, go), app_id) == "failed"
    text, error = go.asked  # type: ignore[attr-defined]
    assert error
    assert text.startswith("Refused: nobody answers questions")
    assert all(name != "browser_ask_person" for name, _ in site.calls)
    assert applier._questions(db, application(db, app_id)) == []
    assert "needs_person" not in kinds(db, app_id)


def test_busy_runs_queue_the_application_again_until_the_retries_run_out(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    async def busy(session: applier.ApplySession, message: str) -> applier.Ran:
        return applier.Ran(stopped="busy")

    app_id = started(db, person_a, Mode.direct)
    assert apply(svc(factory, Site(), busy), app_id) == "queued"
    found = application(db, app_id)
    assert (found.state, found.needs, found.error_code) == (State.queued, "retry", "busy")
    retry = [e for e in events(db, app_id) if e.kind == "retry"]
    assert retry[-1].data["code"] == "busy"
    assert retry[-1].data["delay_s"] > 0
    db.execute(
        sa.update(applier.Application).where(applier.Application.id == app_id).values(attempt=99)
    )
    db.commit()
    assert apply(svc(factory, Site(), busy), app_id) == "failed"
    assert application(db, app_id).error_code == "busy"


def test_a_cancel_stops_the_agent_at_its_next_tool_call(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from app.apply import gate

    app_id = started(db, person_a, Mode.direct)
    site = Site()

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        with factory() as other:
            gate.cancel(other, person_a.user_id, app_id, AT, reject=False)
            other.commit()
        go.after = await session.interact("browser_click", {"ref": "e1"})  # type: ignore[attr-defined]
        return applier.Ran(1, 1)

    assert apply(svc(factory, site, go), app_id) == "superseded"
    assert application(db, app_id).state is State.cancelled
    assert go.after[1]  # type: ignore[attr-defined]
    assert ("browser_click", False) not in site.calls


def test_a_rehearsal_never_runs_the_agent(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    db.execute(
        sa.update(applier.Application).where(applier.Application.id == app_id).values(dry_run=True)
    )
    db.commit()
    site = Site()
    services_ = svc(factory, site, agent())
    assert apply(services_, app_id) == "dry_run"
    assert services_.fill.leases.acquired == []
    assert site.calls == []
