"""A1: the scaffolding of conftest.py itself. Two people with profiles, postings at the same
invented company on the fake ATS, and the documents each holds with a PDF, so a later task can
write its isolation test (B answers 404 to A) on top of them."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.documents import store
from tests.apply.a1_support import PDF, Forms
from tests.apply.conftest import Person


def test_two_people_have_a_posting_each_at_the_same_company_on_the_fake_ats(
    db: Session, person_a: Person, person_b: Person, forms: Forms
) -> None:
    assert person_a.user_id != person_b.user_id
    assert person_a.posting_id != person_b.posting_id
    rows = db.execute(sa.text("SELECT user_id, company, url FROM jobs")).all()
    assert {r.user_id for r in rows} == {person_a.user_id, person_b.user_id}
    assert {r.company for r in rows} == {"Example Robotics"}
    for row in rows:
        assert row.url == forms.base + "/ashby/example-robotics/applied-ai-engineer/application"
        assert forms.get(row.url.removeprefix(forms.base)).status == 200


def test_each_person_has_a_profile_a_cv_and_a_letter_with_a_pdf(
    db: Session, person_a: Person, person_b: Person
) -> None:
    for person in (person_a, person_b):
        sections = db.execute(
            sa.text("SELECT sections FROM profiles WHERE user_id = :u"), {"u": person.user_id}
        ).scalar_one()
        assert {"identity", "answers"} <= set(sections)
        documents = store.posting_documents(db, person.user_id, person.posting_id)
        assert [d.kind.value for d in documents] == ["cv", "letter"]
        for document in documents:
            (version,) = store.versions(db, person.user_id, document.id)
            assert version.pdf == PDF
        assert {v.id for d in documents for v in store.versions(db, person.user_id, d.id)} == {
            person.cv_version_id,
            person.letter_version_id,
        }


def test_a_person_cannot_read_the_documents_of_the_other(
    db: Session, person_a: Person, person_b: Person
) -> None:
    assert store.posting_documents(db, person_b.user_id, person_a.posting_id) == []
    mine = store.posting_documents(db, person_a.user_id, person_a.posting_id)[0]
    assert store.get(db, person_b.user_id, mine.id) is None
