"""Make it fit, `POST /api/documents/{id}/fit`, through the app, two e2e people (SPEC-documents.md,
"Templates", Switching; "API"). The shortening of a posting CV's latest version, a template
switch's included (the automatic pass leaves that one alone); a frozen version untouched and the
shorter one on top; the checks of every model call, each refused with its sentence and nothing
created: the consent, a credential, a write to draw on, a known spend, a ceiling left, a free
run; clicks one after another sharing one budget; one open at a time; a letter, a base CV and
another person's document. The writer's own run is the fake backend's, in `test_writer_cv.py`
and at the end of this file."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials, runs
from app.agents.models import AgentRun, RunStatus
from app.config import get_settings
from app.documents import auto_router, consent, generate, render_job, store
from app.documents import content as c
from app.documents.models import (
    Author,
    DocumentGeneration,
    DocumentVersion,
    FitResult,
    FreezeReason,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Kind,
    Origin,
)
from tests.documents import writer_support as writers
from tests.documents.render_support import CV, FakeRenderer, RecordingQueue, context, pdf_of
from tests.documents.store_support import AT
from tests.documents.test_auto_api import Who, queue
from tests.identity.api_helpers import Api
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url", "queue"]

ALEX = "e2e-docs-fit-alex@example.org"
BLAKE = "e2e-docs-fit-blake@example.org"
CEILING = 100_000
SPENT = (30_000, 5_000)  # the CV run's input and output tokens
LEFT = CEILING - sum(SPENT)


def fit(document: uuid.UUID) -> str:
    return f"/api/documents/{document}/fit"


@pytest.fixture
def alex(factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue) -> Who:
    who = Who(factory, api, ALEX, clock)
    who.credential()
    return who


@pytest.fixture
def blake(factory: sessionmaker[Session], api: Api, clock: Any) -> Who:
    who = Who(factory, api, BLAKE, clock)
    who.credential()
    return who


def _counts(who: Who, run_id: uuid.UUID, spent: tuple[int | None, int | None]) -> None:
    with who.factory() as db:
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(status=RunStatus.done, input_tokens=spent[0], output_tokens=spent[1])
        )
        db.commit()


def _latest(who: Who, document: uuid.UUID) -> DocumentVersion:
    with who.factory() as db:
        found = db.scalars(
            sa.select(DocumentVersion)
            .where(DocumentVersion.document_id == document)
            .order_by(DocumentVersion.number.desc())
            .limit(1)
        ).one()
        db.expunge(found)
        return found


def draw(who: Who, document: uuid.UUID, result: FitResult = FitResult.overflow) -> int:
    """The latest version's page, drawn as the render job stores it: its number."""
    number = _latest(who, document).number
    pages = 2 if result is FitResult.overflow else 1
    with who.factory() as db:
        store.attach_pdf(
            db, who.user, document, number, pdf_of(pages), fit=result, fit_step=None, pages=pages
        )
        db.commit()
    return number


def written(
    who: Who,
    job: uuid.UUID,
    *,
    spent: tuple[int | None, int | None] = SPENT,
    result: FitResult = FitResult.overflow,
) -> uuid.UUID:
    """The posting's CV as a writer's `posting` generation left it, its run's counts `spent`, its
    version drawn with `result`."""
    with who.factory() as db:
        document = store.get_or_create(db, who.user, job, Kind.cv, at=who.clock())
        generation = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.click,
            None,
            {"low": 1, "high": 2},
            CEILING,
            at=who.clock(),
        )
        run = generate.start(db, generation, at=who.clock())
        generate.finish(db, generation, c.parse("cv", CV), [], at=who.clock())
        db.commit()
        document_id, run_id = document.id, run.id
    _counts(who, run_id, spent)
    draw(who, document_id, result)
    return document_id


def shortenings(who: Who) -> list[DocumentGeneration]:
    with who.factory() as db:
        return list(
            db.scalars(
                sa.select(DocumentGeneration)
                .where(
                    DocumentGeneration.user_id == who.user,
                    DocumentGeneration.mode == GenerationMode.shorten,
                )
                .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
            )
        )


def _finish(who: Who, generation_id: uuid.UUID, spent: tuple[int | None, int | None]) -> None:
    """What the writer's final hook does with a shortening's result, its run's counts `spent`."""
    with who.factory() as db:
        generation = db.get(DocumentGeneration, generation_id)
        assert generation is not None
        assert generation.run_id is not None
        shorter = c.parse("cv", {**CV, "summary": "Platform engineer."})
        generate.finish(db, generation, shorter, [], at=who.clock())
        db.commit()
        run_id = generation.run_id
    _counts(who, run_id, spent)


def test_make_it_fit_starts_the_shortening_of_the_latest_version(
    alex: Who, api: Api, queue: RecordingQueue
) -> None:
    document = written(alex, alex.posting(80))
    queue.jobs.clear()
    response = api.request("POST", fit(document), token=alex.token)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "private, no-store"
    [made] = shortenings(alex)
    assert response.json() == {
        "id": str(made.id),
        "status": "running",
        "wait_reason": None,
        "error": None,
    }
    assert (made.trigger, made.base_document_id) == (GenerationTrigger.click, alex.base)
    # What the CV's write left after its own run.
    assert made.token_ceiling == LEFT
    assert [(j["function"], j["args"]) for j in queue.jobs] == [("run_agent", (str(made.run_id),))]
    with alex.factory() as db:
        run = db.get(AgentRun, made.run_id)
        assert run is not None
        assert (run.input["mode"], run.input["token_budget"]) == ("shorten", LEFT)
        assert isinstance(run.input["long_fields"], list)


def test_a_version_made_by_a_template_switch_is_shortened(alex: Who, api: Api, clock: Any) -> None:
    document = written(alex, alex.posting(80), result=FitResult.one_page)
    switched = api.request(
        "PATCH", f"/api/documents/{document}", token=alex.token, json={"template_slug": "compact"}
    )
    assert switched.status_code == 200, switched.text
    draw(alex, document)
    latest = _latest(alex, document)
    assert (latest.origin, latest.generation_id, latest.fit) == (
        Origin.template,
        None,
        FitResult.overflow,
    )
    # The automatic pass follows a writer's version only: it leaves this one alone.
    assert generate.shorten(alex.factory, document, latest.id, clock(), get_settings()) is None
    assert shortenings(alex) == []

    response = api.request("POST", fit(document), token=alex.token)
    assert response.status_code == 202, response.text
    [made] = shortenings(alex)
    assert made.base_draft_revision == latest_draft_revision(alex, document)
    with alex.factory() as db:
        assert db.get(AgentRun, made.run_id).input["mode"] == "shorten"  # type: ignore[union-attr]


def latest_draft_revision(who: Who, document: uuid.UUID) -> int:
    with who.factory() as db:
        found = store.get(db, who.user, document)
        assert found is not None
        return found.draft_revision


def test_a_frozen_latest_version_is_untouched_and_the_shorter_one_goes_on_top(
    alex: Who, api: Api
) -> None:
    document = written(alex, alex.posting(80))
    sent = _latest(alex, document)
    with alex.factory() as db:
        store.freeze(db, alex.user, sent.id, FreezeReason.marked_sent, alex.clock())
        db.commit()
    frozen = _latest(alex, document)
    assert frozen.frozen_at is not None

    assert api.request("POST", fit(document), token=alex.token).status_code == 202
    [made] = shortenings(alex)
    _finish(alex, made.id, (1_000, 500))

    with alex.factory() as db:
        versions = db.scalars(
            sa.select(DocumentVersion)
            .where(DocumentVersion.document_id == document)
            .order_by(DocumentVersion.number)
        ).all()
        kept = next(v for v in versions if v.id == frozen.id)
        assert (kept.frozen_at, kept.pdf_sha256, kept.content_hash, kept.fit) == (
            frozen.frozen_at,
            frozen.pdf_sha256,
            frozen.content_hash,
            frozen.fit,
        )
        top = versions[-1]
        assert top.number == frozen.number + 1
        assert (top.generation_id, top.origin, top.frozen_at) == (made.id, Origin.generated, None)
        assert top.content["summary"] == "Platform engineer."


def test_clicks_one_after_another_share_one_budget(alex: Who, api: Api) -> None:
    document = written(alex, alex.posting(80))
    assert api.request("POST", fit(document), token=alex.token).status_code == 202
    [first] = shortenings(alex)
    _finish(alex, first.id, (20_000, 4_000))
    draw(alex, document)  # still too long

    assert api.request("POST", fit(document), token=alex.token).status_code == 202
    [second] = [g for g in shortenings(alex) if g.id != first.id]
    assert second.token_ceiling == LEFT - 24_000
    # The second pass spends all that is left: a third click has nothing to draw on.
    _finish(alex, second.id, (LEFT - 24_000, 0))
    draw(alex, document)
    refused = api.request("POST", fit(document), token=alex.token)
    assert refused.status_code == 409
    assert refused.json()["detail"] == auto_router.FIT_REFUSED["token_ceiling"][1]
    assert len(shortenings(alex)) == 2


def test_a_pass_with_no_count_leaves_no_known_budget(alex: Who, api: Api) -> None:
    document = written(alex, alex.posting(80))
    assert api.request("POST", fit(document), token=alex.token).status_code == 202
    [first] = shortenings(alex)
    _finish(alex, first.id, (None, 400))
    draw(alex, document)
    refused = api.request("POST", fit(document), token=alex.token)
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        auto_router.FIT_REFUSED["spend_unknown"][1],
    )


def test_a_second_click_while_one_is_open_is_409(alex: Who, api: Api) -> None:
    document = written(alex, alex.posting(80))
    assert api.request("POST", fit(document), token=alex.token).status_code == 202
    again = api.request("POST", fit(document), token=alex.token)
    assert (again.status_code, again.json()["detail"]) == (409, auto_router.RUNNING)
    assert len(shortenings(alex)) == 1


def _no_consent(who: Who) -> None:
    with who.factory() as db:
        db.execute(sa.text("DELETE FROM document_consents WHERE user_id = :u"), {"u": who.user})
        db.commit()


def _no_credential(who: Who) -> None:
    with who.factory() as db:
        assert credentials.delete(db, who.user)
        db.commit()


def _busy(who: Who) -> None:
    with who.factory() as db:
        for _ in range(runs.ACTIVE_PER_USER):
            runs.create_run(db, who.user, "documents_cv", None, {}, "m", who.clock())
        db.commit()


def _hand_written(who: Who, job: uuid.UUID) -> uuid.UUID:
    """A CV the writer never wrote: a draft saved by hand, drawn overflowing."""
    with who.factory() as db:
        document = store.get_or_create(db, who.user, job, Kind.cv, at=who.clock())
        store.save_draft(db, who.user, document.id, CV, 1, Author.user, at=who.clock())
        store.snapshot(db, who.user, document.id, Origin.edited, at=who.clock())
        db.commit()
        document_id = document.id
    draw(who, document_id)
    return document_id


def _edited_after(who: Who, job: uuid.UUID) -> uuid.UUID:
    """The writer's CV, drawn overflowing, then saved by hand: its page is not drawn yet."""
    document = written(who, job)
    with who.factory() as db:
        found = store.get(db, who.user, document)
        assert found is not None
        store.save_draft(
            db,
            who.user,
            document,
            {**CV, "summary": "Edited by hand."},
            found.draft_revision,
            Author.user,
            at=who.clock(),
        )
        db.commit()
    return document


@pytest.mark.parametrize(
    "reason",
    [
        "consent",
        "credential",
        "not_written",
        "spend_unknown_input",
        "spend_unknown_output",
        "token_ceiling",
        "token_ceiling_over",
        "runs_busy",
        "fits",
        "not_drawn_edited",
        "not_drawn_switched",
    ],
)
def test_each_check_refuses_with_its_sentence_and_creates_nothing(
    alex: Who, api: Api, queue: RecordingQueue, reason: str
) -> None:
    job = alex.posting(80)
    expected = reason.removesuffix("_input").removesuffix("_output").removesuffix("_over")
    expected = expected.removesuffix("_edited").removesuffix("_switched")
    if reason == "not_written":
        document = _hand_written(alex, job)
    elif reason == "spend_unknown_input":
        document = written(alex, job, spent=(None, 400))
    elif reason == "spend_unknown_output":
        document = written(alex, job, spent=(400, None))
    elif reason == "token_ceiling":
        document = written(alex, job, spent=(CEILING - 1_000, 1_000))
    elif reason == "token_ceiling_over":
        document = written(alex, job, spent=(CEILING, 1))
    elif reason == "fits":
        document = written(alex, job, result=FitResult.one_page)
    elif reason == "not_drawn_edited":
        document = _edited_after(alex, job)
    elif reason == "not_drawn_switched":
        document = written(alex, job)
        switched = api.request(
            "PATCH",
            f"/api/documents/{document}",
            token=alex.token,
            json={"template_slug": "banner"},
        )
        assert switched.status_code == 200
    else:
        document = written(alex, job)
        {"consent": _no_consent, "credential": _no_credential, "runs_busy": _busy}[reason](alex)
    queue.jobs.clear()
    response = api.request("POST", fit(document), token=alex.token)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == auto_router.FIT_REFUSED[expected][1]
    assert response.headers["cache-control"] == "private, no-store"
    assert shortenings(alex) == []
    assert [j for j in queue.jobs if j["function"] == "run_agent"] == []


def test_one_token_left_is_a_pass_of_one_token(alex: Who, api: Api) -> None:
    document = written(alex, alex.posting(80), spent=(CEILING - 1_001, 1_000))
    assert api.request("POST", fit(document), token=alex.token).status_code == 202
    assert [g.token_ceiling for g in shortenings(alex)] == [1]


def test_a_letter_and_a_base_cv_have_no_shortening_pass(alex: Who, api: Api) -> None:
    job = alex.posting(80)
    with alex.factory() as db:
        letter = store.get_or_create(db, alex.user, job, Kind.letter, at=alex.clock()).id
        db.commit()
    for document in (letter, alex.base):
        refused = api.request("POST", fit(document), token=alex.token)
        assert (refused.status_code, refused.json()["detail"]) == (
            422,
            auto_router.FIT_REFUSED["not_shortened"][1],
        )
    assert shortenings(alex) == []


def test_another_persons_document_is_404_and_untouched(alex: Who, blake: Who, api: Api) -> None:
    document = written(alex, alex.posting(80))
    for token, target in ((blake.token, document), (alex.token, uuid.uuid4())):
        response = api.request("POST", fit(target), token=token)
        assert (response.status_code, response.json()["detail"]) == (
            404,
            auto_router.DOCUMENT_NOT_FOUND,
        )
    assert shortenings(alex) == []
    assert shortenings(blake) == []


def test_every_refusal_has_a_sentence_without_dashes() -> None:
    assert set(auto_router.FIT_REFUSED) == set(generate.FIT_REFUSALS)
    for code, sentence in auto_router.FIT_REFUSED.values():
        assert code in (409, 422)
        assert sentence.endswith(".")
        assert not any(dash in sentence for dash in ("—", "–", " - "))


def test_the_posting_card_reads_the_running_shortening(alex: Who, api: Api) -> None:
    job = alex.posting(80)
    document = written(alex, job)
    assert api.request("POST", fit(document), token=alex.token).status_code == 202
    assert [g.status for g in shortenings(alex)] == [GenerationStatus.running]
    answer = api.get(f"/api/jobs/{job}/documents", token=alex.token)
    [cv] = [d for d in answer.json()["documents"] if d["kind"] == "cv"]
    assert cv["generation"]["status"] == "running"


def test_the_writer_shortens_a_switched_cv_and_the_sent_version_stays(
    factory: sessionmaker[Session], migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path on the fake backend: a writer's CV on one page, a switch to `compact` that
    overflows (the automatic pass leaves it), the version sent, then Make it fit's run."""
    who = writers.person(factory, migrated_url, "e2e-docs-fit-run@example.org")
    monkeypatch.setattr(generate, "RUNTIME", who.runtime())
    with factory() as db:
        consent.record(db, who.user, AT)
        db.commit()
    document, _, _ = writers.write(who, "documents_cv_overflow")
    revision = writers.document_row(factory, document).draft_revision
    asyncio.run(
        render_job.render_document(
            context(factory, FakeRenderer(pages=1), AT), str(document), revision
        )
    )
    with factory() as db:
        render_job.switch(db, who.user, document, at=AT, template_slug="compact")
        render_job.mark_edited(db, who.user, document)
        db.commit()
    asyncio.run(
        render_job.render_document(
            context(factory, FakeRenderer(pages=2), AT), str(document), revision
        )
    )
    *_, switched = writers.versions(factory, document)
    assert (switched.origin, switched.template_slug, switched.fit) == (
        Origin.template,
        "compact",
        FitResult.overflow,
    )
    with factory() as db:
        assert (
            db.scalars(
                sa.select(DocumentGeneration.id).where(
                    DocumentGeneration.document_id == document,
                    DocumentGeneration.mode == GenerationMode.shorten,
                )
            ).all()
            == []
        )
        store.freeze(db, who.user, switched.id, FreezeReason.marked_sent, AT)
        generation, run = generate.make_fit(db, who.user, document, at=AT, settings=who.settings)
        db.commit()
        generation_id, run_id = generation.id, run.id
    writers.execute(who, run_id)

    *_, sent, top = writers.versions(factory, document)
    assert (sent.id, sent.frozen_at, sent.pdf_sha256) == (
        switched.id,
        AT,
        switched.pdf_sha256,
    )
    assert sent.content_hash == switched.content_hash
    assert (top.number, top.origin, top.generation_id, top.template_slug) == (
        sent.number + 1,
        Origin.generated,
        generation_id,
        "compact",
    )
    assert writers.generation_row(factory, generation_id).status is GenerationStatus.done
    assert len(writers.document_row(factory, document).draft["summary"]) <= 330
