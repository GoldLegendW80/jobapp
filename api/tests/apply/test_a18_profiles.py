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


def test_concurrent_approve_creates_one_authorisation(db, factory, person_a):
    from concurrent.futures import ThreadPoolExecutor

    portal = Portal()
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(services(factory, portal), run_id))
    _run, part, rows = section(db, person_a.user_id, run_id)
    key, digest = part.section, profile_sources.value_hash(part, rows)
    db.commit()

    def approve():
        with factory() as session:
            try:
                profile_gate.authorise_section(session, person_a.user_id, run_id, key, digest, AT)
                session.commit()
                return "approved"
            except site_profile.RefusedError:
                session.rollback()
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve(), range(2)))
    assert results.count("approved") == 1
    assert results.count("refused") == 1


def test_replace_selects_only_that_field_and_user_edit_changes_hash(
    db, factory, person_a, monkeypatch
):
    portal = Portal()
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    _run, part, rows = section(db, person_a.user_id, run_id)
    phone = next(r for r in rows if r.label == "Phone")
    old_hash = profile_sources.value_hash(part, rows)
    site_profile.edit(
        db, person_a.user_id, run_id, part.section, phone.field_key, {"replace": True}, AT
    )
    db.flush()
    rows = site_profile.fields(db, _run, part.section)
    assert profile_sources.value_hash(part, rows) != old_hash
    assert [r.label for r in rows if r.replace] == ["Phone"]
    auth = profile_gate.authorise_section(
        db, person_a.user_id, run_id, part.section, profile_sources.value_hash(part, rows), AT
    )
    auth_id, key = auth.id, part.section
    db.commit()
    monkeypatch.setattr(profile_gate, "gate_click", portal.save)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    assert portal.saved["Phone"] != "+33 1 00 00 00 00"
    assert portal.saved["Email"] == "e2e-site@example.org"


def test_pause_after_audit_prevents_click(db, factory, person_a, monkeypatch):
    portal = Portal()
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(env, run_id))
    _run, part, rows = section(db, person_a.user_id, run_id)
    auth = profile_gate.authorise_section(
        db, person_a.user_id, run_id, part.section, profile_sources.value_hash(part, rows), AT
    )
    auth_id, key = auth.id, part.section
    db.commit()
    calls = 0

    async def switches():
        nonlocal calls
        calls += 1
        return True, calls >= 3

    env.profile_switches = switches
    monkeypatch.setattr(profile_gate, "gate_click", portal.save)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    assert portal.clicks == 0
    db.expire_all()
    assert db.get(Authorisation, auth_id).used_at is not None


def test_designation_outside_section_is_refused():
    from app.apply import profile_sites
    from app.apply.ats.base import parse_snapshot

    root = parse_snapshot(
        '- form "Experience":\n  - textbox "Job title" [ref=e2]\n'
        '  - button "Save" [ref=e3]\n- button "Save" [ref=e4]'
    )
    part = profile_sites.sections(root)[0]
    _target, code = profile_sites.save(part, "e4")
    assert code == "outside_section"


def test_profile_tools_cannot_click_save(db, factory, person_a):
    portal = Portal()
    result = asyncio.run(portal.click("e9", "Save"))
    assert result["error"] == "submit"
    assert portal.clicks == 0


def test_startup_recovery_keeps_consumed_save_uncertain(db, person_a):
    run_id = make(db, person_a.user_id)
    run = db.get(SiteProfileRun, run_id)
    run.state = SiteRunState.filling
    db.commit()
    assert site_profile.reset_interrupted(db, AT) == 1
    assert run.state is SiteRunState.failed
    assert run.error_code == "fill_interrupted"


def test_startup_recovery_never_replays_consumed_authorisation(db, factory, person_a):
    portal = Portal()
    run_id = make(db, person_a.user_id)
    asyncio.run(site_profile.fill(services(factory, portal), run_id))
    _run, part, rows = section(db, person_a.user_id, run_id)
    auth = profile_gate.authorise_section(
        db, person_a.user_id, run_id, part.section, profile_sources.value_hash(part, rows), AT
    )
    run, part, _rows = profile_gate.consume(
        db, person_a.user_id, run_id, part.section, auth.id, AT, True, False
    )
    db.commit()
    assert site_profile.reset_interrupted(db, AT) == 1
    assert run.state is SiteRunState.ready
    assert part.state == "ready"
    assert part.error_code == "save_unknown"
    assert auth.used_at is not None
    assert portal.clicks == 0


@pytest.mark.parametrize("kind", ["workday", "linkedin"])
def test_known_site_opens_and_reopens_stable_section_dialog(
    db, factory, person_a, monkeypatch, kind
):
    from app.apply.models import SiteKind
    from tests.apply.a18_support import DialogPortal

    portal = DialogPortal(kind)
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    run = db.get(SiteProfileRun, run_id)
    run.site_kind = SiteKind(kind)
    db.commit()
    monkeypatch.setattr(site_profile.linkedin_guard, "allowed", lambda *args: True)
    asyncio.run(site_profile.fill(env, run_id))
    run, part, rows = section(db, person_a.user_id, run_id)
    assert run.state is SiteRunState.ready, run.error_code
    assert part.section == "experience"
    assert [s.section for s in site_profile.sections(db, run)] == ["experience", "education"]
    assert portal.records["experience"]["Job title"] == ""
    assert portal.opened is None
    auth = profile_gate.authorise_section(
        db, person_a.user_id, run_id, part.section, profile_sources.value_hash(part, rows), AT
    )
    auth_id, key = auth.id, part.section
    db.commit()
    monkeypatch.setattr(profile_gate, "gate_click", portal.save)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    _run, part, _rows = section(db, person_a.user_id, run_id)
    assert part.state == "saved", part.error_code
    assert portal.records["experience"]["Job title"]
    assert portal.clicks == 1
    assert all(name in (*portal.names.values(), "Close") for name in portal.control_clicks)


def test_generic_profile_has_one_aggregate_model_budget(db, factory, person_a):
    from app.apply import form_agent
    from app.apply.models import SiteKind
    from tests.apply.a18_support import DialogPortal

    portal = DialogPortal("workday")
    env = services(factory, portal)
    run_id = make(db, person_a.user_id)
    run = db.get(SiteProfileRun, run_id)
    run.site_kind = SiteKind.workday
    db.commit()
    budgets = []

    async def agent(session):
        budgets.append((session.token_budget, session.turn_budget))
        return form_agent.Ran(input_tokens=23_000, turns=30)

    asyncio.run(site_profile.fill(env, run_id, agent))
    run = site_profile.lock(db, person_a.user_id, run_id)
    db.expire_all()
    parts = site_profile.sections(db, run)
    assert budgets == [(23_000, 30)]
    assert parts[1].state == "left"
    assert parts[1].error_code == "budget_exhausted"
