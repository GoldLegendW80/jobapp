"""A forbidden page recovers through passive inspection before any input resumes."""

import pytest

from app.apply import gate, run
from app.apply.models import Application, State
from app.browser.client import BrowserError
from tests.apply.a4_support import Spec
from tests.apply.a5_support import FakeAgent, FakeLeases, run_ashby
from tests.apply.a15_support import WizardForm
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, fields, fill, services, started, why_answer
from tests.apply.test_a10a_failures import PersonMoves, kinds, with_status
from tests.apply.test_a15_owned import answers_of, configured_fixture_origin, start_wizard

__all__ = ["configured_fixture_origin"]

INPUTS = {"click", "type", "press_key", "select_option", "check", "upload", "navigate"}


def guarded_wait(factory, app_id, form):
    """Model browserd's paused input and check the committed state before resumed input."""
    seen = {"waiting": False, "begun": False, "resumes": 0, "inputs": [], "reads": 0}
    original_log = form._log

    def log(name, **args):
        if seen["begun"] and name in INPUTS:
            assert not seen["waiting"], f"input while waiting: {name}"
            with factory() as db:
                assert db.get(Application, app_id).state is State.filling
            seen["inputs"].append(name)
        original_log(name, **args)

    async def wait(action, reason=None):
        if action == "begin":
            seen["waiting"] = seen["begun"] = True
        elif action == "resume":
            seen["waiting"] = False
            seen["resumes"] += 1
        return {"wait_left_s": 1800, "wait_reason": reason}

    async def passive():
        assert seen["waiting"]
        seen["reads"] += 1
        return await form.snapshot()

    form._log, form._apply_wait, form._person_snapshot = log, wait, passive
    return seen


def setup(factory, db, person, base, kind):
    if kind == "workday":
        app_id, form = start_wizard(db, person, base), WizardForm()
        # Discovery of an empty combobox must remain passive on a deterministic wizard too.
        form.fields.append(Spec("Preferred office", "react", options=("Paris", "Lyon")))
        form.groups[-1] += 1
    else:
        app_id, form = started(db, person), run_ashby()
    with_status(form, 403)
    leases = FakeLeases()
    svc = services(
        factory,
        form,
        leases,
        FakeAgent(why_answer),
        stub_base=base,
        needs_person_wait_s=180,
    )
    return app_id, form, leases, svc, guarded_wait(factory, app_id, form)


@pytest.mark.parametrize("kind", ["ashby", "workday"])
def test_usable_form_resumes_with_active_discovery_only_after_the_database_transition(
    db, factory, person_a, forms_base_url, kind
):
    app_id, form, leases, svc, seen = setup(factory, db, person_a, forms_base_url, kind)
    waiting = []

    def person_returns():
        app = application(db, app_id)
        waiting.append((app.state, app.needs))
        if kind == "ashby":
            for spec in form.fields:
                spec.value = ""

    svc.sleep = PersonMoves(svc, {1: person_returns})
    assert fill(svc, app_id) == "ready"
    assert waiting == [(State.needs_person, "forbidden")]
    app = application(db, app_id)
    assert (app.state, app.needs, app.error_code, app.attempt) == (State.ready, None, None, 1)
    assert seen["reads"] == seen["resumes"] == 1
    assert "click" in seen["inputs"]
    assert "press_key" in seen["inputs"]
    if kind == "ashby":
        assert "type" in seen["inputs"]
    assert kinds(db, app_id).count("person_done") == 1
    assert fields(db, app_id)
    if kind == "workday":
        rows = answers_of(db, app_id)
        assert [r.value for r in rows if r.field_key.startswith("designated:step:")] == [
            "Next",
            "Next",
        ]
    assert form.submit_clicks == 0
    assert len(leases.released) == 1


@pytest.mark.parametrize("kind", ["ashby", "workday"])
@pytest.mark.parametrize(
    "obstacle",
    [
        "blocked",
        "malformed",
        "unavailable",
        "unreadable_files",
        "cut",
        "missing_anchor",
        "ambiguous",
        "captcha",
        "signin",
    ],
)
def test_unusable_passive_page_keeps_the_original_wait_until_its_deadline(
    db, factory, person_a, forms_base_url, kind, obstacle
):
    app_id, form, leases, svc, seen = setup(factory, db, person_a, forms_base_url, kind)
    snapshot = form.snapshot

    async def obstructed():
        shot = await snapshot()
        if obstacle in {"blocked", "malformed"}:
            shot["snapshot"] = (
                '- heading "Access denied" [ref=e1]' if obstacle == "blocked" else "{"
            )
        elif obstacle == "unavailable":
            shot = {"ok": False, "error": "unavailable"}
        elif obstacle == "unreadable_files":
            shot["native_files"] = []
        elif obstacle == "cut":
            shot["cut"] = True
        elif obstacle == "missing_anchor":
            shot["snapshot"] = '- button "Submit application" [ref=e1]'
        elif obstacle == "ambiguous":
            shot["snapshot"] += '\n- button "Submit application" [ref=e99999]'
            if kind == "workday":
                shot["snapshot"] = shot["snapshot"].replace(
                    '\n- button "Submit application" [ref=e99999]',
                    '\n    - button "Submit application" [ref=e99999]',
                )
        else:
            shot["snapshot"] += (
                '\n- checkbox "I\'m not a robot" [ref=e99999]'
                if obstacle == "captcha"
                else '\n- textbox "Password" [ref=e99999]'
            )
        return shot

    def person_still_blocked():
        form.snapshot = obstructed

    svc.sleep = PersonMoves(svc, {1: person_still_blocked})
    assert fill(svc, app_id) == "needs_person"
    app = application(db, app_id)
    assert (app.state, app.needs, app.error_code, app.attempt) == (
        State.needs_person,
        "forbidden",
        None,
        1,
    )
    assert seen["reads"] > 0
    assert seen["resumes"] == 0
    assert seen["inputs"] == []
    assert "person_done" not in kinds(db, app_id)
    assert kinds(db, app_id).count("person_not_back") == 1
    assert svc.sleep.sleeps == 6
    assert form.submit_clicks == 0
    assert len(leases.released) == 1


@pytest.mark.parametrize("obstacle", ["wrong_posting", "missing_scope", "ambiguous_scope"])
def test_wizard_passive_read_keeps_its_posting_and_scope_guards(
    db, factory, person_a, forms_base_url, obstacle
):
    app_id, form, leases, svc, seen = setup(factory, db, person_a, forms_base_url, "workday")
    snapshot = form.snapshot

    async def changed():
        shot = await snapshot()
        if obstacle == "wrong_posting":
            shot["url"] = shot["url"].replace("job=1001", "job=1002")
        elif obstacle == "missing_scope":
            shot["snapshot"] = shot["snapshot"].replace('form "Application"', 'form "Other"')
        else:
            shot["snapshot"] += '\n- form "Application" [ref=e99999]'
        return shot

    def move():
        form.snapshot = changed

    svc.sleep = PersonMoves(svc, {1: move})
    assert fill(svc, app_id) == "needs_person"
    app = application(db, app_id)
    assert (app.state, app.needs) == (State.needs_person, "forbidden")
    assert seen["reads"] > 0
    assert seen["resumes"] == 0
    assert seen["inputs"] == []
    assert form.submit_clicks == 0
    assert len(leases.released) == 1


@pytest.mark.parametrize("obstacle", ["none", "missing_frame", "ambiguous_frame"])
def test_framed_form_requires_exactly_one_usable_form_frame_before_resuming(
    db, factory, person_a, forms_base_url, monkeypatch, obstacle
):
    choose = run.adapter_for

    def framed(*args, **kwargs):
        adapter = choose(*args, **kwargs)
        assert adapter is not None
        adapter.framed = True
        return adapter

    monkeypatch.setattr(run, "adapter_for", framed)
    app_id, form, leases, svc, seen = setup(factory, db, person_a, forms_base_url, "ashby")
    form.frame = True
    snapshot = form.snapshot

    async def changed():
        shot = await snapshot()
        if obstacle == "missing_frame":
            shot["snapshot"] = shot["snapshot"].replace("- iframe ", "- group ")
        elif obstacle == "ambiguous_frame":
            shot["snapshot"] += "\n" + shot["snapshot"]
        return shot

    def move():
        form.snapshot = changed

    svc.sleep = PersonMoves(svc, {1: move})
    assert fill(svc, app_id) == ("ready" if obstacle == "none" else "needs_person")
    app = application(db, app_id)
    assert (app.state, app.needs) == (
        (State.ready, None) if obstacle == "none" else (State.needs_person, "forbidden")
    )
    assert seen["reads"] > 0
    assert seen["resumes"] == (1 if obstacle == "none" else 0)
    assert bool(seen["inputs"]) is (obstacle == "none")
    assert form.submit_clicks == 0
    assert len(leases.released) == 1


@pytest.mark.parametrize(
    "action",
    [
        "cancel",
        "reject",
        "answer",
        "resume_race",
        "browser_lost",
        "snapshot_lost",
        "resume_lost",
        "wait_budget_spent",
    ],
)
def test_person_actions_and_browser_loss_end_the_wait_without_recovery_input(
    db, factory, person_a, forms_base_url, action
):
    app_id, form, leases, svc, seen = setup(factory, db, person_a, forms_base_url, "ashby")

    def person_acts():
        with factory() as other:
            if action in {"cancel", "reject", "resume_race"}:
                gate.cancel(other, person_a.user_id, app_id, AT, reject=action == "reject")
            elif action == "answer":
                row = fields(other, app_id)["Why Example Robotics?"]
                gate.set_answer(
                    other, person_a.user_id, app_id, row.field_key, "Example answer", AT
                )
                form._other = "Access denied"
            other.commit()

    original_wait = form._apply_wait

    async def wait(action_name, reason=None):
        if action_name == "resume" and action == "resume_race":
            person_acts()
        if action_name == "read" and action == "browser_lost":
            raise BrowserError("ended")
        if action_name == "read" and action == "wait_budget_spent":
            return {"wait_left_s": 0}
        if action_name == "resume" and action == "resume_lost":
            raise BrowserError("ended")
        return await original_wait(action_name, reason)

    passive = form._person_snapshot

    async def snapshot():
        if action == "snapshot_lost":
            raise BrowserError("ended")
        return await passive()

    form._person_snapshot = snapshot
    form._apply_wait = wait
    svc.sleep = PersonMoves(
        svc, {1: person_acts} if action in {"cancel", "reject", "answer"} else {}
    )
    waits = {"answer", "browser_lost", "snapshot_lost", "resume_lost", "wait_budget_spent"}
    assert fill(svc, app_id) == ("needs_person" if action in waits else "person_acted")
    app = application(db, app_id)
    assert app.state is (State.needs_person if action in waits else State.cancelled)
    if action == "answer":
        assert fields(db, app_id)["Why Example Robotics?"].value == "Example answer"
    assert seen["inputs"] == []
    assert seen["resumes"] == (1 if action == "resume_race" else 0)
    assert "person_done" not in kinds(db, app_id)
    assert form.submit_clicks == 0
    assert len(leases.released) == 1
