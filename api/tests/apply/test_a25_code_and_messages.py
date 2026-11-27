"""The second slice of A25 (owner, 2026-11-26: "je veux pouvoir converser avec lui ou prendre la
main sur le navigateur"; who types a code received by e-mail or SMS: "les deux sont possibles"),
on a real Postgres: a code the person gives in the app goes to browserd for the ask that waits
for it and is typed there once by `apply_type_code`, never stored, logged or told to the model
(a planted code grepped in every table, log record, api answer and model message); and the
person's messages while the agent works, read with the answer of its next tool call. browserd is
A25's fake site (`AskSite`) with the code kept and typed as browserd's `give_code` and
`type_code` do (their own tables are in api/tests/browser/test_a25_type_code.py)."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import applier, gate, router
from app.apply.models import ApplicationAnswer, Mode, State
from app.browser.client import BrowserError
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, events, started
from tests.apply.test_a6_gate import _password
from tests.apply.test_a19_applier import THANKS, Stop
from tests.apply.test_a20_review_first import ticked
from tests.apply.test_a24_cancel import api_app, put  # noqa: F401  (api_app: the fixture)
from tests.apply.test_a25_waits import SENTENCE, AskSite, go_apply, kinds, svc, waits
from tests.identity.api_helpers import Api

BOX = "e10"  # the site's one time code box on step 1


class CodeSite(AskSite):
    """A19's site whose step 1 has a one time code box, with browserd's side of the code: kept
    for the open ask that takes one (`give`), dropped by the next ask, any other act and
    `erase_code`, typed once into that box only (`browser_type_code`), never answered back."""

    def __init__(self, hooks: dict[int, Callable[[AskSite], None]] | None = None) -> None:
        super().__init__(hooks)
        self.code: str | None = None
        self.typed: list[str] = []
        self.ask, self.takes_code = "", False

    async def ask_begin(self, left_s: int, ask: str = "", code: bool = False) -> str:
        self.code = None
        return await super().ask_begin(left_s, ask, code)

    def give(self, lease_id: str, ask_id: str, code: str) -> bool:
        if lease_id != self.lease_id or ask_id != self.ask or not self.takes_code:
            return False
        if self.where not in ("waiting", "held"):
            return False
        self.code = code
        return True

    def erase(self, _: str) -> None:
        self.code = None

    async def erase_code(self) -> None:
        await super().erase_code()
        self.code = None

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        if name == "browser_type_code":
            code, self.code = self.code, None
            if code is None:
                return {"ok": False, "error": "no_code", "message": "No code waits."}
            if args.get("ref") != BOX:
                return {"ok": False, "error": "not_code_box", "message": "Not a code box."}
            self.typed.append(code)
            return {"ok": True, "url": self.page, "code_typed": True}
        if name not in ("browser_snapshot", "browser_wait_for"):
            self.code = None
        if name == "browser_type" and args.get("ref") == BOX:
            return {"ok": False, "error": "credential", "message": "A code is the person's."}
        return super().act(name, args, capability)

    def text(self) -> str:
        shown = super().text()
        return f'{shown}\n- textbox "Verification code" [ref={BOX}]' if "e8" in shown else shown


class Client:
    """browserd's client as the answer route uses it, on the fake site."""

    def __init__(self, site: CodeSite, down: bool = False) -> None:
        self.site, self.down, self.erased = site, down, 0

    async def give_code(self, lease_id: str, user_id: str, ask: str, code: str) -> bool:
        if self.down:
            raise BrowserError("unavailable")
        return self.site.give(lease_id, ask, code)

    async def erase_code(self, lease_id: str, user_id: str) -> None:
        self.erased += 1
        self.site.erase(lease_id)


def coding(
    *, own_first: bool = False, then: Callable[[applier.ApplySession], Any] | None = None
) -> Any:
    """Fills step 1, asks for the code, types it with apply_type_code (after trying to type it
    itself when `own_first`), then sends the form; every text the model reads is in `seen`."""

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        go.seen = [message]  # type: ignore[attr-defined]

        async def told(result: Any) -> Any:
            go.seen.append(json.dumps(result, ensure_ascii=False))  # type: ignore[attr-defined]
            return result

        await told(await session.navigate(json.loads(message)["posting"]["url"]))
        await told(await session.interact("browser_click", {"ref": "e1"}))
        await told(await session.interact("browser_type", {"ref": "e8", "text": "One month"}))
        go.said = await told(await session.ask_person("code", SENTENCE))  # type: ignore[attr-defined]
        own = {"ref": BOX, "text": "123456"}
        if own_first:
            go.own = await told(await session.interact("browser_type", own))  # type: ignore[attr-defined]
        go.typed = await told(await session.type_code(BOX))  # type: ignore[attr-defined]
        go.again = await told(await session.type_code(BOX))  # type: ignore[attr-defined]
        if not own_first:
            go.own = await told(await session.interact("browser_type", own))  # type: ignore[attr-defined]
        if then is not None:
            await then(session)
        for act in (("browser_click", "e6"), ("browser_type", "e7"), ("browser_click", "e9")):
            args = {"ref": act[1], **({"text": "Your robots."} if act[1] == "e7" else {})}
            await told(await session.interact(act[0], args))
        await told(await session.snapshot())
        if session.url == THANKS:
            session.finish("submitted", "Sent.", session.url)
        else:
            session.finish("failed", "Stopped.", "", sent="no")
        return applier.Ran(5, 1)

    return go


def give(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID, code: str,
         site: CodeSite) -> None:  # fmt: skip
    with factory() as s:
        applier.answer_code(s, person.user_id, app_id, code, AT, site.give, site.erase)
        s.commit()


def test_a_code_given_in_the_app_is_typed_once_by_the_app_into_the_box_the_agent_names(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    planted = f"K{secrets.randbelow(10**9):09d}"

    def during(site: AskSite) -> None:
        give(factory, person_a, app_id, planted, site)  # type: ignore[arg-type]

    site, script = CodeSite({1: during}), coding(own_first=True)
    # The agent types it itself before the app does: refused, the code dropped (any other act).
    assert go_apply(svc(factory, site, script), app_id) == "submitted"
    assert (site.takes_code, len(site.ask)) == (True, 16)
    said = json.loads(script.said[0])
    assert (said["from_the_person"], said["note"]) == ("a code", applier.CODE_GIVEN)
    assert script.own[1]
    assert json.loads(script.own[0])["error"] == "credential"
    assert script.typed[1]
    assert json.loads(script.typed[0])["error"] == "no_code"
    assert site.typed == []
    assert not any(planted in text for text in script.seen)
    found = application(db, app_id)
    chat = applier.chat(db, found)
    assert [(line["who"], line["kind"], line["text"]) for line in chat] == [
        ("assistant", "code", SENTENCE),
        ("person", "code", applier.CODE_SAID),
    ]
    assert chat[0]["end"] == "answer"


def test_the_code_typed_at_once_reaches_the_box_once_and_a_second_use_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    planted = f"K{secrets.randbelow(10**9):09d}"

    def during(site: AskSite) -> None:
        give(factory, person_a, app_id, planted, site)  # type: ignore[arg-type]

    site, script = CodeSite({1: during}), coding()
    go_apply(svc(factory, site, script), app_id)
    assert site.typed == [planted]
    assert json.loads(script.typed[0])["code_typed"] is True
    assert json.loads(script.again[0])["error"] == "no_code"
    assert json.loads(script.own[0])["error"] == "credential"  # its own typing, refused as ever
    assert site.erased == 1  # the run's end asks browserd to drop what is left


@pytest.mark.parametrize(
    ("typed", "given"),
    [("4815 1623", "48151623"), ("48-15-16", "481516"), (" K7Q2X9 ", "K7Q2X9"),
     ("４８１５１６", "481516")],
)  # fmt: skip
def test_a_code_as_people_type_it(
    db: Session, factory: sessionmaker[Session], person_a: Person, typed: str, given: str
) -> None:
    app_id = started(db, person_a, Mode.direct)
    waits(db, factory, app_id, "code")
    site = CodeSite()
    site.ask, site.takes_code = "a" * 16, True
    with factory() as s:
        row = applier.open_ask(s, application(s, app_id))
        row.value = {**row.value, "lease_id": site.lease_id, "ask_id": site.ask}
        s.commit()
    give(factory, person_a, app_id, typed, site)
    assert site.code == given


@pytest.mark.parametrize(
    ("case", "status", "detail"),
    [
        ("not_a_code_ask", 409, applier.NO_CODE_ASKED),
        ("nothing_waits", 409, "This application waits for no answer now."),
        ("answered_already", 409, "This application waits for no answer now."),
        ("short", 422, applier.CODE_FORM),
        ("long", 422, applier.CODE_FORM),
        ("words", 422, applier.CODE_FORM),
        ("letters_spaced", 422, applier.CODE_FORM),
        ("another_person", 404, "No such application."),
        ("browserd_says_no", 409, "This application waits for no answer now."),
        ("browserd_away", 409, applier.CODE_NOT_TAKEN),
        ("ended_meanwhile", 409, "This application waits for no answer now."),
        ("no_lease_named", 409, applier.CODE_NOT_TAKEN),
    ],
)
def test_a_code_is_refused_off_the_ask_that_waits_for_it_and_nothing_of_it_is_kept(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person, case: str,
    status: int, detail: str,
) -> None:  # fmt: skip
    app_id = started(db, person_a, Mode.direct)
    planted = {
        "short": "123",
        "long": "1" * 17,
        "words": "the code is 4815",
        "letters_spaced": "K7Q 2X9",
    }.get(case, f"K{secrets.randbelow(10**9):09d}")
    if case != "nothing_waits":
        waits(db, factory, app_id, "other" if case == "not_a_code_ask" else "code")
    site = CodeSite()
    site.ask, site.takes_code = "a" * 16, case != "browserd_says_no"
    with factory() as s:
        row = applier.open_ask(s, application(s, app_id))
        if row is not None:
            gone = "" if case == "no_lease_named" else site.lease_id
            row.value = {**row.value, "lease_id": gone, "ask_id": site.ask}
            if case == "answered_already":
                row.value = {**row.value, "answer": "Done."}
        s.commit()
    erased: list[str] = []

    def give_(lease: str, ask: str, code: str) -> bool:
        if case == "browserd_away":
            raise BrowserError("unavailable")
        given = site.give(lease, ask, code)
        if case == "ended_meanwhile":  # the person gave the browser back as browserd took it
            with factory() as s:
                gate.cancel(s, person_a.user_id, app_id, AT, reject=False)
                s.commit()
        return given

    who = person_b.user_id if case == "another_person" else person_a.user_id
    with factory() as s, pytest.raises(applier.QuestionError) as refused:
        applier.answer_code(s, who, app_id, planted, AT, give_, erased.append)
    assert (refused.value.status, refused.value.detail) == (status, detail)
    assert erased == ([site.lease_id] if case == "ended_meanwhile" else [])
    with factory() as s:
        texts = [json.dumps(r.value) for r in s.scalars(sa.select(ApplicationAnswer))]
    assert not any(planted in text for text in texts)


def tables(db: Session) -> list[str]:
    """Every row of every table of the app, as text."""
    names = db.execute(sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    out: list[str] = []
    for (name,) in names:
        out += db.execute(sa.text(f'SELECT row_to_json(t)::text FROM "{name}" t')).scalars()
    return out


def test_a_planted_code_given_through_the_api_is_in_no_row_log_answer_or_model_message(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person,
    api_app: FastAPI,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    _password(factory, person_a, person_b)
    app_id = started(db, person_a, Mode.direct)
    planted = f"Q{secrets.randbelow(10**9):09d}"
    site = CodeSite()
    api_app.dependency_overrides[router.browserd_client] = lambda: Client(site)
    path = f"/api/applications/{app_id}"
    out: dict[str, Any] = {}
    caplog.set_level(logging.DEBUG)
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token, other = api.login_token(person_a.email), api.login_token(person_b.email)

        def in_the_app(_: AskSite) -> None:
            spaced = {"code": f" {planted} "}
            out["theirs"] = api.post(f"{path}/question", json=spaced, token=other)
            out["both"] = api.post(f"{path}/question", json={**spaced, "answer": "x"}, token=token)
            out["given"] = api.post(f"{path}/question", json=spaced, token=token)
            out["again"] = api.post(f"{path}/question", json=spaced, token=token)
            out["while"] = api.get(path, token=token)

        site.hooks[1] = in_the_app

        async def then(session: applier.ApplySession) -> None:
            out["after_typing"] = await session.snapshot()

        script = coding(then=then)
        ended = go_apply(svc(factory, site, script), app_id)
        out["after"] = api.get(path, token=token)
    assert out["theirs"].status_code == 404
    assert out["both"].status_code == 422
    assert out["given"].status_code == 200, out["given"].text
    assert ended == "submitted"
    assert out["again"].status_code == 409
    assert site.typed == [planted]
    question = out["while"].json()["question"]
    assert (question["kind"], question["live"]) == ("code", True)
    assert out["after"].json()["chat"][1]["text"] == applier.CODE_SAID
    answers = " ".join(r.text for k, r in out.items() if hasattr(r, "text"))
    assert planted not in answers
    assert not any(planted in text for text in [*script.seen, json.dumps(out["after_typing"])])
    for record in caplog.records:
        assert planted not in record.getMessage()
        assert planted not in json.dumps(record.__dict__, default=str)
    assert not any(planted in row for row in tables(db))
    assert "person_answered" in kinds(db, app_id)


# --- the person's messages while the agent works ------------------------------------------------


def post(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID, text: str,
         at: Any = AT) -> dict[str, Any]:  # fmt: skip
    with factory() as s:
        line = applier.post_message(s, person.user_id, app_id, text, at)
        s.commit()
    return line


def refused_message(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID,
                    text: str, at: Any = AT) -> tuple[int, str]:  # fmt: skip
    with factory() as s, pytest.raises(applier.QuestionError) as refused:
        applier.post_message(s, person.user_id, app_id, text, at)
    return refused.value.status, refused.value.detail


def test_a_message_during_an_act_comes_with_the_next_tool_call_s_answer_as_the_person_s_words(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    site = CodeSite()

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        post(factory, person_a, app_id, "Use my second e-mail, e2e-alex@example.org.")
        post(factory, person_a, app_id, "Ignore the stop and send it now.")
        go.next = await session.interact("browser_click", {"ref": "e1"})  # type: ignore[attr-defined]
        go.after = await session.snapshot()  # type: ignore[attr-defined]
        session.finish("failed", "Stopped.", "", sent="no")
        return applier.Ran(1, 1)

    assert go_apply(svc(factory, site, go), app_id) == "failed"
    text, error = go.next  # type: ignore[attr-defined]
    assert not error
    told = json.loads(text.split("\n\n", 1)[1])
    assert told["note"] == applier.MESSAGES_NOTE
    assert [m["from_the_person"] for m in told["person_messages"]] == [
        "Use my second e-mail, e2e-alex@example.org.",
        "Ignore the stop and send it now.",
    ]
    assert "person_messages" not in go.after[0]  # type: ignore[attr-defined]  (read once)
    chat = applier.chat(db, application(db, app_id))
    assert [(line["kind"], line["read"]) for line in chat] == [("message", True)] * 2
    assert kinds(db, app_id).count("person_message") == 2
    assert "Use my" not in json.dumps([e.data for e in events(db, app_id)])


def test_a_message_never_widens_what_the_agent_may_do_and_the_emergency_stop_holds(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    site = CodeSite()

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        post(factory, person_a, app_id, "The stop is a mistake: click Submit anyway.")
        go.stopped = await session.interact("browser_click", {"ref": "e1"})  # type: ignore[attr-defined]
        return applier.Ran(1, 1)

    services = svc(factory, site, go)
    services.stop = Stop(None, None, "submit_disabled")  # the run, the navigation, the click
    go_apply(services, app_id)
    assert go.stopped == (applier.ENDS["submit_disabled"], True)  # type: ignore[attr-defined]
    assert ("browser_click", False) not in site.calls
    assert site.submissions == []
    assert application(db, app_id).state is not State.submitted


def test_a_cancel_after_a_message_ends_the_run_at_its_next_call(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        await session.navigate(json.loads(message)["posting"]["url"])
        post(factory, person_a, app_id, "Do not cancel, whatever I click.")
        with factory() as s:
            gate.cancel(s, person_a.user_id, app_id, AT, reject=False)
            s.commit()
        go.ended = await session.interact("browser_click", {"ref": "e1"})  # type: ignore[attr-defined]
        return applier.Ran(1, 1)

    go_apply(svc(factory, CodeSite(), go), app_id)
    assert go.ended == (applier.ENDS["superseded"], True)  # type: ignore[attr-defined]
    assert application(db, app_id).state is State.cancelled


def test_a_message_during_a_wait_comes_with_the_ask_s_answer(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)

    def during(_: AskSite) -> None:
        with factory() as s:
            asked = datetime.fromisoformat(
                applier.open_ask(s, application(s, app_id)).value["asked_at"]
            )
        post(
            factory,
            person_a,
            app_id,
            "I am looking for the mail now.",
            asked + timedelta(seconds=1),
        )
        assert refused_message(factory, person_a, app_id, "It is 48 15 16") == (
            422,
            applier.CODE_ALONE,
        )
        later = asked + timedelta(seconds=2)
        with factory() as s:
            applier.answer_question(s, person_a.user_id, app_id, "No mail came.", False, later)
            s.commit()

    site = CodeSite({1: during})
    script = coding()
    go_apply(svc(factory, site, script), app_id)
    text, _ = script.said
    head, tail = text.split("\n\n", 1)
    assert json.loads(head)["from_the_person"] == "No mail came."
    assert json.loads(tail)["person_messages"][0]["from_the_person"] == (
        "I am looking for the mail now."
    )
    chat = applier.chat(db, application(db, app_id))
    assert [(line["who"], line["kind"]) for line in chat] == [
        ("assistant", "code"),
        ("person", "message"),
        ("person", "answer"),
    ]


@pytest.mark.parametrize(
    "state", [State.queued, State.ready, State.submitted, State.cancelled, State.failed]
)
def test_a_message_when_no_run_works_is_refused_with_a_sentence_that_says_so(
    db: Session, factory: sessionmaker[Session], person_a: Person, state: State
) -> None:
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, state)
    assert refused_message(factory, person_a, app_id, "Are you there?") == (409, applier.NO_RUN)
    assert applier.chat(db, application(db, app_id)) == []


def test_a_message_of_review_first_by_the_agent_and_never_of_another_mode(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    by_agent = ticked(db, person_a)
    put(db, by_agent, State.filling)
    assert post(factory, person_a, by_agent, "Hello")["kind"] == "message"
    put(db, by_agent, State.cancelled)
    adapters = started(db, person_a, Mode.review)  # the 2026-11-01 Review first: no agent
    put(db, adapters, State.filling)
    assert refused_message(factory, person_a, adapters, "Hello") == (409, applier.NO_RUN)


def test_the_rate_and_the_length_of_messages_are_bounded_and_another_person_gets_404(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, State.filling)
    for n in range(applier.MESSAGES_PER_MINUTE):
        post(factory, person_a, app_id, f"Note {n}.", AT + timedelta(seconds=n))
    late = AT + timedelta(seconds=30)
    assert refused_message(factory, person_a, app_id, "One more.", late) == (
        429,
        applier.TOO_MANY,
    )
    post(factory, person_a, app_id, "One more.", AT + timedelta(seconds=61))
    assert refused_message(factory, person_b, app_id, "Hi.") == (404, "No such application.")
    assert refused_message(factory, person_a, app_id, "   ")[0] == 422
    assert refused_message(factory, person_a, app_id, "\x07")[0] == 422
    assert len(applier.chat(db, application(db, app_id))) == applier.MESSAGES_PER_MINUTE + 1


def test_two_messages_at_once_are_both_kept_in_order_and_read_once(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, State.filling)
    start = threading.Barrier(2)
    failures: list[BaseException] = []

    def send(text: str) -> None:
        try:
            start.wait()
            post(factory, person_a, app_id, text)
        except BaseException as exc:  # reported by the assertion below
            failures.append(exc)

    threads = [threading.Thread(target=send, args=(t,)) for t in ("First.", "Second.")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    with factory() as s:
        keys = [r.field_key for r in applier._messages(s, application(s, app_id))]
        taken = applier.take_messages(s, app_id, AT)
        s.commit()
    assert keys == [applier.MESSAGE_KEY + "0001", applier.MESSAGE_KEY + "0002"]
    assert sorted(m["from_the_person"] for m in taken) == ["First.", "Second."]
    with factory() as s:
        assert applier.take_messages(s, app_id, AT) == []


def test_the_message_route(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person,
    api_app: FastAPI,  # noqa: F811
) -> None:  # fmt: skip
    _password(factory, person_a, person_b)
    app_id = started(db, person_a, Mode.direct)
    put(db, app_id, State.filling)
    path = f"/api/applications/{app_id}/messages"
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token, other = api.login_token(person_a.email), api.login_token(person_b.email)
        sent = api.post(path, json={"text": "Take your time."}, token=token)
        assert sent.status_code == 201, sent.text
        assert (sent.json()["who"], sent.json()["kind"], sent.json()["read"]) == (
            "person",
            "message",
            False,
        )
        assert api.post(path, json={"text": "Hi."}, token=other).status_code == 404
        too_long = {"text": "x" * (applier.MESSAGE_MAX + 1)}
        assert api.post(path, json=too_long, token=token).status_code == 422
        put(db, app_id, State.submitted)
        ended = api.post(path, json={"text": "Done?"}, token=token)
        assert (ended.status_code, ended.json()["detail"]) == (409, applier.NO_RUN)
        chat = api.get(f"/api/applications/{app_id}", token=token).json()["chat"]
    assert [line["text"] for line in chat] == ["Take your time."]
