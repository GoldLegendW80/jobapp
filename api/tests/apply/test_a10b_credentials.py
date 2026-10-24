"""What a person types to sign in never reaches the app (SPEC-apply, "Accounts and signing in" and
"What the agent may never do"): a credential box is found from browserd's read of the page, not
its label alone; a page that holds one is a sign in wall, a sign in's or a sign up's, before
anything is typed; and no such box is ever a field, an answer, a row, a log line, a Redis value
or a job argument. The label rule as accept and refuse tables, the wall rule, then the whole
path through `run.fill`, the api and the later hand off. browserd's own read and its refusal to
type are tests/browser/test_credential_inputs.py and test_credential_container.py."""

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlsplit

import pytest
import redis
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apply import agent as answer_agent
from app.apply import answers, form_agent, gate, run, signin
from app.apply.ats import base, generic
from app.apply.ats.ashby import Ashby
from app.apply.ats.greenhouse import Greenhouse
from app.apply.models import (
    AnswerBankEntry,
    AnswerSource,
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    State,
)
from app.browser.handoff import Outcome
from app.browser.later_router import router as later_router
from app.browser.router import get_leases
from app.identity import deps
from tests.apply.a5_support import FakeLeases, run_ashby
from tests.apply.a8b_support import WORKABLE_PATH, ScriptedFormAgent, workable
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, fill, services, started
from tests.apply.test_a6_gate import _password, api_app, stub
from tests.apply.test_a8_generic import WORKABLE_SCRIPT, on_custom, rows_of
from tests.identity.api_helpers import Api
from tests.jobs.test_worker import flush_redis, redis_url
from tests.mail import canary

__all__ = ["api_app", "redis_url", "stub"]

# --- the label rule --------------------------------------------------------------------------

# A credential's label: never a field, whatever its kind (`secret`), and a wall on a text entry.
SECRET = [
    # the re-check of round 2
    "Password:", "Create password", "Create a password", "Choose a password", "New password",
    "Confirm new password", "Verify New Password", "Password confirmation", "Repeat password",
    "Retype password", "Password (8 characters minimum)", "Nouveau mot de passe",
    "Créer un mot de passe", "Choisissez un mot de passe", "Mot de passe (8 caractères minimum)",
    "Confirmation du mot de passe", "Passwort", "Contraseña", "Passwordless sign in email",
    "Password hint (do not share your password)", "Verification code", "One-time code",
    "Enter the 6-digit code", "Security code", "Code de vérification", "Code reçu par e-mail",
    "Passcode", "PIN",
    # case, marks, signs, glued and spaced forms, other scripts of the same letters
    "PASSWORD", "password *", "* Password", "Your password.", "Mot de passe :", "Mot-de-passe",
    "Mots de passe", "confirmPassword", "new_password", "Pass phrase", "Paßwort",
    "Ｐａｓｓｗｏｒｄ",
    # the other languages
    "Neues Passwort", "Passwort wiederholen", "Kennwort", "Bestätigungscode", "Sicherheitscode",
    "Nueva contraseña", "Confirmar contraseña", "Código de verificación", "Codice di verifica",
    "Codice monouso", "Senha", "Nova senha", "Confirme a senha", "Palavra-passe",
    "Código de verificação", "Wachtwoord", "Nieuw wachtwoord", "Verificatiecode",
    "Eenmalige code", "Lösenord", "Пароль", "パスワード", "密码", "비밀번호",
    # sign in codes and an account's secrets
    "2FA code", "Two-factor authentication code", "OTP", "SMS code", "Enter the code we sent",
    "Security question", "Secret answer", "Code PIN", "Mot de passe à usage unique",
]  # fmt: skip
# An application's own fields, a card's codes (the payment page's) and near misses: no credential.
PLAIN = [
    "Postal code", "Zip code", "ZIP / Postal code", "Code postal", "Postleitzahl",
    "Código postal", "Codice postale", "Postcode", "Country code", "Area code", "Promo code",
    "Discount code", "Referral code", "Coupon code", "Job code", "Reference code", "Source code",
    "Link to your source code", "Code sample", "Barcode", "Passport number", "Passport",
    "Passeport", "Compass", "Bypass", "Pinterest", "Spinning", "Shipping address", "Opinion",
    "Senhor", "Resenha", "Desenhador", "Engenharia", "Palabra clave", "Mot clé", "Passe-temps",
    "Security clearance", "Card security code", "Card verification code",
    "Code de sécurité de la carte", "Email", "E-mail", "Username", "GitHub username",
    "LinkedIn profile", "First name", "Phone", "Secret word", "Why Example Robotics?",
    "What is your notice period?", "",
]  # fmt: skip
# The stated cost of failing closed: no credential, read as one (the PR's declared open points).
FAIL_CLOSED = ["PIN code", "Which password manager do you use?"]


@pytest.mark.parametrize("label", SECRET + FAIL_CLOSED)
def test_a_label_that_names_a_credential_is_secret(label: str) -> None:
    assert base.secret(label)


@pytest.mark.parametrize("label", PLAIN)
def test_an_applications_own_label_is_not_secret(label: str) -> None:
    assert not base.secret(label)


# --- the wall rule ---------------------------------------------------------------------------


def shot(lines: Sequence[str], read: dict[str, bool] | None = None) -> dict[str, Any]:
    return {"ok": True, "snapshot": "\n".join(lines), "credentials": read or {}}


def one(role: str, label: str, read: bool | None = None) -> dict[str, Any]:
    """A page of one element, e1, and browserd's read of it (left out when unread)."""
    named = f" {json.dumps(label)}" if label else ""
    return shot([f"- {role}{named} [ref=e1]"], None if read is None else {"e1": read})


ROBOT = f"- checkbox {json.dumps("I'm not a robot")} [ref=e1]"
WALL_TABLE = [
    (one("textbox", "Secret word", True), "signin"),  # a password box: its type alone tells
    (one("textbox", "Secret word"), ""),  # unread: its label cannot tell
    (one("textbox", "Secret word", False), ""),
    (one("textbox", "Enter the code", True), "signin"),  # autocomplete one-time-code
    (one("spinbutton", "Enter the code", True), "signin"),
    (one("textbox", "Email", True), "signin"),  # the user name box of a login form
    (one("textbox", "Email", False), ""),
    (one("textbox", "", True), "signin"),  # no label at all
    (one("textbox", "Password:", False), "signin"),  # a label is a second signal
    (one("textbox", "Password:"), "signin"),
    (one("searchbox", "New password"), "signin"),
    (one("combobox", "Security question"), "signin"),
    (one("textbox", "Card security code"), ""),  # the payment page's
    (one("checkbox", "Remember my password"), ""),
    (one("button", "Forgot password?"), ""),
    (one("link", "Forgot your password?"), ""),
    (one("heading", "Create your password"), ""),
    (shot(["- paragraph [ref=e1]: Choose a strong password"]), ""),
    # a captcha beside a password box: the hand off first, the person types the password there
    (shot([ROBOT, '- textbox "Secret word" [ref=e2]'], {"e2": True}), "signin"),
]  # fmt: skip


@pytest.mark.parametrize(("page", "step"), WALL_TABLE)
def test_a_page_with_a_credential_box_is_a_sign_in_wall(page: dict[str, Any], step: str) -> None:
    for adapter in (base.Adapter(), Ashby(), Greenhouse()):
        assert adapter.pending_of(page) == step
    assert signin.walled(page) == (step == "signin")


def test_greenhouses_own_code_is_its_e_mail_step_and_any_other_credential_its_wall() -> None:
    code = one("textbox", "Security code", True)
    assert Greenhouse().pending_of(code) == "email_code"
    assert Ashby().pending_of(code) == "signin"
    with_password = shot(
        ['- textbox "Security code" [ref=e1]', '- textbox "Secret word" [ref=e2]'], {"e2": True}
    )
    assert Greenhouse().pending_of(with_password) == "signin"
    assert not signin.walled(code, Greenhouse())


# --- the whole path ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    label: str
    read: bool | None = None  # browserd's read: a credential input, a plain one, or unread
    value: str = ""  # what the box shows: what the person typed in the live view


def page(boxes: Sequence[Box], button: str = "Sign in") -> dict[str, Any]:
    """A `browser_snapshot` answer of a login or sign up page: its boxes, e2 on, and a button."""
    lines = ['- heading "Example Robotics careers" [level=1] [ref=e1]']
    read: dict[str, bool] = {}
    for n, box in enumerate(boxes, start=2):
        shown = f": {json.dumps(box.value)}" if box.value else ""
        lines.append(f"- textbox {json.dumps(box.label)} [ref=e{n}]{shown}")
        if box.read is not None:
            read[f"e{n}"] = box.read
    lines.append(f'- button "{button}" [ref=e{len(boxes) + 2}]')
    return shot(lines, read)


SIGN_UP = (
    [Box("Email", False), Box("Create a password", True), Box("Repeat password", True)],
    "Create account",
)
WALLS = [
    *(
        pytest.param([Box("Email", read), Box(label, read)], "Sign in", id=f"{label} {read}")
        for label in ("Password:", "Create a password", "New password", "Nouveau mot de passe")
        for read in (True, None, False)
    ),
    pytest.param([Box("Email", False), Box("Secret word", True)], "Sign in", id="by its type"),
    pytest.param([Box("Enter the code", True)], "Verify", id="a one time code"),
    pytest.param([Box("Email", True)], "Next", id="a two step login's first page"),
    pytest.param(*SIGN_UP, id="a sign up page before the form"),
    pytest.param(
        [Box("Email", False), Box("Create a password", True)], "Continue", id="an account step"
    ),
]
ACTING = ("type", "click", "select_option", "check", "upload", "press_key")


def acting(form: Any) -> list[tuple[str, dict[str, Any]]]:
    return [call for call in form.calls if call[0] in ACTING]


def behind(form: Any, wall: dict[str, Any]) -> dict[str, Any]:
    """`form` behind `wall` until `signed` is set; what the run read and asked."""
    seen: dict[str, Any] = {"asked": [], "signed": False, "acted": None, "snapshots": []}
    shown = form.snapshot

    async def snapshot() -> dict[str, Any]:
        got = await shown() if seen["signed"] else wall
        seen["snapshots"].append(got)
        return got

    form.snapshot = snapshot
    return seen


@pytest.mark.parametrize(("boxes", "button"), WALLS)
def test_a_wall_nobody_watches_waits_for_a_later_sign_in_with_nothing_typed_or_kept(
    db, factory, person_a, stub, monkeypatch, boxes, button
):
    """The re-check's pages at a custom form's address: whatever the labels say, the hand off
    is asked for the form's host before any adapter or the form agent reads the page."""
    form = workable()
    agent = ScriptedFormAgent([*(("fill_field", b.label) for b in boxes), ("next_step", button)])
    seen = behind(form, page(boxes, button))

    async def request(tools: Any, site: str, reason: str) -> Outcome:
        seen["asked"].append(site)  # browserd's answer: no live view of the person is open
        return Outcome("nobody_watching")

    form.ask_signin = request  # type: ignore[attr-defined]
    monkeypatch.setattr(signin, "request_signin", request)
    on_custom(db, person_a, stub, WORKABLE_PATH)
    ref = started(db, person_a)
    svc = services(factory, form, FakeLeases(), stub_base=stub, form_agent=agent)
    assert fill(svc, ref) == "needs_person"
    found = application(db, ref)
    assert (found.state, found.needs) == (State.needs_person, "signin")
    assert seen["asked"] == [urlsplit(found.form_url).hostname]
    assert (agent.answers, acting(form), rows_of(db, ref)) == ([], [], {})


def test_an_adapters_form_behind_a_password_box_of_an_innocent_label_waits_too(
    db, factory, person_a, stub, monkeypatch
):
    form = run_ashby()
    seen = behind(form, page([Box("Email", False), Box("Secret word", True)]))

    async def request(tools: Any, site: str, reason: str) -> Outcome:
        seen["asked"].append(site)
        return Outcome("nobody_watching")

    form.ask_signin = request  # type: ignore[attr-defined]
    monkeypatch.setattr(signin, "request_signin", request)
    ref = started(db, person_a)
    assert fill(services(factory, form, FakeLeases(), stub_base=stub), ref) == "needs_person"
    assert application(db, ref).needs == "signin"
    assert len(seen["asked"]) == 1
    assert (acting(form), rows_of(db, ref)) == ([], {})


def test_a_two_step_login_is_a_wall_at_its_first_page_and_no_step_of_it_is_kept(
    db, factory, person_a, stub, monkeypatch
):
    """An identifier first login: its e-mail page, then its password page. browserd reads the
    e-mail box as a credential (a `username` box of a form that holds a hidden password box), so
    the hand off is asked at the first page with nothing typed; the person goes through both
    pages in the live view, and the fill goes on at the form with no step of the login."""
    form, agent = workable(), ScriptedFormAgent(WORKABLE_SCRIPT)
    seen = behind(form, page([Box("Email", True)], "Next"))

    async def request(tools: Any, site: str, reason: str) -> Outcome:
        seen["asked"].append(site)
        seen["acted"] = acting(form)  # what reached the login page before the hand off
        seen["signed"] = True  # the person went through both pages in the live view
        return Outcome("signed_in")

    form.ask_signin = request  # type: ignore[attr-defined]
    monkeypatch.setattr(signin, "request_signin", request)
    on_custom(db, person_a, stub, WORKABLE_PATH)
    ref = started(db, person_a)
    svc = services(factory, form, FakeLeases(), stub_base=stub, form_agent=agent)
    assert fill(svc, ref) == "ready"
    assert len(seen["asked"]) == 1
    assert seen["acted"] == []
    rows = rows_of(db, ref)
    assert not [key for key in rows if key.startswith(generic.STEP_KEY)]
    assert rows[generic.SUBMIT_KEY].value == "Submit application"
    assert sum(1 for row in rows.values() if row.label == "Email") == 1  # the form's own


@pytest.mark.parametrize(
    ("boxes", "button"),
    [
        ([Box("Email", True), Box("Password:", True)], "Sign in"),
        ([Box("Email", False), Box("Secret word", True)], "Sign in"),
        SIGN_UP,
    ],
)
def test_no_box_of_a_wall_is_answered_in_the_app_nor_kept_in_the_bank(
    db, factory, person_a, stub, api_app, monkeypatch, boxes, button
):
    form = workable()
    agent = ScriptedFormAgent([*(("fill_field", b.label) for b in boxes), ("next_step", button)])
    behind(form, page(boxes, button))

    async def request(*_: Any) -> Outcome:
        return Outcome("nobody_watching")

    form.ask_signin = request  # type: ignore[attr-defined]
    monkeypatch.setattr(signin, "request_signin", request)
    on_custom(db, person_a, stub, WORKABLE_PATH)
    ref = started(db, person_a)
    svc = services(factory, form, FakeLeases(), stub_base=stub, form_agent=agent)
    assert fill(svc, ref) == "needs_person"
    typed = canary.marker("typed")
    _password(factory, person_a)
    with TestClient(api_app, follow_redirects=False) as client:
        api = Api(client)
        token = api.login_token(person_a.email)
        path = f"/api/applications/{ref}"
        assert api.get(f"{path}/approval", token=token).json()["answers"] == []
        for box in boxes:
            key = quote(f"text:{base.fold(box.label)}#1", safe="")
            refused = api.request(
                "PUT", f"{path}/answers/{key}", token=token, json={"value": typed}
            )
            assert (refused.status_code, refused.json()["code"]) == (422, "unknown_field")
    db.expire_all()
    assert typed not in json.dumps(db.execute(sa.select(ApplicationAnswer.value)).scalars().all())
    assert db.scalar(sa.select(sa.func.count()).select_from(AnswerBankEntry)) == 0


@pytest.mark.parametrize(
    "label", ["Password:", "Create a password", "Nouveau mot de passe", "One-time code"]
)
def test_a_credential_row_of_an_older_fill_is_never_shown_hashed_refilled_or_answered(
    db, person_a, label
):
    ref = started(db, person_a)
    found = application(db, ref)
    found.state = State.ready
    for key, name in ((f"text:{base.fold(label)}#1", label), ("text:first name#1", "First name")):
        db.add(
            ApplicationAnswer(
                application_id=ref,
                user_id=person_a.user_id,
                field_key=key,
                label=name,
                kind="text",
                value=None,
                source=AnswerSource.left,
                required=True,
                approved=False,
            )
        )
    db.commit()
    assert [row.label for row in gate.rows_of(db, application(db, ref))] == ["First name"]
    with pytest.raises(gate.InvalidAnswerError, match="unknown_field"):
        gate.set_answer(db, person_a.user_id, ref, f"text:{base.fold(label)}#1", "x", AT)
    db.rollback()


def test_no_model_is_asked_about_a_credential_box() -> None:
    left = answers.Left("not_a_known_question", (), True)
    boxes = [
        base.Field("text:secret word#1", "Secret word", "text", required=True, credential=True),
        base.Field("text:tell us#1", "Tell us about a project", "text", required=True),
    ]
    outcomes = [base.Outcome(b, left, base.Filled("left", "not_a_known_question")) for b in boxes]
    assert [q.label for q in answer_agent.eligible(outcomes)] == ["Tell us about a project"]


def test_a_box_browserd_reads_as_a_credential_is_never_written_whatever_its_label(db, person_a):
    found = application(db, started(db, person_a))
    left = answers.Left("not_filled", (), True)
    boxes = [
        base.Field("text:secret word#1", "Secret word", "text", required=True, credential=True),
        base.Field("text:first name#1", "First name", "text", required=True),
    ]
    report = base.Report(
        outcomes=[base.Outcome(b, left, base.Filled("left", "not_filled")) for b in boxes],
        skipped=[base.Skipped("Choose a password", "radiogroup", "unsupported_widget")],
    )
    assert [row.label for row in run._answer_rows(found, report)] == ["First name"]


class Remember:
    """A page that is no wall, with a box named as a password's: a check box."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def snapshot(self) -> dict[str, Any]:
        lines = [
            '- checkbox "Remember my password" [ref=e1]',
            '- textbox "First name" [ref=e2]',
            '- button "Submit application" [ref=e3]',
        ]
        return shot(lines, {"e2": False})

    async def check(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("check")
        return {"ok": True}


def test_no_tool_types_or_ticks_a_box_named_as_a_credential() -> None:
    tools = Remember()
    session = form_agent.FormSession(generic.Generic(), tools, None, str(uuid.uuid4()))  # type: ignore[arg-type]

    async def scenario() -> tuple[Any, ...]:
        found, _ = await session.adapter.read(tools)
        assert found is not None
        assert found.pending == ""
        box = next(f for f in found.fields if f.kind == "checkbox")
        filled = await base.Adapter().fill(tools, box, answers.Answer(True, "answers.remember"))
        refused = (await session.fill_field("e1"), await session.resolve_answer("e1"))
        return box.credential, filled, *refused

    marked, filled, typed, resolved = asyncio.run(scenario())
    assert marked
    assert (filled.status, filled.code) == ("left", "credential")
    assert typed == resolved == (form_agent.CREDENTIAL, True)
    assert session.refused["credential"] == 2
    assert tools.calls == []


# --- the canary ----------------------------------------------------------------------------


class Bound:
    """The api's Redis as the later hand off reads and writes it, on the test's real Redis."""

    session_max_s = 1800

    def __init__(self, client: redis.Redis, lease: SimpleNamespace) -> None:
        self.redis, self.lease = client, lease
        self.client = SimpleNamespace(handoff=self._handoff)
        self.released: list[tuple[str, str, str]] = []

    def clock(self) -> float:
        return AT.timestamp()

    async def _pool(self) -> "Bound":
        return self

    async def get(self, key: str) -> Any:
        return self.redis.get(key)

    async def set(self, key: str, value: str, **kwargs: Any) -> None:
        self.redis.set(key, value, ex=kwargs.get("ex"))

    async def delete(self, key: str) -> None:
        self.redis.delete(key)

    async def current(self, user: str) -> SimpleNamespace:
        return SimpleNamespace(lease=self.lease)

    async def _handoff(self, lease_id: str, action: str) -> dict[str, Any]:
        return {"restarted": action == "done"}

    async def release(self, user: str, lease_id: str, reason: str = "") -> bool:
        self.released.append((user, lease_id, reason))
        return True


def test_what_a_person_types_to_sign_in_reaches_no_log_redis_event_row_or_job(
    db, factory, person_a, stub, api_app, redis_url, caplog, monkeypatch
):
    """SPEC-apply's audit rule over the sign in. A sign up page's boxes show what the person
    typed in the live view (an autofill does the same); the fill reads that page, and its job is
    cut while the person signs up (the later hand off's binding goes to Redis); the person also
    types it into the app's answer of each box; the later sign in queues the fill, which then
    fills the form. The marker is then in no log record, no Redis key or value, no event, row or
    column of the application, no bank entry and no job argument."""
    typed = canary.marker("typed")
    flush_redis(redis_url)
    store = redis.Redis.from_url(redis_url)
    lease = SimpleNamespace(lease_id="lease-1", state="active")
    later = Bound(store, lease)
    form, agent = workable(), ScriptedFormAgent(WORKABLE_SCRIPT)
    boxes = [Box("Email", True, f"{typed}@example.org"), Box("Create a password", True, typed)]
    seen = behind(form, page(boxes, "Create account"))

    async def cut(tools: Any, site: str, reason: str) -> Outcome:
        lease.state = "waiting_person"  # the person signs up, and the job's timeout comes
        raise asyncio.CancelledError

    leases = FakeLeases()
    leases.current, leases._pool = later.current, later._pool  # type: ignore[attr-defined]
    leases.session_max_s = 1800  # type: ignore[attr-defined]
    form.ask_signin = cut  # type: ignore[attr-defined]
    monkeypatch.setattr(signin, "request_signin", cut)
    on_custom(db, person_a, stub, WORKABLE_PATH)
    ref = started(db, person_a)
    _password(factory, person_a)
    queued: list[Any] = []

    async def enqueue(name: str, *args: Any, **kwargs: Any) -> bool:
        queued.append((name, args, kwargs))
        return True

    app = FastAPI()
    app.include_router(later_router)
    app.state.sessionmaker, app.state.clock = factory, lambda: AT
    app.state.queue = SimpleNamespace(enqueue=enqueue)
    app.dependency_overrides[get_leases] = lambda: later
    app.dependency_overrides[deps.current_user] = lambda: SimpleNamespace(id=person_a.user_id)
    signin.register()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            fill(services(factory, form, leases, stub_base=stub, form_agent=agent), ref)
        assert application(db, ref).needs == "signin"
        bound = store.get(f"jobapp:browser:later:{person_a.user_id}")
        assert json.loads(bound or "{}").get("kind") == "handoff"
        with TestClient(api_app, follow_redirects=False) as client:
            api = Api(client)
            token = api.login_token(person_a.email)
            for box in boxes:
                key = quote(f"text:{base.fold(box.label)}#1", safe="")
                answer = api.request(
                    "PUT", f"/api/applications/{ref}/answers/{key}", token=token,
                    json={"value": box.value},
                )  # fmt: skip
                assert answer.status_code == 422
        with TestClient(app) as client:
            body = {"module": "application", "ref": str(ref)}
            assert client.post("/api/browser/handoff/later/done", json=body).status_code == 204
        assert [q[0] for q in queued] == ["apply_fill"]
        seen["signed"], lease.state = True, "active"
        svc = services(factory, form, FakeLeases(), stub_base=stub, form_agent=agent)
        assert fill(svc, ref) == "ready"
    assert canary.find([typed], values=[seen["snapshots"]])  # the run did read it
    db.expire_all()
    rows = db.execute(sa.select(ApplicationAnswer).where(ApplicationAnswer.application_id == ref))
    events = db.execute(sa.select(ApplicationEvent).where(ApplicationEvent.application_id == ref))
    bank = db.execute(sa.select(AnswerBankEntry)).scalars().all()
    item = application(db, ref)
    kept = [
        [(r.field_key, r.label, r.value) for r in rows.scalars()],
        [(e.kind, e.data) for e in events.scalars()],
        {c.name: getattr(item, c.name) for c in Application.__table__.columns},
        [{c.name: getattr(b, c.name) for c in AnswerBankEntry.__table__.columns} for b in bank],
        queued,
    ]
    found = canary.find([typed], logs=caplog.records, redis=store, values=kept)
    store.close()
    flush_redis(redis_url)
    assert found == []
    assert bank == []
