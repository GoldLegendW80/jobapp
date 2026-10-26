"""Gate recovery keeps its audit while requiring a new approval for another external act."""

import uuid

import pytest
import sqlalchemy as sa

from app.apply import gate
from app.apply.models import Application, Authorisation, State
from app.mail.draft_models import MailDraft, MailSend
from tests.apply.conftest import AT
from tests.apply.test_a5_run import application, events
from tests.apply.test_a6_gate import approve, frozen, ready
from tests.apply.test_a9_manual import start_manual
from tests.apply.test_a14_email import prepare


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


@pytest.mark.parametrize("audit_present", [True, False])
@pytest.mark.parametrize("draft_present", [True, False])
def test_email_unknown_retry_resolves_surviving_audit_and_reopens_only_its_draft(
    db, person_a, audit_present, draft_present
):
    found = prepare(db, person_a, text="Send your CV to e2e-recruiting@example.org")
    app_id = found.id
    draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == app_id))
    assert draft is not None
    send_id = uuid.uuid4()
    audit = MailSend(
        id=send_id,
        user_id=person_a.user_id,
        draft_id=draft.id,
        posting_id=person_a.posting_id,
        result="unknown",
        authorisation_kind="person_approval",
        at=AT,
    )
    if audit_present:
        db.add(audit)
    if draft_present:
        draft.status = "send_unknown"
    else:
        db.delete(draft)
    found.state, found.mail_send_id, found.error_code = State.submit_unknown, send_id, "unknown"
    found.submitted_at, found.needs = AT, None
    assert gate.freeze_sent(db, found, AT) == 2
    db.commit()
    recovered = gate.retry(db, person_a.user_id, app_id, AT)
    db.commit()
    assert (recovered.state, recovered.mail_send_id, recovered.error_code) == (
        State.ready,
        None,
        None,
    )
    assert db.get(Application, app_id).state is State.ready
    persisted_audit = db.get(MailSend, send_id)
    if audit_present:
        assert (persisted_audit.result, persisted_audit.resolved_by) == ("unknown", "user_retry")
    else:
        assert persisted_audit is None
    persisted_draft = db.scalar(sa.select(MailDraft).where(MailDraft.application_id == app_id))
    if draft_present:
        assert persisted_draft.status == "draft"
    else:
        assert persisted_draft is None
    assert [event.data for event in events(db, app_id) if event.kind == "retry"] == [
        {"previous": "submit_unknown"}
    ]
    assert frozen(db, person_a.cv_version_id, person_a.letter_version_id) == ["apply", "apply"]
