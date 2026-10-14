"""D5: the storage service against a real Postgres. Base CVs and their default, the documents
of a posting, the draft and its revision, versions and their freeze, generations, the chat,
the settings, isolation between two users, what a delete takes and what it keeps, and the
races, played with two sessions in a fixed order."""

from __future__ import annotations

import hashlib
import itertools
import logging
import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.documents import content as c
from app.documents import store
from app.documents.corrections import Correction
from app.documents.models import (
    Author,
    BaseSource,
    Channel,
    Document,
    DocumentGeneration,
    DocumentUserTemplate,
    FitResult,
    FreezeReason,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    Language,
    Origin,
    Role,
)
from app.documents.settings import DocumentSettings
from app.mail.factcheck_rules import Sources
from tests.documents.canary import find, marker
from tests.documents.conftest import load
from tests.documents.store_support import (
    AT,
    ENGLISH,
    FRENCH,
    PDF,
    USER_A,
    USER_B,
    add_job_file,
    add_posting,
    add_upload,
    add_user,
    db,
    engine,
    factory,
    migrated_url,
    rows_of,
    short_locks,
)

__all__ = ["db", "engine", "factory", "migrated_url", "short_locks"]

CV = load("written/cv_typical.json")
LETTER = load("written/letter_typical.json")


def _cv(headline: str) -> dict[str, Any]:
    return {**CV, "headline": headline}


_TICKS = itertools.count(1)


def _base(db: Session, user: uuid.UUID, name: str = "Main", **more: Any) -> Document:
    """A base CV made a second after the one before, so "the oldest" is well defined."""
    at = AT + timedelta(seconds=next(_TICKS))
    return store.create_base(
        db, user, name, more.pop("source", BaseSource.built), at=at, limit=10, **more
    )


def _posting_cv(db: Session, user: uuid.UUID, text: str = ENGLISH) -> Document:
    return store.get_or_create(db, user, add_posting(db, user, text), Kind.cv, at=AT)


def _version(db: Session, document: Document, headline: str = "One") -> int:
    store.save_draft(
        db,
        document.user_id,
        document.id,
        _cv(headline),
        document.draft_revision,
        Author.user,
        at=AT,
    )
    made, _ = store.snapshot(db, document.user_id, document.id, Origin.edited, at=AT)
    return made.number


def _frozen(db: Session) -> tuple[uuid.UUID, Document, int]:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    number = _version(db, document)
    store.attach_pdf(
        db, user, document.id, number, PDF, fit=FitResult.one_page, fit_step=0, pages=1
    )
    found = store.version(db, user, document.id, number)
    assert found is not None
    store.freeze(db, user, found.id, FreezeReason.marked_sent, AT)
    return user, document, number


# --- base CVs -----------------------------------------------------------------------------------


def test_the_first_base_cv_is_the_default_and_an_upload_replaces_one_built_from_the_profile(
    db: Session,
) -> None:
    user = add_user(db, USER_A)
    profile = _base(db, user, "From my profile", source=BaseSource.profile)
    built = _base(db, user, "Built", source=BaseSource.built)
    assert profile.is_default
    assert not built.is_default
    upload = _base(db, user, "Uploaded", source=BaseSource.uploaded)
    assert store.default_base(db, user) == upload
    later = _base(db, user, "Second upload", source=BaseSource.uploaded)
    assert not later.is_default  # the default is an upload now: it stays
    assert [d.name for d in store.base_cvs(db, user)] == [
        "From my profile",
        "Built",
        "Uploaded",
        "Second upload",
    ]


def test_make_default_moves_the_mark_and_the_index_refuses_a_second_default(db: Session) -> None:
    user = add_user(db, USER_A)
    first, second = _base(db, user, "First"), _base(db, user, "Second")
    store.set_default(db, user, second.id, AT)
    db.refresh(first)
    assert not first.is_default
    assert store.default_base(db, user) == second
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(sa.update(Document).where(Document.id == first.id).values(is_default=True))
    assert store.default_base(db, user) == second


def test_deleting_the_default_promotes_the_oldest_other_and_the_last_may_go(db: Session) -> None:
    user = add_user(db, USER_A)
    first, second, third = (_base(db, user, name) for name in ("First", "Second", "Third"))
    assert first.is_default
    promoted = store.delete_base(db, user, first.id, AT)
    assert promoted == second
    assert second.is_default
    assert store.delete_base(db, user, third.id, AT) == second  # not the default: no change
    assert store.delete_base(db, user, second.id, AT) is None
    assert store.base_cvs(db, user) == []
    assert store.default_base(db, user) is None


def test_an_eleventh_base_cv_is_refused(db: Session) -> None:
    user = add_user(db, USER_A)
    for n in range(10):
        _base(db, user, f"CV {n}")
    with pytest.raises(store.BaseLimitError):
        _base(db, user, "One too many")
    assert len(store.base_cvs(db, user)) == 10


def test_names_are_checked_and_renamed(db: Session) -> None:
    user = add_user(db, USER_A)
    for bad in ("", "   ", "x" * 101):
        with pytest.raises(store.NameInvalidError):
            _base(db, user, bad)
    document = _base(db, user, "  Main   CV ")
    assert document.name == "Main CV"
    assert store.rename_base(db, user, document.id, "Short", AT).name == "Short"


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"scope": "base", "name": "x", "base_source": "built", "posting": True}, "no_posting"),
        ({"scope": "base", "name": "x", "base_source": "built", "kind": "letter"}, "is_a_cv"),
        ({"scope": "base", "base_source": "built"}, "has_a_name"),
        ({"scope": "base", "name": "x"}, "has_source"),
        ({"scope": "posting", "posting": True, "is_default": True}, "not_a_base"),
    ],
)
def test_the_checks_refuse_a_base_cv_that_is_not_one(
    db: Session, values: dict[str, Any], constraint: str
) -> None:
    user = add_user(db, USER_A)
    posting = add_posting(db, user) if values.pop("posting", False) else None
    row = {
        "user_id": user,
        "posting_id": posting,
        "kind": "cv",
        "language": "en",
        "template_slug": "classic",
        "draft": {},
        "draft_schema": 1,
        "draft_written_by": "user",
        "draft_updated_at": AT,
        **values,
    }
    with pytest.raises(IntegrityError, match=constraint):
        db.execute(sa.insert(Document).values(row))


def test_deleting_a_base_cv_leaves_the_posting_cvs_written_from_it(db: Session) -> None:
    user = add_user(db, USER_A)
    base = _base(db, user, template_slug="sidebar")
    document = _posting_cv(db, user)
    assert document.base_document_id == base.id
    assert document.template_slug == "sidebar"
    store.delete_base(db, user, base.id, AT)
    db.expire_all()
    kept = store.get(db, user, document.id)
    assert kept is not None
    assert kept.base_document_id is None
    assert kept.template_slug == "sidebar"


def test_the_uploaded_file_deleted_leaves_the_base_cv(db: Session) -> None:
    user = add_user(db, USER_A)
    upload = add_upload(db, user)
    base = _base(db, user, source=BaseSource.uploaded, source_document_id=upload)
    _version(db, base)
    db.execute(sa.text("DELETE FROM ingestion_documents WHERE id = :i"), {"i": upload})
    db.expire_all()
    kept = store.get(db, user, base.id)
    assert kept is not None
    assert kept.source_document_id is None
    assert len(store.versions(db, user, base.id)) == 1


# --- the documents of a posting -----------------------------------------------------------------


def test_get_or_create_takes_the_language_of_the_posting_and_the_template_it_inherits(
    db: Session,
) -> None:
    user = add_user(db, USER_A)
    _base(db, user, "Default", template_slug="banner")
    other = _base(db, user, "Other", template_slug="compact")
    english = _posting_cv(db, user, ENGLISH)
    assert (english.language, english.template_slug) == (Language.en, "banner")
    posting = add_posting(db, user, FRENCH)
    cv = store.get_or_create(db, user, posting, Kind.cv, at=AT, base_document_id=other.id)
    assert (cv.language, cv.template_slug, cv.base_document_id) == (
        Language.fr,
        "compact",
        other.id,
    )
    letter = store.get_or_create(db, user, posting, Kind.letter, at=AT)
    assert (letter.template_slug, letter.base_document_id) == ("compact", None)
    assert store.get_or_create(db, user, posting, Kind.cv, at=AT) == cv
    lone = add_posting(db, user, "")
    assert store.get_or_create(db, user, lone, Kind.letter, at=AT).template_slug == "classic"


def test_without_a_base_cv_a_posting_cv_is_classic(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    assert (document.template_slug, document.base_document_id) == ("classic", None)
    assert c.parse("cv", document.draft) == c.CvContent()


@pytest.mark.parametrize(
    ("text", "language"),
    [
        (ENGLISH, Language.en),
        (FRENCH, Language.fr),
        ("", Language.en),
        ("Nous cherchons une développeuse pour notre équipe à Lyon.", Language.fr),
        ("We are looking for a developer to join the team in Lyon.", Language.en),
    ],
)
def test_the_language_is_read_from_the_posting_text(text: str, language: Language) -> None:
    assert store.language_of(text) == language


def test_two_get_or_create_at_once_make_one_row(
    factory: sessionmaker[Session], short_locks: None
) -> None:
    with factory() as setup:
        user = add_user(setup, USER_A)
        posting = add_posting(setup, user)
        setup.commit()
    with factory() as first, factory() as second:
        made = store.get_or_create(first, user, posting, Kind.cv, at=AT)
        with pytest.raises(store.DocumentBusyError):
            store.get_or_create(second, user, posting, Kind.cv, at=AT)
        second.rollback()
        first.commit()
        assert store.get_or_create(second, user, posting, Kind.cv, at=AT).id == made.id
        count = second.execute(
            sa.select(sa.func.count()).select_from(Document).where(Document.posting_id == posting)
        ).scalar_one()
        assert count == 1


def test_the_composite_key_refuses_a_document_of_a_on_a_posting_of_b(db: Session) -> None:
    a, b = add_user(db, USER_A), add_user(db, USER_B)
    posting_of_b = add_posting(db, b)
    with pytest.raises(store.NotFoundError):
        store.get_or_create(db, a, posting_of_b, Kind.cv, at=AT)
    with pytest.raises(IntegrityError, match="fk_documents_posting_id_jobs"):
        db.execute(
            sa.insert(Document).values(
                user_id=a,
                scope="posting",
                posting_id=posting_of_b,
                kind="cv",
                language="en",
                template_slug="classic",
                draft={},
                draft_schema=1,
                draft_written_by="user",
                draft_updated_at=AT,
            )
        )


# --- the draft ----------------------------------------------------------------------------------


def test_a_stale_save_gets_the_current_draft(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    saved = store.save_draft(db, user, document.id, _cv("Mine"), 1, Author.user, at=AT)
    assert saved.draft_revision == 2
    assert saved.draft_written_by == Author.user
    with pytest.raises(store.StaleDraftError) as stale:
        store.save_draft(db, user, document.id, _cv("Theirs"), 1, Author.writer, at=AT)
    assert stale.value.revision == 2
    assert stale.value.draft["headline"] == "Mine"


def test_two_saves_on_one_revision_one_wins(
    factory: sessionmaker[Session], short_locks: None
) -> None:
    with factory() as setup:
        user = add_user(setup, USER_A)
        document = _posting_cv(setup, user)
        setup.commit()
    with factory() as first, factory() as second:
        store.save_draft(first, user, document.id, _cv("First"), 1, Author.user, at=AT)
        with pytest.raises(store.DocumentBusyError):
            store.save_draft(second, user, document.id, _cv("Second"), 1, Author.user, at=AT)
        second.rollback()
        first.commit()
        with pytest.raises(store.StaleDraftError) as stale:
            store.save_draft(second, user, document.id, _cv("Second"), 1, Author.user, at=AT)
        assert stale.value.draft["headline"] == "First"
        assert stale.value.revision == 2


def test_a_hand_edit_racing_a_generation_makes_the_generation_stale(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    generation = store.create_generation(
        db,
        user,
        document.id,
        GenerationMode.posting,
        GenerationTrigger.click,
        at=AT,
        estimate={},
        token_ceiling=1000,
    )
    store.save_draft(db, user, document.id, _cv("By hand"), 1, Author.user, at=AT)
    with pytest.raises(store.StaleDraftError):
        store.save_draft(
            db,
            user,
            document.id,
            _cv("Written"),
            generation.base_draft_revision or 0,
            Author.writer,
            at=AT,
        )
    assert store.get(db, user, document.id).draft["headline"] == "By hand"  # type: ignore[union-attr]


def test_the_draft_is_validated_and_its_notes_recomputed(
    db: Session, alex_sources: Sources
) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    with pytest.raises(c.ContentInvalidError):
        store.save_draft(db, user, document.id, {"headline": 3}, 1, Author.user, at=AT)
    with pytest.raises(c.ContentInvalidError) as big:
        store.save_draft(db, user, document.id, _cv("x" * 100_000), 1, Author.user, at=AT)
    assert big.value.errors[0].code == "content_too_large"
    with pytest.raises(c.ContentInvalidError):  # the fixed block is a base CV's only
        store.save_draft(db, user, document.id, {**CV, "fixed": {}}, 1, Author.user, at=AT)
    saved = store.save_draft(db, user, document.id, CV, 1, Author.user, at=AT, sources=alex_sources)
    assert saved.checks_revision == 2
    assert isinstance(saved.checks, list)
    assert all({"field", "code", "level"} <= set(issue) for issue in saved.checks)
    plain = store.save_draft(db, user, document.id, CV, 2, Author.user, at=AT)
    assert plain.checks is None
    assert plain.checks_revision is None


def test_a_letter_and_a_base_cv_take_their_own_content(db: Session) -> None:
    user = add_user(db, USER_A)
    posting = add_posting(db, user)
    letter = store.get_or_create(db, user, posting, Kind.letter, at=AT)
    assert store.save_draft(db, user, letter.id, LETTER, 1, Author.user, at=AT).draft_revision == 2
    with pytest.raises(c.ContentInvalidError):
        store.save_draft(db, user, letter.id, CV, 2, Author.user, at=AT)
    base = _base(db, user)
    fixed = {**CV, "fixed": {"employers": [], "schools": [], "languages": []}}
    assert store.save_draft(db, user, base.id, fixed, 1, Author.user, at=AT).draft["fixed"]


def test_language_and_restore(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    first = _version(db, document, "First")
    _version(db, document, "Second")
    assert store.set_language(db, user, document.id, Language.fr, AT).language == Language.fr
    restored = store.restore(db, user, document.id, first, at=AT)
    assert restored.draft["headline"] == "First"
    assert restored.draft_written_by == Author.restore
    with pytest.raises(store.NotFoundError):
        store.restore(db, user, document.id, 99, at=AT)


# --- versions -----------------------------------------------------------------------------------


def test_a_snapshot_of_an_unchanged_draft_reuses_the_version_another_template_makes_one(
    db: Session,
) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    one = _version(db, document)
    again, made = store.snapshot(db, user, document.id, Origin.edited, at=AT)
    assert (again.number, made) == (one, False)
    switched = store.set_template(db, user, document.id, "sidebar", at=AT)
    assert (switched.number, switched.origin, switched.template_slug) == (
        2,
        Origin.template,
        "sidebar",
    )
    newer, made = store.snapshot(db, user, document.id, Origin.edited, at=AT, template_version=2)
    assert (newer.number, made) == (3, True)
    generation = store.create_generation(
        db,
        user,
        document.id,
        GenerationMode.posting,
        GenerationTrigger.auto,
        at=AT,
        estimate={},
        token_ceiling=1,
    )
    generated, made = store.snapshot(
        db,
        user,
        document.id,
        Origin.generated,
        at=AT,
        generation_id=generation.id,
        template_version=2,
    )
    assert (generated.number, made) == (4, True)


def test_a_snapshot_records_its_corrections(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    fix = Correction("summary", "a", "b", "number", "by_code", "why")
    made, _ = store.snapshot(db, user, document.id, Origin.generated, at=AT, corrections=[fix])
    assert made.corrections == [fix.as_dict()]


def test_a_user_template_must_be_the_persons_own(db: Session) -> None:
    a, b = add_user(db, USER_A), add_user(db, USER_B)
    db.add(_template(b, "upload-abcd1234"))
    db.add(_template(a, "upload-a1a1a1a1", version=3))
    db.flush()
    document = _posting_cv(db, a)
    for slug in ("upload-abcd1234", "../classic", "Classic", ""):
        with pytest.raises(store.TemplateUnknownError):
            store.set_template(db, a, document.id, slug, at=AT)
    made = store.set_template(db, a, document.id, "upload-a1a1a1a1", at=AT)
    assert (made.template_slug, made.template_version) == ("upload-a1a1a1a1", 3)
    assert [t.slug for t in store.user_templates(db, a)] == ["upload-a1a1a1a1"]


def _template(user: uuid.UUID, slug: str, version: int = 1) -> DocumentUserTemplate:
    return DocumentUserTemplate(
        user_id=user,
        slug=slug,
        name="From your CV",
        version=version,
        manifest={},
        cv_html="",
        letter_html="",
        css="",
        font_families=["Inter"],
    )


def test_a_prune_never_deletes_a_frozen_or_approved_version(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    numbers = [_version(db, document, f"V{n}") for n in range(6)]
    assert numbers == [1, 2, 3, 4, 5, 6]
    first = store.version(db, user, document.id, 1)
    store.freeze(db, user, first.id, FreezeReason.applied, AT)  # type: ignore[union-attr]
    store.approve(db, user, document.id, 2, AT)
    assert store.prune(db, user, document.id, keep=2) == 2
    assert [v.number for v in store.versions(db, user, document.id)] == [6, 5, 2, 1]
    _version(db, document, "V7")  # a snapshot prunes with the setting's default (30): none go
    assert len(store.versions(db, user, document.id)) == 5


def test_approve_moves_the_mark(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    one, two = _version(db, document, "One"), _version(db, document, "Two")
    store.approve(db, user, document.id, one, AT)
    store.approve(db, user, document.id, two, AT)
    db.expire_all()
    marks = {v.number: v.approved_at for v in store.versions(db, user, document.id)}
    assert marks == {one: None, two: AT}


def test_a_pdf_is_stored_byte_for_byte_with_its_hash_and_capped(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    number = _version(db, document)
    with pytest.raises(store.PdfTooLargeError):
        store.attach_pdf(
            db,
            user,
            document.id,
            number,
            PDF,
            fit=FitResult.one_page,
            fit_step=0,
            pages=1,
            max_bytes=10,
        )
    store.attach_pdf(
        db, user, document.id, number, PDF, fit=FitResult.overflow, fit_step=3, pages=2
    )
    db.expire_all()
    stored = store.version(db, user, document.id, number, pdf=True)
    assert stored is not None
    assert stored.pdf == PDF
    assert stored.pdf_sha256 == hashlib.sha256(PDF).digest()
    assert (stored.fit, stored.fit_step, stored.pages) == (FitResult.overflow, 3, 2)


# --- frozen means frozen ------------------------------------------------------------------------


def test_every_store_function_leaves_a_frozen_version_as_it_was(db: Session) -> None:
    user, document, number = _frozen(db)
    before = store.version(db, user, document.id, number, pdf=True)
    assert before is not None
    snapshot = (
        before.content,
        before.content_hash,
        before.pdf,
        before.pdf_sha256,
        before.template_slug,
        before.frozen_at,
        before.frozen_reason,
        before.approved_at,
    )
    with pytest.raises(store.FrozenVersionError):
        store.attach_pdf(
            db,
            user,
            document.id,
            number,
            b"%PDF other",
            fit=FitResult.one_page,
            fit_step=0,
            pages=1,
        )
    with pytest.raises(store.FrozenVersionError):
        store.approve(db, user, document.id, number, AT)
    with pytest.raises(store.FrozenVersionError):
        store.delete_version(db, user, document.id, number)
    again = store.freeze(db, user, before.id, FreezeReason.apply, AT.replace(hour=10))
    assert again.frozen_reason == FreezeReason.marked_sent  # the first freeze is the fact kept
    reused, made = store.snapshot(db, user, document.id, Origin.edited, at=AT)
    assert made
    assert reused.number == number + 1
    store.set_template(db, user, document.id, "banner", at=AT)
    store.restore(db, user, document.id, number, at=AT)
    store.save_draft(
        db,
        user,
        document.id,
        _cv("Later"),
        store.get(db, user, document.id).draft_revision,
        Author.user,
        at=AT,
    )  # type: ignore[union-attr]
    for n in range(4):
        _version(db, document, f"More {n}")
    store.prune(db, user, document.id, keep=0)
    db.expire_all()
    after = store.version(db, user, document.id, number, pdf=True)
    assert after is not None
    assert (
        after.content,
        after.content_hash,
        after.pdf,
        after.pdf_sha256,
        after.template_slug,
        after.frozen_at,
        after.frozen_reason,
        after.approved_at,
    ) == snapshot


PROTECTED_CHANGES = {
    "id": "gen_random_uuid()",
    "origin": "'restored'",
    "created_at": "now() - interval '1 day'",
    "content": "'{}'::jsonb",
    "content_hash": "decode(repeat('00', 32), 'hex')",
    "content_schema": "2",
    "language": "'fr'",
    "template_slug": "'banner'",
    "template_version": "9",
    "pdf": "NULL",
    "pdf_sha256": "decode(repeat('11', 32), 'hex')",
    "legacy_file_id": "gen_random_uuid()",
    "fit": "'overflow'",
    "fit_step": "7",
    "pages": "3",
    "corrections": '\'[{"code": "x"}]\'::jsonb',
    "number": "99",
    "frozen_at": "now() - interval '1 day'",
    "frozen_reason": "'apply'",
}


@pytest.mark.parametrize("column", sorted(PROTECTED_CHANGES))
def test_the_trigger_refuses_every_protected_column_of_a_frozen_row(
    db: Session, column: str
) -> None:
    _user, document, number = _frozen(db)
    with pytest.raises(IntegrityError, match="document_version_frozen"):
        db.execute(
            sa.text(
                f"UPDATE document_versions SET {column} = {PROTECTED_CHANGES[column]} "
                "WHERE document_id = :d AND number = :n"
            ),
            {"d": document.id, "n": number},
        )


def test_clearing_frozen_at_raises_and_an_unprotected_column_may_change(db: Session) -> None:
    _user, document, number = _frozen(db)
    where = {"d": document.id, "n": number}
    with pytest.raises(IntegrityError, match="document_version_frozen"), db.begin_nested():
        db.execute(
            sa.text(
                "UPDATE document_versions SET frozen_at = NULL, frozen_reason = NULL "
                "WHERE document_id = :d AND number = :n"
            ),
            where,
        )
    db.execute(
        sa.text(
            "UPDATE document_versions SET approved_at = now() "
            "WHERE document_id = :d AND number = :n"
        ),
        where,
    )


def test_a_frozen_version_is_deleted_only_with_its_document(db: Session) -> None:
    user, document, _number = _frozen(db)
    with pytest.raises(IntegrityError, match="document_version_frozen"), db.begin_nested():
        db.execute(
            sa.text("DELETE FROM document_versions WHERE document_id = :d"), {"d": document.id}
        )
    db.execute(sa.text("DELETE FROM jobs WHERE id = :p"), {"p": document.posting_id})
    assert store.get(db, user, document.id) is None
    assert store.versions(db, user, document.id) == []


def test_a_legacy_applied_version_points_at_its_file_and_goes_with_the_posting(
    db: Session,
) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    file_id = add_job_file(db, document.posting_id)  # type: ignore[arg-type]
    digest = hashlib.sha256(PDF).digest()
    with pytest.raises(ValueError, match="pdf_sha256"):
        store.snapshot(
            db,
            user,
            document.id,
            Origin.legacy,
            at=AT,
            legacy_file_id=file_id,
            pdf_sha256=b"x" * 32,
        )
    made, _ = store.snapshot(
        db, user, document.id, Origin.legacy, at=AT, legacy_file_id=file_id, pdf_sha256=digest
    )
    store.freeze(db, user, made.id, FreezeReason.legacy_applied, AT)
    stored = db.execute(
        sa.text("SELECT bytes FROM job_files WHERE id = :f"), {"f": file_id}
    ).scalar_one()
    assert hashlib.sha256(stored).digest() == made.pdf_sha256
    assert stored == PDF
    with pytest.raises(IntegrityError, match="document_version_frozen"), db.begin_nested():
        db.execute(sa.text("DELETE FROM job_files WHERE id = :f"), {"f": file_id})  # its SET NULL
    other_user = add_user(db, USER_B)
    other = _posting_cv(db, other_user)
    with pytest.raises(store.NotFoundError):  # a file of another posting
        store.snapshot(db, other_user, other.id, Origin.legacy, at=AT, legacy_file_id=file_id)
    db.execute(sa.text("DELETE FROM jobs WHERE id = :p"), {"p": document.posting_id})
    assert store.versions(db, user, document.id) == []


def test_a_frozen_version_keeps_its_legacy_file_once_its_document_has_no_posting(
    db: Session,
) -> None:
    """The `SET NULL` of the legacy file is let through only when the posting row is gone, not
    when the document simply has no posting."""
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    file_id = add_job_file(db, document.posting_id)  # type: ignore[arg-type]
    made, _ = store.snapshot(db, user, document.id, Origin.legacy, at=AT, legacy_file_id=file_id)
    store.freeze(db, user, made.id, FreezeReason.legacy_applied, AT)
    db.execute(
        sa.text(
            "UPDATE documents SET scope = 'base', posting_id = NULL, name = 'Moved', "
            "base_source = 'built' WHERE id = :d"
        ),
        {"d": document.id},
    )
    with pytest.raises(IntegrityError, match="document_version_frozen"), db.begin_nested():
        db.execute(
            sa.text("UPDATE document_versions SET legacy_file_id = NULL WHERE id = :v"),
            {"v": made.id},
        )
    with pytest.raises(IntegrityError, match="document_version_frozen"), db.begin_nested():
        db.execute(sa.text("DELETE FROM job_files WHERE id = :f"), {"f": file_id})


def test_a_freeze_racing_a_render_is_decided_by_the_row_lock(
    factory: sessionmaker[Session], short_locks: None
) -> None:
    with factory() as setup:
        user = add_user(setup, USER_A)
        document = _posting_cv(setup, user)
        number = _version(setup, document)
        found = store.version(setup, user, document.id, number)
        setup.commit()
    assert found is not None
    with factory() as freezer, factory() as renderer:
        store.freeze(freezer, user, found.id, FreezeReason.marked_sent, AT)
        with pytest.raises(store.DocumentBusyError):
            store.attach_pdf(
                renderer,
                user,
                document.id,
                number,
                PDF,
                fit=FitResult.one_page,
                fit_step=0,
                pages=1,
            )
        renderer.rollback()
        freezer.commit()
        with pytest.raises(store.FrozenVersionError):
            store.attach_pdf(
                renderer,
                user,
                document.id,
                number,
                PDF,
                fit=FitResult.one_page,
                fit_step=0,
                pages=1,
            )


# --- generations --------------------------------------------------------------------------------


def _generation(db: Session, user: uuid.UUID, document: Document) -> Any:
    return store.create_generation(
        db,
        user,
        document.id,
        GenerationMode.posting,
        GenerationTrigger.click,
        at=AT,
        estimate={"low": 1},
        token_ceiling=10,
    )


def test_one_open_generation_per_document(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    first = _generation(db, user, document)
    with pytest.raises(store.GenerationOpenError) as refused:
        _generation(db, user, document)
    assert refused.value.generation.id == first.id
    with (
        pytest.raises(IntegrityError, match="uq_document_generations_document_id_open"),
        db.begin_nested(),
    ):
        db.execute(
            sa.text(
                "INSERT INTO document_generations (user_id, document_id, mode, trigger, estimate, "
                "token_ceiling, status) VALUES (:u, :d, 'posting', 'auto', '{}', 1, 'waiting')"
            ),
            {"u": user, "d": document.id},
        )
    first.status = GenerationStatus.done
    db.flush()
    assert _generation(db, user, document).id != first.id


def test_two_generations_at_once_one_is_made(
    factory: sessionmaker[Session], short_locks: None
) -> None:
    with factory() as setup:
        user = add_user(setup, USER_A)
        document = _posting_cv(setup, user)
        setup.commit()
    with factory() as first, factory() as second:
        made = _generation(first, user, document)
        with pytest.raises(store.DocumentBusyError):
            _generation(second, user, document)
        second.rollback()
        first.commit()
        with pytest.raises(store.GenerationOpenError) as refused:
            _generation(second, user, document)
        assert refused.value.generation.id == made.id


def _wait_until_blocked(engine: sa.Engine, pid: int, thread: threading.Thread) -> None:
    """Return once the backend `pid` waits for a lock another session holds (Postgres says so),
    or once its thread has ended. Polls the lock table; no sleep, no clock."""
    with engine.connect() as watcher:
        blocked = sa.text("SELECT cardinality(pg_blocking_pids(:p)) > 0")
        while thread.is_alive() and not watcher.execute(blocked, {"p": pid}).scalar_one():
            watcher.rollback()


def test_deleting_a_base_cv_waits_for_a_generation_written_from_it_never_deadlocks(
    engine: sa.Engine, factory: sessionmaker[Session]
) -> None:
    """The interleaving of the review of D5: the generation holds its posting CV, the delete of
    the base CV starts, then the generation names that base CV. The delete locks the posting CV
    before the base CV, so it waits behind the generation instead of closing a circle."""
    with factory() as setup:
        user = add_user(setup, USER_A)
        base = _base(setup, user)
        document = _posting_cv(setup, user)
        setup.commit()
    assert document.base_document_id == base.id
    outcome: dict[str, Any] = {}
    with factory() as writer, factory() as deleter:
        store.lock_document(writer, user, document.id)
        pid = deleter.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()

        def delete() -> None:
            try:
                outcome["default"] = store.delete_base(deleter, user, base.id, AT)
                store.commit(deleter)
            except Exception as exc:  # the main thread asserts on it
                outcome["error"] = exc
                deleter.rollback()

        thread = threading.Thread(target=delete)
        thread.start()
        _wait_until_blocked(engine, pid, thread)
        made = store.create_generation(
            writer,
            user,
            document.id,
            GenerationMode.posting,
            GenerationTrigger.click,
            at=AT,
            estimate={},
            token_ceiling=1,
            base_document_id=base.id,
        )
        store.commit(writer)
        thread.join()
    assert "error" not in outcome, outcome.get("error")
    assert outcome["default"] is None
    with factory() as check:
        assert store.get(check, user, base.id) is None
        assert store.get(check, user, document.id).base_document_id is None  # type: ignore[union-attr]
        generation = check.get(DocumentGeneration, made.id)
        assert generation is not None
        assert generation.base_document_id is None


def test_a_deadlock_and_a_lock_timeout_are_busy_and_nothing_else_is() -> None:
    class OrigError(Exception):
        def __init__(self, sqlstate: str) -> None:
            super().__init__(sqlstate)
            self.sqlstate = sqlstate

    @store._busy
    def fails(sqlstate: str) -> None:
        raise OperationalError("SELECT 1", {}, OrigError(sqlstate))

    for sqlstate in ("40P01", "55P03"):
        assert store.is_busy(OperationalError("SELECT 1", {}, OrigError(sqlstate)))
        with pytest.raises(store.DocumentBusyError):
            fails(sqlstate)
    with pytest.raises(OperationalError):
        fails("57014")


def test_a_lock_wait_at_a_flush_is_busy(factory: sessionmaker[Session], short_locks: None) -> None:
    """Unmarking the old default happens at a flush, outside any explicit lock: it waits for the
    rename that holds that row, and gives up as DocumentBusyError, not a raw database error."""
    with factory() as setup:
        user = add_user(setup, USER_A)
        current = _base(setup, user, source=BaseSource.profile)
        setup.commit()
    with factory() as renamer, factory() as uploader:
        store.rename_base(renamer, user, current.id, "Renamed", AT)
        with pytest.raises(store.DocumentBusyError):
            _base(uploader, user, "Upload", source=BaseSource.uploaded)
        uploader.rollback()
        store.commit(renamer)
        made = _base(uploader, user, "Upload", source=BaseSource.uploaded)
        assert made.is_default


# --- the chat and the settings ------------------------------------------------------------------


def test_messages_are_capped_and_pruned(db: Session) -> None:
    user = add_user(db, USER_A)
    document = _posting_cv(db, user)
    for bad in ("", "  ", "x" * 2001):
        with pytest.raises(store.MessageInvalidError):
            store.add_message(db, user, document.id, Role.user, bad, at=AT)
    for n in range(5):
        store.add_message(db, user, document.id, Role.user, f"m{n}", at=AT, keep=3)
    store.add_message(
        db, user, document.id, Role.assistant, "d", at=AT, channel=Channel.design, keep=3
    )
    assert [m.text for m in store.messages(db, user, document.id)] == ["m3", "m4"]
    assert [m.text for m in store.messages(db, user, document.id, Channel.design)] == ["d"]


def test_the_settings_have_defaults_until_saved(db: Session) -> None:
    user = add_user(db, USER_A)
    assert store.settings(db, user) == DocumentSettings()
    saved = DocumentSettings(no_dash_separators=True, banned_phrases=["synergy"])
    store.save_settings(db, user, saved, AT)
    store.save_settings(db, user, saved, AT)
    assert store.settings(db, user) == saved
    store.accept_consent(db, user, 1, AT)
    store.accept_consent(db, user, 1, AT)
    assert store.consented(db, user, 1)
    assert not store.consented(db, user, 2)


# --- isolation and deletes ----------------------------------------------------------------------


def _everything(db: Session, user: uuid.UUID, slug: str) -> dict[str, Any]:
    base = _base(db, user)
    document = _posting_cv(db, user)
    number = _version(db, document)
    generation = _generation(db, user, document)
    store.add_message(db, user, document.id, Role.user, "hello", at=AT)
    store.save_settings(db, user, DocumentSettings(no_dash_separators=True), AT)
    store.accept_consent(db, user, 1, AT)
    db.add(_template(user, slug))
    db.flush()
    found = store.version(db, user, document.id, number)
    return {
        "base": base,
        "document": document,
        "number": number,
        "generation": generation,
        "version": found,
    }


def test_one_user_never_reads_or_writes_the_others_documents(db: Session) -> None:
    a, b = add_user(db, USER_A), add_user(db, USER_B)
    mine = _everything(db, a, "upload-aaaaaaaa")
    doc, base, number = mine["document"].id, mine["base"].id, mine["number"]
    posting_of_b = add_posting(db, b)
    assert store.get(db, b, doc) is None
    assert store.base_cvs(db, b) == []
    assert store.default_base(db, b) is None
    assert store.versions(db, b, doc) == []
    assert store.version(db, b, doc, number) is None
    assert store.messages(db, b, doc) == []
    assert store.user_templates(db, b) == []
    assert store.user_template(db, b, "upload-aaaaaaaa") is None
    assert store.posting_documents(db, b, mine["document"].posting_id) == []
    assert store.open_generation(db, b, doc) is None
    assert store.settings(db, b) == DocumentSettings()
    assert not store.consented(db, b, 1)
    writes = [
        lambda: store.save_draft(db, b, doc, CV, 2, Author.user, at=AT),
        lambda: store.set_language(db, b, doc, Language.fr, AT),
        lambda: store.restore(db, b, doc, number, at=AT),
        lambda: store.snapshot(db, b, doc, Origin.edited, at=AT),
        lambda: store.set_template(db, b, doc, "banner", at=AT),
        lambda: store.attach_pdf(
            db, b, doc, number, PDF, fit=FitResult.one_page, fit_step=0, pages=1
        ),
        lambda: store.approve(db, b, doc, number, AT),
        lambda: store.prune(db, b, doc, keep=0),
        lambda: store.delete_version(db, b, doc, number),
        lambda: store.freeze(db, b, mine["version"].id, FreezeReason.marked_sent, AT),
        lambda: store.create_generation(
            db,
            b,
            doc,
            GenerationMode.chat,
            GenerationTrigger.chat,
            at=AT,
            estimate={},
            token_ceiling=1,
        ),
        lambda: store.add_message(db, b, doc, Role.user, "hi", at=AT),
        lambda: store.prune_messages(db, b, doc, keep=0),
        lambda: store.rename_base(db, b, base, "Mine now", AT),
        lambda: store.set_default(db, b, base, AT),
        lambda: store.delete_base(db, b, base, AT),
        lambda: store.get_or_create(db, b, mine["document"].posting_id, Kind.letter, at=AT),
        lambda: store.get_or_create(db, b, posting_of_b, Kind.cv, at=AT, base_document_id=base),
    ]
    for write in writes:
        with pytest.raises(store.NotFoundError):
            write()
    with pytest.raises(store.TemplateUnknownError):
        store.set_template(db, b, _posting_cv(db, b).id, "upload-aaaaaaaa", at=AT)
    with pytest.raises(store.NotFoundError):  # an upload of another person
        _base(db, b, source=BaseSource.uploaded, source_document_id=add_upload(db, a))
    db.expire_all()
    assert store.get(db, a, doc).draft_revision == 2  # type: ignore[union-attr]
    assert rows_of(db, a)["document_versions"] == 1
    assert rows_of(db, a)["document_messages"] == 1


def test_a_posting_cv_cannot_name_another_persons_base_cv(db: Session) -> None:
    a, b = add_user(db, USER_A), add_user(db, USER_B)
    base_of_a = _base(db, a)
    posting_of_b = add_posting(db, b)
    with pytest.raises(store.NotFoundError):
        store.get_or_create(db, b, posting_of_b, Kind.cv, at=AT, base_document_id=base_of_a.id)
    with pytest.raises(store.NotFoundError):
        store.create_generation(
            db,
            b,
            _posting_cv(db, b).id,
            GenerationMode.posting,
            GenerationTrigger.click,
            at=AT,
            estimate={},
            token_ceiling=1,
            base_document_id=base_of_a.id,
        )


def test_the_schema_refuses_a_version_under_another_persons_document(db: Session) -> None:
    a, b = add_user(db, USER_A), add_user(db, USER_B)
    document = _posting_cv(db, a)
    with (
        pytest.raises(IntegrityError, match="fk_document_messages_document_id_documents"),
        db.begin_nested(),
    ):
        db.execute(
            sa.text(
                "INSERT INTO document_messages (document_id, user_id, role, text, at) "
                "VALUES (:d, :u, 'user', 'x', now())"
            ),
            {"d": document.id, "u": b},
        )
    with pytest.raises(IntegrityError, match="fk_document_versions_document_id_documents"):
        db.execute(
            sa.text(
                "INSERT INTO document_versions (document_id, user_id, number, content, "
                "content_schema, content_hash, language, template_slug, template_version, "
                "origin) VALUES (:d, :u, 1, '{}', 1, decode(repeat('00', 32), 'hex'), 'en', "
                "'classic', 1, 'edited')"
            ),
            {"d": document.id, "u": b},
        )


# Every reference from a row of A to a row of B, written by SQL past the store: (the statement,
# the constraint or trigger that refuses it). `{a}` and `{b}` are the rows of `_everything`.
CROSS_USER = {
    "documents.base_document_id": (
        "UPDATE documents SET base_document_id = :b_base WHERE id = :a_document",
        "fk_documents_base_document_id_documents",
    ),
    "documents.source_document_id": (
        "UPDATE documents SET source_document_id = :b_upload WHERE id = :a_base",
        "fk_documents_source_document_id_ingestion_documents",
    ),
    "document_generations.base_document_id": (
        "UPDATE document_generations SET base_document_id = :b_base WHERE id = :a_generation",
        "fk_document_generations_base_document_id_documents",
    ),
    "document_versions.generation_id": (
        "UPDATE document_versions SET generation_id = :b_generation WHERE id = :a_version",
        "fk_document_versions_generation_id_document_generations",
    ),
    "document_versions.legacy_file_id": (
        "UPDATE document_versions SET legacy_file_id = :b_file WHERE id = :a_version",
        "document_version_legacy_file",
    ),
    "document_versions.legacy_file_id of a base CV": (
        "INSERT INTO document_versions (document_id, user_id, number, content, content_schema, "
        "content_hash, language, template_slug, template_version, origin, legacy_file_id) "
        "VALUES (:a_base, :a, 1, '{}', 1, decode(repeat('00', 32), 'hex'), 'en', 'classic', 1, "
        "'legacy', :a_file)",
        "document_version_legacy_file",
    ),
    "document_user_templates.base_document_id": (
        "UPDATE document_user_templates SET base_document_id = :b_base WHERE user_id = :a",
        "fk_document_user_templates_base_document_id_documents",
    ),
    "document_user_templates.source_document_id": (
        "UPDATE document_user_templates SET source_document_id = :b_upload WHERE user_id = :a",
        "fk_document_user_templates_source_document_id_ingestion",
    ),
}


@pytest.mark.parametrize("reference", sorted(CROSS_USER))
def test_the_schema_refuses_every_reference_to_another_persons_row(
    db: Session, reference: str
) -> None:
    a, b = add_user(db, USER_A), add_user(db, USER_B)
    params: dict[str, Any] = {"a": a, "b": b}
    for prefix, user, slug in (("a", a, "upload-aaaaaaaa"), ("b", b, "upload-bbbbbbbb")):
        made = _everything(db, user, slug)
        params[f"{prefix}_base"] = made["base"].id
        params[f"{prefix}_document"] = made["document"].id
        params[f"{prefix}_generation"] = made["generation"].id
        params[f"{prefix}_version"] = made["version"].id
        params[f"{prefix}_upload"] = add_upload(db, user)
        params[f"{prefix}_file"] = add_job_file(db, made["document"].posting_id)
    statement, refused_by = CROSS_USER[reference]
    with pytest.raises(IntegrityError, match=refused_by) as refused:
        db.execute(sa.text(statement), params)
    assert refused.value.orig.sqlstate == "23503"  # type: ignore[union-attr]


def test_the_same_owner_references_hold_for_ones_own_rows(db: Session) -> None:
    a = add_user(db, USER_A)
    made = _everything(db, a, "upload-aaaaaaaa")
    upload = add_upload(db, a)
    file_id = add_job_file(db, made["document"].posting_id)
    params = {"a": a, "base": made["base"].id, "upload": upload, "file": file_id}
    params |= {"generation": made["generation"].id, "version": made["version"].id}
    for statement in (
        "UPDATE documents SET source_document_id = :upload WHERE id = :base",
        "UPDATE document_generations SET base_document_id = :base WHERE id = :generation",
        "UPDATE document_versions SET generation_id = :generation, legacy_file_id = :file "
        "WHERE id = :version",
        "UPDATE document_user_templates SET base_document_id = :base, "
        "source_document_id = :upload WHERE user_id = :a",
    ):
        db.execute(sa.text(statement), params)
    db.execute(sa.text("DELETE FROM documents WHERE id = :base"), params)
    db.execute(sa.text("DELETE FROM ingestion_documents WHERE id = :upload"), params)
    generation = db.execute(
        sa.text(
            "SELECT base_document_id, user_id FROM document_generations WHERE id = :generation"
        ),
        params,
    ).one()
    template = db.execute(
        sa.text(
            "SELECT base_document_id, source_document_id, user_id FROM document_user_templates "
            "WHERE user_id = :a"
        ),
        params,
    ).one()
    assert tuple(generation) == (None, a)  # SET NULL (base_document_id): the owner stays
    assert tuple(template) == (None, None, a)


def test_deleting_a_user_leaves_nothing_of_theirs(db: Session) -> None:
    a, b = add_user(db, USER_A), add_user(db, USER_B)
    _everything(db, a, "upload-aaaaaaaa")
    _everything(db, b, "upload-bbbbbbbb")
    frozen = _posting_cv(db, a)
    found = store.version(db, a, frozen.id, _version(db, frozen))
    store.freeze(db, a, found.id, FreezeReason.applied, AT)  # type: ignore[union-attr]
    file_id = add_job_file(db, frozen.posting_id)  # type: ignore[arg-type]
    made, _ = store.snapshot(db, a, frozen.id, Origin.legacy, at=AT, legacy_file_id=file_id)
    store.freeze(db, a, made.id, FreezeReason.legacy_applied, AT)
    tables = rows_of(db, a)
    assert set(tables) == {
        "document_consents",
        "document_generations",
        "document_messages",
        "document_settings",
        "document_user_templates",
        "document_versions",
        "documents",
    }
    assert all(count > 0 for count in tables.values())
    before_b = rows_of(db, b)
    db.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": a})
    assert set(rows_of(db, a).values()) == {0}
    assert rows_of(db, b) == before_b


# --- privacy ------------------------------------------------------------------------------------


def test_no_document_text_reaches_a_log(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    planted = marker()
    caplog.set_level(logging.DEBUG)
    user = add_user(db, USER_A)
    base = _base(db, user, name=f"CV {planted}", content={**CV, "summary": planted})
    document = _posting_cv(db, user)
    store.save_draft(db, user, document.id, _cv(planted), 1, Author.user, at=AT)
    made, _ = store.snapshot(db, user, document.id, Origin.edited, at=AT)
    store.attach_pdf(
        db,
        user,
        document.id,
        made.number,
        planted.encode(),
        fit=FitResult.one_page,
        fit_step=0,
        pages=1,
    )
    store.freeze(db, user, made.id, FreezeReason.marked_sent, AT)
    store.add_message(db, user, document.id, Role.user, planted, at=AT)
    for n in range(3):
        _version(db, document, f"{planted} {n}")
    store.prune(db, user, document.id, keep=0)
    store.delete_base(db, user, base.id, AT)
    assert find([planted], logs=caplog.text) == []
