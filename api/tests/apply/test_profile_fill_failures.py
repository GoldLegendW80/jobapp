"""Profile fills keep failures and person-only steps durable without a Save."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.apply import site_profile
from app.apply.models import SiteProfileRun, SiteProfileSection, SiteRunState
from tests.apply.a18_support import Leases, Portal, services
from tests.apply.test_a18_profiles import make, section


class UnavailablePortal(Portal):
    def __init__(self, fault):
        super().__init__()
        self.fault = fault

    async def navigate(self, url):
        if self.fault == "navigation":
            return {"ok": False, "error": "unavailable"}
        return await super().navigate(url)

    async def snapshot(self):
        if self.fault == "crashed":
            raise ConnectionError("synthetic portal disconnected")
        if self.fault == "empty":
            return {"ok": True, "snapshot": ""}
        if self.fault == "unreadable":
            return {"ok": False, "snapshot": ""}
        if self.fault == "captcha":
            return {"ok": True, "snapshot": '- checkbox "I am not a robot" [ref=c1]'}
        shot = await super().snapshot()
        if self.fault == "cut":
            shot["cut"] = True
        return shot


@pytest.mark.parametrize(
    ("fault", "state", "code", "needs"),
    [
        ("navigation", SiteRunState.failed, "navigation_failed", None),
        ("crashed", SiteRunState.failed, "profile_run_failed", None),
        ("empty", SiteRunState.failed, "profile_unreadable", None),
        ("unreadable", SiteRunState.failed, "profile_unreadable", None),
        ("cut", SiteRunState.failed, "profile_unreadable", None),
        ("captcha", SiteRunState.needs_person, None, "captcha"),
    ],
)
def test_unavailable_profile_keeps_a_durable_outcome_without_typing(
    db, factory, person_a, fault, state, code, needs
):
    portal = UnavailablePortal(fault)
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    db.expire_all()
    run = db.get(SiteProfileRun, run_id)
    assert (run.state, run.error_code, run.needs) == (state, code, needs)
    assert portal.typed == []
    assert portal.clicks == 0
    assert env.leases.bindings == {}
    assert db.scalar(sa.select(sa.func.count()).select_from(SiteProfileSection)) == 0


def test_busy_browser_keeps_the_profile_queued_without_a_partial_section(db, factory, person_a):
    class Busy(Leases):
        async def acquire(self, *args, **kwargs):
            return SimpleNamespace(lease=None, token=None)

    portal = Portal()
    env = services(factory, portal)
    env.leases = Busy()
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    db.expire_all()
    run = db.get(SiteProfileRun, run_id)
    assert (run.state, run.needs) == (SiteRunState.queued, "browser_busy")
    assert portal.typed == []
    assert db.scalar(sa.select(sa.func.count()).select_from(SiteProfileSection)) == 0


class SigninPortal(Portal):
    def __init__(self, outcome, after="profile"):
        super().__init__()
        self.outcome, self.after = outcome, after
        self.asked = []

    async def ask_signin(self, site, reason):
        self.asked.append((site, reason))
        return {"ok": False, "error": self.outcome}

    async def snapshot(self):
        if not self.asked:
            return {"ok": True, "snapshot": '- textbox "Password" [ref=p1]'}
        if self.after == "captcha":
            return {"ok": True, "snapshot": '- checkbox "I am not a robot" [ref=c1]'}
        return await super().snapshot()


@pytest.mark.parametrize("outcome", ["nobody_watching", "ended", "turn_ended"])
def test_unfinished_profile_signin_retains_only_the_owned_durable_handoff(
    db, factory, person_a, person_b, outcome
):
    portal = SigninPortal(outcome)
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    db.expire_all()
    run = db.get(SiteProfileRun, run_id)
    assert (run.state, run.needs) == (SiteRunState.needs_person, "signin")
    assert len(portal.asked) == 1
    assert portal.typed == []
    assert site_profile.address(db, person_b.user_id, run_id) is None
    assert site_profile.address(db, person_a.user_id, run_id) == run.url
    if outcome == "turn_ended":
        assert any("site-profile:lease:" in key for key in env.leases.bindings)
    else:
        assert env.leases.bindings == {}
    assert site_profile.resume(db, person_a.user_id, run_id, run.updated_at)
    assert not site_profile.resume(db, person_a.user_id, run_id, run.updated_at)


@pytest.mark.parametrize("after", ["profile", "captcha"])
def test_signed_in_profile_rereads_the_page_before_filling(db, factory, person_a, after):
    portal = SigninPortal("signed_in", after)
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    db.expire_all()
    run = db.get(SiteProfileRun, run_id)
    assert len(portal.asked) == 1
    assert portal.clicks == 0
    assert env.leases.bindings == {}
    if after == "captcha":
        assert (run.state, run.needs) == (SiteRunState.needs_person, "captcha")
        assert portal.typed == []
    else:
        assert run.state is SiteRunState.ready
        assert "Full name" in portal.typed


def test_requeued_profile_does_not_rewrite_a_reviewed_section(db, factory, person_a):
    portal = Portal()
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    run, part, rows = section(db, person_a.user_id, run_id)
    before = [(row.field_key, row.value, row.current_value) for row in rows]
    typed = list(portal.typed)
    run.state = SiteRunState.queued
    db.commit()
    asyncio.run(site_profile.fill(env, run_id))
    run, again, rows = section(db, person_a.user_id, run_id)
    assert again.section == part.section
    assert [(row.field_key, row.value, row.current_value) for row in rows] == before
    assert portal.typed == typed
    assert run.state is SiteRunState.ready


def test_profile_model_tools_refuse_other_steps_and_stale_references(db, factory, person_a):
    portal = Portal()
    run_id = make(db, person_a.user_id)

    async def agent(session):
        for method in (session.resolve_answer, session.fill_field):
            message, blocked = await method("missing-ref")
            assert blocked
            assert message
        message, blocked = await session.fill_field("e2")
        assert not blocked
        assert json.loads(message)["status"] == "already_filled"
        message, blocked = await session.next_step("e9")
        assert blocked
        assert "Review" in message
        message, blocked = await session.designate_submit("missing-ref")
        assert blocked
        assert "Save" in message
        return SimpleNamespace(input_tokens=10, output_tokens=2, turns=1, stopped="busy")

    asyncio.run(site_profile.fill(services(factory, portal), run_id, agent))
    run, _part, _rows = section(db, person_a.user_id, run_id)
    assert run.state is SiteRunState.ready
    assert run.cost["input_tokens"] == 10
    assert portal.typed.count("Full name") == 1
    assert portal.clicks == 0


def test_a_site_edit_between_fields_is_kept_for_the_person(db, factory, person_a):
    class ConcurrentEdit(Portal):
        async def type(self, ref, text, element="", submit=False):
            result = await super().type(ref, text, element, submit)
            if self.typed == ["Full name"]:
                self.values["Job title"] = "A title entered on the site"
            return result

    portal = ConcurrentEdit()
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(services(factory, portal), run_id))
    assert portal.values["Job title"] == "A title entered on the site"
    assert "Job title" not in portal.typed
    assert portal.clicks == 0
