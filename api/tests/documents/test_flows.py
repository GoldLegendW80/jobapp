"""The workflow run of a posting's documents (Activity, agents A10): one per click."""

from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import generate, store
from app.documents.models import (
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
)
from tests.documents.store_support import AT, engine, factory, migrated_url
from tests.documents.writer_support import (
    CEILING,
    ESTIMATE,
    person,
    run_row,
    start,
    start_letter,
)

# The database fixtures of the documents tests, used by name.
__all__ = ["engine", "factory", "migrated_url"]

USER = "e2e-flows@example.org"


def test_the_cv_and_letter_of_one_click_are_one_workflow_run_and_a_later_letter_its_own(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER)
    _, cv_generation, cv_run = start(who, None)
    _, letter_generation, letter_run = start_letter(who, None)
    cv, letter = run_row(factory, cv_run), run_row(factory, letter_run)
    assert cv.workflow == letter.workflow == "posting_documents"
    assert cv.workflow_trigger == letter.workflow_trigger == "write"
    assert cv.workflow_run_id == letter.workflow_run_id == min(cv_generation, letter_generation)

    with factory() as db:
        # The letter written, an hour later its Write again: another click.
        db.execute(
            sa.update(DocumentGeneration)
            .where(DocumentGeneration.id == letter_generation)
            .values(status=GenerationStatus.done)
        )
        document = store.get_or_create(db, who.user, who.posting, Kind.letter, at=AT)
        later = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.auto,
            who.base,
            ESTIMATE,
            CEILING,
            at=AT + timedelta(hours=1),
        )
        run = generate.start(db, later, at=AT + timedelta(hours=1), settings=who.settings)
        db.commit()
        alone, later_id = run.id, later.id
    row = run_row(factory, alone)
    assert (row.workflow_run_id, row.workflow_trigger) == (later_id, None)
