"""D25b: the design capture of an uploaded CV, end to end on the fake backend (no model, no key)
with the parser and the renderer played by fakes (the real renderer's check is
test_design_check_docker.py): a capture stored as the person's user template and the base CV's
new version, a repair, a refusal by the rules and by the render check (its template kept), a
fidelity pass that breaks the rules (the accepted template kept), the capture run twice, a DOCX
and a scanned PDF, the file's bytes gone, waiting for the credential, A's template never
resolved for B, a row tampered with, the secret rules on the layout's texts, the grey photo
boxes, and the canary of the file. Real Postgres at head."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import credentials, failures
from app.agents.models import AgentRun, CredentialKind, RunStatus
from app.documents import (
    base_router,
    design_capture,
    design_check,
    fidelity,
    render_client,
    render_job,
    store,
    template_rules,
    writer_base,
    writer_design_tools,
)
from app.documents import photo as photos
from app.documents import templates as template_system
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    DocumentUserTemplate,
    GenerationMode,
    GenerationStatus,
    GenerationWait,
    Origin,
)
from app.documents.template_css import Problem
from app.ingestion import parser_client
from app.ingestion.parser_client import (
    DesignDocx,
    DesignFont,
    DesignPage,
    DesignPdf,
    PageImage,
    Parsed,
    Shape,
    TextRun,
)
from app.profile import photo as profile_photo
from app.profile import service as profile_service
from tests.agents.conftest import API_KEY
from tests.agents.g6_helpers import everything_stored
from tests.documents.canary import find, marker
from tests.documents.conftest import load
from tests.documents.render_support import FakeRenderer
from tests.documents.store_support import AT, USER_A, USER_B, db, engine, factory, migrated_url
from tests.documents.test_writer_base_upload import DOCX, MORGAN, _morgan_converts, add_upload
from tests.documents.writer_support import (
    Person,
    document_row,
    execute,
    generation_row,
    person,
    versions,
)

__all__ = ["db", "engine", "factory", "migrated_url"]

PDF = b"%PDF-1.7\n% an invented CV, never parsed here: the parser is a fake\n"


def jpeg(width: int = 300, height: int = 424) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(out, format="JPEG")
    return out.getvalue()


def design(
    *texts: str, scanned: bool = False, pages: int = 2, headings: tuple[str, ...] = ()
) -> DesignPdf:
    """A page of `texts` in one body style, then `headings` as section headings (capitals, at
    the body's size, met twice or more, below the header: design_spec's role)."""
    runs = tuple(
        TextRun(text, (600, 600 + 200 * n, 3000, 720 + 200 * n), 0, 100, "#1d232b")
        for n, text in enumerate(texts)
    ) + tuple(
        TextRun(text, (600, 3000 + 600 * n, 3000, 3120 + 600 * n), 0, 100, "#1d232b", caps=True)
        for n, text in enumerate(headings)
    )
    page = DesignPage(
        index=0,
        width=5953,
        height=8419,
        margins=(567, 567, 567, 567),
        runs=() if scanned else runs,
        shapes=(Shape("fill", (0, 0, 5953, 900), "#1f4e79", 0),),
        images=((4500, 300, 5400, 1200),),
    )
    font = DesignFont("ArialMT", 400, False, False, False, False, True)
    return DesignPdf(pages, scanned, False, (), (font,), (page,))


class Services:
    """The parser and the render check, as fakes; what each was asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, layout: DesignPdf | DesignDocx) -> None:
        self.layout = layout
        self.checks = 0
        self.problems: list[Problem] = []
        self.asked: list[str] = []

        async def read_design(kind: str, data: bytes, **_: Any) -> Any:
            self.asked.append(kind)
            return self.layout

        async def parse(kind: str, data: bytes, options: Any = None, **_: Any) -> Parsed:
            self.asked.append(kind)
            return Parsed(images=(PageImage(0, 300, 424, jpeg()),))

        async def check_template(template: Any, check: Any = None) -> list[Problem]:
            self.checks += 1
            return list(self.problems)

        monkeypatch.setattr(parser_client, "read_design", read_design)
        monkeypatch.setattr(parser_client, "parse", parse)
        monkeypatch.setattr(design_check, "check_template", check_template)
        self.renderer = FakeRenderer()
        monkeypatch.setattr(render_client, "render", self.renderer)


def uploaded_base(who: Person, raw: bytes | None = PDF, mime: str = "application/pdf") -> uuid.UUID:
    upload = add_upload(who.factory, who.user, load("upload_morgan.txt"), mime=mime)
    with who.factory() as db:
        if raw is not None:
            db.execute(
                sa.text("UPDATE ingestion_documents SET bytes = :b WHERE id = :i"),
                {"b": raw, "i": upload},
            )
        base = store.create_base(
            db,
            who.user,
            "Uploaded",
            BaseSource.uploaded,
            at=AT,
            content=load("base_cv_alex.json"),
            source_document_id=upload,
        )
        db.commit()
        return base.id


def capture(who: Person, base: uuid.UUID, script: str) -> uuid.UUID:
    """The capture requested and its run executed: the generation's id."""
    with who.factory() as db:
        generation, run_id = design_capture.request(
            db, who.user, base, at=AT, script=script, settings=who.settings
        )
        db.commit()
        generation_id = generation.id
    assert run_id is not None
    execute(who, run_id)
    return generation_id


def own_templates(factory: sessionmaker[Session], user: uuid.UUID) -> list[DocumentUserTemplate]:
    with factory() as db:
        return list(
            db.scalars(
                sa.select(DocumentUserTemplate)
                .where(DocumentUserTemplate.user_id == user)
                .options(undefer(DocumentUserTemplate.css), undefer(DocumentUserTemplate.previous))
            )
        )


@pytest.fixture
def who(factory: sessionmaker[Session], migrated_url: str) -> Person:
    return person(factory, migrated_url, USER_A)


def test_a_capture_becomes_the_base_cvs_template_as_a_new_version(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = Services(monkeypatch, design("Morgan Exampleton", "Platform Engineer"))
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_valid"))
    assert generation.mode is GenerationMode.template_capture
    assert generation.status is GenerationStatus.done, generation.error
    (row,) = own_templates(who.factory, who.user)
    assert row.slug.startswith("upload-")
    assert len(row.slug) == len("upload-") + 8
    assert row.version == 1
    assert row.base_document_id == base
    assert row.font_families == ["arimo"]
    assert row.manifest["capture"]["source"] == "pdf"
    assert {"two_pages", "fonts_replaced"} <= set(row.manifest["capture"]["notes"])
    assert document_row(who.factory, base).template_slug == row.slug
    (version,) = versions(who.factory, base)
    assert (version.origin, version.template_slug) == (Origin.template, row.slug)
    assert [job["function"] for job in who.queue.jobs] == [render_job.FUNCTION]
    assert services.asked == ["design_pdf", "pdf_render"]  # read once for the whole run
    assert services.checks == 1
    with who.factory() as db:
        found = template_system.resolve(db, who.user, row.slug)
        assert found is not None
        assert found.fonts == ("arimo",)
        assert render_job.template_of(db, who.user, row.slug).slug == row.slug


# #757 round 1 (B4): a run that stops on its turn limit, wall time or token budget after an
# accepted submit keeps the best accepted template; a cancel or an upload gone keeps nothing.
def test_a_run_stopped_on_its_turn_limit_keeps_the_accepted_template(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton", "Platform Engineer"))
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_turn_limit"))
    with who.factory() as db:
        run = db.get(AgentRun, generation.run_id)
        assert run is not None
        assert (run.status, run.error) == (RunStatus.failed, failures.TURN_LIMIT.format(turns=14))
    assert generation.status is GenerationStatus.done, generation.error
    (row,) = own_templates(who.factory, who.user)
    assert document_row(who.factory, base).template_slug == row.slug
    (version,) = versions(who.factory, base)
    assert (version.origin, version.template_slug) == (Origin.template, row.slug)
    assert [job["function"] for job in who.queue.jobs] == [render_job.FUNCTION]


@pytest.mark.parametrize("end", ["cancelled", "gone"])
def test_a_cancelled_run_or_an_upload_gone_keeps_nothing_it_accepted(
    who: Person, monkeypatch: pytest.MonkeyPatch, end: str
) -> None:
    """The same accepted submit, the run's end seen by settle as a cancel, or with the upload
    gone (`found.gone`) on top of the turn limit."""
    Services(monkeypatch, design("Morgan Exampleton", "Platform Engineer"))
    real = design_capture.settle

    def settle(sessions, run_id, status, found, at):
        assert found.accepted is not None  # the stream's submit was accepted
        if end == "cancelled":
            status = RunStatus.cancelled
        else:
            found.gone = True
        return real(sessions, run_id, status, found, at)

    monkeypatch.setattr(design_capture, "settle", settle)
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_turn_limit"))
    if end == "cancelled":
        assert generation.status is GenerationStatus.cancelled
    else:
        assert (generation.status, generation.error) == (GenerationStatus.failed, "binding_gone")
    assert own_templates(who.factory, who.user) == []
    assert document_row(who.factory, base).template_slug == "band"
    assert versions(who.factory, base) == []
    assert who.queue.jobs == []


STOPS = [
    (RunStatus.failed, failures.TURN_LIMIT.format(turns=14), True),
    (RunStatus.failed, failures.TURN_LIMIT.format(turns=20), True),
    (RunStatus.failed, failures.TIME_LIMIT.format(minutes="15 minutes"), True),
    (RunStatus.failed, failures.TIME_LIMIT.format(minutes="1 minute"), True),
    (RunStatus.failed, failures.TIME_LIMIT.format(minutes="2.5 minutes"), True),
    (RunStatus.failed, failures.TIME_LIMIT.format(minutes="45 seconds"), True),
    (RunStatus.failed, failures.TIME_LIMIT.format(minutes="a while"), False),
    (RunStatus.failed, failures.TOKEN_LIMIT, True),
    (RunStatus.cancelled, failures.TURN_LIMIT.format(turns=14), False),
    (RunStatus.cancelled, None, False),
    (RunStatus.done, None, False),
    (RunStatus.failed, None, False),
    (RunStatus.failed, failures.BINDING_GONE, False),
    (RunStatus.failed, failures.NO_RESULT, False),
    (RunStatus.failed, failures.BUSY, False),
    (RunStatus.failed, failures.INTERNAL, False),
    (RunStatus.failed, failures.API_ERROR.format(status=500), False),
    (RunStatus.failed, failures.FAILED.format(reason="error_during_execution"), False),
    (RunStatus.failed, failures.TURN_LIMIT.format(turns=14) + " Then it failed.", False),
    (RunStatus.failed, "The run reached its limit of turns.", False),
]


@pytest.mark.parametrize(("status", "error", "limit"), STOPS)
def test_only_a_stop_on_a_limit_counts_as_one(status, error, limit) -> None:
    run = AgentRun(status=status, error=error)
    assert design_capture.stopped_on_a_limit(status, run) is limit
    assert design_capture.stopped_on_a_limit(status, None) is False


# #757 round 1 (follow up): each view_render was a full render; a second one in the same pass
# is answered from the first, in words, without a render or an image.
def test_a_second_view_render_in_one_pass_is_answered_without_a_render(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = Services(monkeypatch, design("Morgan Exampleton", "Platform Engineer"))
    answers: list[Any] = []
    real = writer_design_tools.view_render

    async def view_render(scope):
        answers.append(await real(scope))
        return answers[-1]

    monkeypatch.setattr(writer_design_tools, "view_render", view_render)
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_viewed_twice"))
    assert generation.status is GenerationStatus.done, generation.error
    first, second = answers
    assert [block["type"] for block in first] == ["image", "text"]
    assert [block["type"] for block in second] == ["text"]
    assert second[0]["text"] == f"{writer_design_tools.SAME_RENDER}\n{first[1]['text']}"
    # the accepted submit's measure and the first view_render: two renders, not three
    assert len(services.renderer.calls) == 2


def test_a_refusal_is_repaired_once(who: Person, monkeypatch: pytest.MonkeyPatch) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_repaired"))
    assert generation.status is GenerationStatus.done, generation.error
    assert len(own_templates(who.factory, who.user)) == 1


@pytest.mark.parametrize("by", ["rules", "render"])
def test_two_refusals_keep_the_default_design_and_say_so(
    who: Person, monkeypatch: pytest.MonkeyPatch, by: str
) -> None:
    services = Services(monkeypatch, design("Morgan Exampleton"))
    if by == "render":
        # The repaired files pass the static rules; the render check covers a field.
        services.problems = [Problem("style.css", 0, "covered", "name at the loosest step")]
    base = uploaded_base(who)
    script = "documents_design_refused" if by == "rules" else "documents_design_repaired"
    generation = generation_row(who.factory, capture(who, base, script))
    assert generation.status is GenerationStatus.failed
    assert generation.error == "invalid_output"
    assert own_templates(who.factory, who.user) == []
    assert document_row(who.factory, base).template_slug == "band"
    assert versions(who.factory, base) == []
    assert who.queue.jobs == []


def test_a_fidelity_correction_that_breaks_the_rules_keeps_the_accepted_one(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_fidelity"))
    assert generation.status is GenerationStatus.done, generation.error
    (row,) = own_templates(who.factory, who.user)
    assert "@import" not in row.css


def test_a_second_capture_replaces_the_first_and_bumps_its_version(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    base = uploaded_base(who)
    capture(who, base, "documents_design_valid")
    (first,) = own_templates(who.factory, who.user)
    generation = generation_row(who.factory, capture(who, base, "documents_design_valid"))
    assert generation.status is GenerationStatus.done, generation.error
    (second,) = own_templates(who.factory, who.user)
    assert (second.slug, second.version) == (first.slug, 2)
    assert second.previous is not None
    assert second.previous["version"] == 1
    made = versions(who.factory, base)
    assert [(v.origin, v.template_version) for v in made] == [
        (Origin.template, 1),
        (Origin.template, 2),
    ]


def test_a_docx_is_captured_from_its_styles_without_a_page(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    styles = DesignDocx(
        truncated=False,
        notes=(),
        theme=None,
        defaults={"font": "Arial", "size": 22},
        styles=(
            dict.fromkeys(parser_client._STYLE_KEYS)
            | {"id": "Normal", "name": "Normal", "type": "paragraph", "default": True},
        ),
        sections=({"width": 11906, "height": 16838, "margins": [1134] * 4, "columns": 1},),
        tables=0,
        table_columns=0,
        drawings=1,
        paragraphs=12,
        paragraph_styles=(("Normal", 12),),
        formats=(),
    )
    services = Services(monkeypatch, styles)
    base = uploaded_base(who, mime=DOCX)
    generation = generation_row(who.factory, capture(who, base, "documents_design_valid"))
    assert services.asked == ["design_docx"]
    assert generation.status is GenerationStatus.done, generation.error
    (row,) = own_templates(who.factory, who.user)
    assert row.manifest["capture"]["source"] == "docx"
    assert "docx" in row.manifest["capture"]["notes"]
    assert row.manifest["photo"] == "optional"


def test_a_scanned_cv_is_captured_and_said_so(who: Person, monkeypatch: pytest.MonkeyPatch) -> None:
    Services(monkeypatch, design(scanned=True, pages=1))
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_valid"))
    assert generation.status is GenerationStatus.done, generation.error
    (row,) = own_templates(who.factory, who.user)
    assert "scanned" in row.manifest["capture"]["notes"]


def test_the_file_without_its_bytes_fails_the_capture_at_once_and_keeps_its_template(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = Services(monkeypatch, design("Morgan Exampleton"))
    base = uploaded_base(who, raw=None)
    with who.factory() as db:
        generation, run_id = design_capture.request(db, who.user, base, at=AT)
        db.commit()
    assert run_id is None  # no model is called
    assert (generation.status, generation.error) == (GenerationStatus.failed, "binding_gone")
    assert services.asked == []
    assert document_row(who.factory, base).template_slug == "band"


def test_a_capture_that_fails_after_the_conversion_leaves_the_cv_converted(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    """The journeys' path (documents-d24, opportunities-o5): the conversion's final hook starts
    the capture for real, the upload has no bytes left (ingestion drops them with the text), so
    a `template_capture` generation fails at once. The base CV's listing still reports its
    conversion, done: before the fix it reported the newer failed capture, and the upload panel
    said "Your CV could not be converted"."""
    document_id, generation_id = _morgan_converts(factory, migrated_url, MORGAN)
    assert generation_row(factory, generation_id).status is GenerationStatus.done
    with factory() as db:
        modes = dict(
            db.execute(
                sa.select(DocumentGeneration.mode, DocumentGeneration.status).where(
                    DocumentGeneration.document_id == document_id
                )
            ).all()
        )
        listed = base_router._out(db, db.get(Document, document_id))
    assert modes[GenerationMode.template_capture] is GenerationStatus.failed  # the hook ran
    assert listed.generation is not None
    assert (listed.generation.id, listed.generation.status) == (
        generation_id,
        GenerationStatus.done,
    )


def test_bytes_gone_during_the_run_fail_it_as_gone(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    base = uploaded_base(who)
    with who.factory() as db:
        generation, run_id = design_capture.request(
            db, who.user, base, at=AT, script="documents_design_valid", settings=who.settings
        )
        db.execute(sa.text("UPDATE ingestion_documents SET bytes = NULL"))
        db.commit()
    assert run_id is not None
    execute(who, run_id)
    found = generation_row(who.factory, generation.id)
    assert (found.status, found.error) == (GenerationStatus.failed, "binding_gone")


def test_without_a_credential_the_capture_waits_then_starts(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    base = uploaded_base(who)
    with who.factory() as db:
        credentials.delete(db, who.user)
        generation, run_id = design_capture.request(db, who.user, base, at=AT)
        db.commit()
    assert run_id is None
    assert (generation.status, generation.wait_reason) == (
        GenerationStatus.waiting,
        GenerationWait.credential,
    )
    with who.factory() as db:
        credentials.save(db, who.keyring, who.user, CredentialKind.anthropic_api_key, API_KEY, AT)
        started = writer_base.start_ready(db, who.user, at=AT, settings=who.settings)
        db.commit()
    assert len(started) == 1
    assert generation_row(who.factory, generation.id).status is GenerationStatus.running


def test_a_persons_template_never_resolves_for_another(
    who: Person, factory: sessionmaker[Session], migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    capture(who, uploaded_base(who), "documents_design_valid")
    (row,) = own_templates(who.factory, who.user)
    other = person(factory, migrated_url, USER_B)
    with factory() as db:
        assert template_system.resolve(db, other.user, row.slug) is None
        assert render_job.template_of(db, other.user, row.slug).slug == "band"
        with pytest.raises(store.TemplateUnknownError):
            render_job.switch(db, other.user, other.base, at=AT, template_slug=row.slug)


def test_a_row_that_breaks_the_rules_resolves_to_nothing(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    capture(who, uploaded_base(who), "documents_design_valid")
    (row,) = own_templates(who.factory, who.user)
    with who.factory() as db:
        db.execute(
            sa.update(DocumentUserTemplate)
            .where(DocumentUserTemplate.id == row.id)
            .values(css=row.css + "\n.x { position: absolute; }", updated_at=sa.func.now())
        )
        db.commit()
        assert template_system.resolve(db, who.user, row.slug) is None
        assert render_job.template_of(db, who.user, row.slug).slug == "band"
        db.execute(sa.delete(DocumentUserTemplate).where(DocumentUserTemplate.id == row.id))
        db.commit()
        assert render_job.template_of(db, who.user, row.slug).slug == "band"


def test_the_layouts_texts_go_through_the_secret_rules_one_and_two_at_a_time() -> None:
    key = "AKIA" + "QZ" * 8
    texts = ["Morgan Exampleton", f"key {key}", "-----BEGIN RSA", "PRIVATE KEY-----", "Lyon"]
    assert design_capture.scrub(texts) == [
        "Morgan Exampleton",
        design_capture.REDACTED,
        design_capture.REDACTED,
        design_capture.REDACTED,
        "Lyon",
    ]
    assert design_capture.scrub(["Platform Engineer", "2021 to 2023"]) == [
        "Platform Engineer",
        "2021 to 2023",
    ]


def test_the_photo_boxes_are_grey_before_any_model_sees_the_page() -> None:
    page = design("x").read[0]
    painted = Image.open(io.BytesIO(design_capture.grey_boxes(jpeg(), page)))
    x0, y0, x1, y1 = page.images[0]
    middle = ((x0 + x1) / 2 * 300 / page.width, (y0 + y1) / 2 * 424 / page.height)
    assert all(
        abs(a - b) < 12 for a, b in zip(painted.getpixel(middle), design_capture.GREY, strict=True)
    )
    assert painted.getpixel((5, 400)) > (240, 240, 240)


def test_the_canary_of_the_file_reaches_no_log_no_stored_event_and_no_template(
    who: Person, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    planted = marker()
    Services(monkeypatch, design("Morgan Exampleton", f"Planted {planted}"))
    answers: list[str] = []
    read_design = writer_design_tools.read_design

    async def recorded(scope: Any) -> tuple[str, bool]:
        answer = await read_design(scope)
        answers.append(answer[0])
        return answer

    monkeypatch.setattr(writer_design_tools, "read_design", recorded)
    caplog.set_level(logging.DEBUG)
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_valid"))
    assert generation.run_id is not None
    assert answers
    assert all(planted in answer for answer in answers)  # the model did read it
    (row,) = own_templates(who.factory, who.user)
    stored = everything_stored(who.factory, generation.run_id)
    assert planted not in stored
    assert not find(
        [planted],
        logs=caplog.records,
        values=[row.css, row.manifest, [job["args"] for job in who.queue.jobs]],
    )


def test_the_persons_photo_never_reaches_a_render_the_model_sees(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = Services(monkeypatch, design("Morgan Exampleton"))
    out = io.BytesIO()
    Image.new("RGB", (240, 240), (190, 30, 60)).save(out, format="PNG")
    stored = profile_photo.normalize(out.getvalue())
    with who.factory() as db:
        profile_service.set_photo(db, who.user, stored, AT)
        db.commit()
    own = photos.embed(stored.data)
    base = uploaded_base(who)
    # the fidelity stream calls view_render on the accepted template, which prints the photo
    generation = generation_row(who.factory, capture(who, base, "documents_design_fidelity"))
    assert generation.status is GenerationStatus.done, generation.error
    renders = services.renderer.calls
    assert renders
    assert all(own not in html for html in renders)
    assert all(design_check.grey_photo() in html for html in renders)


def test_the_docx_names_and_fonts_go_through_the_secret_rules() -> None:
    key = "AKIA" + "QZ" * 8
    style = dict.fromkeys(parser_client._STYLE_KEYS) | {
        "id": key,
        "name": "Heading 1",
        "type": "paragraph",
        "based_on": key,
        "font": f"Font {key}",
    }
    docx = DesignDocx(
        truncated=False,
        notes=(),
        theme={"major": f"Major {key}", "minor": "Arial", "colours": {}},
        defaults={"font": f"Default {key}", "size": 22},
        styles=(style,),
        sections=(),
        tables=0,
        table_columns=0,
        drawings=0,
        paragraphs=1,
        paragraph_styles=((key, 1),),
        formats=({"style": key, "font": f"Run {key}", "chars": 4},),
    )
    layout = design_capture._docx_layout(docx)
    assert key not in str(layout)
    assert layout["styles"][0]["name"] == "Heading 1"
    assert layout["theme"]["minor"] == "Arimo"  # mapped by code, never the file's string


def _measured(score: float) -> fidelity.Measure:
    """A measure whose score is `score`: the page ink and the share of lines in place alike."""
    return fidelity.Measure(score, 100, 100, round(score * 100), 0.5, 0.5, 1.0, 1.0, ())


def test_decide_allows_one_repair_then_fidelity_passes_that_keep_the_best() -> None:
    bad = (Problem("style.css", 0, "covered", "name at the loosest step"),)

    def run(*answers, passes: int = 4) -> tuple[list[str], design_capture.RunState]:
        state = design_capture.RunState()
        said = []
        for at, (found, score) in enumerate(answers):
            files = {"style.css": f"version {at}"}
            measure = None if score is None else _measured(score)
            said.append(design_capture.decide(state, files, found, passes, measure))
            said[-1] = said[-1].split("\n")[0]
        return said, state

    first = design_capture.ACCEPTED.split("\n")[0]
    better, not_better = design_capture.BETTER, design_capture.NOT_BETTER
    # the passes stop when the score stops rising, and the best is the one kept
    said, state = run(((), 0.60), ((), 0.70), ((), 0.80), ((), 0.803))
    assert said == [first, better, better, not_better]
    assert state.accepted == {"style.css": "version 2"}
    assert state.best == pytest.approx(0.80)
    assert state.closed
    # a later pass that is worse leaves the best in place
    said, state = run(((), 0.70), ((), 0.50))
    assert said == [first, not_better]
    assert state.accepted == {"style.css": "version 0"}
    # a pass that breaks the rules keeps the best
    said, state = run(((), 0.60), ((), 0.75), (bad, None))
    assert said == [first, better, design_capture.KEPT]
    assert state.accepted == {"style.css": "version 1"}
    # at most `passes` fidelity passes after the first accepted template
    said, state = run(((), 0.1), ((), 0.2), ((), 0.3), passes=2)
    assert said == [first, better, design_capture.STORED]
    assert run(((), 0.1), ((), 0.2), ((), 0.3), ((), 0.4), passes=2)[0][3] == design_capture.NO_MORE
    # one repair, then the end
    assert run((bad, None), ((), 0.5))[0] == [run((bad, None))[0][0], first]
    assert run((bad, None), (bad, None), ((), 0.9))[0][1:] == [
        design_capture.REFUSED,
        design_capture.NO_MORE,
    ]
    assert run(((), 0.5), passes=0)[0] == [design_capture.STORED]
    # a DOCX has no page to measure on: its one correction is taken as before
    said, state = run(((), None), ((), None), passes=1)
    assert said == [first, design_capture.STORED]
    assert state.accepted == {"style.css": "version 1"}


def test_the_measure_reaches_the_writer_in_words_and_the_row_in_numbers() -> None:
    offset = fidelity.Offset("EXPERIENCE", 0.2, 4.8, 0.0)
    found = fidelity.Measure(0.81, 50, 49, 47, 0.4, 0.7, 1.2, 4.8, (offset,))
    words = design_capture.said(found)
    assert "47 of 50 lines in place" in words
    assert 'The line "EXPERIENCE" is 4.8pt lower than in the CV.' in words
    summary = found.summary()
    assert summary["in_place"] == 47
    assert "EXPERIENCE" not in json.dumps(summary)


def test_the_estimate_counts_the_images() -> None:
    from app.documents import design_estimate

    pdf = design_estimate.capture(10_000)
    docx = design_estimate.capture(10_000, pages=0)
    # 5 turns of (7,000 + 3,334) fixed, 4 of (5,000 + 3 images of 1,600: two pages and one
    # view_render), 3 of 4,000 output.
    assert pdf.low == 5 * 10_334 + 4 * (5_000 + 4_800) + 3 * 4_000 + 4_000 + 5 * 500
    # each page twice, sent again at each of the 13 later turns: the renders are the same
    assert pdf.high - docx.high == 4 * 1_600 * 13
    # 14 turns, 6 submits: the refused one sent again 10 times, its repair and the four passes
    # 9, 7, 5, 3 and 1 times, each with its view_render, and one more view_render 9 times
    resent = 10 * 13_334 + 25 * (13_334 + 1_600) + 9 * 1_600
    assert docx.high == 14 * 10_334 + 13 * 40_000 + resent + 6 * 13_334 + 14 * 500
    assert design_estimate.capture(design_estimate.prompt_chars("design")).high <= 1_500_000
    assert pdf.ceiling == 2 * pdf.high
    assert asyncio.iscoroutinefunction(design_check.check_template)


def _inputs_with(headings: tuple[str, ...]) -> design_capture.Inputs:
    tokens = template_rules.from_pdf(parser_client.DesignPdf(1, False, False, (), (), ()))
    return design_capture.Inputs("pdf", tokens, "none", {}, (), (), headings=headings)


LABEL_CASES = [
    ({"experience": "Expériences Professionnelles"}, None),
    ({"summary": "About me", "languages": "Langues parlées"}, None),
    ({"summary": "  About me  "}, None),  # trimmed, as the run's text is
    ({"summary": "About you"}, "not a heading of the upload"),
    ({"summary": design_capture.REDACTED}, "not a heading of the upload"),
    # #757 round 1 (B2): a run text of the upload that is no heading would print on every later
    # document, outside the fact check
    ({"summary": "Morgan Exampleton"}, "not a heading of the upload"),  # the name
    ({"summary": "Platform Engineer"}, "not a heading of the upload"),  # a headline
    ({"experience": "Example Robotics"}, "not a heading of the upload"),  # an organisation
    ({"summary": "About me", "skills": "Led a team of 12"}, "not a heading of the upload"),
    ({"summary": "about me"}, "not a heading of the upload"),  # case: exact text only
    ({"summary": "About  me"}, "not a heading of the upload"),
    ({"summary": "About - me"}, "a dash"),
    ({"summary": "About – me"}, "a dash"),
    ({"summary": "-About"}, "a dash"),
    ({"summary": "About <b>"}, "a dash or a <"),
    ({"summary": "x" * 41}, "1 to 40"),
    ({"summary": ""}, "1 to 40"),
    ({"photo": "About me"}, "no label key"),
    ({"summary": 3}, "1 to 40"),
    (["About me"], "an object"),
]


@pytest.mark.parametrize(("proposed", "said"), LABEL_CASES)
def test_a_label_is_taken_only_as_the_exact_text_of_a_heading_of_the_upload(proposed, said):
    headings = ("Expériences Professionnelles", "About me", "Langues parlées", "About - me",
                "About – me", "-About", "About <b>", "x" * 41, design_capture.REDACTED)  # fmt: skip
    labels, problems = design_capture.labels_of(proposed, _inputs_with(headings), "fr")
    if said is None:
        assert problems == ()
        assert labels == {"fr": {k: v.strip() for k, v in proposed.items()}}
    else:
        assert labels is None
        assert any(said in p.detail for p in problems), problems
    assert design_capture.labels_of(None, _inputs_with(headings), "fr") == (None, ())


def test_the_name_a_headline_and_an_organisation_of_a_real_layout_are_no_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inputs as read_inputs builds them from an extraction: only the section heading runs
    are labels; every other run text is refused."""
    found = design(
        "Morgan Exampleton", "Platform Engineer", "Example Robotics", headings=("PROFILE", "SKILLS")
    )
    Services(monkeypatch, found)
    inputs = asyncio.run(design_capture.read_inputs(PDF, "application/pdf"))
    assert inputs.headings == ("PROFILE", "SKILLS")
    labels, problems = design_capture.labels_of({"summary": "PROFILE"}, inputs, "en")
    assert (labels, problems) == ({"en": {"summary": "PROFILE"}}, ())
    for text in ("Morgan Exampleton", "Platform Engineer", "Example Robotics"):
        labels, problems = design_capture.labels_of({"summary": text}, inputs, "en")
        assert labels is None
        assert [p.detail for p in problems] == ["labels: summary is not a heading of the upload"]


def test_several_measured_passes_keep_the_best_with_the_uploads_heading_as_label(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    """documents_design_passes: four accepted submits; the measure rises, then falls: the
    second best is stored with its label, the numbers of its measure, and no text of the CV."""
    layout = design("Morgan Exampleton", "Platform Engineer", headings=("PROFILE", "SKILLS"))
    services = Services(monkeypatch, layout)
    # the score of each version, read from the sheet the renderer was given last (a submit and
    # a view_render measure the same version alike)
    scores = {".pass0 ": 0.50, ".pass1 ": 0.62, ".pass2 ": 0.70, ".pass3 ": 0.40}
    seen: list[str] = []

    def measure(original_page, original, pdf, png):
        seen.append(original[0].text if original else "")
        html = services.renderer.calls[-1]
        return _measured(next(v for k, v in scores.items() if k in html))

    monkeypatch.setattr(fidelity, "measure", measure)
    base = uploaded_base(who)
    generation = generation_row(who.factory, capture(who, base, "documents_design_passes"))
    assert generation.status is GenerationStatus.done, generation.error
    assert seen[0] == "Morgan Exampleton"  # the original's lines, as the model read them
    (row,) = own_templates(who.factory, who.user)
    assert ".pass2 " in row.css
    assert ".pass3 " not in row.css
    assert row.manifest["labels"] == {"en": {"summary": "PROFILE"}}
    kept = row.manifest["capture"]["fidelity"]
    assert kept["score"] == pytest.approx(0.70)
    assert kept["in_place"] == 70
    assert "Morgan" not in json.dumps(row.manifest["capture"])
    with who.factory() as db:
        document = store.get(db, who.user, base)
        assert document is not None
        found = base_router._out(db, document).design_fidelity
    assert found is not None
    assert (found.in_place, found.lines) == (70, 100)


def test_the_base_view_shows_no_fidelity_for_a_bundled_template_or_a_bad_row(
    who: Person, monkeypatch: pytest.MonkeyPatch
) -> None:
    Services(monkeypatch, design("Morgan Exampleton"))
    base = uploaded_base(who)
    with who.factory() as db:
        document = store.get(db, who.user, base)
        assert document is not None
        assert base_router._out(db, document).design_fidelity is None  # band, the default
    capture(who, base, "documents_design_valid")
    with who.factory() as db:
        row = db.scalars(sa.select(DocumentUserTemplate)).one()
        row.manifest = {**row.manifest, "capture": {"fidelity": {"lines": 3, "in_place": 9}}}
        db.commit()
        document = store.get(db, who.user, base)
        assert document is not None
        assert base_router._out(db, document).design_fidelity is None
