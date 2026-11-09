"""The workflow run of a posting's documents (Activity, agents A10): one per click; a base CV
a click builds first joins it, the hiring panel's rebuild joins the run of the writer it reviewed,
a design capture joins its conversion's run once, and only a person's click records a trigger."""

import dataclasses
import uuid
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.runs import WorkflowRef
from app.documents import consent, flows, generate, panel, store
from app.documents.models import (
    DocumentGeneration,
    DocumentPanel,
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
    execute,
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
    # Named by the CV's generation, whichever id sorts first.
    assert cv.workflow_run_id == letter.workflow_run_id == cv_generation

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
        # The Base CV page's New CV, a click of its own, is its own run.
        later = _generation(who, GenerationMode.base_profile, GenerationTrigger.click, 5)
        assert flows.base_flow(db, later) == WorkflowRef("base_cv", who.base, "new_base")
        # A base CV the documents tick builds is nobody's click: no trigger.
        tick = _generation(who, GenerationMode.base_profile, GenerationTrigger.auto)
        assert flows.base_flow(db, tick) == WorkflowRef("base_cv", who.base, None)


def test_make_it_fit_is_a_run_of_its_own_triggered_by_its_click(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER)
    start(who, None)
    with factory() as db:
        fit = _generation(who, GenerationMode.shorten, GenerationTrigger.click)
        assert flows.posting_flow(db, fit) == WorkflowRef("posting_documents", fit.id, "fit")


def test_the_panels_rebuild_after_the_click_joins_the_clicks_run_with_no_trigger(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER)
    on = dataclasses.replace(
        who, settings=who.settings.model_copy(update={"documents_panel_enabled": True})
    )
    with factory() as db:
        consent.record(db, who.user, AT)
        db.commit()
    _, click, writer = start(on, "documents_cv_valid")
    execute(on, writer)
    with factory() as db:
        panel_id = db.scalar(
            sa.select(DocumentPanel.id).where(DocumentPanel.source_run_id == writer)
        )
        loaded = panel.load(db, panel_id)
        assert loaded is not None
        later = AT + timedelta(minutes=12)
        rebuild = panel._start_rebuild(db, loaded, uuid.uuid4().hex, later, on.settings, None)
        assert isinstance(rebuild, uuid.UUID)
        db.commit()
    row = run_row(factory, rebuild)
    assert (row.agent, row.created_at) == ("documents_cv", later)
    assert (row.workflow, row.workflow_run_id, row.workflow_trigger) == (
        "posting_documents",
        click,
        None,
    )


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
