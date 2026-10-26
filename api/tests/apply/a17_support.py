"""Invented, committed privacy subjects. Request tests never replace ownership helpers."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.apply import service
from app.apply.models import (
    AnswerSource,
    ApplicationAnswer,
    ApplicationScreenshot,
    Authorisation,
    AuthorisationKind,
    Mode,
    ShotKind,
    SiteFieldSource,
    SiteKind,
    SiteProfileField,
    SiteProfileRun,
    SiteProfileSection,
    SiteProfileShot,
    SiteRunState,
    SiteShotKind,
    State,
)
from app.documents.models import DocumentVersion, FreezeReason
from app.mail.draft_models import MailDraft, MailSend
from tests.apply.conftest import AT, add_posting
from tests.apply.test_start import STUB

JPEG = b"\xff\xd8\xff" + bytes(range(64))


@dataclass(frozen=True)
class Subject:
    user: uuid.UUID
    posting_id: uuid.UUID
    application_id: uuid.UUID
    shot_id: uuid.UUID
    run_id: uuid.UUID
    profile_shot: uuid.UUID
    draft_id: uuid.UUID
    send_id: uuid.UUID
    document_id: uuid.UUID
    versions: tuple[uuid.UUID, ...]

    def ids(self):
        return {
            **{key: str(value) for key, value in vars(self).items()},
            "shot_id": str(self.shot_id),
            "section": "experience",
            "key": "name",
            "field_key": "letter/body",
            "number": "1",
        }


def subject(db, user: uuid.UUID) -> Subject:
    posting, versions = add_posting(db, user, STUB + "/ashby/example-robotics/privacy/application")
    application = service.start(
        db, user, posting, Mode.review, False, now=AT, stub_base=STUB
    ).application
    application.ats = "email"
    application.state, application.needs = State.needs_person, "signin"
    db.add(
        ApplicationAnswer(
            application_id=application.id,
            user_id=user,
            field_key="letter/body",
            label="Cover letter",
            kind="textarea",
            value="Invented application letter.",
            source=AnswerSource.person,
            document_letter=True,
            left_reason="person_required",
        )
    )
    shot = ApplicationScreenshot(
        application_id=application.id,
        user_id=user,
        kind=ShotKind.before,
        jpeg=JPEG,
        sha256=hashlib.sha256(JPEG).digest(),
        created_at=AT,
    )
    run = SiteProfileRun(
        user_id=user,
        host="portal.example.org",
        url="https://portal.example.org/profile",
        site_kind=SiteKind.generic,
        state=SiteRunState.needs_person,
        needs="signin",
        created_at=AT,
        updated_at=AT,
    )
    db.add_all([shot, run])
    db.flush()
    profile_shot = SiteProfileShot(
        run_id=run.id,
        user_id=user,
        section="experience",
        kind=SiteShotKind.before,
        jpeg=JPEG,
        sha256=hashlib.sha256(JPEG).digest(),
        created_at=AT,
    )
    db.add_all(
        [
            profile_shot,
            SiteProfileSection(
                run_id=run.id, user_id=user, section="experience", position=0, label="Experience"
            ),
            SiteProfileField(
                run_id=run.id,
                user_id=user,
                section="experience",
                field_key="name",
                label="Full name",
                kind="text",
                value="Example Applicant",
                source=SiteFieldSource.person,
            ),
        ]
    )
    for target, kind in (
        ({"application_id": application.id}, AuthorisationKind.person_approval),
        ({"site_run_id": run.id}, AuthorisationKind.profile_section),
    ):
        db.add(
            Authorisation(
                user_id=user,
                **target,
                kind=kind,
                answers_hash=b"a" * 32,
                created_at=AT,
                expires_at=AT + timedelta(days=1),
            )
        )
    draft = MailDraft(
        user_id=user,
        application_id=application.id,
        kind="application",
        to_addrs=["e2e-jobs@example.org"],
        cc_addrs=[],
        subject="Invented application",
        body="Invented application letter.",
        status="send_unknown",
        document_versions=application.document_versions,
        created_at=AT,
        updated_at=AT,
    )
    db.add(draft)
    db.flush()
    send = MailSend(
        user_id=user,
        draft_id=draft.id,
        posting_id=posting,
        result="unknown",
        authorisation_kind="person_approval",
        at=AT,
        updated_at=AT,
    )
    db.add(send)
    db.flush()
    application.mail_send_id = send.id
    version = db.get(DocumentVersion, versions[next(iter(versions))])
    version.frozen_at, version.frozen_reason = AT, FreezeReason.apply
    document = version.document_id
    db.commit()
    return Subject(
        user,
        posting,
        application.id,
        shot.id,
        run.id,
        profile_shot.id,
        draft.id,
        send.id,
        document,
        tuple(versions.values()),
    )
