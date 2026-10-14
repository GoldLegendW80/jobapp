"""D6: the render job against a real Postgres and a fake renderer (render_support). Coalescing,
a save during a render, the renderer down, an overflow, the status move and its lock, a frozen
version, the worker stopped mid render, the cron that queues a lost render, and the canary."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.documents import corrections, render_job, store
from app.documents.models import (
    Author,
    Document,
    DocumentVersion,
    FitResult,
    FreezeReason,
    RenderState,
)
from app.jobs import service as jobs_service
from app.jobs.models import Job, JobEvent
from tests.documents.canary import find, marker
from tests.documents.render_support import (
    CV,
    FakeRenderer,
    RecordingQueue,
    context,
    cv_draft,
    give_profile,
)
from tests.documents.store_support import (
    AT,
    USER_A,
    USER_B,
    add_posting,
    add_user,
    db,
    engine,
    factory,
    migrated_url,
    short_locks,
)

__all__ = ["db", "engine", "factory", "migrated_url", "short_locks"]


def _setup(factory: sessionmaker[Session], content: dict[str, Any] | None = None) -> dict[str, Any]:
    with factory() as db:
        user = add_user(db, USER_A)
        posting = add_posting(db, user)
        db.commit()
        give_profile(factory, user, AT)
        document = cv_draft(db, user, posting, AT, content)
    return {"user": user, "posting": posting, "document": document}


def _ask(
    factory: sessionmaker[Session], user: uuid.UUID, document: uuid.UUID, queue: RecordingQueue
) -> int:
    with factory() as db:
        found, fresh = render_job.mark_queued(db, user, document)
        revision = found.draft_revision
        db.commit()
    if fresh:
        asyncio.run(render_job.enqueue(queue, document, revision))
    return revision


def _run(ctx: dict[str, Any], document: uuid.UUID, revision: int) -> None:
    asyncio.run(render_job.render_document(ctx, str(document), revision))


def _document(factory: sessionmaker[Session], document: uuid.UUID) -> Document:
    with factory() as db:
        return db.execute(sa.select(Document).where(Document.id == document)).scalar_one()


def _versions(factory: sessionmaker[Session], document: uuid.UUID) -> list[DocumentVersion]:
    with factory() as db:
        query = (
            sa.select(DocumentVersion)
            .where(DocumentVersion.document_id == document)
            .order_by(DocumentVersion.number)
            .options(sa.orm.undefer(DocumentVersion.pdf))
        )
        return list(db.execute(query).scalars())


def _status(factory: sessionmaker[Session], posting: uuid.UUID) -> str:
    with factory() as db:
        return db.execute(sa.select(Job.status).where(Job.id == posting)).scalar_one().value


def _set_status(factory: sessionmaker[Session], posting: uuid.UUID, status: str) -> None:
    with factory() as db:
        db.execute(
            sa.text("UPDATE jobs SET status = :s WHERE id = :j"), {"s": status, "j": posting}
        )
        db.commit()


def test_a_render_stores_the_pdf_moves_the_posting_and_keeps_the_unprintable(
    factory: sessionmaker[Session],
) -> None:
    content = copy.deepcopy(CV)
    content["experience"][0]["bullets"][0] += " 漢字"
    made = _setup(factory, content)
    queue, render = RecordingQueue(), FakeRenderer()
    revision = _ask(factory, made["user"], made["document"], queue)
    (job,) = queue.jobs
    assert (job["function"], job["queue"]) == (render_job.FUNCTION, queues.DOCUMENTS)
    assert job["args"] == (str(made["document"]), revision)
    _run(context(factory, render, AT), made["document"], revision)
    (version,) = _versions(factory, made["document"])
    assert version.pdf is not None
    assert version.pdf.startswith(b"%PDF")
    assert (version.fit, version.pages, version.fit_step) == (FitResult.one_page, 1, 0)
    codes = [record["code"] for record in version.corrections]
    assert corrections.NOT_PRINTABLE in codes  # stored with the version
    document = _document(factory, made["document"])
    assert (document.render_state, document.render_error) == (RenderState.idle, None)
    assert _status(factory, made["posting"]) == "cv_ready"
    with factory() as db:
        events = [
            e.data
            for e in db.execute(
                sa.select(JobEvent).where(JobEvent.job_id == made["posting"])
            ).scalars()
        ]
    assert {"from": "found", "to": "cv_ready", "via": "documents"} in events


def test_three_requests_in_a_second_are_one_render(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    queue, render = RecordingQueue(), FakeRenderer()
    revisions = {_ask(factory, made["user"], made["document"], queue) for _ in range(3)}
    assert len(queue.jobs) == 1
    assert len(revisions) == 1
    _run(context(factory, render, AT), made["document"], revisions.pop())
    assert len(render.calls) == 1
    # The same job delivered again (a retry) renders nothing more.
    _run(context(factory, render, AT), made["document"], queue.jobs[0]["args"][1])
    assert len(render.calls) == 1


def test_a_save_during_a_render_replaces_the_next_render_and_skips_the_old_one(
    factory: sessionmaker[Session],
) -> None:
    made = _setup(factory)
    queue = RecordingQueue()
    first = _ask(factory, made["user"], made["document"], queue)

    def save() -> None:
        with factory() as db:
            store.save_draft(
                db,
                made["user"],
                made["document"],
                {**CV, "headline": "Edited"},
                first,
                Author.user,
                at=AT,
            )
            render_job.mark_queued(db, made["user"], made["document"])
            db.commit()

    render = FakeRenderer(during=save)
    _run(context(factory, render, AT), made["document"], first)
    asyncio.run(render_job.enqueue(queue, made["document"], first + 1))  # after the save's commit
    document = _document(factory, made["document"])
    assert (document.render_state, document.render_revision) == (RenderState.queued, first + 1)
    assert len(queue.jobs) == 2
    # The first job delivered again is replaced; the second renders the new draft.
    _run(context(factory, render, AT), made["document"], first)
    assert len(render.calls) == 1
    _run(context(factory, render, AT), made["document"], first + 1)
    assert len(render.calls) == 2
    assert [v.pdf is not None for v in _versions(factory, made["document"])] == [True, True]
    assert _document(factory, made["document"]).render_state == RenderState.idle


def test_a_revision_older_than_the_draft_is_skipped(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    queue, render = RecordingQueue(), FakeRenderer()
    revision = _ask(factory, made["user"], made["document"], queue)
    with factory() as db:
        store.save_draft(db, made["user"], made["document"], CV, revision, Author.writer, at=AT)
        db.commit()
    _run(context(factory, render, AT), made["document"], revision)
    assert render.calls == []
    assert _versions(factory, made["document"]) == []
    assert _document(factory, made["document"]).render_state == RenderState.idle


def test_the_renderer_down_is_tried_once_more_then_idle_with_its_code(
    factory: sessionmaker[Session],
) -> None:
    made = _setup(factory)
    queue, render = RecordingQueue(), FakeRenderer(fail="renderer_unavailable")
    revision = _ask(factory, made["user"], made["document"], queue)
    _run(context(factory, render, AT), made["document"], revision)
    assert len(render.calls) == 2
    document = _document(factory, made["document"])
    assert (document.render_state, document.render_error) == (
        RenderState.idle,
        "renderer_unavailable",
    )
    assert render_job.MESSAGES["renderer_unavailable"] == "Preview not available right now."
    assert _status(factory, made["posting"]) == "found"
    # The next request renders it.
    render.fail = None
    revision = _ask(factory, made["user"], made["document"], queue)
    queue.jobs.clear()
    _run(context(factory, render, AT), made["document"], revision)
    assert _versions(factory, made["document"])[-1].pdf is not None


def test_a_timeout_is_not_tried_again(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    render = FakeRenderer(fail="timeout")
    revision = _ask(factory, made["user"], made["document"], RecordingQueue())
    _run(context(factory, render, AT), made["document"], revision)
    assert len(render.calls) == 1
    assert _document(factory, made["document"]).render_error == "timeout"


def test_an_overflow_is_stored_moves_nothing_and_calls_the_hook_once(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    made = _setup(factory)
    called: list[tuple[uuid.UUID, uuid.UUID]] = []

    def hook(document_id: uuid.UUID, version_id: uuid.UUID) -> None:
        called.append((document_id, version_id))

    monkeypatch.setattr(render_job, "on_overflow", hook)
    render = FakeRenderer(pages=2)
    revision = _ask(factory, made["user"], made["document"], RecordingQueue())
    _run(context(factory, render, AT), made["document"], revision)
    (version,) = _versions(factory, made["document"])
    assert (version.fit, version.pages) == (FitResult.overflow, 2)
    assert version.pdf is not None
    assert called == [(made["document"], version.id)]
    assert _status(factory, made["posting"]) == "found"


def test_the_default_hook_is_a_no_op() -> None:
    assert asyncio.run(render_job.on_overflow(uuid.uuid4(), uuid.uuid4())) is None


def test_the_status_moves_once_only_and_never_from_staged(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    render = FakeRenderer()
    revision = _ask(factory, made["user"], made["document"], RecordingQueue())
    _run(context(factory, render, AT), made["document"], revision)
    assert _status(factory, made["posting"]) == "cv_ready"
    _set_status(factory, made["posting"], "shortlisted")
    with factory() as db:
        store.save_draft(
            db,
            made["user"],
            made["document"],
            {**CV, "headline": "Two"},
            revision,
            Author.user,
            at=AT,
        )
        db.commit()
    revision = _ask(factory, made["user"], made["document"], RecordingQueue())
    _run(context(factory, render, AT), made["document"], revision)
    assert _status(factory, made["posting"]) == "shortlisted"  # not the first one page version

    with factory() as db:
        other = add_posting(db, made["user"])
        db.commit()
        second = cv_draft(db, made["user"], other, AT)
    _set_status(factory, other, "staged")
    revision = _ask(factory, made["user"], second, RecordingQueue())
    _run(context(factory, render, AT), second, revision)
    assert _status(factory, other) == "staged"
    assert _versions(factory, second)[0].fit == FitResult.one_page


def test_a_posting_locked_elsewhere_is_tried_once_more_then_skipped_and_logged(
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    made = _setup(factory)
    monkeypatch.setattr(jobs_service, "LOCK_TIMEOUT_MS", 30)
    tries: list[int] = []
    real = jobs_service.lock_job

    def counted(*args: Any, **kwargs: Any) -> Any:
        tries.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(jobs_service, "lock_job", counted)
    holder = factory()
    holder.execute(sa.text("SELECT id FROM jobs WHERE id = :j FOR UPDATE"), {"j": made["posting"]})
    try:
        revision = _ask(factory, made["user"], made["document"], RecordingQueue())
        with caplog.at_level(logging.WARNING, logger="app.documents.render"):
            _run(context(factory, FakeRenderer(), AT), made["document"], revision)
    finally:
        holder.rollback()
        holder.close()
    assert len(tries) == 2
    assert "documents.render.status_skipped" in caplog.text
    assert _versions(factory, made["document"])[0].pdf is not None
    assert _status(factory, made["posting"]) == "found"
    assert _document(factory, made["document"]).render_state == RenderState.idle


def test_a_frozen_version_is_never_rendered_again(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    render = FakeRenderer()
    revision = _ask(factory, made["user"], made["document"], RecordingQueue())
    _run(context(factory, render, AT), made["document"], revision)
    (version,) = _versions(factory, made["document"])
    with factory() as db:
        store.freeze(db, made["user"], version.id, FreezeReason.marked_sent, AT)
        db.commit()
    revision = _ask(factory, made["user"], made["document"], RecordingQueue())
    _run(context(factory, render, AT), made["document"], revision)
    assert len(render.calls) == 1
    (again,) = _versions(factory, made["document"])
    assert bytes(again.pdf or b"") == bytes(version.pdf or b"")
    assert again.frozen_at is not None


def test_a_worker_stopped_mid_render_leaves_it_interrupted(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    revision = _ask(factory, made["user"], made["document"], RecordingQueue())

    class Stop(FakeRenderer):
        async def __call__(self, html: str) -> Any:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _run(context(factory, Stop(), AT), made["document"], revision)
    document = _document(factory, made["document"])
    assert (document.render_state, document.render_error) == (RenderState.idle, "interrupted")
    # A process that died: at the next start.
    with factory() as db:
        db.execute(
            sa.update(Document).values(render_state=RenderState.rendering, render_error=None)
        )
        db.commit()
    assert asyncio.run(render_job.reset_interrupted_at_start({"sessions": factory})) == 1
    assert _document(factory, made["document"]).render_error == "interrupted"


def test_a_lost_render_is_queued_again_by_the_cron(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    with factory() as db:
        found, _ = render_job.mark_queued(db, made["user"], made["document"])
        revision = found.draft_revision
        db.commit()

    class Pool:
        def __init__(self) -> None:
            self.queue = RecordingQueue()

        async def enqueue_job(
            self, function: str, *args: Any, _job_id: str, _queue_name: str
        ) -> Any:
            fresh = await self.queue.enqueue(function, *args, queue=_queue_name, job_id=_job_id)
            return object() if fresh else None

    pool = Pool()
    ctx = {"sessions": factory, "redis": pool}
    assert asyncio.run(render_job.resend_queued(ctx)) == 1
    assert asyncio.run(render_job.resend_queued(ctx)) == 0  # its job is there: not twice
    assert pool.queue.jobs[0]["job_id"] == queues.render_job_id(made["document"], revision)


def test_queue_render_for_the_writer(factory: sessionmaker[Session]) -> None:
    made = _setup(factory)
    queue = RecordingQueue()
    revision = _document(factory, made["document"]).draft_revision
    assert asyncio.run(
        render_job.queue_render(made["document"], revision, sessions=factory, queue=queue)
    )
    assert not asyncio.run(
        render_job.queue_render(made["document"], revision, sessions=factory, queue=queue)
    )
    assert not asyncio.run(
        render_job.queue_render(made["document"], revision - 1, sessions=factory, queue=queue)
    )
    assert len(queue.jobs) == 1


def test_a_person_waits_with_a_bounded_number_of_renders(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_job, "QUEUED_PER_USER", 1)
    made = _setup(factory)
    with factory() as db:
        other = add_posting(db, made["user"])
        db.commit()
        second = cv_draft(db, made["user"], other, AT)
    _ask(factory, made["user"], made["document"], RecordingQueue())
    with factory() as db, pytest.raises(render_job.RenderQueueFullError):
        render_job.mark_queued(db, made["user"], second)
    with factory() as db:  # another person is not counted
        user_b = add_user(db, USER_B)
        posting_b = add_posting(db, user_b)
        db.commit()
        document_b = cv_draft(db, user_b, posting_b, AT)
        assert render_job.mark_queued(db, user_b, document_b)[1]


def test_the_canary_never_reaches_logs_arq_arguments_or_redis(
    factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    planted = marker()
    content = copy.deepcopy(CV)
    content["experience"][0]["bullets"][1] = f"Ran {planted} across the platform."
    made = _setup(factory, content)
    queue, render = RecordingQueue(), FakeRenderer(fail=None)
    with caplog.at_level(logging.DEBUG):
        revision = _ask(factory, made["user"], made["document"], queue)
        _run(context(factory, render, AT), made["document"], revision)
        render.fail = "render_failed"
        with factory() as db:
            store.save_draft(
                db,
                made["user"],
                made["document"],
                content | {"headline": "B"},
                revision,
                Author.user,
                at=AT,
            )
            db.commit()
        revision = _ask(factory, made["user"], made["document"], queue)
        _run(context(factory, render, AT), made["document"], revision)
    assert any(planted in html for html in render.calls)  # it was rendered
    redis_client = None
    url = os.environ.get("REDIS_URL")
    if url:
        import redis

        redis_client = redis.Redis.from_url(url)

        async def through_redis() -> None:
            real = queues.Queue(url)
            try:
                await render_job.enqueue(real, made["document"], revision + 100)
            finally:
                await real.close()

        asyncio.run(through_redis())
    try:
        assert (
            find(
                [planted],
                logs=caplog.records,
                values=[job["args"] for job in queue.jobs],
                redis=redis_client,
            )
            == []
        )
    finally:
        if redis_client is not None:
            redis_client.delete(f"arq:job:{queues.render_job_id(made['document'], revision + 100)}")
            redis_client.close()
