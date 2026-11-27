"""The apply agent waits for the person (A25, owner 2026-11-26: "je veux pouvoir converser avec lui
ou prendre la main sur le navigateur"), on a real Postgres: `apply_ask_person`, the wait with its
three ways out (an answer typed in the app, the browser used then given back, nobody in time), a
Cancel during it, a code kept out of the app's box, a take over known to the applier, and
apply_done's true ends. The fake site of A19 answers the ask's browserd calls as `agent_ask` does;
the clock is virtual (the fake sleep moves it), and `hooks` run what the person does at a given
poll of the wait (browserd's lease read), never during the pace's pauses."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import applier, gate
from app.apply.models import Mode, State
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, events, run_ashby, services, started
from tests.apply.test_a6_gate import _password
from tests.apply.test_a19_applier import STEP_1, STEP_2, THANKS, Leases, Site, Stop
from tests.apply.test_a24_cancel import api_app, put  # noqa: F401  (api_app: the fixture)
from tests.identity.api_helpers import Api

WAIT_S = 60  # 30 polls of ASK_POLL_S
SENTENCE = "jobs.example.org sent a code to your e-mail. Type it in the browser, please."


class AskSite(Site):
    """A19's site with the ask of browserd (`agent_ask`) and its lease as `GET /leases/current`
    reads it: `where` is what the next read says (waiting, held, back, ended); `hooks[n]` runs at
    the n-th read."""

    def __init__(self, hooks: dict[int, Callable[[AskSite], None]] | None = None) -> None:
        super().__init__()
        self.where, self.hooks, self.polls = "waiting", dict(hooks or {}), 0
        self.begun: list[int] = []
        self.resumed = 0

    async def ask_begin(self, left_s: int, ask: str = "", code: bool = False) -> str:
        self.begun.append(left_s)
        self.ask, self.takes_code = ask, code
        return "jobs.example.org"

    async def ask_state(self) -> str:
        self.polls += 1
        if (hook := self.hooks.pop(self.polls, None)) is not None:
            hook(self)
        return self.where

    async def ask_resume(self) -> None:
        self.resumed += 1


def svc(factory: sessionmaker[Session], site: AskSite, script: Any) -> applier.Services:
    fill = services(factory, run_ashby(), Leases())
    fill.tools_for = lambda user, lease, token: site
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds

    fill.sleep = sleep
    return applier.Services(
        fill=fill, agent=script, stop=Stop(), monotonic=lambda: clock[0], wait_s=WAIT_S
    )


def go_apply(services_: applier.Services, app_id: uuid.UUID) -> str:
    return asyncio.run(applier.apply(services_, app_id))


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


def answer(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID, text: str) -> None:
    with factory() as s:
        applier.answer_question(s, person.user_id, app_id, text, False, AT)
        s.commit()


def waits(db: Session, factory: sessionmaker[Session], app_id: uuid.UUID, kind: str) -> None:
    """A run of the agent asked and waits (its database step, no run)."""
    put(db, app_id, State.filling)
    with factory() as s:
        assert applier.asked(s, app_id, kind, SENTENCE, "jobs.example.org", WAIT_S, AT)
        s.commit()


def asking(kind: str = "code", *, after_submit: bool = False, then_submit: bool = True) -> Any:
    """Fills step 1, asks (after the Next, a submit element, when `after_submit`), then reads
    the page and sends what is left, or reports what it read."""

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        go.brief = json.loads(message)  # type: ignore[attr-defined]
        await session.navigate(go.brief["posting"]["url"])  # type: ignore[attr-defined]
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        if after_submit:
            await session.interact("browser_click", {"ref": "e6"})
        go.said = await session.ask_person(kind, SENTENCE)  # type: ignore[attr-defined]
        go.refused = await session.interact(  # type: ignore[attr-defined]
            "browser_type", {"ref": "e2", "text": "Alex Example"}
        )
        if session.stopped:
            return applier.Ran(5, 1)
        await session.snapshot()
        if then_submit and session.url == STEP_1:
            await session.interact("browser_click", {"ref": "e6"})
        if then_submit and session.url == STEP_2:
            await session.interact("browser_type", {"ref": "e7", "text": "Your robots."})
            await session.interact("browser_click", {"ref": "e9"})
            await session.snapshot()
        if session.url == THANKS:
            session.finish("submitted", "Sent.", session.url)
        else:
            session.finish("failed", "Stopped.", "", sent="no")
        return applier.Ran(5, 1)

    return go


def test_an_answer_typed_in_the_app_is_the_tool_s_answer_and_the_run_goes_on(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    seen: dict[str, Any] = {}

    def during(_: AskSite) -> None:
        found = application(db, app_id)
        seen["state"] = (found.state, found.needs)
        seen["question"] = applier.open_ask(db, found)
        answer(factory, person_a, app_id, "I clicked the link in the mail.")

    site, script = AskSite({1: during}), asking("confirmation_link")
    assert go_apply(svc(factory, site, script), app_id) == "submitted"
    assert seen["state"] == (State.needs_person, applier.WAITS)
    assert seen["question"].label == SENTENCE
    assert seen["question"].kind == "ask:confirmation_link"
    assert (site.begun, site.resumed) == ([WAIT_S], 1)
    text, error = script.said
    assert not error
    assert json.loads(text)["from_the_person"] == "I clicked the link in the mail."
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.submitted, None)
    said = kinds(db, app_id)
    assert said.index("agent_asked") < said.index("person_answered") < said.index("agent_goes_on")
    chat = applier.chat(db, found)
    assert [(line["who"], line["text"]) for line in chat] == [
        ("assistant", SENTENCE),
        ("person", "I clicked the link in the mail."),
    ]
    assert chat[0]["end"] == "answer"


def test_the_browser_used_then_given_back_says_so_and_the_page_is_read_before_any_act(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    """The person holds the browser past the wait's deadline (a take over begun in time runs to
    its Give back), sends the form there, and gives it back: the agent reads the confirmation,
    and the send is the person's (no act of the agent on a submit element)."""
    app_id = started(db, person_a, Mode.direct)

    def take(site: AskSite) -> None:
        site.where = "held"

    def send_and_give_back(site: AskSite) -> None:
        site.page, site.where = THANKS, "back"

    site = AskSite({1: take, 40: send_and_give_back})
    script = asking("signin", then_submit=False)
    assert go_apply(svc(factory, site, script), app_id) == "submitted"
    assert script.said == (applier.USED, False)
    text, error = script.refused
    assert error
    assert text.startswith("Refused: The person used the browser")
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submitted, applier.SENT_BY_PERSON)
    assert "agent_submit" not in kinds(db, app_id)
    assert site.resumed == 0  # browserd made it active: the person's Give back
    done = next(e for e in events(db, app_id) if e.kind == "agent_done")
    assert done.data["sent"] == "person"


def test_nobody_in_time_ends_the_run_needs_person_with_its_sentence_and_try_again(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    site, script = AskSite(), asking("captcha")
    assert go_apply(svc(factory, site, script), app_id) == "needs_person"
    assert script.said == (applier.ENDS[applier.PERSON_LATE], True)
    assert site.polls == WAIT_S // int(applier.ASK_POLL_S)
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, applier.ASKED)
    assert applier.open_ask(db, found).label == SENTENCE
    assert applier.chat(db, found)[0]["end"] == applier.PERSON_LATE
    assert "failed" not in kinds(db, app_id)
    assert kinds(db, app_id).count(applier.PERSON_LATE) == 1
    assert site.submissions == []
    # The app's box answers nothing once the run ended: Try again runs the agent once more.
    with factory() as s, pytest.raises(applier.QuestionError):
        applier.answer_question(s, person_a.user_id, app_id, "Too late?", False, AT)
    with factory() as s:
        gate.retry(s, person_a.user_id, app_id, AT)
        s.commit()
    assert application(db, app_id).state is State.queued
    again = asking("captcha")
    go_apply(svc(factory, AskSite(), again), app_id)
    assert again.brief["your_earlier_asks"] == [
        {"kind": "captcha", "you_asked": SENTENCE, "person_answered": None, "end": "person_late"}
    ]


def test_after_a_submit_act_nobody_in_time_is_submit_unknown_never_needs_person(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    seen: dict[str, Any] = {}

    def during(_: AskSite) -> None:
        found = application(db, app_id)
        seen["state"] = (found.state, found.needs)

    script = asking("confirmation_link", after_submit=True)
    assert go_apply(svc(factory, AskSite({1: during}), script), app_id) == "submit_unknown"
    assert seen["state"] == (State.submitting, applier.WAITS)
    found = application(db, app_id)
    assert (found.state, found.error_code, found.needs) == (
        State.submit_unknown,
        applier.PERSON_LATE,
        None,
    )
    assert applier.open_ask(db, found).label == SENTENCE  # shown beside It was sent


@pytest.mark.parametrize("after_submit", [False, True])
def test_a_cancel_during_the_wait_ends_it_at_once(
    db: Session, factory: sessionmaker[Session], person_a: Person, after_submit: bool
) -> None:
    app_id = started(db, person_a, Mode.direct)

    def cancel(_: AskSite) -> None:
        with factory() as s:
            gate.cancel(s, person_a.user_id, app_id, AT, reject=False)
            s.commit()

    site, script = AskSite({1: cancel}), asking("other", after_submit=after_submit)
    assert go_apply(svc(factory, site, script), app_id) == "superseded"
    assert script.said == (applier.ENDS["superseded"], True)
    assert site.polls == 1  # read at the next poll, not at the deadline
    found = application(db, app_id)
    expected = State.submit_unknown if after_submit else State.cancelled
    assert (found.state, found.error_code, found.needs) == (
        expected,
        gate.CANCELLED_BY_PERSON,
        None,
    )
    assert applier.chat(db, found)[0]["end"] == "cancelled"
    assert applier.PERSON_LATE not in kinds(db, app_id)


@pytest.mark.parametrize("end", ["activity_cancel", "crash", "worker_start"])
def test_a_run_cancelled_in_activity_or_dead_during_its_wait(
    db: Session, factory: sessionmaker[Session], person_a: Person, end: str
) -> None:
    """Activity's Cancel ends it as the application's Cancel does; a crash, or the worker's
    start, leaves the sentence and Try again, never `failed`."""
    app_id = started(db, person_a, Mode.direct)
    waits(db, factory, app_id, "other")
    with factory() as s:
        if end == "worker_start":
            assert applier.reset_interrupted(s, AT) == 1
        elif end == "crash":
            assert applier.interrupted(s, app_id, AT) == "kept"
        else:
            session = applier.ApplySession(tools=None, capability=None, stop=None, arm=None)  # type: ignore[arg-type]
            applier.finish(s, app_id, session, applier.Ran(stopped="cancelled"), AT)
        s.commit()
    found = application(db, app_id)
    if end == "activity_cancel":
        assert (found.state, found.error_code) == (State.cancelled, gate.CANCELLED_BY_PERSON)
        assert applier.chat(db, found)[0]["end"] == "cancelled"
    else:
        assert (found.state, found.needs) == (State.needs_person, applier.ASKED)
        assert applier.chat(db, found)[0]["end"] == "interrupted"
        assert "ask_interrupted" in kinds(db, app_id)
    assert applier.PERSON_LATE not in kinds(db, app_id)  # somebody may have come: not "late"


def test_a_code_is_never_taken_as_words_in_the_app_s_box(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    """Planted code: words to a run's code question that hold a code are refused (the code goes
    in the code box, `answer_code`, and words reach the model), and nothing of it is stored."""
    app_id = started(db, person_a, Mode.direct)
    waits(db, factory, app_id, "code")
    planted = str(uuid.uuid4().int)[:8]
    for words in (planted, f"The code is {planted[:4]} {planted[4:]}."):
        with factory() as s, pytest.raises(applier.QuestionError) as refused:
            applier.answer_question(s, person_a.user_id, app_id, words, False, AT)
        assert (refused.value.status, refused.value.detail) == (422, applier.CODE_ALONE)
    found = application(db, app_id)
    assert applier.open_ask(db, found).value["answer"] is None
    assert planted not in json.dumps([e.data for e in events(db, app_id)])
    assert planted not in json.dumps(applier.chat(db, found))
    # Words with no code still answer it: the person says what came, or did not.
    answer(factory, person_a, app_id, "No mail came: ask the site to send it again.")
    assert applier.open_ask(db, application(db, app_id)).value["answer"].startswith("No mail")


@pytest.mark.parametrize(
    ("outcome", "sent", "state", "code"),
    [
        ("failed", "no", State.failed, "agent_failed"),
        ("failed", "yes", State.submit_unknown, "send_clicked"),
        ("failed", "unsure", State.submit_unknown, "send_unsure"),
        ("posting_closed", "no", State.posting_closed, "posting_closed"),
        ("posting_closed", "unsure", State.submit_unknown, "send_unsure"),
    ],
)
def test_apply_done_says_whether_a_send_was_clicked_and_a_closed_posting_ends_closed(
    db: Session, factory: sessionmaker[Session], person_a: Person, outcome: str, sent: str,
    state: State, code: str,
) -> None:  # fmt: skip
    app_id = started(db, person_a, Mode.direct)

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        go.missing = session.finish(outcome, "The site says so.", "")  # type: ignore[attr-defined]
        session.finish(outcome, "The site says so.", "", sent=sent)
        return applier.Ran(1, 1)

    assert go_apply(svc(factory, AskSite(), go), app_id) == state.value
    text, error = go.missing  # type: ignore[attr-defined]
    assert error
    assert "say with `sent`" in text
    found = application(db, app_id)
    assert (found.state, found.error_code) == (state, code)


def test_the_detail_serves_the_question_and_the_chat_and_the_route_takes_the_answer(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    api_app: FastAPI,  # noqa: F811
) -> None:
    _password(factory, person_a, person_b)
    app_id = started(db, person_a, Mode.direct)
    waits(db, factory, app_id, "signin")
    path = f"/api/applications/{app_id}"
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token, token_b = api.login_token(person_a.email), api.login_token(person_b.email)
        detail = api.get(path, token=token).json()
        assert (detail["state"], detail["needs"]) == ("needs_person", applier.WAITS)
        question = detail["question"]
        assert (question["said_by"], question["sentence"]) == ("assistant", SENTENCE)
        assert (question["kind"], question["host"], question["live"]) == (
            "signin",
            "jobs.example.org",
            True,
        )
        assert question["until"].startswith("2026")
        assert not question["after_send"]
        assert api.post(f"{path}/question", json={"answer": "x"}, token=token_b).status_code == 404
        took = api.post(f"{path}/question", json={"answer": "I signed in."}, token=token)
        assert took.status_code == 200
        twice = api.post(f"{path}/question", json={"answer": "Again."}, token=token)
        assert twice.status_code == 409
        chat = api.get(path, token=token).json()["chat"]
        assert [(c["who"], c["text"]) for c in chat] == [
            ("assistant", SENTENCE),
            ("person", "I signed in."),
        ]
        count = api.get("/api/apply/queue/count", token=token).json()
        assert (count["waiting_on_you"], count["needs_you"]) == (1, 1)
    put(db, app_id, State.submitting, needs=applier.WAITS)  # asked after a submit act
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        count = api.get("/api/apply/queue/count", token=api.login_token(person_a.email)).json()
        assert (count["waiting_on_you"], count["needs_you"]) == (1, 1)


# --- review round 1 -----------------------------------------------------------------------------


@pytest.mark.parametrize("sent", ["yes", "unsure"])
def test_try_again_after_the_agent_said_it_clicked_send_runs_it_once_more_looking_first(
    db: Session, factory: sessionmaker[Session], person_a: Person, sent: str
) -> None:
    """A send the agent says it clicked through a control browserd did not mark is a possible
    send: Try again is one more run of the agent in the same mode, which looks on the site first
    (A19), never an empty Review first item."""
    from app.apply.router import _agent

    app_id = started(db, person_a, Mode.direct)

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        session.finish("failed", "I clicked Send, no confirmation came.", "", sent=sent)
        return applier.Ran(1, 1)

    assert go_apply(svc(factory, AskSite(), go), app_id) == "submit_unknown"
    found = application(db, app_id)
    assert found.error_code == applier.SAID_SENT[sent]
    assert applier.SAID_SENT_EVENT in kinds(db, app_id)
    assert _agent(db, found)  # the detail's `agent`: Try again runs the agent
    with factory() as s:
        gate.retry(s, person_a.user_id, app_id, AT)
        s.commit()
    found = application(db, app_id)
    assert (found.state, found.mode) == (State.queued, Mode.direct)
    retried = [e for e in events(db, app_id) if e.kind == "retry"]
    assert retried[-1].data["previous"] == State.submit_unknown.value
    again = asking("other")
    go_apply(svc(factory, AskSite(), again), app_id)
    assert again.brief["earlier_send"] is True


class UsedSite(AskSite):
    """The person took the browser over and sent the form there: browserd answers `person_used`
    to the agent's next call, whichever tool it is."""

    def __init__(self, tool: str) -> None:
        super().__init__()
        self.tool, self.armed = tool, False

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == self.tool and self.armed:
            self.armed, self.page = False, THANKS
            return {"ok": False, "error": "person_used", "message": "The person used it."}
        return await super().call(name, args)


@pytest.mark.parametrize("tool", ["browser_wait_for", "browser_take_screenshot", "browser_click"])
def test_a_take_over_answered_to_a_wait_a_screenshot_or_an_act_is_known_to_the_run(
    db: Session, factory: sessionmaker[Session], person_a: Person, tool: str
) -> None:
    app_id = started(db, person_a, Mode.direct)
    site = UsedSite(tool)

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        site.armed = True
        if tool == "browser_wait_for":
            go.told = await session.wait_for("Loaded", None)  # type: ignore[attr-defined]
        elif tool == "browser_click":
            go.told = await session.interact(tool, {"ref": "e6"})  # type: ignore[attr-defined]
        else:
            go.told = await session.screenshot()  # type: ignore[attr-defined]
        go.refused = await session.interact(  # type: ignore[attr-defined]
            "browser_type", {"ref": "e2", "text": "Alex Example"}
        )
        await session.snapshot()
        go.after = await session.interact(  # type: ignore[attr-defined]
            "browser_type", {"ref": "e2", "text": "Alex Example"}
        )
        await session.snapshot()
        session.finish("submitted", "The person sent it.", session.url)
        return applier.Ran(3, 1)

    assert go_apply(svc(factory, site, go), app_id) == "submitted"
    told, error = go.told  # type: ignore[attr-defined]
    assert error
    assert told.endswith(applier.USED)
    text, error = go.refused  # type: ignore[attr-defined]
    assert error
    assert text.startswith("Refused: The person used the browser")
    assert not go.after[1]  # type: ignore[attr-defined]  # the page read, the agent acts again
    assert [name for name, _ in site.calls].count("browser_type") == 1  # the refused one never
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submitted, applier.SENT_BY_PERSON)
    assert "person_used" in kinds(db, app_id)


def test_an_answer_at_the_deadline_is_kept_and_given_to_the_agent(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    """Two sessions: the answer commits after the poll read nothing and before the deadline's
    write; `late` reads it under the lock and keeps the run waiting, so the next poll gives it."""
    app_id = started(db, person_a, Mode.direct)

    def at_the_deadline(_: AskSite) -> None:
        answer(factory, person_a, app_id, "I signed in just now.")

    deadline = WAIT_S // int(applier.ASK_POLL_S)
    site, script = AskSite({deadline: at_the_deadline}), asking("signin")
    assert go_apply(svc(factory, site, script), app_id) == "submitted"
    assert site.polls == deadline
    text, error = script.said
    assert not error
    assert json.loads(text)["from_the_person"] == "I signed in just now."
    said = kinds(db, app_id)
    assert applier.PERSON_LATE not in said
    assert not [k for k in said if k.startswith("ask_")]
    assert applier.chat(db, application(db, app_id))[0]["end"] == "answer"


def test_an_answer_typed_as_the_browser_is_given_back_reaches_the_agent(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)

    def answer_and_give_back(site: AskSite) -> None:
        answer(factory, person_a, app_id, "I accepted the cookies and signed in.")
        site.where = "back"

    site, script = AskSite({2: answer_and_give_back}), asking("signin")
    assert go_apply(svc(factory, site, script), app_id) == "submitted"
    text, error = script.said
    assert not error
    said = json.loads(text)
    assert said["from_the_person"] == "I accepted the cookies and signed in."
    assert said["then"] == applier.USED
    assert script.refused[1]  # the page is read before any act
    assert applier.chat(db, application(db, app_id))[0]["end"] == "browser"


def test_the_lease_ending_during_the_wait_leaves_the_sentence_and_try_again(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)

    def lease_gone(site: AskSite) -> None:
        site.where = "ended"

    site, script = AskSite({3: lease_gone}), asking("captcha")
    assert go_apply(svc(factory, site, script), app_id) == "needs_person"
    assert script.said == (applier.ENDS["lease_ended"], True)
    found = application(db, app_id)
    assert (found.state, found.needs) == (State.needs_person, applier.ASKED)
    assert applier.chat(db, found)[0]["end"] == "run_ended"
    said = kinds(db, app_id)
    assert "ask_run_ended" in said
    assert applier.PERSON_LATE not in said
    with factory() as s:
        gate.retry(s, person_a.user_id, app_id, AT)
        s.commit()
    assert application(db, app_id).state is State.queued


def cut_by_the_time_limit(*, after_submit: bool) -> Any:
    """Asks, then the runner's time limit interrupts the tool (the hook cancels its task) and
    the run ends with the runner's code."""

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        if after_submit:
            await session.interact("browser_click", {"ref": "e6"})
        go.task = asyncio.ensure_future(session.ask_person("other", SENTENCE))  # type: ignore[attr-defined]
        with contextlib.suppress(asyncio.CancelledError):
            await go.task  # type: ignore[attr-defined]
        return applier.Ran(5, 1, "error")

    return go


@pytest.mark.parametrize("after_submit", [False, True])
def test_the_run_s_time_limit_during_the_wait(
    db: Session, factory: sessionmaker[Session], person_a: Person, after_submit: bool
) -> None:
    app_id = started(db, person_a, Mode.direct)
    script = cut_by_the_time_limit(after_submit=after_submit)
    site = AskSite({3: lambda _: script.task.cancel()})
    expected = State.submit_unknown if after_submit else State.needs_person
    assert go_apply(svc(factory, site, script), app_id) == expected.value
    found = application(db, app_id)
    if after_submit:
        assert (found.state, found.error_code, found.needs) == (expected, "error", None)
    else:
        assert (found.state, found.needs) == (expected, applier.ASKED)
    assert applier.chat(db, found)[0]["end"] == "run_ended"
    assert applier.PERSON_LATE not in kinds(db, app_id)


def test_the_wait_is_cut_to_the_run_s_time_left_and_the_brief_says_how_long(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    left = 100

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        go.brief = json.loads(message)  # type: ignore[attr-defined]
        await session.navigate(go.brief["posting"]["url"])  # type: ignore[attr-defined]
        session.run_until = session.monotonic() + left
        await session.ask_person("captcha", SENTENCE)
        return applier.Ran(1, 1)

    site = AskSite()
    assert go_apply(svc(factory, site, go), app_id) == "needs_person"
    cut = left - applier.ASK_MARGIN_S
    assert site.begun == [cut]
    assert site.polls == cut // int(applier.ASK_POLL_S)
    value = applier.open_ask(db, application(db, app_id)).value
    asked_at, until = (datetime.fromisoformat(value[k]) for k in ("asked_at", "until"))
    assert until - asked_at == timedelta(seconds=cut)
    assert go.brief["ask_waits_minutes"] == WAIT_S // 60  # type: ignore[attr-defined]
    assert "15 minutes" not in applier.PROMPT
