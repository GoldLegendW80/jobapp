"""D6, first review: the reviewer's probe of a template switch during a render of the same
revision."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import render_job
from app.documents.models import Document, DocumentVersion, RenderState
from tests.documents.render_support import FakeRenderer, RecordingQueue, context
from tests.documents.store_support import AT, db, engine, factory, migrated_url, short_locks
from tests.documents.test_render_job import _ask, _run, _setup

__all__ = ["db", "engine", "factory", "migrated_url", "short_locks"]


def test_probe_switch_during_a_render_of_the_same_revision(
    factory: sessionmaker[Session],
) -> None:
    made = _setup(factory)
    queue = RecordingQueue()
    revision = _ask(factory, made["user"], made["document"], queue)

    def switch() -> None:
        with factory() as db:
            render_job.switch(db, made["user"], made["document"], at=AT, template_slug="sidebar")
            render_job.mark_queued(db, made["user"], made["document"])
            db.commit()

    _run(context(factory, FakeRenderer(during=switch), AT), made["document"], revision)
    with factory() as db:
        doc = db.execute(sa.select(Document).where(Document.id == made["document"])).scalar_one()
        versions = list(
            db.execute(
                sa.select(DocumentVersion)
                .where(DocumentVersion.document_id == made["document"])
                .order_by(DocumentVersion.number)
            ).scalars()
        )
    # The switched version must get a render eventually: either queued, or rendered.
    assert doc.render_state == RenderState.queued or all(v.pdf_sha256 for v in versions)
