"""What a person types to sign in never reaches the app (SPEC-apply, "Accounts and signing in" and
"What the agent may never do"): a credential box is found from browserd's read of the page, not
its label alone; a page that holds one is a sign in wall, a sign in's or a sign up's, before
anything is typed; and no such box is ever a field, an answer, a row, a log line, a Redis value
or a job argument. The label rule as accept and refuse tables, the wall rule, then the whole
path through `run.fill`, the api and the later hand off. Only a box that takes typed text is
one: a check box, a choice, a file field or a skipped widget so labelled is filled, approved and
submitted as any other, and a box no row keeps always stands on a wall (browserd's refusal to
type included). browserd's own read and its refusal to type are
tests/browser/test_credential_inputs.py and test_credential_container.py."""

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
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
    Mode,
    State,
)
from app.browser.handoff import Outcome
from app.browser.later_router import router as later_router
from app.browser.router import get_leases
from app.identity import deps
from tests.apply.a4_support import FakeForm, Spec
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.a6_support import SubmitForm, submit_ashby
from tests.apply.a8b_support import WORKABLE_PATH, ScriptedFormAgent, workable
from tests.apply.conftest import AT
from tests.apply.test_a5_run import WHY, application, fill, services, started, why_answer
from tests.apply.test_a6_gate import _password, api_app, approve, run_submit, stub, submit_services
from tests.apply.test_a8_generic import WORKABLE_SCRIPT, on_custom, rows_of
from tests.identity.api_helpers import Api
from tests.jobs.test_worker import flush_redis, redis_url
from tests.mail import canary

__all__ = ["api_app", "redis_url", "stub"]

# --- the label rule --------------------------------------------------------------------------

# A credential's label (`secret`): on a box that takes typed text, never a field and a wall.
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
    "Authenticator code", "Enter the code from your authenticator app", "Backup code",
    "Recovery code", "Passkey", "Clave de acceso", "Saisissez le code à 6 chiffres",
    "6-stelliger Code", "Código de 6 dígitos", "Codice a 6 cifre", "6-cijferige code",
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
    "What is your notice period?", "Enter your 5-digit zip code", "Accessibility code",
    "Two factors that motivate you", "Pinyin name", "Code of conduct", "Dress code", "Tax code",
    "Social security number", "Background verification consent", "",
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
    (one("combobox", "Security question"), "signin"),  # a typeahead takes typed text
    # a native select takes none: never one, whatever its label or a read that names it
    (shot(['- combobox "Security question" [ref=e1]:', '  - option "Your first pet"']), ""),
    (shot(['- combobox "Pick one" [ref=e1]:', '  - option "PIN"'], {"e1": True}), ""),
    (one("textbox", "Card security code"), ""),  # the payment page's
    (one("checkbox", "Remember my password"), ""),
    (one("checkbox", "Remember my password", True), ""),
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


def test_a_credential_box_is_never_written_and_no_other_box_is_one_whatever_its_label(db, person_a):
    """A box that takes typed text, read or labelled as a credential or refused as one by
    browserd at the typing, is no row; a check box, a choice, a file field or a skipped widget
    so labelled is a row as any other."""
    found = application(db, started(db, person_a))
    left = answers.Left("not_filled", (), True)
    kept = [
        base.Field("text:first name#1", "First name", "text", required=True),
        base.Field("checkbox:remember my password#1", "Remember my password", "checkbox"),
        base.Field("yes_no:pin code#1", "PIN code", "yes_no", options=("Yes", "No")),
        base.Field("select:security question#1", "Security question", "select", options=("A",)),
        base.Field("file:password protected pdf#1", "Password protected PDF", "file"),
    ]
    dropped = [
        base.Field("text:secret word#1", "Secret word", "text", credential=True),
        base.Field("combobox:security question#1", "Security question", "combobox"),
        base.Field("text:new password#1", "New password", "text"),
    ]
    outcomes = [base.Outcome(b, left, base.Filled("left", "not_filled")) for b in kept + dropped]
    refused = base.Field("text:account word#1", "Account word", "text")
    typed = answers.Answer("e2e", "answers.word")
    outcomes.append(base.Outcome(refused, typed, base.Filled("failed", base.CREDENTIAL)))
    report = base.Report(
        outcomes=outcomes,
        skipped=[
            base.Skipped("Choose a password", "radiogroup", "unsupported_widget"),
            base.Skipped("Autofill your password", "text", "autofill", credential=True),
            base.Skipped("", "textbox", "no_label", credential=True),
            base.Skipped("Autofill from resume", "text", "autofill"),
        ],
    )
    rows = [row.label for row in run._answer_rows(found, report)]
    assert rows == [b.label for b in kept] + ["Choose a password", "Autofill from resume"]


class Remember:
    """A page with a check box named as a password's, and, when `wall`, a password box too."""

    def __init__(self, wall: bool = False) -> None:
        self.wall, self.ticked = wall, False
        self.calls: list[tuple[str, str]] = []

    async def snapshot(self) -> dict[str, Any]:
        mark = " [checked]" if self.ticked else ""
        lines = [
            f'- checkbox "Remember my password"{mark} [ref=e1]',
            '- textbox "First name" [ref=e2]',
            '- button "Submit application" [ref=e3]',
        ]
        if self.wall:
            lines.append('- textbox "Secret word" [ref=e4]')
        return shot(lines, {"e2": False, "e4": True})

    async def check(self, ref: str, checked: bool = True, element: str = "") -> dict[str, Any]:
        self.calls.append(("check", ref))
        self.ticked = checked
        return {"ok": True}

    async def type(self, ref: str, text: str, element: str = "", submit: bool = False) -> Any:
        self.calls.append(("type", ref))
        return {"ok": True}


def test_a_check_box_named_as_a_password_is_ticked_and_nothing_is_done_on_a_wall() -> None:
    async def scenario(tools: Remember) -> tuple[Any, ...]:
        session = form_agent.FormSession(generic.Generic(), tools, None, str(uuid.uuid4()))  # type: ignore[arg-type]
        found, _ = await session.adapter.read(tools)
        assert found is not None
        box = next(f for f in found.fields if f.kind == "checkbox")
        filled = await base.Adapter().fill(tools, box, answers.Answer(True, "answers.remember"))
        if not tools.wall:
            return found.pending, box.credential, filled
        secret = next(f for f in found.fields if f.label == "Secret word")
        typed = await base.Adapter().fill(tools, secret, answers.Answer("e2e", "answers.word"))
        asked = (await session.fill_field("e4"), await session.resolve_answer("e1"))
        return found.pending, box.credential, filled, secret.credential, typed, *asked

    plain = Remember()
    assert asyncio.run(scenario(plain)) == ("", False, base.Filled("filled"))
    assert plain.calls == [("check", "e1")]
    walled = Remember(wall=True)
    pending, marked, filled, secret, typed, *asked = asyncio.run(scenario(walled))
    assert (pending, marked, filled) == ("signin", False, base.Filled("failed", "signin"))
    assert (secret, typed) == (True, base.Filled("failed", "signin"))
    assert asked == [(form_agent.WALL, True)] * 2
    assert walled.calls == []


# --- only a box that takes typed text is a credential, and its page is always a wall -------------

TYPED_SPECS = ("text", "react", "typeahead")  # the a4_support kinds that take typed text
SPEC_KINDS = (*TYPED_SPECS, "select", "checkbox", "yesno", "file", "listbox", "buttons")
# Labels the rule marks (a credential's, or an application's own read as one: the stated cost),
# and plain ones.
KIND_LABELS = (
    "Password reset consent", "PIN code", "Password protected PDF", "Security question",
    "Do you use a password manager?", "Have you read our Security Code of Conduct?",
    "Are you a PWD (Person with Disability)?", "New password",
    "Opt in to two-factor authentication", "Describe your experience with Erlang/OTP",
    "First name", "Have you read our Code of Conduct?", "Autofill with your password",
    "Autofill from resume",
)  # fmt: skip


def spec_of(label: str, kind: str, required: bool = False) -> Spec:
    choices = ("1Password", "Bitwarden") if kind == "buttons" else ("Your pet", "Your school")
    return Spec(label, kind, required, () if kind in ("text", "yesno", "file") else choices)


def read_as(form: Any, reads: dict[str, bool | None]) -> Any:
    """`form`'s snapshots carry browserd's read of every element of the fields named in `reads`
    (left out when None), as a hostile read naming a check box or a select would."""
    shown = form.snapshot

    async def snapshot() -> dict[str, Any]:
        got = await shown()
        got["credentials"] = {
            ref: read
            for ref, (_, i, _) in form._refs.items()
            if i >= 0 and (read := reads.get(form.fields[i].label)) is not None
        }
        return got

    form.snapshot = snapshot
    return form


@pytest.mark.parametrize("kind", SPEC_KINDS)
@pytest.mark.parametrize("read", [True, False, None])
@pytest.mark.parametrize("label", KIND_LABELS)
def test_a_box_no_row_keeps_is_always_on_a_wall_and_takes_typed_text(
    kind: str, read: bool | None, label: str
) -> None:
    """Invariant 1: only a box that takes typed text is a credential, by browserd's read or its
    label. Invariant 2: a box no row records always stands on a sign in wall."""
    form = read_as(FakeForm([Spec("Full name", "text", True), spec_of(label, kind)]), {label: read})
    page = base.parse_shot(asyncio.run(form.snapshot()))
    found = base.discover_fields(page, frozenset({"submit application"}))
    marked = [box.label for box in (*found.fields, *found.skipped) if box.credential]
    assert bool(marked) == (kind in TYPED_SPECS and (read is True or base.secret(label)))
    assert (base.Adapter().pending_at(page) == "signin") == bool(marked)
    left = answers.Left("not_filled", (), False)
    report = base.Report(
        outcomes=[base.Outcome(f, left, base.Filled("left", "not_filled")) for f in found.fields],
        skipped=found.skipped,
    )
    person = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    kept = {row.label for row in run._answer_rows(person, report)}  # type: ignore[arg-type]
    assert (label in kept) == (not marked)


@pytest.mark.parametrize("read", [True, False, None])
@pytest.mark.parametrize("role", ["textbox", "searchbox", "spinbutton", "combobox", "checkbox"])
def test_a_box_with_no_label_is_kept_unless_browserd_reads_it_as_a_credential(
    role: str, read: bool | None
) -> None:
    page = base.parse_shot(one(role, "", read))
    found = base.discover_fields(page, frozenset())
    credential = role != "checkbox" and read is True
    assert [s.credential for s in found.skipped] == [credential]
    assert (base.Adapter().pending_at(page) == "signin") == credential
    person = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    rows = run._answer_rows(person, base.Report(skipped=found.skipped))  # type: ignore[arg-type]
    assert len(rows) == (0 if credential else 1)


def ashby_with(spec: Spec) -> SubmitForm:
    """A5's Ashby form with `spec` at its end, a fresh copy for each lease."""
    form = run_ashby()
    return SubmitForm([*form.fields, replace(spec)], submit=form.submit, title=form.title)


# A credential looking label on a box that takes no typed text: a field, never a wall. The brief's
# three, a native select, a skipped list box and button group, and the re-check's probes.
WIDGETS = [
    pytest.param(spec_of("Password reset consent", "checkbox"), id="check box"),
    pytest.param(spec_of("PIN code", "yesno"), id="yes or no"),
    pytest.param(spec_of("Password protected PDF", "file"), id="file"),
    pytest.param(spec_of("Security question", "select"), id="native select"),
    pytest.param(spec_of("Do you use a password manager?", "listbox"), id="skipped list box"),
    pytest.param(spec_of("Which password manager do you use?", "buttons"), id="skipped buttons"),
    pytest.param(spec_of("Have you read our Security Code of Conduct?", "checkbox"), id="conduct"),
    pytest.param(spec_of("Are you a PWD (Person with Disability)?", "yesno"), id="PWD"),
]


@pytest.mark.parametrize("spec", WIDGETS)
def test_a_widget_named_as_a_credential_is_kept_approved_refilled_and_submitted(
    db, factory, person_a, stub, spec
):
    """Optional: the fill ends `ready` with its row shown, the approval passes, and the submit
    lease reads the same form back and clicks, once: never `form_changed` at each approval."""
    ref = started(db, person_a)
    filled = services(factory, ashby_with(spec), None, FakeAgent(why_answer), stub_base=stub)
    assert fill(filled, ref) == "ready"
    assert spec.label in {row.label for row in gate.rows_of(db, application(db, ref))}
    assert approve(factory, person_a, ref).passed
    form = ashby_with(spec)
    assert run_submit(submit_services(factory, form, stub), ref) == "submitted"
    assert [c["what"] for c in form.gate_clicks] == ["submit"]


REQUIRED = [
    pytest.param(spec_of("Password reset consent", "checkbox", True), True, id="check box"),
    pytest.param(spec_of("PIN code", "yesno", True), False, id="yes or no"),
    pytest.param(spec_of("Security question", "select", True), "Your pet", id="native select"),
    pytest.param(
        spec_of("Have you read our Security Code of Conduct?", "checkbox", True), True, id="conduct"
    ),
    pytest.param(spec_of("Are you a PWD (Person with Disability)?", "yesno", True), True, id="PWD"),
]


@pytest.mark.parametrize(("spec", "value"), REQUIRED)
def test_a_required_widget_named_as_a_credential_is_the_persons_question_then_submitted(
    db, factory, person_a, stub, spec, value
):
    """Required and left: the item waits `required_left` with the question shown, the person's
    answer is taken (never 422), then the approval and one click."""
    ref = started(db, person_a)
    filled = services(factory, ashby_with(spec), None, FakeAgent(why_answer), stub_base=stub)
    assert fill(filled, ref) == "needs_person"
    found = application(db, ref)
    assert (found.state, found.needs) == (State.needs_person, "required_left")
    (row,) = [row for row in gate.rows_of(db, found) if row.label == spec.label]
    assert (row.required, row.value) == (True, None)
    gate.set_answer(db, person_a.user_id, ref, row.field_key, value, AT)
    db.commit()
    assert application(db, ref).state is State.ready
    assert approve(factory, person_a, ref).passed
    form = ashby_with(spec)
    assert run_submit(submit_services(factory, form, stub), ref) == "submitted"
    assert [c["what"] for c in form.gate_clicks] == ["submit"]


# A box that takes typed text, labelled as a credential or read as one: a wall, whether it is a
# sign in's ("New password", read plain as type=text) or an application's own (the stated cost).
TYPED_WALLS = [
    pytest.param(spec_of("New password", "text"), False, id="New password read plain"),
    pytest.param(spec_of("Describe your experience with Erlang/OTP", "text"), None, id="OTP"),
    pytest.param(spec_of("Which password manager do you use?", "react"), None, id="react"),
    pytest.param(spec_of("Account word", "text"), True, id="read as one"),
]


@pytest.mark.parametrize("mode", [Mode.review, Mode.full_auto])
@pytest.mark.parametrize(("spec", "read"), TYPED_WALLS)
def test_a_box_that_takes_typed_text_read_or_named_as_a_credential_is_a_wall_even_optional(
    db, factory, person_a, stub, spec, read, mode
):
    """Never `ready` then `form_changed`: nobody watches, so the item waits for a later sign
    in, as a review item in Full auto, with nothing typed and no row of the box."""
    ref = started(db, person_a, mode)
    form = read_as(ashby_with(spec), {spec.label: read})
    svc = services(factory, form, None, FakeAgent(why_answer), stub_base=stub)
    assert fill(svc, ref) == "needs_person"
    found = application(db, ref)
    assert (found.state, found.needs, found.mode) == (State.needs_person, "signin", Mode.review)
    assert [c for c in form.calls if c[0] in ACTING] == []
    assert spec.label not in {row.label for row in gate.rows_of(db, found)}
    assert spec.label not in {row.label for row in rows_of(db, ref).values()}


def refusing(form: Any, label: str) -> Any:
    """browserd's refusal to type into the box `label`: a credential input that no read before
    the typing saw (made one since, or left unread)."""
    form.refused = []
    typed = form.type

    async def type_(ref: str, text: str, element: str = "", submit: bool = False) -> Any:
        target = form._target(ref)
        if target is not None and target[0] == "input" and form.fields[target[1]].label == label:
            form.refused.append(label)
            return {"ok": False, "error": "credential"}
        return await typed(ref, text, element, submit)

    form.type = type_
    return form


def appears(spec: Spec) -> Any:
    """`spec` shown once the fill typed its first value: a box no read before saw."""

    def on_call(form: Any, n: int) -> None:
        if form.calls[-1][0] == "type" and spec not in form.fields:
            form.fields.append(spec)

    return on_call


@pytest.mark.parametrize(
    ("label", "added"),
    [("Email address", True), ("Email", False), (WHY, False)],
    ids=["added after the read", "made one since", "typed by the answer agent"],
)
def test_a_typing_browserd_refuses_as_a_credential_is_a_wall_and_its_box_never_answerable(
    db, factory, person_a, stub, label, added
):
    """The re-check's follow up 3: the box browserd refused at the typing is no field the person
    answers; its page is a wall (`needs_person: signin`), never `required_left`."""
    form = run_ashby()
    if added:
        form.on_call = appears(Spec(label, "text", True))
    refusing(form, label)
    ref = started(db, person_a)
    svc = services(factory, form, None, FakeAgent(why_answer), stub_base=stub)
    assert fill(svc, ref) == "needs_person"
    found = application(db, ref)
    assert (found.state, found.needs) == (State.needs_person, "signin")
    assert form.refused == [label]
    rows = rows_of(db, ref)
    assert label not in {row.label for row in rows.values()}
    with pytest.raises(gate.InvalidAnswerError, match="unknown_field"):
        gate.set_answer(db, person_a.user_id, ref, f"text:{base.fold(label)}#1", "e2e", AT)
    db.rollback()


def test_a_typing_browserd_refuses_in_the_form_agents_run_keeps_none_of_its_step(
    db, factory, person_a, stub
):
    form = refusing(workable(), "Email")
    agent = ScriptedFormAgent(WORKABLE_SCRIPT)
    on_custom(db, person_a, stub, WORKABLE_PATH)
    ref = started(db, person_a)
    svc = services(factory, form, FakeLeases(), stub_base=stub, form_agent=agent)
    assert fill(svc, ref) == "needs_person"
    assert application(db, ref).needs == "signin"
    assert form.refused == ["Email"]
    assert (form_agent.REFUSED, True) in [answer[1:] for answer in agent.answers]
    assert rows_of(db, ref) == {}  # First name was typed, but the page is a wall: none of it


def test_a_typing_browserd_refuses_at_the_refill_is_a_wall_never_form_changed(
    db, factory, person_a, stub
):
    ref = started(db, person_a)
    svc = services(factory, run_ashby(), None, FakeAgent(why_answer), stub_base=stub)
    assert fill(svc, ref) == "ready"
    assert approve(factory, person_a, ref).passed
    form = refusing(submit_ashby(), "Email")
    assert run_submit(submit_services(factory, form, stub), ref) == "needs_person"
    found = application(db, ref)
    assert (found.state, found.needs) == (State.needs_person, "signin")
    assert form.refused == ["Email"]
    assert form.gate_clicks == []


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
