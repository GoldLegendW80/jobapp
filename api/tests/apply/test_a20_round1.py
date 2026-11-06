"""Round 1 of the review of #753 (A20), on a real Postgres and the fake sites of A19 and A20:

- B1: every choice the agent makes by a click is listed (radios, a custom combobox, Yes or No
  buttons of a group, a checkbox), entries are keyed by ref so two fields of one label stay two,
  and a chosen answer no recorded act matches is kept, never dropped;
- B2: a step act browserd traces on the review lease (`step_submit`) makes every end of the run
  but `ready` a `submit_unknown` with `sent_before_confirm`, and a new Apply is refused;
- B3: Try again of a review agent's `submit_unknown` is one more run of the agent (`queued`,
  `earlier_send`), and a Confirm is accepted only while a held session of the current run waits;
- the hold: 15 minutes, outside the apply queue's slot (`hand_over`, the hold job), bounded,
  and it gives the browser up when another application needs it;
- the worker's start ends a review agent's `ready` or `approved` application as `session_lost`;
- in Review first, browser_evaluate is not available, and its refusal says so.
No browser, no model, no company: every submission counted is the fake site's."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.apply import applier, confirm, gate, service, submit
from app.apply.models import ApplicationAnswer, Mode, State
from app.browser.client import BrowserError
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, located
from tests.apply.test_a19_applier import CHOSEN, STEP_2, Leases, Site, Stop, svc
from tests.apply.test_a20_review_first import (
    ReviewSite,
    confirm_now,
    kinds,
    on_wait,
    review_agent,
    run,
    ticked,
)

GENDER = {"field": "Gender", "value": "Prefer not to say", "why": "A personal fact: neutral."}
PRONOUNS = {"field": "Pronouns", "value": "Prefer not to say", "why": "No field held it."}


class Choices:
    """Step 2 also holds choices made by clicks, drawn as Playwright's aria snapshot draws them:
    a radiogroup, a custom combobox whose options open in an unnamed listbox, Yes or No buttons
    of a group, a checkbox, and two text boxes of one label (a work history)."""

    ticked = False
    opened = False

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        ref = args.get("ref")
        if name == "browser_click" and ref == "e31":
            self.ticked = not self.ticked
        if name == "browser_click" and ref == "e24":
            self.opened = True
        if name == "browser_click" and ref in ("e26", "e27"):
            self.opened = False
        return super().act(name, args, capability)  # type: ignore[misc]

    def text(self) -> str:
        text = super().text()  # type: ignore[misc]
        if self.page != STEP_2:  # type: ignore[attr-defined]
            return text
        listbox = (
            '\n- listbox [ref=e25]:\n  - option "resume-2024.pdf" [ref=e26]\n'
            '  - option "Upload a new file" [ref=e27]'
            if self.opened
            else ""
        )
        tick = " [checked]" if self.ticked else ""
        return (
            text + '\n- radiogroup "Gender" [ref=e21]:\n  - radio "Woman" [ref=e22]\n'
            '  - radio "Prefer not to say" [ref=e23]\n'
            '- combobox "Resume on file" [ref=e24]' + listbox + "\n"
            '- group "Are you authorised to work in the EU?" [ref=e28]:\n'
            '  - button "Yes" [ref=e29]\n  - button "No" [ref=e30]\n'
            f'- checkbox "I agree to the privacy notice"{tick} [ref=e31]\n'
            '- textbox "Company" [ref=e32]\n- textbox "Company" [ref=e33]'
        )


class ChoiceSite(Choices, ReviewSite):
    pass


class DirectChoiceSite(Choices, Site):
    pass


async def choose(session: applier.ApplySession) -> None:
    for ref in ("e22", "e23", "e24", "e27", "e29", "e30", "e31"):
        await session.interact("browser_click", {"ref": ref})
    await session.interact("browser_type", {"ref": "e32", "text": "Example Robotics"})
    await session.interact("browser_type", {"ref": "e33", "text": "Example Labs"})


def choice_agent(*, review: bool) -> Any:
    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        if brief.get("gate_clicked"):
            await session.snapshot()
            session.finish("submitted", "The site thanked the person.", session.url)
            return applier.Ran(1, 1)
        await session.navigate(brief["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e2", "text": "Alex Example"})
        await session.upload({"ref": "e5", "paths": brief["documents_to_upload"]})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        await session.interact("browser_click", {"ref": "e6"})
        await session.interact("browser_type", {"ref": "e7", "text": "Your robots."})
        await choose(session)
        chosen = [CHOSEN, GENDER, PRONOUNS]
        if review:
            session.ready_at("e9", chosen)
        else:
            await session.interact("browser_click", {"ref": "e9"})
            await session.snapshot()
            session.finish("submitted", "Sent.", session.url, chosen)
        return applier.Ran(10, 2)

    return go


QUESTIONS = [
    ("Questions", "Why this company?", "text", "Your robots.", None),
    ("Questions", "Gender", "choice", "Prefer not to say", GENDER["why"]),
    ("Questions", "Resume on file", "choice", "Upload a new file", None),
    ("Questions", "Are you authorised to work in the EU?", "choice", "No", None),
    ("Questions", "I agree to the privacy notice", "tick", "Ticked", None),
    ("Questions", "Company", "text", "Example Robotics", None),
    ("Questions", "Company", "text", "Example Labs", None),
    (applier.CHOSEN_STEP, "Pronouns", "choice", "Prefer not to say", PRONOUNS["why"]),
]


def test_every_choice_made_by_a_click_is_listed_hashed_and_chosen(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ChoiceSite()
    services_ = svc(factory, site, choice_agent(review=True))
    seen: dict[str, Any] = {}

    def person() -> None:
        with factory() as other:
            found = application(other, app_id)
            seen["stored"] = confirm.fields_of(other, found)
            seen["hash"] = found.answers_hash
            seen["versions"] = found.document_versions
        seen["code"] = confirm_now(factory, person_a, app_id, services_.fill.clock())

    on_wait(services_, person)
    assert run(services_, app_id) == "submitted"
    assert seen["code"] == "confirmed"
    fields = seen["stored"]["fields"]
    listed = [(f["step"], f["label"], f["kind"], f["value"], f.get("chosen")) for f in fields]
    assert listed[:4] == [
        ("Your details", "Full name", "text", "Alex Example", None),
        ("Your details", "Resume", "file", "cv.pdf, letter.pdf", None),
        ("Your details", "Notice period *", "text", "One month", CHOSEN["why"]),
        # The click on Next is no field: never listed.
        ("Questions", "Why this company?", "text", "Your robots.", None),
    ]
    assert listed[3:] == QUESTIONS
    # The hash the person's Confirm was bound to covers every one of them.
    assert seen["hash"] == confirm.fields_hash(fields, "Submit application", seen["versions"])
    assert seen["hash"] != confirm.fields_hash(fields[:-1], "Submit application", seen["versions"])
    report = db.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
    assert {row["label"] for row in report.value["chosen"]} == {
        "Notice period *",
        "Gender",
        "Pronouns",
    }
    assert len(site.submissions) == 1


def test_one_click_apply_keeps_a_chosen_answer_made_by_a_click(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from tests.apply.test_a5_run import started

    app_id = started(db, person_a, Mode.direct)
    assert run(svc(factory, DirectChoiceSite(), choice_agent(review=False)), app_id) == (
        "submitted"
    )
    report = db.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
    rows = {row["label"]: (row["value"], row["why"]) for row in report.value["chosen"]}
    assert rows == {
        "Notice period *": ("One month", CHOSEN["why"]),
        "Gender": ("Prefer not to say", GENDER["why"]),  # chosen by a click on a radio
        "Pronouns": ("Prefer not to say", PRONOUNS["why"]),  # no act matched: kept
    }


@pytest.mark.parametrize(
    ("role", "line", "expected"),
    [
        ("radio", '- radiogroup "Sex" [ref=e1]:\n  - radio "Female" [ref=e2]', ("Sex", "Female")),
        ("option", '- listbox "Country" [ref=e1]:\n  - option "France" [ref=e2]',
         ("Country", "France")),
        ("switch", '- switch "Remote only" [checked] [ref=e2]', ("Remote only", "Ticked")),
        ("checkbox", '- checkbox "Relocate" [ref=e2]', ("Relocate", "Unticked")),
        ("menuitemradio", '- menuitemradio "Full time" [ref=e2]', ("Full time", "Chosen")),
        ("button", '- group "Visa needed?" [ref=e1]:\n  - button "No" [ref=e2]',
         ("Visa needed?", "No")),
    ],
)  # fmt: skip
def test_a_click_that_chooses_is_recorded_by_its_group(
    role: str, line: str, expected: tuple[str, str]
) -> None:
    session = applier.ApplySession(tools=None, capability=None, stop=Stop(), arm=None)  # type: ignore[arg-type]
    session.page = line
    session._record("browser_click", {"ref": "e2"}, line, "Step")
    assert [(e["label"], e["value"]) for e in session.filled] == [expected]


def test_a_click_that_chooses_nothing_is_not_listed() -> None:
    page = '- button "Next" [ref=e1]\n- link "Help" [ref=e2]\n- list "Jobs" [ref=e3]:\n' \
        '  - button "Save" [ref=e4]'  # fmt: skip
    session = applier.ApplySession(tools=None, capability=None, stop=Stop(), arm=None)  # type: ignore[arg-type]
    session.page = page
    for ref in ("e1", "e2", "e4"):
        session._record("browser_click", {"ref": ref}, page, "Step")
    assert session.filled == []


# --- B2: a step act browserd traced -------------------------------------------------------------


class StepSite(ReviewSite):
    """Next (e6) passes on the review lease and browserd traces it (`step_submit`); `end` is
    what happens after: the act that follows loses the lease, or the page is the thanks page."""

    def __init__(self, *, lose_after_step: bool = False, **rest: Any) -> None:
        super().__init__(**rest)
        self.lose_after_step, self.stepped = lose_after_step, 0

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        if self.stepped and self.lose_after_step and name != "browser_snapshot":
            raise BrowserError("ended")
        result = super().act(name, args, capability)
        if name == "browser_click" and args.get("ref") == "e6" and capability is None:
            self.stepped += 1
            return {**result, "step_submit": True}
        return result


def step_agent(end: str) -> Any:
    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        go.briefs.append(brief)  # type: ignore[attr-defined]
        await session.navigate(brief["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        await session.interact("browser_click", {"ref": "e6"})
        await session.interact("browser_click", {"ref": "e6"})  # traced twice, one event
        if end == "raise":
            raise RuntimeError("the model stream broke")
        if end == "submitted":
            await session.snapshot()
            session.finish("submitted", "The site showed its thanks.", session.url)
        elif end == "failed":
            session.finish("failed", "The page did not load.", "")
        else:
            await session.interact("browser_type", {"ref": "e7", "text": "more"})
        return applier.Ran(10, 2)

    go.briefs = []  # type: ignore[attr-defined]
    return go


@pytest.mark.parametrize("end", ["failed", "raise", "lease", "submitted"])
def test_after_a_traced_step_act_every_end_but_ready_is_submit_unknown(
    db: Session, factory: sessionmaker[Session], person_a: Person, end: str
) -> None:
    app_id = ticked(db, person_a)
    site = StepSite(lose_after_step=end == "lease")
    assert run(svc(factory, site, step_agent(end)), app_id) == State.submit_unknown.value
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, "sent_before_confirm")
    assert kinds(db, app_id).count(applier.STEP_SENT) == 1
    assert site.gate_clicks == []
    # Maybe sent: a new Apply of the same posting is refused as a duplicate, nothing sent again.
    with pytest.raises(service.RefusedError) as refused:
        service.start(
            db, person_a.user_id, person_a.posting_id, Mode.review, False, now=AT,
            agent_review=True,
        )  # fmt: skip
    assert refused.value.refusal.code == "submit_unknown"
    db.rollback()


def test_a_held_session_lost_after_a_traced_step_act_is_submit_unknown(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = StepSite(lose_lease=True)
    assert run(svc(factory, site, review_agent()), app_id) == State.submit_unknown.value
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.submit_unknown, "sent_before_confirm")
    assert site.gate_clicks == []


def test_the_review_prompt_reports_a_step_that_sent_and_has_no_evaluate() -> None:
    assert "call apply_done submitted" in applier.PROMPT
    assert "In review_first, browser_evaluate is not\navailable." in applier.PROMPT


def test_browser_evaluate_is_refused_in_review_first_with_its_own_sentence() -> None:
    session = applier.ApplySession(tools=None, capability=None, stop=Stop(), arm=None, review=True)  # type: ignore[arg-type]
    text, error = asyncio.run(session.interact("browser_evaluate", {"function": "() => 1"}))
    assert error
    assert text == applier.REVIEW_EVALUATE
    assert "not available in Review first" in text
    assert "with this ref" not in text  # never the sentence of a refused submit


# --- B3: Try again, and a Confirm only with a live hold -------------------------------------------


def test_try_again_of_a_review_agent_s_submit_unknown_is_one_more_review_run(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    assert run(svc(factory, StepSite(), step_agent("failed")), app_id) == "submit_unknown"
    retried = gate.retry(db, person_a.user_id, app_id, AT)
    db.commit()
    # Never `ready` with the old list: queued for one run of the agent in review mode.
    assert (retried.state, retried.mode) == (State.queued, Mode.review)
    assert confirm_now(factory, person_a, app_id, AT) == "not_ready"
    from app.apply.router import _filled

    assert _filled(db, application(db, app_id), AT).get("filled") is None
    site, script = ReviewSite(), review_agent()
    services_ = svc(factory, site, script)

    def cancel() -> None:
        with factory() as other:
            gate.cancel(other, person_a.user_id, app_id, AT, reject=False)
            other.commit()

    on_wait(services_, cancel)
    assert run(services_, app_id) == "cancelled"
    assert script.briefs[0]["earlier_send"] is True
    assert script.briefs[0]["review_first"] is True
    assert site.submissions == []


def test_try_again_after_the_gate_s_click_is_one_more_review_run(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()

    async def unread(session: applier.ApplySession, message: str) -> applier.Ran:
        if json.loads(message).get("gate_clicked"):
            session.finish("failed", "The page showed nothing.", "")
            return applier.Ran(1, 1)
        return await filler(session, message)

    filler = review_agent()
    services_ = svc(factory, site, unread)
    on_wait(services_, lambda: confirm_now(factory, person_a, app_id, services_.fill.clock()))
    assert run(services_, app_id) == "submit_unknown"
    assert application(db, app_id).error_code == "no_confirmation"
    assert gate.retry(db, person_a.user_id, app_id, AT).state is State.queued
    db.commit()
    assert application(db, app_id).state is State.queued


def test_a_confirm_without_a_live_hold_of_the_current_run_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from app.apply import run as apply_run
    from app.apply.router import _filled

    app_id = ticked(db, person_a)
    services_ = svc(factory, ReviewSite(), review_agent())
    kept: list[applier.Held] = []

    async def lost_job(held: applier.Held) -> bool:
        kept.append(held)  # handed over, and the hold job never runs (a dead process)
        return True

    services_.hand_over = lost_job
    assert run(services_, app_id) == "ready"
    now = application(db, app_id).updated_at
    assert _filled(db, application(db, app_id), now)["filled"].fields_hash is not None
    # An end written after the list (here the run's own `ended`): no hold of this run waits.
    apply_run._event(db, application(db, app_id), "ended", now, code="interrupted")
    db.commit()
    assert _filled(db, application(db, app_id), now)["filled"].fields_hash is None
    assert confirm_now(factory, person_a, app_id, now) == "session_lost"
    db.expire_all()
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "session_lost")


def test_the_tick_never_submits_a_review_agent_s_approval(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    services_ = svc(factory, ReviewSite(), review_agent())
    seen: list[list[uuid.UUID]] = []

    def person() -> None:
        confirm_now(factory, person_a, app_id, services_.fill.clock())
        with factory() as other:
            assert application(other, app_id).state is State.approved
            seen.append(submit.due(other, AT))

    on_wait(services_, person)
    assert run(services_, app_id) == "submitted"
    assert app_id not in seen[0]


# --- the hold: 15 minutes, its own job, it gives the browser up ---------------------------------


def test_the_hold_waits_15_minutes_then_the_session_is_lost(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    assert confirm.READY_WAIT_S == 900
    app_id = ticked(db, person_a)
    site = ReviewSite()
    assert run(svc(factory, site, review_agent()), app_id) == "failed"
    assert application(db, app_id).error_code == "session_lost"
    assert site.kept == int(confirm.READY_WAIT_S // confirm.KEEP_S) - 1
    assert site.submissions == []


def test_the_held_session_runs_in_its_own_job_and_releases_the_lease_there(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()
    services_ = svc(factory, site, review_agent())
    kept: list[applier.Held] = []

    async def hand_over(held: applier.Held) -> bool:
        if not applier.keep_held(held):
            return False
        kept.append(held)
        return True

    services_.hand_over = hand_over
    # The fill job ends at `ready`: the apply queue's slot is free, the lease still held.
    assert run(services_, app_id) == "ready"
    assert services_.fill.leases.released == []
    assert applier.take_held(app_id) is kept[0]
    assert applier.take_held(app_id) is None  # a second job of the same id runs nothing
    assert confirm_now(factory, person_a, app_id, services_.fill.clock()) == "confirmed"
    assert asyncio.run(applier.run_held(kept[0])) == "submitted"
    assert [r[1] for r in services_.fill.leases.released] == ["lease-1"]
    assert len(site.submissions) == 1
    assert app_id not in applier._HELD


def test_no_room_for_a_hold_ends_the_run_and_sends_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()
    services_ = svc(factory, site, review_agent())

    async def full(held: applier.Held) -> bool:
        return False

    services_.hand_over = full
    assert run(services_, app_id) == "failed"
    assert application(db, app_id).error_code == "session_lost"
    assert [r[1] for r in services_.fill.leases.released] == ["lease-1"]
    assert site.submissions == []


def test_holds_are_bounded_and_one_per_person() -> None:
    def held(user: uuid.UUID) -> applier.Held:
        brief = applier.Brief(user, uuid.uuid4(), "")
        return applier.Held(None, None, brief, brief.application_id, None, applier.Ran(), "l")  # type: ignore[arg-type]

    first, second = uuid.uuid4(), uuid.uuid4()
    one = held(first)
    try:
        assert applier.keep_held(one)
        assert not applier.keep_held(held(first))  # one per person
        two = held(second)
        assert applier.keep_held(two)
        assert len(applier._HELD) == confirm.HOLDS_MAX
        assert not applier.keep_held(held(uuid.uuid4()))  # the bound
    finally:
        applier._HELD.clear()


@pytest.mark.parametrize(
    ("waiter", "purpose", "gives_up"),
    [
        ("other", "apply_fill", True),
        ("other", "apply_agent", True),
        ("other", "apply_review", True),
        ("other", "apply_submit", True),
        ("other", "full_auto", True),
        ("other", "takeover", True),
        ("other", "sweep", False),
        ("own", "apply_submit", True),
        ("own", "full_auto", True),
        ("own", "takeover", True),
        ("own", "signin", True),
        ("own", "apply_manual", True),
        ("own", "profile_edit", True),
        ("own", "apply_fill", False),
        ("own", "apply_agent", False),
        ("own", "apply_review", False),
        ("own", "sweep", False),
    ],
)
def test_who_a_hold_gives_the_browser_up_to(waiter: str, purpose: str, gives_up: bool) -> None:
    assert confirm.yields("u1" if waiter == "own" else "u2", "u1", purpose) is gives_up


class Waiting(Leases):
    def __init__(self, waiters: list[tuple[str, str]]) -> None:
        super().__init__()
        self.waiting = waiters

    async def waiters(self) -> list[tuple[str, str]]:
        return self.waiting


@pytest.mark.parametrize("other_waits", [True, False])
def test_the_hold_gives_the_browser_up_when_another_application_waits(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    person_b: Person,
    other_waits: bool,
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()
    services_ = svc(factory, site, review_agent())
    own, other = str(person_a.user_id), str(person_b.user_id)
    waiting = [(own, "apply_fill"), (other, "sweep")]
    if other_waits:
        waiting.append((other, "apply_submit"))
    leases = Waiting(waiting)
    services_.fill.leases = leases
    if not other_waits:
        on_wait(services_, lambda: confirm_now(factory, person_a, app_id, services_.fill.clock()))
    written = run(services_, app_id)
    if other_waits:
        assert written == "failed"
        assert application(db, app_id).error_code == "session_lost"
        assert site.submissions == []
    else:
        assert written == "submitted"  # the person's own other fill waits behind the hold
    assert [r[1] for r in leases.released] == ["lease-1"]


def test_the_leases_waiters_are_the_live_members_before_the_sweeps() -> None:
    from app.browser import leases as browser_leases

    class Redis:
        def __init__(self) -> None:
            self.queues = {
                browser_leases.QUEUES[0]: [b"u1|takeover"],
                browser_leases.QUEUES[1]: [b"u2|apply_fill", b"u3|apply_submit", b"junk"],
                browser_leases.QUEUES[2]: [b"u4|full_auto"],
                browser_leases.QUEUES[3]: [b"u5|sweep"],
            }
            self.tickets = {"u1|takeover", "u2|apply_fill", "u4|full_auto", "u5|sweep"}

        async def zrange(self, key: str, start: int, end: int) -> list[bytes]:
            return self.queues.get(key, [])

        async def exists(self, key: str) -> int:
            return int(key.removeprefix(browser_leases.TICKET) in self.tickets)

    redis = Redis()

    async def pool() -> Any:
        return redis

    store = browser_leases.Leases(pool, None, None, idle_s=300, session_max_s=1800)  # type: ignore[arg-type]
    # u3's ticket is gone (it left the queue), the sweep waits, a malformed member is skipped.
    assert asyncio.run(store.waiters()) == [
        ("u1", "takeover"),
        ("u2", "apply_fill"),
        ("u4", "full_auto"),
    ]


# --- the worker's start ---------------------------------------------------------------------------


def test_the_start_ends_a_review_agent_s_ready_or_approved_and_a_traced_fill(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    from app.apply import run as apply_run

    ready_id = ticked(db, person_a)
    services_ = svc(factory, ReviewSite(), review_agent())

    async def dead(held: applier.Held) -> bool:
        return True  # handed over, then the worker is killed hard

    services_.hand_over = dead
    assert run(services_, ready_id) == "ready"
    traced_id = ticked(db, person_b)
    found = application(db, traced_id)
    found.state = State.filling
    apply_run._event(db, found, applier.STEP_SENT, AT)
    db.commit()
    later = AT + timedelta(minutes=1)
    with factory() as other:
        assert applier.reset_interrupted(other, later) == 2
        other.commit()
    db.expire_all()
    ready, traced = application(db, ready_id), application(db, traced_id)
    assert (ready.state, ready.error_code) == (State.failed, "session_lost")
    assert (traced.state, traced.error_code) == (State.submit_unknown, "sent_before_confirm")
