"""D6 on the real renderer: the render job puts a hand written CV through the fit loop of the
renderer service (Chromium in its container, the stack of test_renderer_container.py) and stores
a one page PDF that holds the invented name and the first employer's organisation; a switch of
language makes a second one, and the posting is `cv_ready`.

Marked `docker` (the module of the stack fixture carries the mark): it needs a Docker daemon;
the image is built under a tag of its own unless RENDERER_TEST_IMAGE names one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.documents import render_job
from app.documents.models import DocumentVersion, FitResult, Language
from app.documents.render_client import Rendered
from tests.documents.render_support import RecordingQueue, cv_draft, give_profile
from tests.documents.store_support import (
    AT,
    USER_A,
    add_posting,
    add_user,
    db,
    engine,
    factory,
    migrated_url,
)
from tests.documents.test_renderer_container import Stack, stack, text_of

__all__ = ["db", "engine", "factory", "migrated_url", "stack"]

pytestmark = [pytest.mark.docker]


def test_the_job_renders_a_one_page_cv_on_the_real_renderer(
    factory: sessionmaker[Session], stack: Stack
) -> None:
    async def real(html: str) -> Rendered:
        return await asyncio.to_thread(stack.render, html)

    with factory() as db:
        user = add_user(db, USER_A)
        posting = add_posting(db, user)
        db.commit()
    give_profile(factory, user, AT)
    with factory() as db:
        document = cv_draft(db, user, posting, AT)
    ctx: dict[str, Any] = {"sessions": factory, "clock": lambda: AT, "documents_render": real}
    for switch in (None, Language.fr):
        with factory() as db:
            if switch is not None:
                render_job.switch(db, user, document, at=AT, language=switch)
            found, _ = render_job.mark_queued(db, user, document)
            revision = found.draft_revision
            db.commit()
        asyncio.run(render_job.enqueue(RecordingQueue(), document, revision))
        asyncio.run(render_job.render_document(ctx, str(document), revision))
    with factory() as db:
        versions = list(
            db.execute(
                sa.select(DocumentVersion)
                .where(DocumentVersion.document_id == document)
                .order_by(DocumentVersion.number)
                .options(sa.orm.undefer(DocumentVersion.pdf))
            ).scalars()
        )
        status = db.execute(
            sa.text("SELECT status FROM jobs WHERE id = :j"), {"j": posting}
        ).scalar_one()
    assert [(v.fit, v.pages) for v in versions] == [(FitResult.one_page, 1)] * 2
    text = text_of(bytes(versions[0].pdf or b""))
    assert "Alex Example" in text
    assert "Example Corp" in text
    assert status == "cv_ready"
