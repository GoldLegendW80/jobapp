"""The workflow run a posting's CV or letter belongs to (Activity, agents A10).

One click writes a posting's documents: the generations it makes share their posting and the
moment of the click (one transaction). They form one workflow run, named by the first of them,
so the CV, the letter and their rebuilds show as one run of "Documents for a posting". A
generation made alone (a later Write of the letter only) is a run of its own.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.agents.runs import WorkflowRef
from app.documents.models import Document, DocumentGeneration, GenerationTrigger


def posting_flow(db: Session, generation: DocumentGeneration) -> WorkflowRef:
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
        .order_by(DocumentGeneration.id)
        .limit(1)
    )
    return WorkflowRef(
        "posting_documents",
        first or generation.id,
        # A click on Write; an Apply or a Like is not told apart on the generation (`auto`).
        "write" if generation.trigger == GenerationTrigger.click else None,
    )
