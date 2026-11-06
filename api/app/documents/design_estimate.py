"""The token estimate of one design capture and its ceiling (task D25b; SPEC-documents.md, "Design
capture of an upload", "Limits"). The same turn by turn count as a writer run (estimate.py), with
the page images in it.

- `F` and `P` as for a writer: ingestion's fixed context per turn and the prompt
  (`prompts/design.md`), paid at every turn.
- `R`, read once and sent again at every later turn: the layout extraction (`read_design`),
  LAYOUT_LOW_CHARS for a typical CV page, LAYOUT_HIGH_CHARS for two full pages of runs (the
  tool's caps: 400 runs a page of 160 characters each, with their boxes, fonts and measures,
  and the measured spec of design_spec, D28).
- `I`, the images, each IMAGE_TOKENS (ingestion's figure for one page image, G0): for a PDF the
  two pages and, per accepted submit, one view_render (the render laid over the original); the
  high case views each page twice and makes every pass. A DOCX has no page: the render only.
- `O`, one submit of the three files: TEMPLATE_LOW_CHARS (the bundled templates hold 6,000 to
  12,000 characters), TEMPLATE_HIGH_CHARS for files near their caps.
- Low: `n = 5` turns (the reads, the pages, one submit, the render, the closing line), one
  submit: `input = n*(F+P) + (n-1)*(R_low + I_low) + (n-2)*O_low`, `output = O_low + n*T`.
- High: `n = DOCUMENTS_DESIGN_MAX_TURNS`, `k = 1 + DOCUMENTS_DESIGN_PASSES` submits. A submit
  and the image that follows it are sent again only at the turns after them: the first at
  turn FIRST_SUBMIT, then one every two turns (a view_render, a submit), so the j-th of them
  (from 0) is sent again `r_j = max(n - FIRST_SUBMIT - 2j, 0)` times; the reads and the pages
  at every turn after the first:
  `input = n*(F+P) + (n-1)*(R_high + I_pages) + sum_j r_j*(O_high + I_render)`,
  `output = k*O_high + n*T`.
- The ceiling is twice the high figure, as for a writer.

Pure but for `for_capture`, which reads the upload's type, never its content.
"""

from __future__ import annotations

import math
import uuid
from fractions import Fraction

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.documents.estimate import TEXT_OUTPUT, as_json, prompt_chars
from app.documents.models import Document
from app.ingestion.documents import DOCX_MIME
from app.ingestion.estimate import IMAGE_TOKENS, F, RunEstimate, text_tokens
from app.ingestion.models import IngestionDocument

__all__ = ["as_json", "capture", "for_capture"]

LAYOUT_LOW_CHARS = 15_000
# The texts are capped at parser_client.DESIGN_TEXT (40,000 characters, both pages), each run
# an array of about 60 characters besides (design_capture.RUN_FIELDS), and the measured spec
# (design_spec: 24 styles, 20 headings, 60 shapes of each kind a page) under 25,000.
LAYOUT_HIGH_CHARS = 120_000
TEMPLATE_LOW_CHARS = 12_000
TEMPLATE_HIGH_CHARS = 40_000
TURNS_LOW = 5
TURNS_HIGH_DEFAULT = 14  # DOCUMENTS_DESIGN_MAX_TURNS
FIRST_SUBMIT = 4  # read_design, view_page 0 and 1, then the first submit
PASSES_DEFAULT = 4  # DOCUMENTS_DESIGN_PASSES


def capture(
    prompt: int,
    *,
    pages: int = 2,
    turns: int = TURNS_HIGH_DEFAULT,
    passes: int = PASSES_DEFAULT,
) -> RunEstimate:
    """One capture whose prompt has `prompt` characters, of a file with `pages` page images
    (0 for a DOCX) (module docstring)."""
    fixed = F + text_tokens(prompt)
    read_low, read_high = text_tokens(LAYOUT_LOW_CHARS), text_tokens(LAYOUT_HIGH_CHARS)
    images_low = IMAGE_TOKENS * (pages + 1)
    pages_high = IMAGE_TOKENS * 2 * pages
    o_low, o_high = text_tokens(TEMPLATE_LOW_CHARS), text_tokens(TEMPLATE_HIGH_CHARS)
    n_low, n_high, k = TURNS_LOW, max(turns, TURNS_LOW), 1 + max(passes, 0)
    low_input = n_low * fixed + (n_low - 1) * (read_low + images_low) + (n_low - 2) * o_low
    low_output = o_low + n_low * TEXT_OUTPUT
    resent = sum(max(n_high - FIRST_SUBMIT - 2 * j, 0) for j in range(k))
    high_input = (
        n_high * fixed + (n_high - 1) * (read_high + pages_high) + resent * (o_high + IMAGE_TOKENS)
    )
    high_output = k * o_high + n_high * TEXT_OUTPUT
    high = high_input + high_output
    return RunEstimate(
        steps=1,
        reused=0,
        low=math.ceil(low_input + low_output),
        high=high,
        ceiling=2 * high,
        low_input=Fraction(low_input),
        low_fresh=fixed + read_low + images_low + o_low,
        low_output=low_output,
        high_input=Fraction(high_input),
        high_output=high_output,
    )


def for_capture(db: Session, user_id: uuid.UUID, document: Document) -> RunEstimate:
    """The estimate of a capture of this base CV now, with the module's settings."""
    from app.documents.writer_cv import setting

    mime = None
    if document.source_document_id is not None:
        mime = db.scalar(
            sa.select(IngestionDocument.mime).where(
                IngestionDocument.id == document.source_document_id,
                IngestionDocument.user_id == user_id,
            )
        )
    return capture(
        prompt_chars("design"),
        pages=0 if mime == DOCX_MIME else 2,
        turns=int(setting("documents_design_max_turns")),
        passes=int(setting("documents_design_passes")),
    )
