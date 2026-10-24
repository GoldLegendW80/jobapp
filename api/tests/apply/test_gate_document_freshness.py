"""Documents approved after the submission decision cannot be sent under the old approval."""

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.apply.models import State
from app.documents.models import Kind, RenderState
from tests.apply import a11_support as a11
from tests.apply.a6_support import GateLeases, submit_ashby
from tests.apply.conftest import AT, Person
from tests.apply.test_356_render_wait import land, second_version
from tests.apply.test_a5_run import application
from tests.apply.test_a6_gate import approve, frozen, ready, run_submit, submit_services
from tests.apply.test_a6_recheck import at_the_dwell


@pytest.mark.parametrize("kind", [Kind.cv, Kind.letter])
@pytest.mark.parametrize("render", [RenderState.queued, RenderState.rendering, RenderState.idle])
@pytest.mark.parametrize("when", ["before_lease", "after_pacing", "during_dwell"])
def test_review_approval_rechecks_current_documents(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    forms_base_url: str,
    kind: Kind,
    render: RenderState,
    when: str,
) -> None:
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    form, leases = submit_ashby(), GateLeases()
    svc = submit_services(factory, form, forms_base_url, leases=leases)

    def changed() -> None:
        document_id = second_version(db, person_a, kind, render)
        if render is RenderState.idle:
            land(db, person_a, document_id)

    if when == "during_dwell":
        at_the_dwell(form, changed)
    else:
        if when == "after_pacing":
            previous = a11.clicked(db, person_a.user_id, AT)
            current = application(db, app_id)
            previous.ats, previous.form_url = current.ats, current.form_url
            a11.click(db, previous, AT)
            db.commit()
            assert run_submit(svc, app_id) == "paced"
            assert application(db, app_id).needs == "host_pace"
            svc.clock = lambda: AT + timedelta(seconds=180)
        changed()
    result = run_submit(svc, app_id)
    assert result == ("ready" if when == "during_dwell" else "documents_changed")
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "documents_changed")
    assert form.gate_clicks == []
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == [None, None]
    if when != "during_dwell":
        assert leases.acquired == []


def test_unchanged_documents_still_submit_once(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    forms_base_url: str,
) -> None:
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    assert run_submit(submit_services(factory, form, forms_base_url), app_id) == "submitted"
    assert len(form.gate_clicks) == 1


@pytest.mark.parametrize("code", ["person_input", "person_used"])
def test_a_definitely_refused_click_does_not_freeze_or_guess_submission(
    db: Session,
    factory: sessionmaker[Session],
    person_a: Person,
    forms_base_url: str,
    code: str,
) -> None:
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    form = submit_ashby()
    svc = submit_services(factory, form, forms_base_url)

    async def refused(*args):
        return {"ok": False, "error": code}

    svc.click = refused
    assert run_submit(svc, app_id) == "ready"
    found = application(db, app_id)
    assert (found.state, found.error_code) == (State.ready, "form_changed")
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == [None, None]
    assert form.gate_clicks == []
