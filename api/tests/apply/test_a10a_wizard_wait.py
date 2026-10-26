"""Persisted known-wizard person waits retain the complete approved replay."""

import pytest

from app.apply.models import State
from tests.apply.a15_support import WizardForm
from tests.apply.a16_support import LinkedInForm
from tests.apply.test_a5_run import application, fill, services
from tests.apply.test_a6_gate import approve, run_submit, submit_services
from tests.apply.test_a10a_failures import PersonMoves
from tests.apply.test_a15_owned import answers_of, configured_fixture_origin, start_wizard
from tests.apply.test_a16_linkedin import start as start_linkedin
from tests.apply.test_b10_linkedin_guard import switch

__all__ = ["configured_fixture_origin"]


@pytest.mark.parametrize("kind", ["workday", "linkedin"])
@pytest.mark.parametrize("reason", ["captcha", "email_code"])
def test_later_step_wait_retains_all_answers_and_replays_one_approved_submit(
    db, factory, person_a, forms_base_url, kind, reason
):
    if kind == "linkedin":
        switch(factory, person_a)
        app_id = start_linkedin(db, person_a)
        form = LinkedInForm()
    else:
        app_id = start_wizard(db, person_a, forms_base_url)
        form = WizardForm()
    click = form.click
    challenged = False

    async def challenge(ref, element=""):
        nonlocal challenged
        result = await click(ref, element)
        if form.step == 1 and not challenged:
            challenged = True
            form.extras = [
                ("checkbox", "I'm not a robot")
                if reason == "captcha"
                else ("textbox", "Verification code")
            ]
        return result

    form.click = challenge
    svc = services(factory, form, stub_base=forms_base_url, needs_person_wait_s=1800)
    seen = []

    def solve():
        app = application(db, app_id)
        seen.append((app.state, app.needs))
        form.extras = []

    svc.sleep = PersonMoves(svc, {1: solve}, step_s=1200)
    if reason == "email_code":
        # Main's rule (base.secret): a code box is a sign in wall on every adapter but
        # Greenhouse, so the fill stops for the person's sign in instead of waiting on the page.
        assert fill(svc, app_id) == "needs_person"
        assert application(db, app_id).needs == "signin"
        assert seen == []
        assert not form.gate_clicks
        assert not form.submit_clicks
        return
    assert fill(svc, app_id) == "ready"
    assert seen == [(State.needs_person, reason)]
    rows = answers_of(db, app_id)
    assert any(r.label == "First name" and r.value for r in rows)
    assert any(r.field_key.startswith("s2/") for r in rows)
    assert [r.value for r in rows if r.field_key.startswith("designated:step:")] == (
        ["Next", "Next"] if kind == "workday" else ["Next", "Review"]
    )
    assert not form.gate_clicks
    assert not form.submit_clicks
    assert approve(factory, person_a, app_id).passed
    fresh = LinkedInForm() if kind == "linkedin" else WizardForm()
    submit = submit_services(factory, fresh, forms_base_url)
    assert run_submit(submit, app_id) == "submitted"
    assert len(fresh.gate_clicks) == 1
    assert run_submit(submit, app_id) == "not_approved"
    assert len(fresh.gate_clicks) == 1
