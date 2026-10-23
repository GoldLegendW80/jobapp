"""Fresh-connection deletion proof for 0025 profiles, 0026 mail and 0027 answer provenance."""

import pytest
import sqlalchemy as sa

from app.apply import privacy
from app.apply.models import (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    ApplicationScreenshot,
    Authorisation,
    SiteProfileField,
    SiteProfileRun,
    SiteProfileSection,
    SiteProfileShot,
)
from app.documents.models import Document, DocumentVersion
from app.identity.models import User
from app.jobs.models import Job
from app.mail.draft_models import MailDraft, MailSend
from tests.apply.a17_support import subject
from tests.apply.conftest import AT
from tests.apply.test_start import a, b, engine, factory, migrated_url

__all__ = ["a", "b", "engine", "factory", "migrated_url"]

APPLICATION_ROWS = (
    Application,
    ApplicationAnswer,
    ApplicationEvent,
    ApplicationScreenshot,
    MailDraft,
)
PROFILE_ROWS = (SiteProfileRun, SiteProfileSection, SiteProfileField, SiteProfileShot)
ALL_ROWS = (*APPLICATION_ROWS, *PROFILE_ROWS, Authorisation, MailSend, Document, DocumentVersion)


def counts(db, user):
    return {
        model: db.scalar(sa.select(sa.func.count()).select_from(model).where(model.user_id == user))
        for model in ALL_ROWS
    }


@pytest.mark.parametrize("delete_account", [False, True], ids=["posting", "account"])
def test_delete_removes_owned_content_and_preserves_the_other_person(factory, a, b, delete_account):
    with factory() as db:
        owner, other = subject(db, a), subject(db, b)
    with factory() as db:
        before_a, before_b = counts(db, a), counts(db, b)
        assert all(before_a.values()), before_a
        assert all(before_b.values()), before_b
        answer = db.get(ApplicationAnswer, (owner.application_id, "letter/body"))
        assert answer.document_letter is True
        assert answer.left_reason == "person_required"
        frozen = db.get(DocumentVersion, owner.versions[0])
        assert frozen.frozen_at == AT
        assert frozen.pdf is not None
        # Temporary screenshot pruning never removes retained documents or frozen versions.
        privacy.prune(db, AT, keep_days=30)
        db.commit()
    with factory() as db:
        assert counts(db, a) == before_a
        model, ref = (User, owner.user) if delete_account else (Job, owner.posting_id)
        # SQL DELETE exercises database CASCADE, with no ORM relationship deletion to mask it.
        db.execute(sa.delete(model).where(model.id == ref))
        db.commit()
    with factory() as db:
        assert counts(db, b) == before_b
        after = counts(db, a)
        if delete_account:
            assert set(after.values()) == {0}, after
            assert db.get(User, a) is None
        else:
            for model in (*APPLICATION_ROWS, Document, DocumentVersion):
                assert after[model] == 0, model.__tablename__
            for model in PROFILE_ROWS:
                assert after[model] == before_a[model], model.__tablename__
            remaining = db.scalars(sa.select(Authorisation).where(Authorisation.user_id == a)).all()
            assert len(remaining) == 1
            assert remaining[0].site_run_id == owner.run_id
            assert remaining[0].application_id is None
            # SPEC-mail retains the minimal send audit365 days after content deletion.
            audit = db.get(MailSend, owner.send_id)
            assert audit.user_id == owner.user
            assert audit.draft_id == owner.draft_id
            assert audit.posting_id == owner.posting_id
            assert after[MailSend] == before_a[MailSend]
            assert db.get(User, a) is not None
        # Fresh reads also prove B's exact subjects and frozen PDF still exist.
        assert db.get(Application, other.application_id).user_id == b
        assert db.get(MailDraft, other.draft_id).user_id == b
        assert db.get(DocumentVersion, other.versions[0]).frozen_at == AT
