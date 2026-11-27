"""The code given in the app, the second slice of A25 (owner, 2026-11-26: who types a code
received by e-mail or SMS, "les deux sont possibles"), on a real Postgres: a code the person
gives in the app goes to browserd for the ask that waits for it and is typed there once by
`apply_type_code`, never stored, logged or told to the model (a planted code grepped in every
table, log record, api answer and model message). browserd is A25's fake site (`AskSite`) with
the code kept and typed as browserd's `give_code` and `type_code` do (their own tables are in
api/tests/browser/test_a25_type_code.py)."""

from __future__ import annotations

import json
import logging
import secrets
import sys
import unicodedata
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.apply import applier, gate, router
from app.apply.models import ApplicationAnswer, Mode
from app.browser.client import BrowserError
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, started
from tests.apply.test_a6_gate import _password
from tests.apply.test_a19_applier import THANKS
from tests.apply.test_a24_cancel import api_app, put  # noqa: F401  (api_app: the fixture)
from tests.apply.test_a25_waits import SENTENCE, AskSite, go_apply, kinds, svc, waits
from tests.identity.api_helpers import Api

BOX = "e10"  # the site's one time code box on step 1
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))
try:  # browserd's own tools on fake pages (pure Python), so the planted code tests prove it
    from browserd.tools import actions, codes

    from tests.browser import a25_pages as pages_fake
except ImportError:  # the repository root is not mounted here
    actions = codes = pages_fake = None  # type: ignore[assignment]


class CodeSite(AskSite):
    """A19's site whose step 1 has a one time code box, with browserd's side of the code: kept
    for the open ask that takes one (`give`), dropped by the next ask, any other act and
    `erase_code`, typed once into that box only (`browser_type_code`), never answered back."""

    def __init__(self, hooks: dict[int, Callable[[AskSite], None]] | None = None) -> None:
        super().__init__(hooks)
        self.code: str | None = None
        self.typed: list[str] = []
        self.ask, self.takes_code = "", False
        self.shown = codes.Codes() if codes is not None else None

    async def ask_begin(self, left_s: int, ask: str = "", code: bool = False) -> str:
        self.code = None
        return await super().ask_begin(left_s, ask, code)

    def give(self, lease_id: str, ask_id: str, code: str) -> bool:
        if lease_id != self.lease_id or ask_id != self.ask or not self.takes_code:
            return False
        if self.where not in ("waiting", "held") or self.code is not None:
            return False
        self.code = code
        if self.shown is not None:
            self.shown.add(code)
        return True

    def erase(self, _: str) -> None:
        self.code = None

    async def erase_code(self) -> None:
        await super().erase_code()
        self.code = None

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        answer = self._act(name, args, capability)
        if self.shown is not None and self.shown.found(answer):  # as browserd's run
            return {"ok": False, "error": "code_shown", "message": "The code showed again."}
        return answer

    def _act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        if name == "browser_type_code":
            code, self.code = self.code, None
            if code is None:
                return {"ok": False, "error": "no_code", "message": "No code waits."}
            if args.get("ref") != BOX:
                return {"ok": False, "error": "not_code_box", "message": "Not a code box."}
            self.typed.append(code)
            return {"ok": True, "code": "taken"}
        if name not in ("browser_snapshot", "browser_wait_for"):
            self.code = None
        if name == "browser_type" and args.get("ref") == BOX:
            return {"ok": False, "error": "credential", "message": "A code is the person's."}
        return super().act(name, args, capability)

    def text(self) -> str:
        """The page's text, the code box's value hidden by the element once typed into, as
        browserd's snapshot writes it (actions.boxes_hidden)."""
        shown = super().text()
        value = "[hidden: a box the app typed the person's code into]" if self.typed else ""
        box = f'\n- textbox "Verification code" [ref={BOX}]: {value}'
        return shown + box if "e8" in shown else shown


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
    # Try again's first message names the earlier ask's answer as a code, never the code.
    ask = applier.open_ask(db, found) or applier._asks(db, found)[0]
    assert applier._said(ask)["person_answered"] == "a code, typed by the app"


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
    assert json.loads(script.typed[0])["code"] == "taken"
    assert json.loads(script.again[0])["error"] == "no_code"
    assert json.loads(script.own[0])["error"] == "credential"  # its own typing, refused as ever
    assert site.erased == 1  # the run's end asks browserd to drop what is left


@pytest.mark.parametrize(
    ("typed", "given"),
    [("4815 1623", "48151623"), ("48-15-16", "481516"), (" K7Q2X9 ", "K7Q2X9"),
     ("４８１５１６", "481516"), ("K7Q-2X9", "K7Q2X9"), ("ABC-123", "ABC123"),
     ("7KD.3QX", "7KD3QX")],
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
        ("short", 422, applier.CODE_IN_BROWSER),
        ("long", 422, applier.CODE_IN_BROWSER),
        ("words", 422, applier.CODE_FORM),
        ("labelled", 422, applier.CODE_FORM),
        ("another_person", 404, "No such application."),
        ("browserd_says_no", 409, "This application waits for no answer now."),
        ("browserd_away", 409, applier.CODE_NOT_TAKEN),
        ("client_breaks", 409, applier.CODE_NOT_TAKEN),
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
        "labelled": "Code: 7KD-3QX",
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
        if case == "browserd_away":  # a timeout after browserd took it, or before
            site.give(lease, ask, code)
            raise BrowserError("unavailable")
        if case == "client_breaks":  # any other failure after browserd took it: erased too
            site.give(lease, ask, code)
            raise RuntimeError("the event loop is closed")
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
    erasing = ("ended_meanwhile", "browserd_away", "client_breaks")
    assert erased == ([site.lease_id] if case in erasing else [])
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
    if codes is None:
        pytest.skip("browserd's finder is not mounted here")
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


class PagesSite(AskSite):
    """A25's site whose browser is browserd's own tools (`actions.run`) on a fake page of
    api/tests/browser/a25_pages.py: the code kept, typed, sent and found as browserd does."""

    def __init__(self, shape: str, length: int, monkeypatch: Any) -> None:
        super().__init__()
        self.fake = pages_fake.FakePage(shape, length)
        self.tab = pages_fake.tab_on(self.fake, monkeypatch)
        self.client = self  # agent_act's capability call: `client._tool`

    async def ask_begin(self, left_s: int, ask: str = "", code: bool = False) -> str:
        self.tab.state.drop_code()
        return await super().ask_begin(left_s, ask, code)

    def give(self, lease_id: str, ask_id: str, code: str) -> bool:
        if (lease_id, ask_id, self.takes_code) != (self.lease_id, self.ask, True):
            return False
        if self.tab.state.code is not None:
            return False
        pages_fake.give(self.tab, code)
        return True

    def erase(self, _: str) -> None:
        self.tab.state.drop_code()

    async def erase_code(self) -> None:
        await super().erase_code()
        self.tab.state.drop_code()

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._tool(self.lease_id, "t", name, args or {}, None)

    async def _tool(
        self, lease_id: str, token: str, name: str, args: dict[str, Any], capability: Any
    ) -> dict[str, Any]:
        self.tab.gate = capability is not None
        return await actions.run(self.tab, name, args)


def on_pages(site: PagesSite) -> Any:
    """Opens the code's page, asks for the code, types it with its button, reads three times."""

    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        go.seen = [message]  # type: ignore[attr-defined]

        async def told(result: Any) -> None:
            go.seen.append(json.dumps(result, ensure_ascii=False))  # type: ignore[attr-defined]

        await told(await session.navigate(pages_fake.VERIFY))
        await told(await session.ask_person("code", SENTENCE))
        verify = site.fake.verify
        await told(await session.type_code(site.fake.boxes[0].ref, verify and verify.ref))
        for _ in range(3):
            await told(await session.snapshot())
        if not session.stopped:
            session.finish("failed", "Stopped.", "", sent="unsure")
        return applier.Ran(5, 1)

    return go


@pytest.mark.skipif(codes is None, reason="browserd is not mounted here")
@pytest.mark.parametrize("shape", pages_fake.SHAPES if pages_fake else [])
def test_a_whole_exchange_on_each_page_shape_keeps_the_code_out_of_answers_events_logs_and_rows(
    db: Session, factory: sessionmaker[Session], person_a: Person, shape: str,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    app_id = started(db, person_a, Mode.direct)
    planted = "".join(secrets.choice("GHJKMNPQRSTVWXYZ") for _ in range(10))
    site = PagesSite(shape, len(planted), monkeypatch)
    site.hooks[1] = lambda s: give(factory, person_a, app_id, planted, s)  # type: ignore[arg-type]
    caplog.set_level(logging.DEBUG)
    script = on_pages(site)
    go_apply(svc(factory, site, script), app_id)
    assert site.fake.code_seen in (planted, "")  # the site got it, or kept its emptied box
    assert site.tab.state.code_closed is (shape in ("get", "stuck", "later"))
    for text in script.seen:
        assert planted not in text
        assert not site.tab.state.shown.found(text)
    for record in caplog.records:
        assert planted not in record.getMessage()
        assert planted not in json.dumps(record.__dict__, default=str)
    assert not any(planted in row for row in tables(db))
    if site.tab.state.code_closed:
        with factory() as s:
            report = s.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
            assert report is not None
            assert report.value["summary"] == applier.CODE_SHOWN_SAID


def test_two_code_answers_at_once_give_one_code_and_the_second_erases_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = started(db, person_a, Mode.direct)
    waits(db, factory, app_id, "code")
    site = CodeSite()
    site.ask, site.takes_code = "a" * 16, True
    with factory() as s:
        row = applier.open_ask(s, application(s, app_id))
        row.value = {**row.value, "lease_id": site.lease_id, "ask_id": site.ask}
        s.commit()
    erased: list[str] = []
    second: dict[str, Any] = {}

    def give_(lease: str, ask: str, code: str) -> bool:
        # Another tab answers while browserd takes the first code: refused before browserd,
        # as are words to the same question; nothing of theirs is given or erased.
        calls = {
            "code": lambda s: applier.answer_code(
                s, person_a.user_id, app_id, "Z9Z9Z9", AT, site.give, erased.append
            ),
            "words": lambda s: applier.answer_question(
                s, person_a.user_id, app_id, "No mail came", False, AT
            ),
        }
        for how, call in calls.items():
            with factory() as s, pytest.raises(applier.QuestionError) as refused:
                call(s)
            second[how] = (refused.value.status, refused.value.detail)
        return site.give(lease, ask, code)

    with factory() as s:
        applier.answer_code(s, person_a.user_id, app_id, "K7Q-2X9", AT, give_, erased.append)
        s.commit()
    assert second == {"code": (409, applier.CODE_ON_ITS_WAY),
                      "words": (409, applier.CODE_ON_ITS_WAY)}  # fmt: skip
    assert (site.code, erased) == ("K7Q2X9", [])
    with factory() as s:
        value = applier.open_ask(s, application(s, app_id)).value
    assert (value["code"], "giving" in value) == (True, False)
    # A marker left by an answer that died is stale after GIVING_S: the ask takes a code again.
    with factory() as s:
        row = applier.open_ask(s, application(s, app_id))
        row.value = {k: v for k, v in row.value.items() if k != "code"}
        row.value = {**row.value, "giving": "dead", "giving_at": AT.isoformat()}
        s.commit()
    site.code = None
    later = AT + timedelta(seconds=applier.GIVING_S)
    with factory() as s:
        applier.answer_code(s, person_a.user_id, app_id, "481516", later, site.give, erased.append)
        s.commit()
    assert site.code == "481516"


# Words to an ask of kind code (`looks_like_code`): refused when they may hold a code, so they
# never reach the model; the code box takes the code-shaped ones (`code_of`).
WORDS_REFUSED = [
    "K7Q-2X9",
    "K7Q 2X9",
    "ABC-123",
    "Code: 7KD-3QX",
    "12/34/56",
    "123,456",
    "12:34:56",
    "123\u2013456",
    "123\u2011456",
    "481516",
    "4815",
    "48 15 16",
    "4 8 1 5 1 6",
    "XKCDQW",
    "the code is XKCD",
    "mon code : 48-15",
    "Der Code ist 4815",
    "It is 2024",
    "OK 404",
    "aBcDeF",
    "K 7 Q 2",
    "code \uff14\uff18\uff11\uff15",
    "K7Q2",
    "it says K7Q2X9 thanks",
    "A B C D",
    # Small letters (review of PR 849, round 2): a token whose letters and digits, its marks
    # aside, number 4 or more with a digit among them.
    "abc-123",
    "Abc-123",
    "abc.123",
    "abc_123",
    "ab-12-cd",
    "12-ab-34",
    "the code is abc-123",
    "abc!123",
    "(abc-123)",
    "abc/123",
    "a1b2",
    "x-4815",
    "q-4-8-1",
    "Il est 9h30",
    "abc\u200b123",
]
WORDS_KEPT = [
    "No mail came",
    "Nothing arrived yet",
    "I have no phone here",
    "Try again in 5 minutes",
    "It says error 404",
    "The mail went to spam, give me a minute",
    "I am on it",
    "Je n'ai rien re\u00e7u",
    "Ich habe keine Mail bekommen",
    "Wait 10 min please",
    "Use my other address",
    "The site says the code expired",
    "Rien pour l'instant",
    "Give me 2 minutes",
    "Ok",
    "I",
    "Done",
    "Retry",
    "Not yet, the SMS is late",
    "No code came, send another one",
    "",
    "Il est 9h",
    "Can you click Resend",
    "My phone is off for 1 hour",
    "My e-mail is fine",
    "Maybe a 2nd mail",
    "Step 3 of 5",
    "abc-def",
]


@pytest.mark.parametrize("text", WORDS_REFUSED)
def test_words_that_may_hold_a_code_are_refused(text: str) -> None:
    assert applier.looks_like_code(text)


@pytest.mark.parametrize("text", WORDS_KEPT)
def test_words_that_hold_no_code_reach_the_assistant(text: str) -> None:
    assert not applier.looks_like_code(text)


CODE_BOX = [
    ("481516", "481516"),
    ("4815 1623", "48151623"),
    ("48-15-16", "481516"),
    ("K7Q-2X9", "K7Q2X9"),
    ("K7Q 2X9", "K7Q2X9"),
    ("ABC-123", "ABC123"),
    ("7KD.3QX", "7KD3QX"),
    ("123\u2013456", "123456"),
    ("123\u2011456", "123456"),
    ("12 34 56", "123456"),
    ("\uff14\uff18\uff11\uff15\uff11\uff16", "481516"),
    (" K7Q2X9 ", "K7Q2X9"),
    ("XKCDQW", "XKCDQW"),
    ("G-123456", "G123456"),
    ("4815_1623", "48151623"),
    ("ABCD-EFGH", "ABCDEFGH"),
    ("1234567890123456", "1234567890123456"),
    ("Q4815162342", "Q4815162342"),
    # The words box's code like tokens, in small letters too (review of PR 849, round 2).
    ("abc-123", "abc123"),
    ("Abc-123", "Abc123"),
    ("abc.123", "abc123"),
    ("abc_123", "abc123"),
    ("ab-12-cd", "ab12cd"),
    ("12-ab-34", "12ab34"),
    ("12/34/56", "123456"),
    ("123,456", "123456"),
    ("12:34:56", "123456"),
    ("K7Q+2X9", "K7Q2X9"),
    ("(481516)", "481516"),
    ("481516.", "481516"),
    ("abc\u200b123", "abc123"),
]
CODE_BOX_REFUSED = [
    "123",
    "1" * 17,
    "the code is 4815",
    "Code: 7KD-3QX",
    "abc-def",
    "4815!",
    "",
    "   ",
    "-4815",
    "481 516 ok",
    "\u00c4BCD",
    "\u03ba\u03c9\u03b4\u03b9\u03ba\u03cc\u03c2",
    "48/15",
    "My code 4815",
    "12 34 56 78 90 12 34 56 7",
    "co de",
    "xkcdqw",
    "4 8 1 5",
    "4815",
    "48151",
    "abc 123",
    "\u0661\u0662\u0663\u0664\u0665\u0666",
]


@pytest.mark.parametrize(("typed", "given"), CODE_BOX)
def test_the_code_box_takes_the_shapes_sites_send(typed: str, given: str) -> None:
    assert applier.code_of(typed) == given


@pytest.mark.parametrize("typed", CODE_BOX_REFUSED)
def test_the_code_box_refuses_what_is_no_code(typed: str) -> None:
    assert applier.code_of(typed) == ""
    assert applier.code_refused(typed) in (applier.CODE_FORM, applier.CODE_IN_BROWSER)


@pytest.mark.parametrize(
    ("typed", "said"),
    [("4815", applier.CODE_IN_BROWSER), ("48151", applier.CODE_IN_BROWSER),
     ("48 15", applier.CODE_IN_BROWSER), ("ab-12", applier.CODE_IN_BROWSER),
     ("1" * 17, applier.CODE_IN_BROWSER),
     ("\u0661\u0662\u0663\u0664\u0665\u0666", applier.CODE_IN_BROWSER),
     ("the code is 4815", applier.CODE_FORM), ("xkcdqw", applier.CODE_FORM),
     ("Code: 7KD-3QX", applier.CODE_FORM)],
)  # fmt: skip
def test_a_code_the_app_does_not_type_is_sent_to_the_browser(typed: str, said: str) -> None:
    # 4 or 5 characters would end honest runs on a year or a phone number (browserd closes the
    # tools when an answer holds the code): the person types such a code in the browser.
    assert applier.code_refused(typed) == said
    assert typed.strip() not in said or not typed.strip()


# Generated inputs over a code's alphabet, its marks and other scripts: the two properties of
# the words box and the code box (review of PR 849, round 2).
ALPHABET = (
    "abcxyzABCXYZ0123456789"
    + "-._/:,+!?()'"
    + "  "
    + "\u00a0\u2013\u200b\u00ad"
    + "\uff14\u00e9\u00c4\u03ba\u0661"
)


def generated(count: int, *, spaces: bool) -> list[str]:
    import random

    pick = random.Random(849)  # noqa: S311  a fixed draw of test inputs, no secret
    letters = ALPHABET if spaces else ALPHABET.replace(" ", "").replace("\u00a0", "")
    return ["".join(pick.choice(letters) for _ in range(pick.randint(1, 22))) for _ in range(count)]


def test_nothing_the_code_box_takes_passes_as_words() -> None:
    taken = [text for text in generated(20_000, spaces=True) if applier.code_of(text)]
    assert len(taken) > 500  # the generator reaches codes
    assert [text for text in taken if not applier.looks_like_code(text)] == []


def test_a_token_refused_as_code_like_is_taken_or_sent_to_the_browser() -> None:
    tokens = [t for t in generated(20_000, spaces=False) if len(t.split()) == 1]
    refused = [t for t in tokens if applier._code_token(applier.WORD.findall(
        unicodedata.normalize("NFKC", t)))]  # fmt: skip
    assert len(refused) > 500
    for token in refused:
        assert applier.looks_like_code(token)
        if not applier.code_of(token):
            assert applier.code_refused(token) == applier.CODE_IN_BROWSER, token
