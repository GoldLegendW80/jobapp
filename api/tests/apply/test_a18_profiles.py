"""A18's complete fill, owned review and code-only Save contract."""

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.apply import profile_gate, profile_sources, site_profile
from app.apply.models import Authorisation, SiteProfileRun, SiteProfileShot, SiteRunState
from tests.apply.a18_support import Portal, services
from tests.apply.conftest import AT


def make(db, user_id, confirmed=False):
    run = site_profile.start(db, user_id, "https://portal.example/profile", confirmed, now=AT)
    db.commit()
    return run.id


def section(db, user_id, run_id):
    db.expire_all()
    run = site_profile.lock(db, user_id, run_id)
    part = site_profile.sections(db, run)[0]
    rows = site_profile.fields(db, run, part.section)
    return run, part, rows


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/profile",
        "javascript:alert(1)",
        "ftp://example.org",
        "https://user:password@example.org",
        "https://",
        "https://example.org/\nprofile",
    ],
)
def test_invalid_address_spends_nothing(db, person_a, url):
    with pytest.raises(site_profile.RefusedError):
        site_profile.start(db, person_a.user_id, url, now=AT)
    assert db.scalar(sa.select(sa.func.count()).select_from(SiteProfileRun)) == 0


def test_linkedin_refused_without_person_optin(db, person_a):
    with pytest.raises(site_profile.RefusedError, match="LinkedIn"):
        site_profile.start(db, person_a.user_id, "https://www.linkedin.com/in/example", now=AT)


def test_second_live_run_on_host_refused(db, person_a):
    make(db, person_a.user_id)
    with pytest.raises(site_profile.RefusedError):
        make(db, person_a.user_id)


def test_fill_keeps_current_values_and_protected_fields(db, factory, person_a):
    portal = Portal()
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(services(factory, portal), run_id))
    run, part, rows = section(db, person_a.user_id, run_id)
    assert run.state is SiteRunState.ready, run.error_code
    assert part.error_code is None
    assert portal.values["Full name"]
    assert portal.values["Job title"]
    assert portal.values["Phone"] == "+33 1 00 00 00 00"
    assert "Email" not in portal.typed
    assert "Phone" not in portal.typed
    assert portal.values["Team size"] == ""
    assert portal.clicks == 0
    assert portal.saved["Full name"] == ""
    assert next(r for r in rows if r.label == "Email").refusal == "email"
    assert db.scalar(sa.select(sa.func.count()).select_from(SiteProfileShot)) == 1


def test_save_once_with_hash_and_owned_approval(db, factory, person_a, person_b, monkeypatch):
    portal = Portal()
    run_id = make(db, person_a.user_id)
    env = services(factory, portal)
    asyncio.run(site_profile.fill(env, run_id))
    run, part, rows = section(db, person_a.user_id, run_id)
    with pytest.raises(site_profile.NotFoundError):
        profile_gate.authorise_section(db, person_b.user_id, run_id, part.section, b"x" * 32, AT)
    with pytest.raises(site_profile.RefusedError):
        profile_gate.authorise_section(db, person_a.user_id, run_id, part.section, b"x" * 32, AT)
    digest = profile_sources.value_hash(part, rows)
    auth = profile_gate.authorise_section(db, person_a.user_id, run_id, part.section, digest, AT)
    auth_id, key = auth.id, part.section
    db.commit()
    monkeypatch.setattr(profile_gate, "gate_click", portal.save)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    run, part, _ = section(db, person_a.user_id, run_id)
    assert part.state == "saved", part.error_code
    assert portal.clicks == 1
    assert db.get(Authorisation, auth_id).used_at is not None
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    assert portal.clicks == 1
    assert run.state is SiteRunState.done


def test_autosave_requires_consent_and_fills_empty_only(db, factory, person_a):
    portal = Portal(autosave=True)
    run_id = make(db, person_a.user_id)
    env = services(factory, portal)
    asyncio.run(site_profile.fill(env, run_id))
    db.expire_all()
    run = db.get(SiteProfileRun, run_id)
    assert run.needs == "autosave_confirmation"
    assert portal.typed == []
    run.autosave_confirmed, run.state = True, SiteRunState.queued
    db.commit()
    asyncio.run(site_profile.fill(env, run_id))
    run, part, _ = section(db, person_a.user_id, run_id)
    assert run.autosave
    assert part.state == "saved", (run.error_code, part.error_code)
    assert portal.saved["Full name"]
    assert portal.saved["Phone"] == "+33 1 00 00 00 00"
    assert portal.saved["Email"] == "e2e-site@example.org"
    assert portal.clicks == 0
    assert any(e["kind"] == "save_decision" for e in part.events)


@pytest.mark.parametrize("fault", ["expired", "paused", "readback"])
def test_gate_expiry_pause_and_readback(db, factory, person_a, monkeypatch, fault):
    portal = Portal(changed=fault == "readback")
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    _run, part, rows = section(db, person_a.user_id, run_id)
    auth = profile_gate.authorise_section(
        db, person_a.user_id, run_id, part.section, profile_sources.value_hash(part, rows), AT
    )
    if fault == "expired":
        env.clock = lambda: AT + timedelta(hours=25)
    if fault == "paused":

        async def paused():
            return True, True

        env.profile_switches = paused
    key, auth_id = part.section, auth.id
    db.commit()
    monkeypatch.setattr(profile_gate, "gate_click", portal.save)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    _, part, _ = section(db, person_a.user_id, run_id)
    assert part.state == "ready"
    assert portal.clicks == (1 if fault == "readback" else 0)
