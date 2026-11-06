"""The design capture of an uploaded CV (task D25b; SPEC-documents.md, "Design capture of an
upload", steps 1 to 6): the second step of a conversion, after its content.

**The request.** `prepare_after_conversion` records the capture of an upload whose content
conversion is done (D24) in the conversion's own transaction, under a savepoint: one
`template_capture` generation of the base CV, `queued`, `waiting` with `consent` or `credential`
(`writer_base.wait_reason`), or `failed` with `binding_gone` when the file's bytes are gone.
`start_prepared` starts that generation's run (`writer_design`) once the bytes and the reason to
wait are read again under the document lock: `enqueue(final, generation_id)` calls it after the
conversion's commit, `request` right after preparing one, and `start_ready`, which
`writer_base.start_ready` calls, for each open one (D10's tick); one that meets the person's
limit of active runs stays `queued`. Its estimate and ceiling are `design_estimate`'s. The run's
input holds ids, the file's kind and numbers only.

**The inputs** (`read_inputs`), read by the run's tools from the ingestion document's bytes,
never stored: for a PDF the sandbox's layout extraction (`design_pdf`) and its page render of the
first DESIGN_PAGES pages (`pdf_render`), each image box the extraction found painted grey by the
worker before any model sees the page; for a DOCX the styles (`design_docx`) and no page. The
fonts are mapped by code (`font_map`, through `template_rules.from_pdf` or `from_docx`, and a
DOCX's style, default and theme fonts to their bundled face); the texts of the extraction (its
runs, a DOCX's style names, ids and `based_on`) go through ingestion's secret rules
(`ingestion.secrets.scan`) one by one and two by two, and a text that matches is replaced by
REDACTED before the model reads it.

**The checks** (`validate`): the manifest is written by code (`manifest_of`: the captured
density ladder, the mapped fonts, the accent, the default template's soft limits), never by the
model; the three files the model wrote pass `template_rules.check` (the static allow list), the
template system's `from_files`, then `design_check.check_template` (the render check in the
renderer's Chromium: every field visible, uncovered, inside the page, contrasted, and the
density read). A refusal goes back to the model once (`decide`); a second refusal ends the
capture and the base CV keeps its template. An accepted template gets one fidelity pass: the
model sees the base CV printed with it beside the original's page 1 and may submit once more; a
correction that is refused leaves the accepted one in place. At most DOCUMENTS_DESIGN_PASSES
passes after the first submit.

**The result** (`settle`, from the run's final hook, when the run ends or stops on its turn
limit, wall time or token budget after an accepted submit: `stopped_on_a_limit`; never on a
cancel or when the upload is gone): the best scoring accepted files become the person's
user template (`document_user_templates`, `upload-` and 8 characters), or the next version of
the one this base CV was captured to before (a capture run again replaces it, `previous` keeps
the files before); the base CV takes it as a new version with origin `template`, and its render
is queued. Any other end fails the generation with a code of `generate.ERRORS`, which is what
the person is told ("We kept the default design for this CV"); the content is never touched.
`manifest["capture"]` keeps what the person is shown about fidelity: the source kind and the
codes of CAPTURE_NOTES.

**The loader** (`load`, registered with `templates.register_loader`): `templates.resolve` finds
a person's own template through it, by the person's id and the slug only, and validates it again
at every resolve (the authoring rules and `template_rules.check`); a row that fails them, or
another person's slug, resolves to None, and `render_job.template_of` prints the default.

Logs carry ids, codes and counts, never a text of the file or of the template.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import secrets
import threading
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from PIL import Image, ImageDraw
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.agents import failures
from app.agents.definitions import FinalRun
from app.agents.models import AgentRun, RunStatus
from app.documents import (
    design_check,
    design_spec,
    fidelity,
    font_map,
    font_measure,
    store,
    template_rules,
    templates,
)
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    DocumentUserTemplate,
    GenerationMode,
    GenerationStatus,
    GenerationTrigger,
    Origin,
    Scope,
)
from app.documents.template_css import Problem
from app.ingestion import parser_client
from app.ingestion import secrets as secret_rules
from app.ingestion.documents import DOCX_MIME
from app.ingestion.models import IngestionDocument

logger = logging.getLogger("app.documents.design_capture")

DESIGN_PAGES = 2
REDACTED = "[redacted]"
RUNS_PER_PAGE = 400  # text runs of one page the model reads, in reading order
RUN_CHARS = 160
# A run as read_design gives it (D28): one array in this order, the font an index of `fonts`, so
# that the key names are not sent once per run. The extraction's own cap (DESIGN_TEXT, 40,000
# characters for both pages) bounds the texts.
RUN_FIELDS = ("text", "x0", "y0", "x1", "y1", "font", "size", "colour", "baseline", "spacing_em",
              "caps")  # fmt: skip
IMAGE_MAX_BYTES = 300_000  # one image block, as ingestion's view_image sends at most
GREY = (160, 160, 160)
SLUG_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
CAPTURE_NOTES = ("scanned", "docx", "two_pages", "fonts_replaced", *template_rules.NOTE_CODES)
NAME = {"en": "From your CV", "fr": "D'après votre CV"}
DESCRIPTION = {
    "en": "The look of the CV you uploaded.",
    "fr": "L'apparence du CV que vous avez importé.",
}


class InputsGoneError(Exception):
    """The upload's bytes are no longer there, or the sandbox refused the file."""


# --- the inputs -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    source: str  # "pdf" or "docx"
    tokens: template_rules.Tokens
    photo: str  # the manifest's: "optional" when the first page holds an image box (a drawing)
    layout: Mapping[str, Any]  # what read_design answers, texts scrubbed
    pages: tuple[bytes, ...]  # JPEG, image boxes grey; () for a DOCX
    notes: tuple[str, ...]  # codes of CAPTURE_NOTES
    # D28, for a PDF: the extraction itself (the fidelity measure matches its lines; never
    # stored, never logged) and the original's section headings as scrubbed, a replaced one
    # left out (design_spec.measured): the only texts labels_of accepts
    design: parser_client.DesignPdf | None = None
    headings: tuple[str, ...] = ()


def scrub(texts: list[str]) -> list[str]:
    """Each text, or REDACTED where it, or it with a neighbour, matches a secret rule."""
    alone = [bool(secret_rules.scan(text)) for text in texts]
    out = [REDACTED if hit else text for text, hit in zip(texts, alone, strict=True)]
    for i in range(1, len(texts)):
        if not (alone[i - 1] or alone[i]) and secret_rules.scan(f"{texts[i - 1]} {texts[i]}"):
            out[i - 1] = out[i] = REDACTED
    return out


def _pt(value: int) -> float:
    return round(value / 10, 1)


def _pdf_layout(
    design: parser_client.DesignPdf, faces: list[font_map.Mapped] | None = None
) -> dict[str, Any]:
    if faces is None:
        faces = [
            font_map.face(f.name, f.weight, f.italic, f.serif, f.monospace) for f in design.fonts
        ]
    every = [scrub([run.text for run in page.runs]) for page in design.read]
    pages = []
    for page, scrubbed in zip(design.read, every, strict=True):
        runs = page.runs[:RUNS_PER_PAGE]
        texts = scrubbed[:RUNS_PER_PAGE]
        pages.append(
            {
                "page": page.index,
                "size_pt": [_pt(page.width), _pt(page.height)],
                "margins_pt": [_pt(m) for m in page.margins] if page.margins else None,
                "runs": [
                    [
                        text[:RUN_CHARS],
                        *(_pt(v) for v in run.box),
                        run.font,
                        _pt(run.size),
                        run.colour,
                        _pt(run.baseline),
                        round(run.spacing / 1000, 3),
                        run.caps,
                    ]
                    for run, text in zip(runs, texts, strict=True)
                ],
                "shapes": [
                    {"type": s.type, "box_pt": [_pt(v) for v in s.box], "colour": s.colour}
                    for s in page.shapes
                ],
                "images_pt": [[_pt(v) for v in box] for box in page.images],
            }
        )
    return {
        "pages": design.pages,
        "scanned": design.scanned,
        "run_fields": list(RUN_FIELDS),
        "fonts": [
            {"family": face.css_name, "weight": face.weight, "italic": face.italic}
            for face in faces
        ],
        "read": pages,
        "measured": design_spec.measured(design, faces, every),
    }


def _face(name: object) -> str | None:
    """A font named by the file, as the bundled face it maps to: never the file's own string."""
    return font_map.face(name).css_name if isinstance(name, str) and name else None


def _docx_layout(design: parser_client.DesignDocx) -> dict[str, Any]:
    texts = {
        key: scrub([str(style.get(key) or "") for style in design.styles])
        for key in ("name", "id", "based_on")
    }
    styles = [
        {**dict(s), **{key: texts[key][n] for key in texts}, "font": _face(s.get("font"))}
        for n, s in enumerate(design.styles)
    ]
    used = scrub([name for name, _ in design.paragraph_styles])
    theme = dict(design.theme) if design.theme else None
    if theme is not None:
        theme.update({key: _face(theme.get(key)) for key in ("major", "minor")})
    return {
        "defaults": {**dict(design.defaults), "font": _face(design.defaults.get("font"))},
        "theme": theme,
        "styles": styles,
        "sections": [dict(s) for s in design.sections],
        "tables": design.tables,
        "table_columns": design.table_columns,
        "paragraph_styles": [
            [n, count] for n, (_, count) in zip(used, design.paragraph_styles, strict=True)
        ],
    }


def grey_boxes(jpeg: bytes, page: parser_client.DesignPage) -> bytes:
    """The page image with each image box of the extraction painted grey, as a JPEG."""
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    sx, sy = image.width / max(page.width, 1), image.height / max(page.height, 1)
    draw = ImageDraw.Draw(image)
    for x0, y0, x1, y1 in page.images:
        draw.rectangle((x0 * sx, y0 * sy, x1 * sx, y1 * sy), fill=GREY)
    for quality in (85, 70, 50):
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality)
        if len(out.getvalue()) <= IMAGE_MAX_BYTES:
            return out.getvalue()
    raise InputsGoneError("page_too_heavy")


async def read_inputs(raw: bytes, mime: str) -> Inputs:
    """The inputs of a capture from the upload's bytes (module docstring). Raises
    InputsGoneError when the sandbox refuses the file."""
    try:
        if mime == DOCX_MIME:
            docx = await parser_client.read_design("design_docx", raw)
            if not isinstance(docx, parser_client.DesignDocx):
                raise InputsGoneError("not_docx")
            tokens = template_rules.from_docx(docx)
            notes = ("docx", *(n.code for n in tokens.notes))
            photo = "optional" if docx.drawings else "none"  # a drawing may be the photo
            return Inputs("docx", tokens, photo, _docx_layout(docx), (), _notes(notes))
        design = await parser_client.read_design("design_pdf", raw)
        if not isinstance(design, parser_client.DesignPdf):
            raise InputsGoneError("not_pdf")
        indexes = [page.index for page in design.read][:DESIGN_PAGES]
        rendered = (
            await parser_client.parse("pdf_render", raw, {"pages": indexes, "limit": len(indexes)})
            if indexes
            else None
        )
    except parser_client.ParserError as error:
        raise InputsGoneError(type(error).__name__) from None
    faces = await asyncio.to_thread(font_measure.faces, design)
    tokens = template_rules.from_pdf(design, faces)
    by_index = {page.index: page for page in design.read}
    pages = tuple(
        grey_boxes(image.jpeg, by_index[image.position])
        for image in (rendered.images if rendered else ())
        if image.position in by_index
    )
    photo = "optional" if design.read and design.read[0].images else "none"
    notes = [n.code for n in tokens.notes]
    replaced = any(face.notes and not face.notes[0].startswith("exact:") for face in faces)
    notes += ["fonts_replaced"] if replaced else []
    notes += ["scanned"] if design.scanned else []
    notes += ["two_pages"] if design.pages > 1 else []
    layout = _pdf_layout(design, faces)
    headings = tuple(layout["measured"]["headings"])
    return Inputs("pdf", tokens, photo, layout, pages, _notes(notes), design, headings)


def _notes(codes: Any) -> tuple[str, ...]:
    return tuple(code for code in dict.fromkeys(codes) if code in CAPTURE_NOTES)


def upload_bytes(db: Session, user_id: uuid.UUID, upload_id: uuid.UUID) -> tuple[bytes, str]:
    """The bytes and the type of the person's upload; InputsGoneError when gone."""
    row = db.execute(
        sa.select(IngestionDocument.raw, IngestionDocument.mime).where(
            IngestionDocument.id == upload_id, IngestionDocument.user_id == user_id
        )
    ).one_or_none()
    if row is None or not row.raw:
        raise InputsGoneError("no_bytes")
    return bytes(row.raw), row.mime


# --- the manifest and the checks ------------------------------------------------------------


def fonts_of(tokens: template_rules.Tokens) -> tuple[str, ...]:
    return tokens.fonts or (font_map.GENRES[font_map.DEFAULT_GENRE],)


def manifest_of(
    slug: str,
    version: int,
    inputs: Inputs,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """The manifest of a user template, written by code from the captured tokens; `labels`
    as labels_of accepted them."""
    tokens = inputs.tokens
    default = templates.registry()[templates.DEFAULT]
    own = {"labels": {lang: dict(items) for lang, items in labels.items()}} if labels else {}
    return {
        **own,
        "slug": slug,
        "version": version,
        "name": dict(NAME),
        "description": dict(DESCRIPTION),
        "tags": [
            "two columns" if tokens.columns == 2 else "one column",
            "photo" if inputs.photo != "none" else "no photo",
        ],
        "photo": inputs.photo,
        "fonts": list(fonts_of(tokens)),
        "accent": tokens.accent,
        **{
            kind: {
                "steps": template_rules.steps(tokens, kind),
                "soft_limits": asdict(default.kinds[kind].soft_limits),
            }
            for kind in templates.KINDS
        },
        "thumbnail_of": "",
    }


@dataclass(frozen=True)
class Validated:
    template: templates.Template | None
    problems: tuple[Problem, ...]


async def validate(
    files: Mapping[str, str],
    inputs: Inputs,
    check: design_check.Check | None = None,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> Validated:
    """The static rules, then the template system, then the render check. Raises
    design_check.CheckUnavailableError when the renderer cannot be reached."""
    tokens = inputs.tokens
    problems = await asyncio.to_thread(
        template_rules.check,
        files,
        photo=inputs.photo,
        fonts=fonts_of(tokens),
        accent=tokens.accent,
    )
    if problems:
        return Validated(None, tuple(problems))
    try:
        template = templates.from_files(manifest_of("upload-check00", 1, inputs, labels), files)
    except templates.TemplateError:
        return Validated(None, (Problem("style.css", 0, "authoring", "the template system"),))
    found = await design_check.check_template(template, check)
    return Validated(template if not found else None, tuple(found))


# --- one run's state, from its tools to its final hook ----------------------------------------


@dataclass
class RunState:
    inputs: Inputs | None = None
    submits: int = 0
    accepted: dict[str, str] | None = None  # the best scoring accepted files (D28)
    labels: dict[str, dict[str, str]] | None = None  # theirs
    measure: fidelity.Measure | None = None  # theirs
    best: float = 0.0
    passes: int = 0  # fidelity passes done
    closed: bool = False  # no submit is taken any more
    refused: bool = False  # closed by a refusal, nothing accepted
    gone: bool = False  # the upload could not be read
    turn: asyncio.Lock = field(default_factory=asyncio.Lock)  # one submit at a time
    reading: asyncio.Lock = field(default_factory=asyncio.Lock)  # the inputs read once


RUNS_KEPT = 64
_RUNS: dict[uuid.UUID, RunState] = {}
_RUNS_LOCK = threading.Lock()


def state(run_id: uuid.UUID) -> RunState:
    with _RUNS_LOCK:
        found = _RUNS.get(run_id)
        if found is None:
            found = _RUNS[run_id] = RunState()
            while len(_RUNS) > RUNS_KEPT:
                del _RUNS[next(iter(_RUNS))]
        return found


def take(run_id: uuid.UUID) -> RunState:
    with _RUNS_LOCK:
        return _RUNS.pop(run_id, RunState())


ACCEPTED = (
    "Accepted. The fidelity pass: call view_render to see the base CV printed with your "
    "template over the original, with the measure; correct what the numbers say within the "
    "rules and submit again, or stop and answer with one short line when nothing is left to "
    "correct."
)
BETTER = "Better: this version is now the one kept. Correct again from the numbers, or stop."
STORED = "Stored. Your work is done: answer with one short line."
KEPT = (
    "This correction breaks the rules: the best template accepted before is kept. Stop and "
    "answer with one short line."
)
NOT_BETTER = (
    "This version does not lie closer to the original: the best one before is kept. Stop and "
    "answer with one short line."
)
REFUSED = (
    "Refused again: the default design is kept for this CV. Stop and answer with one short line."
)
NO_MORE = "No submit is left in this run. Stop and answer with one short line."
MARGIN = 0.005  # of the score: a pass that does not raise it by this much ends the passes


def said(found: fidelity.Measure | None) -> str:
    """The measure, in words for the writer: the score, the lines in place, the offsets."""
    if found is None:
        return ""
    lines = [
        f"Measure: score {found.score:.3f} (page ink {found.page:.3f}); {found.in_place} of "
        f"{found.lines} lines in place within {fidelity.PLACE_PT:g}pt, {found.matched} matched; "
        f"vertical offset mean {found.mean_dy:.1f}pt, max {found.max_dy:.1f}pt; horizontal "
        f"mean {found.mean_dx:.1f}pt, max {found.max_dx:.1f}pt.",
        *found.sentences(),
    ]
    return "\n".join(lines)


def decide(
    run: RunState,
    files: dict[str, str],
    problems: tuple[Problem, ...],
    passes: int,
    measure: fidelity.Measure | None = None,
    labels: dict[str, dict[str, str]] | None = None,
) -> str:
    """What one submit does to the run's state, and the answer to the model. After the first
    accepted template, up to `passes` fidelity passes; the best scoring accepted version is the
    one kept, a pass that is refused or does not raise the score by MARGIN ends them."""
    if run.closed:
        return NO_MORE
    run.submits += 1
    score = measure.score if measure is not None else 0.0
    if run.accepted is None:
        if not problems:
            run.accepted, run.labels, run.measure, run.best = files, labels, measure, score
            if passes <= 0:
                run.closed = True
                return STORED
            return f"{ACCEPTED}\n{said(measure)}".strip()
        if run.submits > 1:
            run.closed = run.refused = True
            return REFUSED
        listed = "\n".join(f"- {p.file}: {p.code}: {p.detail}" for p in problems)
        return f"Refused. Correct every problem and submit once more, the only repair:\n{listed}"
    run.passes += 1
    if problems:
        run.closed = True
        return KEPT
    if measure is not None and score < run.best + MARGIN:  # a DOCX has no measure
        run.closed = True
        return f"{NOT_BETTER}\n{said(measure)}".strip()
    run.accepted, run.labels, run.measure, run.best = files, labels, measure, score
    if run.passes >= passes:
        run.closed = True
        return STORED
    return f"{BETTER}\n{said(measure)}".strip()


LABEL_DASH = templates.LABEL_SEPARATOR  # one rule for bundled and user labels


def labels_of(
    proposed: object, inputs: Inputs, language: str
) -> tuple[dict[str, dict[str, str]] | None, tuple[Problem, ...]]:
    """The labels the writer proposes for the template (key: the original's heading), each
    accepted only when its text is exactly, trimmed, one of the upload's own section headings
    (`inputs.headings`: as the model read them, scrubbed, a replaced one left out), of 1 to
    design_spec.LABEL_MAX characters, without a dash used as a separator or a `<`; given for
    the base CV's `language` only. Any other text of the upload (the name, a headline, an
    organisation) would print on every later document outside the fact check: refused."""
    if proposed is None or proposed == {}:
        return None, ()
    if not isinstance(proposed, dict) or len(proposed) > len(template_rules.LABELS):
        return None, (Problem("labels", 0, "labels", "labels: an object of label keys"),)
    headings = frozenset(inputs.headings)
    problems = []
    for key, text in proposed.items():
        if key not in template_rules.LABELS:
            problems.append(Problem("labels", 0, "labels", f"labels: {key} is no label key"))
        elif not isinstance(text, str) or not 1 <= len(text.strip()) <= design_spec.LABEL_MAX:
            problems.append(Problem("labels", 0, "labels", f"labels: {key}: 1 to 40 characters"))
        elif LABEL_DASH.search(text.strip()) or "<" in text:
            problems.append(Problem("labels", 0, "labels", f"labels: {key} holds a dash or a <"))
        elif text.strip() not in headings or text.strip() == REDACTED:
            problems.append(
                Problem("labels", 0, "labels", f"labels: {key} is not a heading of the upload")
            )
    if problems:
        return None, tuple(problems)
    return {language: {key: text.strip() for key, text in proposed.items()}}, ()


# --- the loader -------------------------------------------------------------------------------

_LOADED: dict[tuple[uuid.UUID, int, str], templates.Template | None] = {}
_LOADED_LOCK = threading.Lock()


def _build(manifest: Mapping[str, Any], files: Mapping[str, str]) -> templates.Template | None:
    own = {key: value for key, value in manifest.items() if key != "capture"}
    try:
        template = templates.from_files(own, files)
    except templates.TemplateError:
        return None
    problems = template_rules.check(
        files, photo=template.photo, fonts=template.fonts, accent=template.accent
    )
    return None if problems else template


def load(db: Session, user_id: uuid.UUID, slug: str) -> templates.Template | None:
    """The person's own template of that slug, validated (module docstring); None otherwise."""
    if not isinstance(slug, str) or not slug.startswith("upload-") or db is None:
        return None
    row = db.execute(
        sa.select(DocumentUserTemplate)
        .where(DocumentUserTemplate.user_id == user_id, DocumentUserTemplate.slug == slug)
        .options(
            undefer(DocumentUserTemplate.cv_html),
            undefer(DocumentUserTemplate.letter_html),
            undefer(DocumentUserTemplate.css),
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    key = (row.id, row.version, row.updated_at.isoformat())
    with _LOADED_LOCK:
        if key in _LOADED:
            return _LOADED[key]
    files = {"cv.html.j2": row.cv_html, "letter.html.j2": row.letter_html, "style.css": row.css}
    found = _build(row.manifest, files)
    with _LOADED_LOCK:
        _LOADED[key] = found
        while len(_LOADED) > RUNS_KEPT:
            del _LOADED[next(iter(_LOADED))]
    return found


templates.register_loader(load)


# --- the request ------------------------------------------------------------------------------


class CaptureRefusedError(Exception):
    """`not_uploaded`: the document is not a base CV converted from an upload of the person."""


def _kept(db: Session, document: Document) -> int:
    """The size of the base CV's original file still kept, 0 once it is gone."""
    kept = db.scalar(
        sa.select(sa.func.coalesce(sa.func.octet_length(IngestionDocument.raw), 0)).where(
            IngestionDocument.id == document.source_document_id,
            IngestionDocument.user_id == document.user_id,
        )
    )
    return kept or 0


def _failed_for_gone_file(db: Session, document: Document) -> DocumentGeneration | None:
    """The base CV's last capture, when it failed for a file that is still gone and nothing
    else of the document is open."""
    if _kept(db, document) or store.open_generation(db, document.user_id, document.id):
        return None
    last = db.scalars(
        sa.select(DocumentGeneration)
        .where(
            DocumentGeneration.user_id == document.user_id,
            DocumentGeneration.document_id == document.id,
            DocumentGeneration.mode == GenerationMode.template_capture,
        )
        .order_by(DocumentGeneration.created_at.desc(), DocumentGeneration.id.desc())
        .limit(1)
    ).first()
    if last is None or (last.status, last.error) != (GenerationStatus.failed, "binding_gone"):
        return None
    return last


def _prepare(
    db: Session,
    document: Document,
    *,
    at: datetime,
    trigger: GenerationTrigger = GenerationTrigger.click,
) -> DocumentGeneration:
    """Price and persist capture intent under the document lock, without starting a run."""
    from app.documents import design_estimate, generate, writer_base

    user_id = document.user_id
    figures = design_estimate.for_capture(db, user_id, document)
    kept = _kept(db, document)
    reason = writer_base.wait_reason(db, user_id)
    if not kept:
        # Nothing left to read: failed at once, no model is called (`binding_gone`).
        status = GenerationStatus.failed
    elif reason is not None:
        status = GenerationStatus.waiting
    else:
        status = GenerationStatus.queued
    generation = generate.create(
        db,
        document,
        GenerationMode.template_capture,
        trigger,
        None,
        design_estimate.as_json(figures),
        figures.ceiling,
        at=at,
        status=status,
        wait_reason=reason if status is GenerationStatus.waiting else None,
    )
    if status is GenerationStatus.failed:
        generation.error, generation.finished_at = "binding_gone", at
        db.flush()
        logger.info("documents.design.no_bytes", extra={"generation_id": str(generation.id)})
    return generation


def prepare_after_conversion(
    db: Session, conversion: DocumentGeneration, *, at: datetime
) -> DocumentGeneration | None:
    """Intent in a fresh conversion's completion transaction, even if its source is gone. It
    has its own savepoint: a preparation that fails records no intent, logs its class and
    returns None, and the converted content still commits (SPEC-documents.md, Failure modes)."""
    if (
        conversion.mode is not GenerationMode.base_upload
        or conversion.status is not GenerationStatus.done
    ):
        raise ValueError("not a completed upload conversion")
    try:
        with db.begin_nested():
            document = store.lock_document(db, conversion.user_id, conversion.document_id)
            return _prepare(db, document, at=at)
    except Exception as exc:
        # The class only: a database error's message can carry values.
        logger.error(
            "documents.design.not_prepared",
            extra={"generation_id": str(conversion.id), "error": type(exc).__name__},
        )
        return None


def request(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    at: datetime,
    trigger: GenerationTrigger = GenerationTrigger.click,
    script: str | None = None,
    settings: Any = None,
) -> tuple[DocumentGeneration, uuid.UUID | None]:
    """Prepare and attempt a capture; the caller commits before enqueueing its run ID. While the
    original file is still gone, the capture that failed for it is answered again, unchanged."""
    document = store.lock_document(db, user_id, document_id)
    if document.scope != Scope.base or document.base_source is not BaseSource.uploaded:
        raise CaptureRefusedError("not_uploaded")
    failed = _failed_for_gone_file(db, document)
    if failed is not None:
        return failed, None
    generation = _prepare(db, document, at=at, trigger=trigger)
    return generation, start_prepared(
        db, user_id, generation.id, at=at, script=script, settings=settings
    )


def start_prepared(
    db: Session,
    user_id: uuid.UUID,
    generation_id: uuid.UUID,
    *,
    at: datetime,
    script: str | None = None,
    settings: Any = None,
) -> uuid.UUID | None:
    """Start only this owned capture, after refreshing its binding and permission."""
    from app.agents import runs as agent_runs
    from app.documents import writer_base, writer_design

    generation = db.get(DocumentGeneration, generation_id)
    if (
        generation is None
        or generation.user_id != user_id
        or generation.mode is not GenerationMode.template_capture
    ):
        return None
    try:
        document = store.lock_document(db, user_id, generation.document_id)
    except store.NotFoundError:
        return None
    db.refresh(generation)
    if generation.status not in (GenerationStatus.waiting, GenerationStatus.queued):
        return None
    kept = _kept(db, document)
    reason = writer_base.wait_reason(db, user_id)
    if not kept:
        generation.status, generation.wait_reason = GenerationStatus.failed, None
        generation.error, generation.finished_at = "binding_gone", at
        logger.info("documents.design.no_bytes", extra={"generation_id": str(generation.id)})
    elif reason is not None:
        generation.status, generation.wait_reason = GenerationStatus.waiting, reason
    else:
        try:
            return writer_design.start(db, generation, at=at, script=script, settings=settings).id
        except agent_runs.ActiveRunsError:
            # Set here, not before the start: its refresh drops changes not yet flushed.
            generation.status, generation.wait_reason = GenerationStatus.queued, None
    db.flush()
    return None


def start_ready(db: Session, user_id: uuid.UUID, *, at: datetime, settings: Any = None) -> list:
    """Start the person's open captures that may start now (writer_base.start_ready's twin)."""
    started: list[uuid.UUID] = []
    open_ids = db.scalars(
        sa.select(DocumentGeneration.id)
        .where(
            DocumentGeneration.user_id == user_id,
            DocumentGeneration.mode == GenerationMode.template_capture,
            DocumentGeneration.status.in_((GenerationStatus.waiting, GenerationStatus.queued)),
        )
        .order_by(DocumentGeneration.created_at, DocumentGeneration.id)
    ).all()
    for generation_id in open_ids:
        run_id = start_prepared(db, user_id, generation_id, at=at, settings=settings)
        if run_id is not None:
            started.append(run_id)
    db.flush()
    return started


async def enqueue(final: FinalRun, generation_id: uuid.UUID) -> None:
    """Dispatch the exact capture prepared by the committed conversion, at most once."""
    from app import queue as queues
    from app.documents import generate

    def made() -> uuid.UUID | None:
        with final.sessions() as db:
            run_id = start_prepared(
                db, final.user_id, generation_id, at=final.clock(), settings=final.settings
            )
            store.commit(db)
            return run_id

    run_id = await asyncio.to_thread(made)
    if run_id is None:
        return
    queue = final.services.get(generate.QUEUE_SERVICE)
    own = queue is None
    if own:
        queue = queues.Queue(final.settings.redis_url.get_secret_value())
    try:
        await generate.enqueue_run(queue, run_id)
    except queues.QueueUnavailableError:
        logger.warning("documents.design.run_not_queued", extra={"run_id": str(run_id)})
    finally:
        if own:
            await queue.close()


# --- the result -------------------------------------------------------------------------------


def _slug(db: Session, user_id: uuid.UUID) -> str:
    while True:
        slug = "upload-" + "".join(secrets.choice(SLUG_CHARS) for _ in range(8))
        if store.user_template(db, user_id, slug) is None:
            return slug


def store_template(
    db: Session,
    document: Document,
    files: Mapping[str, str],
    inputs: Inputs,
    at: datetime,
    labels: Mapping[str, Mapping[str, str]] | None = None,
    measure: fidelity.Measure | None = None,
) -> DocumentUserTemplate:
    """The base CV's user template: a new row, or the next version of its own one."""
    row = db.execute(
        sa.select(DocumentUserTemplate)
        .where(
            DocumentUserTemplate.user_id == document.user_id,
            DocumentUserTemplate.base_document_id == document.id,
        )
        .order_by(DocumentUserTemplate.created_at.desc())
        .limit(1)
        .with_for_update()
        .options(
            undefer(DocumentUserTemplate.cv_html),
            undefer(DocumentUserTemplate.letter_html),
            undefer(DocumentUserTemplate.css),
        )
    ).scalar_one_or_none()
    if row is None:
        row = DocumentUserTemplate(
            user_id=document.user_id,
            slug=_slug(db, document.user_id),
            version=0,
            name=NAME["en"],
            source_document_id=document.source_document_id,
            base_document_id=document.id,
            created_at=at,
        )
        db.add(row)
    else:
        row.previous = {
            "version": row.version,
            "files": {
                "cv.html.j2": row.cv_html,
                "letter.html.j2": row.letter_html,
                "style.css": row.css,
            },
            "manifest": row.manifest,
        }
    row.version += 1
    manifest = manifest_of(row.slug, row.version, inputs, labels)
    manifest["capture"] = {"source": inputs.source, "notes": list(inputs.notes)}
    if measure is not None:  # numbers only: no text of the CV is kept (fidelity.summary)
        manifest["capture"]["fidelity"] = measure.summary()
    row.manifest = manifest
    row.cv_html, row.letter_html, row.css = (
        files["cv.html.j2"], files["letter.html.j2"], files["style.css"],
    )  # fmt: skip
    row.font_families = list(manifest["fonts"])
    row.updated_at = at
    db.flush()
    return row


def _error(run: AgentRun | None, status: RunStatus, found: RunState) -> str:
    if found.gone:
        return "binding_gone"
    if found.refused:
        return "invalid_output"
    if status is RunStatus.done:
        return "no_result"
    error = run.error if run is not None else None
    if error == failures.TOKEN_LIMIT:
        return "token_limit"
    if error == failures.BINDING_GONE:
        return "binding_gone"
    return "run_failed"


def _limit(pattern: str, name: str, value: str) -> re.Pattern[str]:
    return re.compile(re.escape(pattern).replace(re.escape("{" + name + "}"), value))


# The runner's stops on a limit (runner.py, `outcome_of`): its turns, its wall time (`_minutes`)
# and, compared whole, its token budget.
LIMITS = (
    _limit(failures.TURN_LIMIT, "turns", r"\d+"),
    _limit(failures.TIME_LIMIT, "minutes", r"\d+(?:\.\d+)? (?:seconds|minutes?)"),
)


def stopped_on_a_limit(status: RunStatus, run: AgentRun | None) -> bool:
    """Whether a run failed only because it reached its turn limit, its wall time or its token
    budget: what it accepted before still stands (B4 of #757). A cancel, a binding gone or any
    other failure is not one."""
    error = run.error if run is not None else None
    if status is not RunStatus.failed or error is None:
        return False
    return error == failures.TOKEN_LIMIT or any(p.fullmatch(error) for p in LIMITS)


def settle(
    sessions: sessionmaker[Session],
    run_id: uuid.UUID,
    status: RunStatus,
    found: RunState,
    at: datetime,
) -> tuple[uuid.UUID, int] | None:
    """The capture of a finished run (module docstring). Returns `(document_id, revision)` of a
    render to queue after the commit."""
    from app.documents import render_job

    with sessions() as db:
        owner = db.execute(
            sa.select(DocumentGeneration.user_id, DocumentGeneration.document_id).where(
                DocumentGeneration.run_id == run_id,
                DocumentGeneration.mode == GenerationMode.template_capture,
            )
        ).one_or_none()
        if owner is None:
            return None
        document = store.lock_document(db, owner.user_id, owner.document_id)
        generation = db.execute(
            sa.select(DocumentGeneration)
            .where(DocumentGeneration.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        if generation.status is not GenerationStatus.running:
            return None
        render = None
        # The best accepted version is kept when the run ends, or when it stopped on a limit
        # after accepting one; never on a cancel, nor when the upload or the document is gone.
        ended = status is RunStatus.done or (
            not found.gone and stopped_on_a_limit(status, db.get(AgentRun, run_id))
        )
        if ended and found.accepted is not None and found.inputs is not None:
            row = store_template(
                db, document, found.accepted, found.inputs, at, found.labels, found.measure
            )
            document.template_slug = row.slug
            document.updated_at = at
            db.flush()
            context = render_job.read_context(db, document, at)
            version, _ = store.snapshot(
                db,
                owner.user_id,
                document.id,
                Origin.template,
                at=at,
                generation_id=generation.id,
                corrections=context.corrections,
                template_slug=context.template.slug,
                template_version=context.template.version,
            )
            generation.status = GenerationStatus.done
            generation.version_number = version.number
            marked, fresh = render_job.mark_queued(
                db, owner.user_id, document.id, revision=document.draft_revision, bounded=False
            )
            render = (document.id, marked.draft_revision) if fresh else None
        elif status is RunStatus.cancelled:
            generation.status = GenerationStatus.cancelled
        else:
            generation.status = GenerationStatus.failed
            generation.error = _error(db.get(AgentRun, run_id), status, found)
        generation.finished_at = at
        store.commit(db)
        logger.info(
            "documents.design.settled",
            extra={
                "generation_id": str(generation.id),
                "status": generation.status.value,
                "code": generation.error,
                "submits": found.submits,
            },
        )
        return render


async def on_final(final: FinalRun) -> None:
    """The final hook of the design run: settle, then queue the render of the new version."""
    from app import queue as queues
    from app.documents import generate, render_job

    found = take(final.run_id)
    render = await asyncio.to_thread(
        settle, final.sessions, final.run_id, final.status, found, final.clock()
    )
    if render is None:
        return
    queue = final.services.get(generate.QUEUE_SERVICE)
    own = queue is None
    if own:
        queue = queues.Queue(final.settings.redis_url.get_secret_value())
    try:
        await render_job.enqueue(queue, render[0], render[1])
    except queues.QueueUnavailableError:
        logger.warning("documents.design.render_not_queued", extra={"run_id": str(final.run_id)})
    finally:
        if own:
            await queue.close()


def layout_json(inputs: Inputs) -> str:
    return json.dumps(inputs.layout, ensure_ascii=False, separators=(",", ":"))
