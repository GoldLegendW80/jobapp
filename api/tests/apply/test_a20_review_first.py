"""Review first with the apply agent (A20, owner 2026-11-06: "I want to see all the input he
filled in but don't matter of the website or whatever, then a button to confirm"), on a real
Postgres: the agent fills a two step fake form on a lease that answers as browserd's
`apply_review` does (a wizard's Next passes, the form's send is refused without the capability),
names the send (`apply_ready`), the list comes from its recorded acts, and only the gate's click
after the person's Confirm sends it. No browser, no model, no company: every submission counted
is the fake site's. The box unticked, a sign in wall and a guest path (A20 point 2)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.apply import applier, confirm, gate, service
from app.apply.models import ApplicationAnswer, Mode, State
from app.browser.client import BrowserError
from tests.apply.conftest import AT, Person
from tests.apply.test_a5_run import application, events, located
from tests.apply.test_a19_applier import (
    CHOSEN,
    POSTING,
    STEP_1,
    STEP_2,
    THANKS,
    Client,
    Site,
    Stop,
    svc,
)

HEADINGS = {STEP_1: '- heading "Your details" [level=1]\n', STEP_2: '- heading "Questions"\n'}


class ReviewClient(Client):
    async def gate_click(
        self, lease_id: str, token: str, ref: str, capability: str, element: str = ""
    ) -> dict[str, Any]:
        self.site.gate_clicks.append((ref, element))
        return self.site.act("browser_click", {"ref": ref, "element": element}, capability)


class ReviewSite(Site):
    """The fake site on an `apply_review` lease: Next (e6) is a wizard's step (detect.review_step),
    Submit application (e9) refused without the capability; a call with it is a click only."""

    def __init__(self, *, lose_lease: bool = False, change: bool = False) -> None:
        super().__init__()
        self.client = ReviewClient(self)
        self.gate_clicks: list[tuple[str, str]] = []
        self.lose_lease, self.change, self.kept = lose_lease, change, 0

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        if capability is not None and name != "browser_click":
            raise BrowserError("bad_capability")  # browserd's 403 on this lease
        if name == "browser_wait_for" and args.get("time") == 0.1:
            self.kept += 1
            if self.lose_lease:
                raise BrowserError("ended")
        if name == "browser_snapshot" and self.change and self.gate_clicks == [] and self.kept:
            self.values["e7"] = "changed by the page"
        if name == "browser_click" and args.get("ref") == "e6" and capability is None:
            result = super().act(name, args, "step")  # a step button passes without it
            self.calls[-1] = (name, False)
            return result
        return super().act(name, args, capability)

    def text(self) -> str:
        text = super().text()
        if self.page == STEP_2 and self.values.get("e7") == "changed by the page":
            text += '\n- text "Your answer was changed"'
        return HEADINGS.get(self.page, "") + text


def review_agent(*, ready: bool = True) -> Any:
    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        go.briefs.append(brief)  # type: ignore[attr-defined]
        if brief.get("gate_clicked"):
            go.blocked = await session.interact(  # type: ignore[attr-defined]
                "browser_type", {"ref": "e7", "text": "more"}
            )
            await session.snapshot()
            session.finish("submitted", "The site thanked the person.", session.url)
            return applier.Ran(5, 1)
        await session.navigate(brief["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        await session.interact("browser_type", {"ref": "e2", "text": "Alex Example"})
        await session.interact("browser_type", {"ref": "e3", "text": "e2e-alex@example.org"})
        await session.upload({"ref": "e5", "paths": brief["documents_to_upload"]})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        await session.interact("browser_click", {"ref": "e6"})
        await session.interact("browser_type", {"ref": "e7", "text": "Your robots, my letter."})
        go.refused = await session.interact(  # type: ignore[attr-defined]
            "browser_click", {"ref": "e9"}
        )
        if ready:
            go.ready = session.ready_at("e9", [CHOSEN])  # type: ignore[attr-defined]
        return applier.Ran(100, 20)

    go.briefs = []  # type: ignore[attr-defined]
    return go


def ticked(db: Session, person: Person) -> uuid.UUID:
    """The Apply button with "Let me review each filled form before it is sent" ticked."""
    located(db, person)
    started = service.start(
        db, person.user_id, person.posting_id, Mode.review, False, now=AT, agent_review=True
    )
    return started.application.id


def run(services_: Any, app_id: uuid.UUID) -> str:
    return asyncio.run(applier.apply(services_, app_id))


def on_wait(services_: Any, act: Any) -> None:
    """The person's act while the held session waits: at the job's first sleep in `hold`."""
    sleep = services_.fill.sleep

    async def waiting(seconds: float) -> None:
        if seconds == confirm.POLL_S and not getattr(waiting, "done", False):
            waiting.done = True  # type: ignore[attr-defined]
            act()
        await sleep(seconds)

    services_.fill.sleep = waiting


def kinds(db: Session, app_id: uuid.UUID) -> list[str]:
    return [e.kind for e in events(db, app_id)]


def confirm_now(factory: sessionmaker[Session], person: Person, app_id: uuid.UUID, at: Any) -> str:
    with factory() as other:
        found = application(other, app_id)
        try:
            confirm.confirm(other, person.user_id, app_id, found.answers_hash, at)
        except gate.RefusedError as exc:
            other.commit()
            return exc.code
        other.commit()
        return "confirmed"


def test_the_agent_fills_the_person_confirms_and_the_gate_sends_once(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    assert confirm.review_agent(db, application(db, app_id))
    site, script, stop = ReviewSite(), review_agent(), Stop()
    services_ = svc(factory, site, script, stop)
    seen: dict[str, Any] = {}

    def person() -> None:
        with factory() as other:
            from app.apply.router import _filled

            seen.update(_filled(other, application(other, app_id)))
        seen["code"] = confirm_now(factory, person_a, app_id, services_.fill.clock())

    on_wait(services_, person)
    assert run(services_, app_id) == "submitted"
    assert seen["code"] == "confirmed"
    assert script.briefs[0]["review_first"] is True
    assert "browser_ask_person" not in applier.TOOLS
    # The agent's own click on Submit was refused by the lease, and never sent again with the
    # capability: the only call with it is the gate's click, after the Confirm.
    text, error = script.refused
    assert error
    assert "only the person's Confirm" in text
    assert [c for c in site.calls if c[1]] == [("browser_click", True)]
    assert site.gate_clicks == [("e9", "Submit application")]
    assert len(site.submissions) == 1
    assert services_.fill.leases.acquired == [{"purpose": "apply_review", "hosts": []}]
    # The list the person read: every field the agent filled, in order, by step, from its acts.
    filled = seen["filled"]
    assert filled.submit == "Submit application"
    assert [(f.step, f.label, f.kind, f.value, f.chosen) for f in filled.fields] == [
        ("Your details", "Full name", "text", "Alex Example", None),
        ("Your details", "Email", "text", "e2e-alex@example.org", None),
        ("Your details", "Resume", "file", "cv.pdf, letter.pdf", None),
        ("Your details", "Notice period *", "text", "One month", CHOSEN["why"]),
        ("Questions", "Why this company?", "text", "Your robots, my letter.", None),
    ]
    assert filled.fields_hash == application(db, app_id).answers_hash.hex()
    # After the click the agent reads only: its typing was refused.
    assert script.blocked[1] is True
    order = kinds(db, app_id)
    assert order.index("agent_ready") < order.index("person_confirmed") < order.index("gate_submit")
    assert order.index("gate_submit") < order.index("submitted")
    assert application(db, app_id).state is State.submitted
    assert stop.reads >= 2  # before the run, and again just before the gate's click


def test_cancel_while_it_waits_sends_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()
    services_ = svc(factory, site, review_agent())

    def cancel() -> None:
        with factory() as other:
            gate.cancel(other, person_a.user_id, app_id, AT, reject=False)
            other.commit()

    on_wait(services_, cancel)
    assert run(services_, app_id) == "cancelled"
    assert site.submissions == []
    assert site.gate_clicks == []
    assert not any(capability for _, capability in site.calls)


def test_a_lost_session_on_confirm_submits_nothing_and_offers_try_again(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite(lose_lease=True)
    services_ = svc(factory, site, review_agent())
    assert run(services_, app_id) == "failed"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.failed, "session_lost")
    assert site.submissions == []
    assert site.gate_clicks == []
    assert "Try again" in gate.SENTENCES["session_lost"]


def test_a_confirm_after_the_held_session_stopped_beating_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()
    services_ = svc(factory, site, review_agent())
    late = AT + timedelta(hours=1)
    codes: list[str] = []
    on_wait(services_, lambda: codes.append(confirm_now(factory, person_a, app_id, late)))
    assert run(services_, app_id) == "failed"
    assert codes == ["session_lost"]
    assert application(db, app_id).error_code == "session_lost"
    assert site.submissions == []


def test_a_confirm_of_another_list_or_another_person_is_refused(
    db: Session, factory: sessionmaker[Session], person_a: Person, person_b: Person
) -> None:
    app_id = ticked(db, person_a)
    services_ = svc(factory, ReviewSite(), review_agent())
    codes: list[str] = []

    def person() -> None:
        with factory() as other:
            for user, sent in ((person_a.user_id, b"\0" * 32), (person_b.user_id, b"\0" * 32)):
                try:
                    confirm.confirm(other, user, app_id, sent, AT)
                except (gate.RefusedError, gate.NotFoundError) as exc:
                    codes.append(getattr(exc, "code", "not_found"))
                other.rollback()
            # The old Approve of Review first is not this application's approval.
            hashed = application(other, app_id).answers_hash
            with pytest.raises(gate.RefusedError):
                gate.authorise_approval(other, person_a.user_id, app_id, hashed, AT, 60)
            other.rollback()
            gate.cancel(other, person_a.user_id, app_id, AT, reject=False)
            other.commit()

    on_wait(services_, person)
    assert run(services_, app_id) == "cancelled"
    assert codes == ["stale", "not_found"]


def test_a_page_changed_since_the_list_is_never_sent(
    db: Session, factory: sessionmaker[Session], person_a: Person, monkeypatch: Any
) -> None:
    monkeypatch.setattr(confirm, "KEEP_S", 0.0)
    app_id = ticked(db, person_a)
    site = ReviewSite(change=True)
    services_ = svc(factory, site, review_agent())
    on_wait(services_, lambda: confirm_now(factory, person_a, app_id, services_.fill.clock()))
    assert run(services_, app_id) == "form_changed"
    assert application(db, app_id).error_code == "form_changed"
    assert site.submissions == []
    assert site.gate_clicks == []


def test_the_emergency_stop_at_the_confirm_sends_nothing(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()
    reads = 1 + 9  # before the run and before each of the agent's nine acts
    services_ = svc(factory, site, review_agent(), Stop(*([None] * reads), "paused"))
    on_wait(services_, lambda: confirm_now(factory, person_a, app_id, services_.fill.clock()))
    assert run(services_, app_id) == "blocked"
    assert application(db, app_id).error_code == "paused"
    assert site.submissions == []
    assert site.gate_clicks == []


def test_an_end_without_apply_ready_fails_and_nothing_waits(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    app_id = ticked(db, person_a)
    site = ReviewSite()
    assert run(svc(factory, site, review_agent(ready=False)), app_id) == "failed"
    assert site.submissions == []
    assert confirm.fields_of(db, application(db, app_id)) is None


def test_apply_ready_names_only_a_control_of_the_last_snapshot(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    session = applier.ApplySession(
        tools=None,
        capability=None,
        stop=Stop(),
        arm=None,
        review=True,  # type: ignore[arg-type]
    )
    session.page = '- button "Submit application" [ref=e9]'
    assert session.ready_at("e99", [])[1] is True
    assert session.ready_at("e9", [])[0] == "Refused: fill the application first."
    direct = applier.ApplySession(tools=None, capability=None, stop=Stop(), arm=None)  # type: ignore[arg-type]
    assert direct.ready_at("e9", [])[0].startswith("Refused: apply_ready is for Review first")


def test_likes_and_the_box_unticked_never_take_the_review_path(
    db: Session, person_a: Person
) -> None:
    located(db, person_a)
    liked = service.start(db, person_a.user_id, person_a.posting_id, Mode.review, False, now=AT)
    assert not confirm.review_agent(db, liked.application)
    assert not applier.runs_agent(db, liked.application)


# --- the box unticked: the end without a question (A20 point 2) --------------------------------


class Wall(Site):
    """A posting whose Apply opens a sign in wall, with or without a guest path."""

    def __init__(self, guest: bool) -> None:
        super().__init__()
        self.guest = guest

    def act(self, name: str, args: dict[str, Any], capability: Any) -> dict[str, Any]:
        if name == "browser_click" and args.get("ref") == "e1":
            self.page = "https://jobs.example.org/signin"
            return {"ok": True, "url": self.page}
        if name == "browser_click" and args.get("ref") == "g1" and self.guest:
            self.page = STEP_1
            return {"ok": True, "url": self.page}
        return super().act(name, args, capability)

    def text(self) -> str:
        if self.page == "https://jobs.example.org/signin":
            guest = '\n- button "Apply as a guest" [ref=g1]' if self.guest else ""
            return '- textbox "Email" [ref=s1]\n- textbox "Password" [ref=s2]' + guest
        return super().text()


def wall_agent() -> Any:
    async def go(session: applier.ApplySession, message: str) -> applier.Ran:
        brief = json.loads(message)
        await session.navigate(brief["posting"]["url"])
        await session.interact("browser_click", {"ref": "e1"})
        if "Apply as a guest" not in session.page:
            session.finish(
                "failed",
                "jobs.example.org asks you to sign in. Sign in once in the live view, then Try "
                "again.",
                "",
            )
            return applier.Ran(10, 2)
        await session.interact("browser_click", {"ref": "g1"})
        await session.interact("browser_type", {"ref": "e2", "text": "Alex Example"})
        await session.interact("browser_type", {"ref": "e8", "text": "One month"})
        await session.interact("browser_click", {"ref": "e6"})
        await session.interact("browser_type", {"ref": "e7", "text": "Your robots."})
        await session.interact("browser_click", {"ref": "e9"})
        await session.snapshot()
        session.finish("submitted", "Applied as a guest.", session.url)
        return applier.Ran(10, 2)

    return go


def test_a_sign_in_wall_ends_failed_with_its_sentence_and_no_question(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from tests.apply.test_a5_run import started

    app_id = started(db, person_a, Mode.direct)
    site = Wall(guest=False)
    assert run(svc(factory, site, wall_agent()), app_id) == "failed"
    found = application(db, app_id)
    assert found.state is State.failed
    assert found.needs is None
    assert "needs_person" not in kinds(db, app_id)
    report = db.get(ApplicationAnswer, (app_id, applier.REPORT_KEY))
    assert report.value["summary"].startswith("jobs.example.org asks you to sign in.")
    assert site.submissions == []
    assert "guest apply" in applier.PROMPT


def test_a_guest_path_is_taken_when_the_site_offers_one(
    db: Session, factory: sessionmaker[Session], person_a: Person
) -> None:
    from tests.apply.test_a5_run import started

    app_id = started(db, person_a, Mode.direct)
    site = Wall(guest=True)
    assert run(svc(factory, site, wall_agent()), app_id) == "submitted"
    assert len(site.submissions) == 1
    assert POSTING != THANKS
