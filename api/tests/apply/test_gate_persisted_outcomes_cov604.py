"""Gate recovery keeps its audit while requiring a new approval for another external act."""

import pytest
import sqlalchemy as sa

from app.apply import gate
from app.apply.models import Authorisation, State
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, events
from tests.apply.test_a6_gate import approve, frozen, ready
from tests.apply.test_a9_manual import start_manual


def test_manual_application_cannot_mint_a_form_submit_approval(db, person_a):
    app_id = start_manual(db, person_a)
    found = application(db, app_id)
    with pytest.raises(gate.RefusedError, match="manual"):
        gate.authorise_approval(db, person_a.user_id, app_id, gate.current_hash(db, found), AT, 60)
    assert found.state is State.queued
    assert db.scalar(sa.select(sa.func.count()).select_from(Authorisation)) == 0
    assert [event.kind for event in events(db, app_id)] == ["created"]


def test_gate_does_not_mint_another_approval_for_an_already_approved_form(
    db, factory, person_a, forms_base_url
):
    app_id = ready(db, factory, person_a, forms_base_url)
    assert approve(factory, person_a, app_id).passed
    found = application(db, app_id)
    with pytest.raises(gate.RefusedError, match="not_ready"):
        gate.authorise_approval(db, person_a.user_id, app_id, gate.current_hash(db, found), AT, 60)
    assert found.state is State.approved
    assert db.scalar(sa.select(sa.func.count()).select_from(Authorisation)) == 1
    assert frozen(db, person_a.cv_version_id) == [None]
