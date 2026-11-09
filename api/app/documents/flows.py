"""The workflow run a posting's CV or letter belongs to (Activity, agents A10).

One click writes a posting's documents: the generations it makes share their posting and the
moment of the click (one transaction). They form one workflow run, named by its CV generation,
so the CV and the letter show as one run of "Documents for a posting". A generation made alone
(a later Write of the letter only, a Make it fit) is a run of its own. A base CV built from the
profile for a click with no base CV joins that click's run: the click's CV waits for it. The
hiring panel's rebuild joins the run of the writer its panel reviewed. Only a person's click
records a trigger.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.runs import WorkflowRef, joining
from app.documents.models import (
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationTrigger,
    Kind,
)


def posting_flow(db: Session, generation: DocumentGeneration) -> WorkflowRef:
    clicked = generation.trigger is GenerationTrigger.click
    if generation.mode is GenerationMode.shorten:
        return WorkflowRef("posting_documents", generation.id, "fit" if clicked else None)
    first = db.scalar(
        sa.select(DocumentGeneration.id)
        .join(Document, Document.id == DocumentGeneration.document_id)
        .where(
            DocumentGeneration.user_id == generation.user_id,
            DocumentGeneration.created_at == generation.created_at,
            Document.posting_id
            == sa.select(Document.posting_id)
            .where(Document.id == generation.document_id)
            .scalar_subquery(),
        )
        # The CV's first: its run starts before the letter's generation may exist.
        .order_by(sa.case((Document.kind == Kind.cv, 0), else_=1), DocumentGeneration.id)
        .limit(1)
    )
    return WorkflowRef(
        "posting_documents",
        first or generation.id,
        # A click on Write; an Apply or a Like is not told apart on the generation (`auto`).
        "write" if clicked and generation.mode is GenerationMode.posting else None,
    )


def base_flow(db: Session, generation: DocumentGeneration) -> WorkflowRef:
    if (
        generation.mode is GenerationMode.base_profile
        and generation.trigger is GenerationTrigger.click
    ):
        # `writer_base.click_wait` builds it in the click's transaction, at the click's moment.
        posting = db.scalar(
            sa.select(DocumentGeneration)
            .where(
                DocumentGeneration.user_id == generation.user_id,
                DocumentGeneration.created_at == generation.created_at,
                DocumentGeneration.mode == GenerationMode.posting,
            )
            .order_by(DocumentGeneration.id)
            .limit(1)
        )
        if posting is not None:
            return posting_flow(db, posting)
    clicked = generation.trigger is GenerationTrigger.click and generation.mode in (
        GenerationMode.base_profile,
        GenerationMode.base_upload,
    )
    return WorkflowRef("base_cv", generation.document_id, "new_base" if clicked else None)


def panel_flow(db: Session, base: bool, source_run_id: uuid.UUID | None) -> WorkflowRef:
    """The hiring panel's rebuild: the workflow run of the writer run its panel reviewed, with
    no trigger of its own; a run alone when that writer run recorded none (an older run)."""
    joined = joining(db, source_run_id)
    if joined is not None:
        return joined
    return WorkflowRef("base_cv") if base else WorkflowRef("posting_documents")


def capture_flow(db: Session, generation: DocumentGeneration) -> WorkflowRef:
    """The first design capture of an upload joins its conversion's Base CV run; a later one is
    a click on Try the design again, a run of its own."""
    earlier = db.scalar(
        sa.select(DocumentGeneration.id)
        .where(
            DocumentGeneration.user_id == generation.user_id,
            DocumentGeneration.document_id == generation.document_id,
            DocumentGeneration.mode == GenerationMode.template_capture,
            DocumentGeneration.created_at < generation.created_at,
        )
        .limit(1)
    )
    if earlier is None:
        return WorkflowRef("base_cv", generation.document_id)
    return WorkflowRef("base_cv", generation.id, "capture")
