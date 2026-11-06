"""What the job titles agent reads of the person's documents (app/profile/titles_input.py,
`documents`): the text the app already keeps, never a file parsed again, under the consent that
sends it to Anthropic, in order and within its caps. Real Postgres; no model."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.documents import consent as documents_consent
from app.documents import store
from app.documents.models import BaseSource
from app.ingestion import estimate_router
from app.ingestion.models import IngestionConsent, IngestionDocument, TextSource, TextStatus
from app.profile import titles, titles_input
from tests.identity.api_helpers import make_user

T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
EMAIL = "e2e-titles-reading@example.org"
OTHER = "e2e-titles-reading-other@example.org"


def person(factory: sessionmaker[Session], email: str = EMAIL) -> uuid.UUID:
    return make_user(factory, email=email, name=email.split("@")[0]).id


def consent(factory: sessionmaker[Session], user_id: uuid.UUID, *, uploads: bool, cvs: bool):
    with factory() as db:
        if uploads:
            version = estimate_router.CONSENT_VERSION
            db.add(IngestionConsent(user_id=user_id, text_version=version))
        if cvs:
            store.accept_consent(db, user_id, documents_consent.VERSION, T0)
        db.commit()


def upload(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    text: str | None,
    *,
    name: str = "notes.txt",
    minutes: int = 0,
    status: TextStatus = TextStatus.done,
    secret: bool = False,
    confirmed: bool = False,
) -> uuid.UUID:
    data = (text or name).encode("utf-8")
    with factory() as db:
        document = IngestionDocument(
            user_id=user_id,
            name=name,
            mime="text/plain",
            size=len(data),
            sha256=hashlib.sha256(data + user_id.bytes + name.encode()).digest(),
            text=text,
            text_source=TextSource.pasted,
            text_status=status,
            parser_version=1,
            secret_warning=secret,
            secret_confirmed_at=T0 if confirmed else None,
            created_at=T0 + timedelta(minutes=minutes),
        )
        db.add(document)
        db.commit()
        return document.id


def base_cv(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    name: str,
    headline: str,
    *,
    source: uuid.UUID | None = None,
    minutes: int = 0,
) -> uuid.UUID:
    content = {
        "target": f"{headline} roles",
        "headline": headline,
        "summary": "Builds data pipelines.",
        "highlights": ["Cut the cost of the warehouse by a third."],
        "competencies": ["SQL"],
    }
    with factory() as db:
        made = store.create_base(
            db,
            user_id,
            name,
            BaseSource.uploaded if source else BaseSource.built,
            at=T0 + timedelta(minutes=minutes),
            content=content,
            source_document_id=source,
        )
        db.commit()
        return made.id


def read(factory: sessionmaker[Session], user_id: uuid.UUID) -> list[tuple[str, str, str]]:
    with factory() as db:
        return [(one.source, one.name, one.text) for one in titles_input.documents(db, user_id)]


def test_uploads_are_read_newest_first_only_under_the_consent_of_fill_my_profile(
    factory: sessionmaker[Session],
) -> None:
    user_id = person(factory)
    upload(factory, user_id, "Older notes: SQL and dbt.", name="old.txt", minutes=1)
    upload(factory, user_id, "Newer notes: Python.", name="new.txt", minutes=2)
    assert read(factory, user_id) == []  # no consent yet: nothing goes
    consent(factory, user_id, uploads=False, cvs=True)
    assert read(factory, user_id) == []  # the documents module's consent covers base CVs only
    consent(factory, user_id, uploads=True, cvs=False)
    assert read(factory, user_id) == [
        ("upload", "new.txt", "Newer notes: Python."),
        ("upload", "old.txt", "Older notes: SQL and dbt."),
    ]


def test_an_upload_not_read_or_held_by_a_secret_warning_is_left_out(
    factory: sessionmaker[Session],
) -> None:
    user_id = person(factory)
    consent(factory, user_id, uploads=True, cvs=False)
    upload(factory, user_id, None, name="pending.pdf", status=TextStatus.pending)
    upload(factory, user_id, None, name="failed.pdf", status=TextStatus.failed)
    upload(factory, user_id, "token sk-xxx", name="secret.txt", secret=True)
    upload(factory, user_id, "Confirmed notes.", name="confirmed.txt", secret=True, confirmed=True)
    other = person(factory, OTHER)
    consent(factory, other, uploads=True, cvs=False)
    upload(factory, other, "Someone else's CV.", name="other.txt")
    assert read(factory, user_id) == [("upload", "confirmed.txt", "Confirmed notes.")]


def test_the_default_base_cv_comes_first_and_stands_for_the_upload_it_came_from(
    factory: sessionmaker[Session],
) -> None:
    user_id = person(factory)
    consent(factory, user_id, uploads=True, cvs=True)
    cv_file = upload(factory, user_id, "Raw CV text.", name="cv.pdf", minutes=1)
    upload(factory, user_id, "A cover letter.", name="letter.txt", minutes=2)
    base_cv(factory, user_id, "Built", "Analyst", minutes=3)  # the default: the first one
    base_cv(factory, user_id, "From my CV", "Data engineer", source=cv_file, minutes=4)
    found = read(factory, user_id)
    # An upload becomes the default over one built from the profile only; this one was built,
    # so it stays the default, and the converted one comes after the uploads.
    assert [(source, name) for source, name, _ in found] == [
        ("cv", "Built"),
        ("upload", "letter.txt"),
        ("cv", "From my CV"),
    ]
    assert found[0][2] == (
        "Analyst roles\nAnalyst\nBuilds data pipelines.\n"
        "Cut the cost of the warehouse by a third.\nSQL"
    )


def test_each_document_and_all_of_them_stay_within_their_caps(
    factory: sessionmaker[Session],
) -> None:
    user_id = person(factory)
    consent(factory, user_id, uploads=True, cvs=False)
    for n in range(titles_input.DOCS_COUNT_MAX + 2):
        upload(factory, user_id, f"{n}" * (titles_input.DOC_MAX + 500), name=f"{n}.txt", minutes=n)
    found = read(factory, user_id)
    sizes = [len(text) for _, _, text in found]
    assert all(size <= titles_input.DOC_MAX for size in sizes)
    assert sum(sizes) <= titles_input.DOCS_MAX
    assert len(found) <= titles_input.DOCS_COUNT_MAX
    # The newest first: the last uploads fill the budget.
    newest = titles_input.DOCS_COUNT_MAX + 1
    assert found[0][1] == f"{newest}.txt"
    assert sum(sizes) == titles_input.DOCS_MAX


def test_a_document_s_spaces_and_control_characters_are_made_short(
    factory: sessionmaker[Session],
) -> None:
    user_id = person(factory)
    consent(factory, user_id, uploads=True, cvs=False)
    upload(factory, user_id, "Data\t\t engineer\x0b\n\n\n\n  SQL   dbt  \n", name="cv.txt")
    assert read(factory, user_id) == [("upload", "cv.txt", "Data engineer\n\nSQL dbt")]


@pytest.mark.parametrize("cvs", [True, False])
def test_the_run_reads_the_documents_and_a_document_alone_is_enough(
    factory: sessionmaker[Session], cvs: bool
) -> None:
    user_id = person(factory)
    consent(factory, user_id, uploads=True, cvs=cvs)
    upload(factory, user_id, "Data engineer at Example Robotics, SQL and dbt.", name="cv.pdf")
    with factory() as db:
        request = titles._request(db, user_id, None)
        assert request is not None
        assert titles.readable(request)
        assert "Example Robotics, SQL and dbt." in titles.prompt_for(request)
