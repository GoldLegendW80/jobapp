"""The token estimate of one design capture and its ceiling (task D25b; SPEC-documents.md, "Design
capture of an upload", "Limits"). The same turn by turn count as a writer run (estimate.py), with
the page images in it.

- `F` and `P` as for a writer: ingestion's fixed context per turn and the prompt
  (`prompts/design.md`), paid at every turn.
- `R`, read once and sent again at every later turn: the layout extraction (`read_design`),
  LAYOUT_LOW_CHARS for a typical CV page, LAYOUT_HIGH_CHARS for two full pages of runs (the
  tool's caps: 200 runs a page of 80 characters each, with their boxes and fonts).
- `I`, the images, each IMAGE_TOKENS (ingestion's figure for one page image, G0): for a PDF the
  two pages and the render of the fidelity pass; the high case views each page twice. A DOCX
  has no page: the render only.
- `O`, one submit of the three files: TEMPLATE_LOW_CHARS (the bundled templates hold 6,000 to
  12,000 characters), TEMPLATE_HIGH_CHARS for files near their caps.
- Low: `n = 5` turns (the reads, the pages, one submit, the render, the closing line), one
  submit: `input = n*(F+P) + (n-1)*(R_low + I_low) + (n-2)*O_low`, `output = O_low + n*T`.
- High: `n = DOCUMENTS_GENERATION_MAX_TURNS`, `k = 1 + DOCUMENTS_DESIGN_PASSES` submits:
  `input = n*(F+P) + (n-1)*(R_high + I_high + k*O_high)`, `output = k*O_high + n*T`.
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

LAYOUT_LOW_CHARS = 12_000
LAYOUT_HIGH_CHARS = 70_000
TEMPLATE_LOW_CHARS = 12_000
TEMPLATE_HIGH_CHARS = 40_000
TURNS_LOW = 5
TURNS_HIGH_DEFAULT = 10  # DOCUMENTS_GENERATION_MAX_TURNS
PASSES_DEFAULT = 2  # DOCUMENTS_DESIGN_PASSES


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
    images_high = IMAGE_TOKENS * (2 * pages + 1)
    o_low, o_high = text_tokens(TEMPLATE_LOW_CHARS), text_tokens(TEMPLATE_HIGH_CHARS)
    n_low, n_high, k = TURNS_LOW, max(turns, TURNS_LOW), 1 + max(passes, 0)
    low_input = n_low * fixed + (n_low - 1) * (read_low + images_low) + (n_low - 2) * o_low
    low_output = o_low + n_low * TEXT_OUTPUT
    high_input = n_high * fixed + (n_high - 1) * (read_high + images_high + k * o_high)
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
        turns=int(setting("documents_generation_max_turns")),
        passes=int(setting("documents_design_passes")),
    )
