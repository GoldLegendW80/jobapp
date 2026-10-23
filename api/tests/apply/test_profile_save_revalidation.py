"""Approved profile writes revalidate the site and preserve uncertain outcomes."""

import asyncio
import uuid
from datetime import timedelta

import pytest

from app.apply import profile_gate, profile_sources, site_profile
from app.apply.models import Authorisation, SiteRunState
from tests.apply.a18_support import Portal, services
from tests.apply.conftest import AT
from tests.apply.test_a18_profiles import make, section


class ChangingPortal(Portal):
    def __init__(self):
        super().__init__()
        self.fault = None
        self.attempts = 0
        self.save_typed = []

    async def navigate(self, url):
        if self.fault == "navigation_failed":
            return {"ok": False}
        if self.fault == "save_unknown" and self.clicks:
            return {"ok": False, "error": "navigation_failed"}
        result = await super().navigate(url)
        if self.fault == "site_value_changed":
            self.values["Full name"] = "A site edit"
        if self.fault == "form_changed":
            self.values.pop("Team size", None)
        return result

    async def snapshot(self):
        if self.fault == "section_unreadable" or (
            self.fault == "readback_section_missing" and self.clicks
        ):
            return {"ok": False, "snapshot": ""}
        shot = await super().snapshot()
        shot["snapshot"] += '  \n  - checkbox "I agree" [ref=c1]'
        if self.fault == "legal_commitment":
            shot["snapshot"] += " [checked]"
        if self.fault == "payment_requested":
            shot["snapshot"] += "\n  - paragraph: Payment required"
        if self.fault == "save_missing":
            shot["snapshot"] = shot["snapshot"].replace('  - button "Save" [ref=e9]', "")
        return shot

    async def select_option(self, ref, values, element=""):
        if self.fault == "unmarked_save":
            return {"ok": False, "error": "stale_ref"}
        return await super().select_option(ref, values, element)

    async def type(self, ref, text, element="", submit=False):
        if self.fault == "typing_refused":
            return {"ok": False, "error": "stale_ref"}
        result = await super().type(ref, text, element, submit)
        if self.fault == "value_differs":
            self.save_typed.append(self.typed[-1])
            if len(self.save_typed) == 2:
                self.values[self.save_typed[0]] = "A concurrent edit after typing"
        return result

    async def save(self, tools, ref, capability, element=""):
        self.attempts += 1
        if self.fault == "save_refused":
            return {"ok": False, "error": "stale_ref"}
        return await super().save(tools, ref, capability, element)

    async def screenshot(self, **kwargs):
        if self.fault == "screenshot_failed" and self.clicks:
            return {"ok": False}
        return await super().screenshot(**kwargs)


def approved(db, factory, person):
    portal = ChangingPortal()
    env = services(factory, portal)
    run_id = make(db, person.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    _run, part, rows = section(db, person.user_id, run_id)
    auth = profile_gate.authorise_section(
        db, person.user_id, run_id, part.section, profile_sources.value_hash(part, rows), AT
    )
    key, auth_id = part.section, auth.id
    db.commit()
    return portal, env, run_id, key, auth_id


@pytest.mark.parametrize(
    ("fault", "expected", "attempts", "consumed"),
    [
        ("navigation_failed", "navigation_failed", 0, False),
        ("section_unreadable", "section_unreadable", 0, False),
        ("form_changed", "form_changed", 0, False),
        ("legal_commitment", "legal_commitment", 0, False),
        ("payment_requested", "payment_requested", 0, False),
        ("site_value_changed", "site_value_changed", 0, False),
        ("typing_refused", "form_changed", 0, False),
        ("save_missing", "save_missing", 0, False),
        ("unmarked_save", "save_missing", 0, False),
        ("value_differs", "value_differs", 0, False),
        ("save_refused", "save_refused", 1, True),
        ("save_unknown", "save_unknown", 1, True),
        ("readback_section_missing", "readback_section_missing", 1, True),
        ("screenshot_failed", "screenshot_failed", 1, True),
    ],
)
def test_changed_profile_or_failed_save_preserves_a_reviewable_durable_outcome(
    db, factory, person_a, monkeypatch, fault, expected, attempts, consumed
):
    portal, env, run_id, key, auth_id = approved(db, factory, person_a)
    portal.fault = fault
    monkeypatch.setattr(profile_gate, "gate_click", portal.save)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    run, part, rows = section(db, person_a.user_id, run_id)
    assert (run.state, part.state, part.error_code) == (SiteRunState.ready, "ready", expected)
    assert portal.attempts == attempts
    assert (db.get(Authorisation, auth_id).used_at is not None) is consumed
    assert env.leases.bindings == {}
    assert any(event["kind"] == "readback_failed" for event in part.events)
    if expected in ("value_differs", "site_value_changed"):
        assert any(row.refusal == "value_differs" for row in rows)
    if not consumed:
        assert portal.saved["Full name"] == ""


@pytest.mark.parametrize("change", ["missing", "used", "expired", "stale", "cancelled"])
def test_profile_authority_is_rechecked_before_consumption(db, factory, person_a, change):
    _portal, _env, run_id, key, auth_id = approved(db, factory, person_a)
    run, part, rows = section(db, person_a.user_id, run_id)
    auth = db.get(Authorisation, auth_id)
    expected = "invalid_authorisation"
    if change == "missing":
        auth_id = uuid.uuid4()
    elif change == "used":
        auth.used_at = AT
    elif change == "expired":
        auth.expires_at = AT
    elif change == "stale":
        next(row for row in rows if row.approved).value = "Changed after approval"
        expected = "stale"
    else:
        run.state = SiteRunState.cancelled
        expected = "not_ready"
    _run, part, selected = profile_gate.consume(
        db, person_a.user_id, run_id, key, auth_id, AT, True, False
    )
    assert selected == []
    assert (part.state, part.error_code) == ("ready", expected)
    assert run.state is (SiteRunState.cancelled if change == "cancelled" else SiteRunState.ready)


def test_profile_authority_expiring_while_the_lock_is_held_is_not_consumed(db, factory, person_a):
    _portal, _env, run_id, key, auth_id = approved(db, factory, person_a)
    samples = iter((AT, AT + timedelta(hours=24)))
    _run, part, rows = profile_gate.consume(
        db, person_a.user_id, run_id, key, auth_id, AT, True, False, clock=lambda: next(samples)
    )
    assert rows == []
    assert part.error_code == "invalid_authorisation"
    assert db.get(Authorisation, auth_id).used_at is None


@pytest.mark.parametrize("fault", ["disabled", "cancelled", "expired", "stale"])
def test_profile_final_external_write_rechecks_changes_after_the_audit(
    db, factory, person_a, monkeypatch, fault
):
    portal, env, run_id, key, auth_id = approved(db, factory, person_a)
    original = profile_gate.consume

    def consume(*args, **kwargs):
        result = original(*args, **kwargs)
        run, part, rows = result
        if fault == "disabled":

            async def disabled():
                return False, False

            env.profile_switches = disabled
        elif fault == "cancelled":
            run.state = SiteRunState.cancelled
        elif fault == "expired":
            env.clock = lambda: AT + timedelta(hours=24)
        else:
            next(row for row in rows if row.approved).value = "Changed after audit"
        return run, part, rows

    monkeypatch.setattr(profile_gate, "consume", consume)
    monkeypatch.setattr(profile_gate, "gate_click", portal.save)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    run, part, _rows = section(db, person_a.user_id, run_id)
    assert portal.attempts == 0
    assert portal.saved["Full name"] == ""
    assert db.get(Authorisation, auth_id).used_at is not None
    assert env.leases.bindings == {}
    if fault == "cancelled":
        assert run.state is SiteRunState.cancelled
    else:
        expected = {"disabled": "paused", "expired": "invalid_authorisation", "stale": "stale"}
        assert part.error_code == expected[fault]
