"""D25b: the design capture of an uploaded CV, end to end on the fake backend (no model, no key)
with the parser and the renderer played by fakes (the real renderer's check is
test_design_check_docker.py): a capture stored as the person's user template and the base CV's
new version, a repair, a refusal by the rules and by the render check (`classic` kept), a
fidelity pass that breaks the rules (the accepted template kept), the capture run twice, a DOCX
and a scanned PDF, the file's bytes gone, waiting for the credential, A's template never
resolved for B, a row tampered with, the secret rules on the layout's texts, the grey photo
boxes, and the canary of the file. Real Postgres at head."""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import credentials
from app.agents.models import CredentialKind
from app.documents import (
    consent,
    design_capture,
    design_check,
    render_client,
    render_job,
    store,
    writer_base,
    writer_design_tools,
)
from app.documents import photo as photos
from app.documents import templates as template_system
from app.documents.models import (
    BaseSource,
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
from tests.documents.test_writer_base_upload import DOCX, add_upload
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


def design(*texts: str, scanned: bool = False, pages: int = 2) -> DesignPdf:
    runs = tuple(
        TextRun(text, (600, 600 + 200 * n, 3000, 720 + 200 * n), 0, 100, "#1d232b")
        for n, text in enumerate(texts)
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
        consent.record(db, who.user, AT)
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
    assert document_row(who.factory, base).template_slug == "classic"
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


def test_the_file_without_its_bytes_fails_the_capture_at_once_and_keeps_classic(
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
    assert document_row(who.factory, base).template_slug == "classic"


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
        assert render_job.template_of(db, other.user, row.slug).slug == "classic"
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
        assert render_job.template_of(db, who.user, row.slug).slug == "classic"
        db.execute(sa.delete(DocumentUserTemplate).where(DocumentUserTemplate.id == row.id))
        db.commit()
        assert render_job.template_of(db, who.user, row.slug).slug == "classic"


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


def test_decide_allows_one_repair_and_one_fidelity_pass() -> None:
    bad = (Problem("style.css", 0, "covered", "name at the loosest step"),)
    files = {"style.css": "a"}

    def run(*answers: tuple[Problem, ...], passes: int = 2) -> list[str]:
        state = design_capture.RunState()
        return [design_capture.decide(state, files, found, passes) for found in answers]

    assert run((), ()) == [design_capture.ACCEPTED, design_capture.STORED]
    assert run((), bad) == [design_capture.ACCEPTED, design_capture.KEPT]
    assert run(bad, (), ())[1:] == [design_capture.ACCEPTED, design_capture.STORED]
    assert run(bad, bad, ())[1:] == [design_capture.REFUSED, design_capture.NO_MORE]
    assert run(bad, (), (), ())[3] == design_capture.NO_MORE
    assert run((), passes=0) == [design_capture.STORED]
    assert run(bad, (), passes=1)[1] == design_capture.STORED


def test_the_estimate_counts_the_images() -> None:
    from app.documents import design_estimate

    pdf = design_estimate.capture(10_000)
    docx = design_estimate.capture(10_000, pages=0)
    # 5 turns of (7,000 + 3,334) fixed, 4 of (4,000 + 3 images of 1,600), 3 of 4,000 output.
    assert pdf.low == 5 * 10_334 + 4 * (4_000 + 4_800) + 3 * 4_000 + 4_000 + 5 * 500
    assert pdf.high - docx.high == 9 * 1_600 * 4
    assert pdf.ceiling == 2 * pdf.high
    assert asyncio.iscoroutinefunction(design_check.check_template)
