"""A refused native Save spends its one approval without claiming a site write or replaying."""

import asyncio

import pytest

from app.apply import profile_gate, profile_sources, site_profile, states
from app.apply.models import Application, Authorisation, Mode, State
from tests.apply.a18_support import Portal, services
from tests.apply.conftest import AT
from tests.apply.test_a18_profiles import make, section


@pytest.mark.parametrize("code", ["person_input", "person_used"])
def test_profile_save_refused_by_takeover_records_no_write_and_never_replays(
    db,
    factory,
    person_a,
    monkeypatch,
    code,
):
    portal = Portal()
    run_id = make(db, person_a.user_id)
    env = services(factory, portal)
    asyncio.run(site_profile.fill(env, run_id))
    _, part, rows = section(db, person_a.user_id, run_id)
    auth = profile_gate.authorise_section(
        db,
        person_a.user_id,
        run_id,
        part.section,
        profile_sources.value_hash(part, rows),
        AT,
    )
    auth_id, key = auth.id, part.section
    db.commit()
    attempts = []

    async def refused(*args):
        attempts.append(True)
        return {"ok": False, "error": code}

    monkeypatch.setattr(profile_gate, "gate_click", refused)
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    _, part, _ = section(db, person_a.user_id, run_id)
    assert part.state == "ready"
    assert part.error_code == "save_refused"
    assert portal.clicks == 0
    assert not portal.saved["Full name"]
    assert db.get(Authorisation, auth_id).used_at is not None
    db.commit()
    asyncio.run(profile_gate.save_section(env, run_id, key, auth_id))
    assert attempts == [True]
    assert portal.clicks == 0


def test_only_an_explicit_browser_refusal_can_restore_a_submitting_application():
    application = Application(mode=Mode.review, state=State.submitting)
    with pytest.raises(states.TransitionError):
        states.move(application, State.ready, AT)
    states.move(application, State.ready, AT, click_refused=True)
    assert application.state is State.ready
    assert application.submitted_at is None
    with pytest.raises(states.TransitionError):
        states.move(application, State.submitted, AT, click_refused=True)
