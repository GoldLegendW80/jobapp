"""The workflow run of a posting's documents (Activity, agents A10): one per click; a base CV
a click builds first joins it, and a design capture joins its conversion's run once."""

import uuid
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.runs import WorkflowRef
from app.documents import flows, generate, store
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
    Person,
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


def _generation(
    who: Person, mode: GenerationMode, trigger: GenerationTrigger, minutes: int = 0
) -> DocumentGeneration:
    return DocumentGeneration(
        id=uuid.uuid4(),
        user_id=who.user,
        document_id=who.base,
        mode=mode,
        trigger=trigger,
        estimate=ESTIMATE,
        token_ceiling=CEILING,
        status=GenerationStatus.done,
        created_at=AT + timedelta(minutes=minutes),
    )


def test_a_base_cv_a_click_builds_first_joins_that_clicks_run_and_any_other_is_its_own(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER)
    _, cv_generation, _ = start(who, None)
    with factory() as db:
        # Built in the click's transaction, at the click's moment (`writer_base.click_wait`).
        first = _generation(who, GenerationMode.base_profile, GenerationTrigger.click)
        assert flows.base_flow(db, first) == WorkflowRef(
            "posting_documents", cv_generation, "write"
        )
        # A base CV the documents tick builds later, or the Base CV page's, is its own run.
        later = _generation(who, GenerationMode.base_profile, GenerationTrigger.click, 5)
        assert flows.base_flow(db, later) == WorkflowRef("base_cv", who.base, "new_base")
        page = _generation(who, GenerationMode.base_profile, GenerationTrigger.auto)
        assert flows.base_flow(db, page) == WorkflowRef("base_cv", who.base, "new_base")


def test_the_first_design_capture_joins_its_base_cvs_run_and_a_retry_is_its_own(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER)
    with factory() as db:
        first = _generation(who, GenerationMode.template_capture, GenerationTrigger.auto)
        assert flows.capture_flow(db, first) == WorkflowRef("base_cv", who.base)
        db.add(first)
        db.flush()
        again = _generation(who, GenerationMode.template_capture, GenerationTrigger.click, 30)
        assert flows.capture_flow(db, again) == WorkflowRef("base_cv", again.id, "capture")
        db.rollback()
