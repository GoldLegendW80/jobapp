"""How close a template written from an upload lies on the upload (task D28; SPEC-documents.md,
"Design capture of an upload", step 5): the base CV, whose content is this very upload's,
printed with the candidate at its loosest step (step 0, the captured density) and set on the
original's page 1.

    measure(original_page, original_lines, candidate_pdf) -> Measure   (pure but for PDFium)
    overlay(original_page, candidate_png) -> JPEG

- The page score: the ink of both pages at the original page image's size (pixels off each
  page's median grey by INK; the original's photo boxes are grey already, and the candidate's
  photo is the grey stand in) as intersection over union.
- The lines: the original's runs (the extraction of page 0, texts scrubbed) and the candidate's
  characters (pypdfium2 on our own render, never on an upload) are joined into lines (one
  baseline within BASELINE_PT, gaps under GAP_PT); each original line is matched to the unused
  candidate line of the most similar text (difflib, the first MATCH_CHARS characters, a ratio of
  MATCH_RATIO at least); a match is `in place` within PLACE_PT on both axes.
- `score`: the mean of the page score and the share of the original's lines in place, 0..1:
  what the passes of design_capture compare.
- `sentences`: the SAID largest offsets in plain words, for the writer ("the line "Experience"
  is 4.8pt lower than in the CV"). They hold the scrubbed text of the person's CV: they go to
  the model of the private run only, never to a log or a row; `summary` is numbers only, what
  `manifest.capture` keeps.
"""

from __future__ import annotations

import difflib
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any

from PIL import Image, ImageChops

from app.documents import render_client

INK = 64
BASELINE_PT = 1.5
GAP_PT = 12.0
MATCH_CHARS = 40
MATCH_RATIO = 0.6
PLACE_PT = 2.0
SAID = 15
OVERLAY_MAX_BYTES = 300_000
ORIGINAL, CANDIDATE, BOTH = (214, 48, 49), (38, 99, 214), (20, 20, 20)


@dataclass(frozen=True)
class Line:
    text: str
    x: float  # pt from the left
    y: float  # its baseline, pt from the top
    size: float  # pt


@dataclass(frozen=True)
class Offset:
    text: str  # the original's
    dx: float  # the candidate's x minus the original's: > 0 further right
    dy: float  # > 0 lower
    dsize: float


@dataclass(frozen=True)
class Measure:
    page: float  # ink intersection over union
    lines: int  # the original's
    matched: int
    in_place: int
    mean_dx: float
    mean_dy: float
    max_dx: float
    max_dy: float
    offsets: tuple[Offset, ...]  # the SAID largest

    @property
    def score(self) -> float:
        return round((self.page + (self.in_place / self.lines if self.lines else 0.0)) / 2, 4)

    def summary(self) -> dict[str, Any]:
        """Numbers only: what the person is shown and the template row keeps."""
        return {
            "score": self.score,
            "page": round(self.page, 4),
            "lines": self.lines,
            "matched": self.matched,
            "in_place": self.in_place,
            "mean_dy_pt": round(self.mean_dy, 1),
            "max_dy_pt": round(self.max_dy, 1),
            "mean_dx_pt": round(self.mean_dx, 1),
            "max_dx_pt": round(self.max_dx, 1),
        }

    def sentences(self) -> list[str]:
        return [_said(offset) for offset in self.offsets]


def _said(offset: Offset) -> str:
    parts = []
    if abs(offset.dy) >= 0.5:
        parts.append(f"{abs(offset.dy):.1f}pt {'lower' if offset.dy > 0 else 'higher'}")
    if abs(offset.dx) >= 0.5:
        parts.append(
            f"{abs(offset.dx):.1f}pt {'further right' if offset.dx > 0 else 'further left'}"
        )
    if abs(offset.dsize) >= 0.25:
        parts.append(f"{abs(offset.dsize):.1f}pt {'larger' if offset.dsize > 0 else 'smaller'}")
    where = " and ".join(parts) if parts else "in place"
    return f'The line "{offset.text[:MATCH_CHARS]}" is {where} than in the CV.'


def lines_of(chars: Iterable[tuple[str, float, float, float, float]]) -> list[Line]:
    """Lines of (text, x0, x1, baseline, size) pieces: one baseline within BASELINE_PT, a gap
    under GAP_PT and one size, left to right, top to bottom."""
    ordered = sorted(chars, key=lambda c: (round(c[3] / BASELINE_PT), c[1]))
    out: list[Line] = []
    current: list[tuple[str, float, float, float, float]] = []

    def flush() -> None:
        text = " ".join(" ".join(piece[0] for piece in current).split())
        if text:
            first = current[0]
            out.append(Line(text, round(first[1], 1), round(first[3], 1), round(first[4], 1)))

    for piece in ordered:
        if current and (
            abs(piece[3] - current[-1][3]) > BASELINE_PT
            or piece[1] - current[-1][2] > GAP_PT
            or abs(piece[4] - current[-1][4]) > 0.2
        ):
            flush()
            current = []
        current.append(piece)
    if current:
        flush()
    return out


def original_lines(layout: Mapping[str, Any], page: int = 0) -> list[Line]:
    """The lines of a page of design_capture's layout (its runs, texts scrubbed, as arrays of
    `run_fields`)."""
    fields = {name: at for at, name in enumerate(layout.get("run_fields", ()))}
    pieces = []
    for run in layout["read"][page]["runs"] if layout.get("read") else ():
        baseline = run[fields["baseline"]] or run[fields["y1"]]
        pieces.append((run[0], run[fields["x0"]], run[fields["x1"]], baseline, run[fields["size"]]))
    return lines_of(pieces)


def candidate_lines(pdf: bytes) -> list[Line]:
    """The lines of page 1 of a PDF the app's renderer made (our own file, never an upload)."""
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    pieces: list[tuple[str, float, float, float, float]] = []
    with render_client.PDFIUM:
        document = pdfium.PdfDocument(pdf)
        try:
            page = document[0]
            top = page.get_size()[1]
            text = page.get_textpage()
            x, y = ctypes.c_double(), ctypes.c_double()
            for index in range(text.count_chars()):
                ch = text.get_text_range(index, 1)
                if not ch or ch.isspace():
                    pieces.append((" ", 0.0, 0.0, 0.0, 0.0))  # a word ends
                    continue
                left, _, right, _ = text.get_charbox(index, loose=True)
                pdfium_c.FPDFText_GetCharOrigin(text.raw, index, ctypes.byref(x), ctypes.byref(y))
                size = pdfium_c.FPDFText_GetFontSize(text.raw, index)
                pieces.append((ch, left, right, top - y.value, abs(size)))
            text.close()
            page.close()
        finally:
            document.close()
    # characters into words: a space, another baseline or a jump back ends one
    words: list[list] = []
    joined = False
    for piece in pieces:
        if piece[0] == " ":
            joined = False
            continue
        last = words[-1] if words else None
        if joined and last and abs(piece[3] - last[3]) <= 0.3 and piece[1] >= last[2] - 0.5:
            last[0] += piece[0]
            last[2] = piece[2]
        else:
            words.append(list(piece))
        joined = True
    return lines_of(tuple(word) for word in words)


def match(original: Sequence[Line], candidate: Sequence[Line]) -> list[tuple[Line, Line]]:
    used: set[int] = set()
    pairs = []
    for line in original:
        best, ratio = None, 0.0
        matcher = difflib.SequenceMatcher(None, b=line.text[:MATCH_CHARS], autojunk=False)
        for at, other in enumerate(candidate):
            if at in used:
                continue
            matcher.set_seq1(other.text[:MATCH_CHARS])
            if matcher.real_quick_ratio() <= ratio or matcher.quick_ratio() <= ratio:
                continue
            found = matcher.ratio()
            if found > ratio:
                best, ratio = at, found
        if best is not None and ratio >= MATCH_RATIO:
            used.add(best)
            pairs.append((line, candidate[best]))
    return pairs


def _ink(image: Image.Image) -> Image.Image:
    grey = image.convert("L")
    histogram = grey.histogram()
    half, seen, back = grey.width * grey.height / 2, 0, 255
    for level, count in enumerate(histogram):
        seen += count
        if seen >= half:
            back = level
            break
    return grey.point([255 if abs(level - back) > INK else 0 for level in range(256)])


def _pages(original: bytes, candidate: bytes) -> tuple[Image.Image, Image.Image]:
    first = Image.open(io.BytesIO(original)).convert("L")
    second = Image.open(io.BytesIO(candidate)).convert("L")
    if second.size != first.size:
        second = second.resize(first.size, Image.Resampling.BILINEAR)
    return first, second


def page_score(original: bytes, candidate: bytes) -> float:
    first, second = (_ink(page) for page in _pages(original, candidate))
    both = ImageChops.multiply(first, second).histogram()[255]
    either = ImageChops.lighter(first, second).histogram()[255]
    return both / either if either else 0.0


def overlay(original: bytes, candidate: bytes) -> bytes:
    """The original's ink in red, the candidate's in blue, both in black: a JPEG."""
    first, second = (_ink(page) for page in _pages(original, candidate))
    both = ImageChops.multiply(first, second)
    image = Image.new("RGB", first.size, (255, 255, 255))
    image.paste(ORIGINAL, mask=first)
    image.paste(CANDIDATE, mask=second)
    image.paste(BOTH, mask=both)
    for quality in (80, 65, 50):
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality)
        if out.tell() <= OVERLAY_MAX_BYTES:
            return out.getvalue()
    return out.getvalue()


def measure_lines(original: Sequence[Line], candidate: Sequence[Line], page: float) -> Measure:
    pairs = match(original, candidate)
    offsets = [
        Offset(a.text, round(b.x - a.x, 1), round(b.y - a.y, 1), round(b.size - a.size, 1))
        for a, b in pairs
    ]
    placed = sum(1 for o in offsets if abs(o.dx) <= PLACE_PT and abs(o.dy) <= PLACE_PT)
    largest = sorted(offsets, key=lambda o: -max(abs(o.dx), abs(o.dy), abs(o.dsize) * 2))
    largest = [o for o in largest if max(abs(o.dx), abs(o.dy)) >= 0.5 or abs(o.dsize) >= 0.25]
    return Measure(
        page=page,
        lines=len(original),
        matched=len(pairs),
        in_place=placed,
        mean_dx=mean(abs(o.dx) for o in offsets) if offsets else 0.0,
        mean_dy=mean(abs(o.dy) for o in offsets) if offsets else 0.0,
        max_dx=max((abs(o.dx) for o in offsets), default=0.0),
        max_dy=max((abs(o.dy) for o in offsets), default=0.0),
        offsets=tuple(largest[:SAID]),
    )


def measure(original_page: bytes, original: Sequence[Line], pdf: bytes, png: bytes) -> Measure:
    """The candidate's PDF and its page 1 image (`png`, at the original image's width) against
    the original's page image and lines."""
    return measure_lines(original, candidate_lines(pdf), page_score(original_page, png))
