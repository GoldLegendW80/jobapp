"""Helpers of the tests of the transcription step (ingestion G14): a document that waits for
its transcription with its page images, an ingestion run with one running transcription step and
its agent run, created directly; the step's tools, built as the runner builds them; and the
builders of fake streams. Every document here is made up: no real person, no real address."""

import asyncio
import base64
import hashlib
import io
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions, runner, runs
from app.agents import tools as app_tools
from app.agents.models import AgentRun
from app.ingestion import agents, plan
from app.ingestion.models import IngestionDocument, IngestionImage, TextSource, TextStatus
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, StepStatus
from tests.agents.run_helpers import now
from tests.ingestion import engine_helpers, extract_helpers
from tests.ingestion.extract_helpers import Step

VIEW = "mcp__app__view_image"
STORE = "mcp__app__store_transcription"
WIDTH, HEIGHT = 64, 48


def jpeg(seed: int = 0) -> bytes:
    """A small real JPEG, different for each seed."""
    out = io.BytesIO()
    colour = (seed * 37 % 256, seed * 91 % 256, 200)
    Image.new("RGB", (WIDTH, HEIGHT), colour).save(out, "JPEG", quality=80)
    return out.getvalue()


def add_scan(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    pages: Sequence[str | None],
    *,
    digest: bytes | None = None,
) -> uuid.UUID:
    """A document as G8 stores it: one slot per page, a text for a page read as text, None for
    a page stored as an image (its slot is empty and its image waits at that position)."""
    text = "\f".join(page or "" for page in pages)
    images = [index for index, page in enumerate(pages) if page is None]
    read_as_text = len(pages) - len(images)
    with factory() as db, db.begin():
        document = IngestionDocument(
            id=uuid.uuid4(),
            user_id=user_id,
            name="scan.pdf",
            mime="application/pdf",
            size=1000,
            sha256=digest or hashlib.sha256(uuid.uuid4().bytes).digest(),
            text=text,
            text_source=TextSource.parsed if read_as_text else TextSource.transcribed,
            text_status=TextStatus.awaiting_transcription,
            pages=len(pages),
            pages_text=read_as_text,
            pages_image=len(images),
            parser_version=2,
        )
        db.add(document)
        db.flush()
        for position in images:
            db.add(
                IngestionImage(
                    document_id=document.id,
                    user_id=user_id,
                    position=position,
                    jpeg=jpeg(position),
                    width=WIDTH,
                    height=HEIGHT,
                )
            )
        return document.id


def add_transcribe_step(
    factory: sessionmaker[Session],
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    positions: Sequence[int],
    *,
    script: str | None = None,
    budget: int = 1_000_000,
    after: Step | None = None,
) -> Step:
    """An ingestion run with one transcription step that is running, and the step's agent run,
    as the engine creates them. `after`: one more step of that step's ingestion run."""
    with factory() as db, db.begin():
        sha256 = db.execute(
            sa.select(IngestionDocument.sha256).where(IngestionDocument.id == document_id)
        ).scalar_one()
        if after is None:
            run = IngestionRun(
                user_id=user_id,
                document_ids=[document_id],
                base_revision=0,
                deadline_at=now() + timedelta(hours=1),
                estimate_low=1_000,
                estimate_high=2_000,
                token_ceiling=4_000,
            )
            db.add(run)
            db.flush()
            ingestion_run_id, position = run.id, 0
        else:
            ingestion_run_id = after.ingestion_run_id
            position = 1 + int(
                db.execute(
                    sa.select(sa.func.max(IngestionRunStep.position)).where(
                        IngestionRunStep.ingestion_run_id == ingestion_run_id
                    )
                ).scalar_one()
            )
        step = IngestionRunStep(
            ingestion_run_id=ingestion_run_id,
            position=position,
            kind=plan.StepKind.transcribe,
            document_id=document_id,
            sha256=sha256,
            image_positions=list(positions),
            identity_hash=f"{position:064x}",
            status=StepStatus.running,
            token_budget=budget,
        )
        db.add(step)
        db.flush()
        raw: dict[str, Any] = {
            "ingestion_run_id": str(ingestion_run_id),
            "step_id": str(step.id),
            "token_budget": budget,
        }
        if script is not None:
            raw["script"] = script
        agent_run = runs.create_run(
            db,
            user_id,
            agents.TRANSCRIBE_NAME,
            None,
            agents.TRANSCRIBE.validate_input(raw),
            definitions.DEFAULT_MODEL,
            now(),
        )
        step.agent_run_id = agent_run.id
        return Step(user_id, document_id, ingestion_run_id, step.id, agent_run.id)


def binding_of(factory: sessionmaker[Session], step: Step) -> dict[str, Any] | None:
    """What the agent's `bind` hook answers for the step's agent run, now."""
    with factory() as db:
        found = agents.bind_transcription(db, db.get_one(AgentRun, step.agent_run_id))
        return None if found is None else dict(found)


def tools_for(
    factory: sessionmaker[Session],
    step: Step,
    *,
    user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    binding: dict[str, Any] | None = None,
) -> app_tools.AppTools:
    """The step's tools as the runner builds them: bound, the binding frozen."""
    bound = binding if binding is not None else binding_of(factory, step)
    assert bound is not None
    return app_tools.build(
        factory,
        user_id or step.user_id,
        agents.TRANSCRIBE.tools,
        run_id=run_id or step.agent_run_id,
        binding=runner.frozen(bound),
    )


def view(tools: app_tools.AppTools, position: Any, **extra: Any) -> dict[str, Any]:
    """The whole result of `view_image`, as the model's client receives it."""
    handler = tools.by_name[VIEW].handler
    return dict(asyncio.run(handler({"position": position, **extra})))


def shown(result: dict[str, Any]) -> bytes:
    """The bytes of the one image block of a result."""
    (block,) = result["content"]
    assert (block["type"], block["mimeType"]) == ("image", "image/jpeg")
    return base64.b64decode(block["data"])


def store(tools: app_tools.AppTools, position: Any, text: Any, **extra: Any) -> tuple[Any, bool]:
    return extract_helpers.call(tools, "store_transcription", position=position, text=text, **extra)


def transcribe(tools: app_tools.AppTools, position: int, text: str) -> tuple[Any, bool]:
    """View then store, as the prompt asks."""
    assert "is_error" not in view(tools, position)
    return store(tools, position, text)


def images_of(factory: sessionmaker[Session], document_id: uuid.UUID) -> dict[int, str | None]:
    """The text of each image still stored for the document, by position."""
    with factory() as db:
        rows = db.execute(
            sa.select(IngestionImage.position, IngestionImage.text).where(
                IngestionImage.document_id == document_id
            )
        ).all()
        return {int(position): text for position, text in rows}


def document_of(factory: sessionmaker[Session], document_id: uuid.UUID) -> Any:
    with factory() as db:
        return db.execute(
            sa.select(
                IngestionDocument.text,
                IngestionDocument.text_status,
                IngestionDocument.text_source,
                IngestionDocument.transcribed_pages,
                IngestionDocument.secret_warning,
                IngestionDocument.truncated,
            ).where(IngestionDocument.id == document_id)
        ).one()


# --- a run planned at 60 steps ------------------------------------------------------------

PARSED_CHARS = 96_000  # the parsed page of the mixed PDF: one part, under a part's limit
LETTER = "A page of a made-up letter."


def sixty_steps(factory: sessionmaker[Session], user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A run planned at exactly 60 steps: a mixed PDF (parsed text and one scanned page) first,
    then 29 scans of one image each. Each document is a transcription and a predicted
    extraction. Returns the run and the mixed PDF."""
    mixed = add_scan(factory, user_id, ["word " * (PARSED_CHARS // 5), None])
    scans = [add_scan(factory, user_id, [None]) for _ in range(29)]
    run_id = engine_helpers.start(factory, user_id, [mixed, *scans])
    assert len(engine_helpers.steps_of(factory, run_id)) == plan.RUN_MAX_STEPS
    return run_id, mixed


def scanned_page(characters: int) -> str:
    """The text of the mixed PDF's scanned page, `characters` long."""
    return "word " * (characters // 5)


def transcribed_at_once(
    factory: sessionmaker[Session], run_id: uuid.UUID, mixed: uuid.UUID, page: str
) -> None:
    """Every transcription step of the run done and every document holding its whole text, as
    if the transcriptions had run: for the reads of `next_step`, which writes nothing."""
    with factory() as db, db.begin():
        for step in db.scalars(
            sa.select(IngestionRunStep).where(
                IngestionRunStep.ingestion_run_id == run_id,
                IngestionRunStep.kind == plan.StepKind.transcribe,
            )
        ):
            step.status = StepStatus.done
            whole = (
                "word " * (PARSED_CHARS // 5) + "\f" + page if step.document_id == mixed else LETTER
            )
            db.execute(
                sa.update(IngestionDocument)
                .where(IngestionDocument.id == step.document_id)
                .values(text=whole, text_status=TextStatus.done)
            )


def read_until_closed(
    factory: sessionmaker[Session],
    settings: Any,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    mixed: uuid.UUID,
    page: str,
    *,
    stop_at_extraction_of: uuid.UUID | None = None,
) -> None:
    """Drive the run by hand, with no model: a transcription step stores
    its pages (`page` for the mixed PDF) and its agent run ends, an extraction step's agent run
    ends, 10 tokens each. Stops when the run is no longer running, or when the extraction of
    `stop_at_extraction_of` has started."""
    moved = engine_helpers.advance(factory, run_id, settings)
    while moved.enqueue is not None:
        step = engine_helpers.running(factory, run_id)
        if step.kind == plan.StepKind.extract:
            if step.document_id == stop_at_extraction_of:
                return
        else:
            attempt = Step(user_id, step.document_id, run_id, step.id, moved.enqueue)  # type: ignore[arg-type]
            tools = tools_for(factory, attempt)
            for position in step.image_positions or ():
                transcribe(tools, position, page if step.document_id == mixed else LETTER)
        engine_helpers.end_agent(factory, moved.enqueue, tokens=10)
        moved = engine_helpers.advance(factory, run_id, settings)


# --- fake streams ---------------------------------------------------------------------------------


def view_call(number: int, position: Any, **extra: Any) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": f"toolu_transcribe_{number:03d}v",
        "name": VIEW,
        "input": {"position": position, **extra},
    }


def store_call(number: int, position: Any, text: Any, **extra: Any) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": f"toolu_transcribe_{number:03d}s",
        "name": STORE,
        "input": {"position": position, "text": text, **extra},
    }


def stream(description: str, *messages: tuple[dict[str, Any], ...] | str) -> dict[str, Any]:
    """A whole stream of a transcription run: one tuple of blocks per request that calls tools,
    then the one line of the final answer."""
    body = extract_helpers.stream(description, *messages)
    body["steps"][0]["message"]["tools"] = [VIEW, STORE]
    return body
