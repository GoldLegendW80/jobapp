"""The layout of the first pages of a PDF, for the design capture of an uploaded CV (task D25a of
the documents plan; SPEC-documents.md, "Design capture of an upload", step 1). Runs only inside
the locked down child of the parser service: main.py imports this file after the lockdown and
calls `handle`. The bytes are hostile, and so is everything PDFium makes of them.

    handle(data, options, budget) -> the whole answer, as app/ingestion/parser_client.py reads it

`options` is empty: the first PAGES_READ pages are read, never more. The answer, every length in
tenths of a point from the top left corner of the page, every number an integer:

    {"ok": true, "kind": "design_pdf", "pages": N, "scanned": bool, "truncated": bool,
     "notes": [{"code": ...}],
     "fonts": [{"name": as embedded, "weight": 0 or 100..900, "italic": bool, "serif": bool,
                "monospace": bool, "symbolic": bool, "embedded": bool}],
     "read": [{"index": i, "width": W, "height": H, "margins": [top, right, bottom, left] | null,
               "background": "#rrggbb" | null,
               "runs": [{"text": ..., "box": [x0, y0, x1, y1], "font": index of `fonts`,
                         "size": tenths of a point, "colour": "#rrggbb", "baseline": y,
                         "spacing": thousandths of an em, "caps": bool, "pitch": tenths}],
               "shapes": [{"type": "fill" | "rule" | "outline", "box": [...],
                           "colour": "#rrggbb", "width": stroke width, "radius": tenths}],
               "dots": [{"box": [...], "colour": "#rrggbb"}],
               "images": [{"box": [...], "round": bool}]}],
     "samples": [{"font": index, "page": index, "text": a word, "size": tenths,
                  "width": tenths, "crop": base64 of a grey PNG}]}

- A run is the characters PDFium's text page gives in a row with one font, size, colour and
  direction on one line; a jump back, a gap wider than GAP_EM times the size, or another line
  ends it. The direction is the one of four (right, up, left, down) the glyphs advance in most,
  so text turned a quarter or a half turn, or set with a negative size, still makes runs. Text
  turned about 28 to 62 degrees off an axis comes back letter by letter: PDFium's character
  boxes are upright, and overlap too much along such a line.
- A run's size is the height of its glyphs on the page: the size of `Tf` (its absolute value)
  times the scale across the baseline of the text matrix and of every transformation above it
  (`_height`), as PDF writers put the size in any of them (`1 Tf 11 0 0 11 x y Tm`, or
  `0.1 0 0 0.1 0 0 cm` around `110 Tf`). It is clamped to 0..LENGTH_MAX, as any length is.
- Only text that is painted is read: filled (render modes 0, 2, 4, 6, in its fill colour) or
  stroked alone (1, 5, in its stroke colour). Invisible text (3: the text layer of an OCR'd
  scan), text that only clips (7), transparent text and text outside the page are left out.
  The runs of a page are put in reading order by `reading_order`.
- `margins` are those of the text: where the runs of the page start and end.
- A shape is a path painted with a colour that is not transparent: a fill, or a stroke that is a
  rule (thinner than RULE_MAX on one side) or an outline. A stroke's width is the one of `w`
  scaled by the path's own matrix and the forms around it (by the square root of their
  determinant: exact for a uniform scale, the mean of the two for an uneven one), clamped to
  0..STROKE_MAX. Shading, clipping and the pixels of an image are never read; an image is its
  box only.
- The measures (documents D28, the copy of a CV by measurement): a run's `baseline` is the median
  of its characters' origins across the line (FPDFText_GetCharOrigin); its `spacing` the median,
  over the run's pairs of letters in a word, of the gap between a character's advance end and
  the next one's start (the loose boxes PDFium builds from the advance), in thousandths of the
  run's size; `caps` that its letters, two at least, are all capitals; its `pitch` the distance
  from its baseline to the nearest one below of a run of the same font and size whose start or
  end it overlaps (the same column), within PITCH_LOW_EM..PITCH_HIGH_EM times the size, else 0.
- A shape's `radius` is read from its path segments: a path of CURVES_MIN Bezier points or more
  has the largest distance, along a side of its box, from a point on that side to the nearest
  corner (the end of the rounded corner), at most half its shorter side; a plain rectangle 0. A
  filled path DOT_LOW..DOT_HIGH points wide and as high (within ROUND), drawn with curves, is a
  `dot` (a bullet: DOTS_MAX per page), even under SHAPE_MIN. A fill that covers BACKGROUND of the
  page on both sides gives the page its `background` (the first one painted); it stays a shape.
  An image is `round` when one path of its clip is drawn with curves and is as wide as high.
- `samples`: for each font (the fonts with most characters first), up to SAMPLES_PER_FONT words
  of SAMPLE_LETTERS letters or more, the longest of the upright runs, each with its size, its
  width (the sum of its characters' advances: kerning and spacing left out) and a grey PNG crop
  of its ink drawn at SAMPLE_SCALE times the page image's scale, SAMPLE_BYTES for all of them
  together (`samples_limit` past it). Only the worker's font measure reads them
  (documents/font_measure.py): they are never logged, stored or shown to a model.
- `scanned` is true when no page read has a run and an image covers at least half of one of
  them: the design is then in the picture alone, and the extraction says so.

Caps: RUNS_MAX runs, SHAPES_MAX shapes (the largest kept), IMAGES_MAX images and OBJECTS_MAX
objects looked at per page, CHARS_MAX characters per page, TEXT_MAX characters and FONTS_MAX
fonts per answer. Each one met is a note and `truncated`. Together they hold the answer under
parser_client.DESIGN_MAX_ANSWER (tests/ingestion/parser/test_design_pdf.py builds the largest).
A value the client would refuse is cleaned here, so one odd value never costs the whole answer:
a run's text is cut at RUN_TEXT_MAX after NFC too, the page count is given as PAGES_MAX at most,
and a page under a tenth of a point on a side is not read (`pages_not_read`).

What comes back otherwise, `{"ok": false, "failure": "file", "code": ...}`, is pdf_render.py's:
`unreadable`, `protected`, `encryption`, `bomb` (a page whose content decodes to more than
pdf_render.CONTENT_MAX, weighed before PDFium loads it, or the memory of the child), `too_slow`.

Only main.py may import this file in production. The tests import it too, to call `handle` in
their own process on files they make.
"""

import base64
import ctypes
import io
import math
import statistics
import unicodedata

import pdf_render  # sandbox/pdf_render.py, beside this file: the opening, the weighing, the guards
import pypdfium2.raw as pdfium_c

PAGES_READ = 2
RUNS_MAX = 1_500
SHAPES_MAX = 400
IMAGES_MAX = 50
OBJECTS_MAX = 5_000
FORM_DEPTH_MAX = 4
CHARS_MAX = 20_000
TEXT_MAX = 40_000
RUN_TEXT_MAX = 300
FONTS_MAX = 64
FONT_NAME_MAX = 128
GAP_EM = 2.0
RULE_MAX = 3.0  # points: a stroke box thinner than this on one side is a rule
SHAPE_MIN = 2.0  # points: a path smaller than this on both sides (a dot of an icon) is left out
COVER = pdf_render.COVER
BUDGET_EVERY = 500  # characters or objects between two looks at the budget
LENGTH_MAX = 144_000  # tenths of a point: parser_client.DESIGN_LENGTH, the most a size may be
STROKE_MAX = 1_000  # tenths of a point: parser_client.DESIGN_STROKE
PAGES_MAX = 100_000  # parser_client.MAX_PAGES: a file that claims more is given as this
FILLED = frozenset({0, 2, 4, 6})  # text render modes that fill the glyphs
STROKED = frozenset({1, 5})  # text render modes that only stroke them
RIGHT, UP, LEFT, DOWN = range(4)  # the direction a line of text runs in on the page
GUTTER = 12.0  # points: the narrowest gap between two columns
ORDER_DEPTH_MAX = 8
COLUMN_BANDS_MIN = 3  # bands a block must span to be read as columns
# The measures of D28 (the design capture copies a CV by measurement).
SPACING_LOW, SPACING_HIGH = -500, 1_000  # thousandths of an em: a run's letter spacing
PITCH_LOW_EM, PITCH_HIGH_EM = 0.8, 3.0  # a line pitch outside these times the size is no pitch
DOTS_MAX = 200
DOT_LOW, DOT_HIGH = 0.8, 12.0  # points: the diameter of a bullet dot
ROUND = 0.12  # a dot or a round clip: its width and height within this share of each other
CURVES_MIN = 4  # Bezier points a rounded corner set or a circle has at least
SEGMENTS_MAX = 64  # segments of one path or clip looked at for its corners
EDGE_NEAR = 0.3  # points: a point this close to a side of the box lies on it
BACKGROUND = 0.98  # of the page on both sides: a fill this large is the page's background
SAMPLES_PER_FONT = 30
SAMPLE_LETTERS = 3  # letters of a sampled word at least
SAMPLE_SCALE = 4  # times the scale of the page image (pdf_render.LONG_SIDE)
SAMPLE_BYTES = 400_000  # PNG bytes of every crop of an answer together
SAMPLE_SIDE_MAX = 2_400  # pixels: a word crop larger than this on a side is not drawn
SAMPLE_PAD = 1.0  # points around a word's box
SAMPLE_TEXT_MAX = 40  # characters of a sampled word
# PDF font descriptor flags (ISO 32000, 9.8.2).
_FIXED, _SERIF, _SYMBOLIC, _ITALIC = 1, 2, 4, 64


class _Stop(Exception):  # noqa: N818, an outcome, not an error of this program
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _tenths(value: float) -> int:
    return round(value * 10)


def _bounded(points: float, high: int) -> int:
    """`points` in tenths of a point, within 0..high: NaN and below zero are 0, infinity high."""
    if not points > 0:
        return 0
    return min(_tenths(min(points, high / 10)), high)


def _height(a: float, b: float, c: float, d: float) -> float:
    """How far the matrix (a, b, c, d) puts one unit of text space from the baseline: the scale
    of a glyph's height whether the text is upright, turned, slanted or narrowed."""
    advance = math.hypot(a, b)
    return abs(a * d - b * c) / advance if advance > 0 else math.hypot(c, d)


def _way(x: float, y: float) -> int:
    """The direction on the page (PDF space, y upwards) the advance (x, y) points in most."""
    if abs(x) >= abs(y):
        return LEFT if x < 0 else RIGHT
    return UP if y > 0 else DOWN


def _along(box: list[int], way: int) -> tuple[int, int, int, int]:
    """`box` ([x0, y0, x1, y1], y downwards) turned so that a run going `way` reads along its
    first axis: (start, low, end, high), start and end along the run, low and high across."""
    x0, y0, x1, y1 = box
    if way == LEFT:
        return -x1, y0, -x0, y1
    if way == UP:
        return -y1, x0, -y0, x1
    if way == DOWN:
        return y0, x0, y1, x1
    return x0, y0, x1, y1


def _colour(red: int, green: int, blue: int) -> str:
    return f"#{red & 0xFF:02x}{green & 0xFF:02x}{blue & 0xFF:02x}"


def _clean_name(raw: bytes) -> str:
    text = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    kept = "".join(ch for ch in text if ch.isprintable() and not 0xD800 <= ord(ch) <= 0xDFFF)
    return kept[:FONT_NAME_MAX]


def _kept(code: int) -> str:
    """The character of `code`, or '' for what is not text (a control, a surrogate, an unmapped
    glyph)."""
    if not 0x20 <= code <= 0x10FFFF or 0x7F <= code <= 0x9F or 0xD800 <= code <= 0xDFFF:
        return ""
    ch = chr(code)
    return "" if unicodedata.category(ch) in ("Cc", "Cs", "Cn") else ch


# --- reading order ------------------------------------------------------------------------------


def _free(spans: list[tuple[float, float]], low: float, high: float) -> list[tuple[float, float]]:
    """The intervals of [low, high] that no span covers, GUTTER wide at least."""
    free, reach = [], low
    for start, end in sorted(spans):
        if start - reach >= GUTTER:
            free.append((reach, start))
        reach = max(reach, end)
    if high - reach >= GUTTER:
        free.append((reach, high))
    return free


def _shared(first: list[tuple[float, float]], second: list[tuple[float, float]]):
    out = []
    for a0, a1 in first:
        for b0, b1 in second:
            low, high = max(a0, b0), min(a1, b1)
            if high - low >= GUTTER:
                out.append((low, high))
    return out


def _bands(items: list[tuple[int, float, float, float, float]]):
    """The items cut into horizontal bands, top to bottom. Two items share a band when the
    middle halves of their heights meet: loose boxes of lines set tight touch, their cores
    do not."""
    bands, reach = [], None
    for item in sorted(items, key=lambda it: (it[2] + it[4], it[1])):
        quarter = (item[4] - item[2]) / 4
        core_top, core_bottom = item[2] + quarter, item[4] - quarter
        if reach is None or core_top > reach:
            bands.append([item])
            reach = core_bottom
        else:
            bands[-1].append(item)
            reach = max(reach, core_bottom)
    return bands


def _cut(items, free: list[tuple[float, float]]) -> tuple[float, float] | None:
    """The first free interval with items on both sides: a gutter between two columns."""
    for low, high in free:
        if any(it[3] <= low for it in items) and any(it[1] >= high for it in items):
            return low, high
    return None


def _order(items: list[tuple[int, float, float, float, float]], depth: int) -> list[int]:
    if len(items) <= 1 or depth >= ORDER_DEPTH_MAX:
        return [it[0] for it in sorted(items, key=lambda it: (it[2], it[1]))]
    low, high = min(it[1] for it in items), max(it[3] for it in items)
    # Consecutive bands that leave one vertical strip free make a block. A block of
    # COLUMN_BANDS_MIN bands or more with items on both sides of that strip is read column
    # by column; any other block band by band, each left to right.
    blocks: list[tuple[list[list], list[tuple[float, float]]]] = []
    for band in _bands(items):
        free = _free([(it[1], it[3]) for it in band], low, high)
        shared = _shared(blocks[-1][1], free) if blocks else []
        if shared:
            blocks[-1][0].append(band)
            blocks[-1] = (blocks[-1][0], shared)
        else:
            blocks.append(([band], free))
    out: list[int] = []
    for bands, free in blocks:
        together = [it for band in bands for it in band]
        cut = _cut(together, free) if len(bands) >= COLUMN_BANDS_MIN else None
        if cut is None:
            for band in bands:
                out += [it[0] for it in sorted(band, key=lambda it: (it[1], it[2]))]
            continue
        left = [it for it in together if it[3] <= cut[0]]
        right = [it for it in together if it[3] > cut[0]]
        out += _order(left, depth + 1) + _order(right, depth + 1)
    return out


def reading_order(boxes: list[list[int]]) -> list[int]:
    """The indexes of `boxes` ([x0, y0, x1, y1], y downwards) in reading order: bands top to
    bottom, each left to right; where COLUMN_BANDS_MIN bands or more in a row leave a gutter of
    GUTTER points free with text on both sides, that block is read column by column, left
    column first. A sort per level, ORDER_DEPTH_MAX levels at most."""
    items = [(i, b[0] / 10, b[1] / 10, b[2] / 10, b[3] / 10) for i, b in enumerate(boxes)]
    return _order(items, 0)


# --- one page -----------------------------------------------------------------------------------


class _Page:
    """What one page holds, read through PDFium."""

    def __init__(self, page, fonts: "_Fonts", room: list[int], budget) -> None:
        self.page, self.fonts, self.room, self.budget = page, fonts, room, budget
        left, bottom, right, top = page.get_bbox()
        self.left, self.top = left, top
        self.width, self.height = right - left, top - bottom
        self.notes: set[str] = set()
        self.runs: list[dict] = []
        self.shapes: list[tuple[float, int, dict]] = []
        self.images: list[dict] = []
        self.dots: list[dict] = []
        self.background: str | None = None
        self.words: list[tuple] = []  # (font, size, text, advance, box in PDF space)
        self.looked = 0

    def box(self, left: float, top: float, right: float, bottom: float) -> list[int] | None:
        """[x0, y0, x1, y1] in tenths of a point from the top left corner, clipped to the page,
        or None when nothing of it is on the page or a side is not a number."""
        if not all(math.isfinite(side) for side in (left, top, right, bottom)):
            return None
        x0 = max(min(left, right) - self.left, 0.0)
        x1 = min(max(left, right) - self.left, self.width)
        y0 = max(self.top - max(top, bottom), 0.0)
        y1 = min(self.top - min(top, bottom), self.height)
        if x1 < x0 or y1 < y0:
            return None
        return [_tenths(x0), _tenths(y0), _tenths(x1), _tenths(y1)]

    # --- text

    def read_text(self) -> None:
        text_page = pdfium_c.FPDFText_LoadPage(self.page.raw)
        if not text_page:
            return
        try:
            self._chars(text_page)
        finally:
            pdfium_c.FPDFText_ClosePage(text_page)

    def _chars(self, text_page) -> None:
        count = pdfium_c.FPDFText_CountChars(text_page)
        if count > CHARS_MAX:
            self.notes.add("chars_limit")
        run: dict | None = None
        rect = pdfium_c.FS_RECTF()
        matrix = pdfium_c.FS_MATRIX()
        origin_x, origin_y = ctypes.c_double(), ctypes.c_double()
        red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
        paint = [ctypes.byref(part) for part in (red, green, blue, alpha)]
        for index in range(min(count, CHARS_MAX)):
            if index % BUDGET_EVERY == 0 and self.budget.spent():
                self._close(run)
                raise _Stop("time_limit")
            code = pdfium_c.FPDFText_GetUnicode(text_page, index)
            if pdfium_c.FPDFText_IsGenerated(text_page, index) == 1:
                if code in (10, 13):
                    run = self._close(run)
                elif code == 32 and run is not None:
                    run["chars"].append(" ")
                    self._word_end(run)
                continue
            ch = _kept(code)
            found = pdfium_c.FPDFText_GetTextObject(text_page, index)
            if not ch or not found:
                continue
            mode = pdfium_c.FPDFTextObj_GetTextRenderMode(found)
            if mode in FILLED:
                painted = pdfium_c.FPDFText_GetFillColor(text_page, index, *paint)
            elif mode in STROKED:
                painted = pdfium_c.FPDFText_GetStrokeColor(text_page, index, *paint)
            else:
                continue
            if not painted or not alpha.value:
                continue
            if not pdfium_c.FPDFText_GetLooseCharBox(text_page, index, ctypes.byref(rect)):
                continue
            if not pdfium_c.FPDFText_GetMatrix(text_page, index, ctypes.byref(matrix)):
                continue
            box = self.box(rect.left, rect.top, rect.right, rect.bottom)
            if box is None:
                continue
            font = self.fonts.index(pdfium_c.FPDFTextObj_GetFont(found))
            if font is None:
                self.notes.add("fonts_limit")
                continue
            size = pdfium_c.FPDFText_GetFontSize(text_page, index)
            sign = -1.0 if size < 0 else 1.0
            way = _way(sign * matrix.a, sign * matrix.b)
            height = _height(matrix.a, matrix.b, matrix.c, matrix.d)
            tenths = _bounded(abs(size) * height, LENGTH_MAX)
            key = (font, tenths, _colour(red.value, green.value, blue.value), way)
            turned = _along(box, way)
            if run is not None and not self._continues(run, key, turned, ch):
                run = self._close(run)
            if run is None:
                run = {"key": key, "chars": [], "box": list(box), "end": turned[0]}
                run.update(across=(turned[1], turned[3]), baselines=[], gaps=[], last=None)
                run.update(word=None, words=[])
            run["chars"].append(ch)
            if ch.isspace():
                self._word_end(run)
            else:
                exact = _along([rect.left, -rect.top, rect.right, -rect.bottom], way)
                self._measure(run, ch, exact, rect, way)
                if pdfium_c.FPDFText_GetCharOrigin(
                    text_page, index, ctypes.byref(origin_x), ctypes.byref(origin_y)
                ) and math.isfinite(origin_y.value):
                    run["baselines"].append(self.top - origin_y.value)
                run["box"] = [
                    min(run["box"][0], box[0]),
                    min(run["box"][1], box[1]),
                    max(run["box"][2], box[2]),
                    max(run["box"][3], box[3]),
                ]
                low, high = run["across"]
                run["across"] = (min(low, turned[1]), max(high, turned[3]))
                run["end"] = turned[2]
        self._close(run)

    @staticmethod
    def _continues(run: dict, key: tuple, turned: tuple, ch: str) -> bool:
        """Whether the character `ch`, of `key` and the box `turned` (by `_along`), goes on
        the line of `run`."""
        if run["key"] != key or len(run["chars"]) >= RUN_TEXT_MAX:
            return False
        if ch.isspace():
            return True
        low, high = run["across"]
        overlap = min(high, turned[3]) - max(low, turned[1])
        if overlap * 2 < min(high - low, turned[3] - turned[1]):
            return False
        em = max(key[1], 10)
        return run["end"] - em / 2 <= turned[0] <= run["end"] + GAP_EM * em

    @staticmethod
    def _measure(run: dict, ch: str, exact: tuple, rect, way: int) -> None:
        """The gap to the letter before (`exact` is the character's loose box along the run, in
        points) and the word this letter belongs to."""
        if run["last"] is not None and ch.isalpha():
            run["gaps"].append(exact[0] - run["last"])
        run["last"] = exact[2] if ch.isalpha() else None
        if not ch.isalpha() or way != RIGHT:
            _Page._word_end(run)
            return
        word = run["word"]
        if word is None:
            word = run["word"] = {
                "chars": [],
                "advance": 0.0,
                "box": [rect.left, rect.bottom, rect.right, rect.top],
            }
        word["chars"].append(ch)
        word["advance"] += max(exact[2] - exact[0], 0.0)
        box = word["box"]
        word["box"] = [min(box[0], rect.left), min(box[1], rect.bottom),
                       max(box[2], rect.right), max(box[3], rect.top)]  # fmt: skip

    @staticmethod
    def _word_end(run: dict) -> None:
        word, run["word"] = run.get("word"), None
        run["last"] = None
        if word is not None and SAMPLE_LETTERS <= len(word["chars"]) <= SAMPLE_TEXT_MAX:
            run["words"].append(word)

    def _close(self, run: dict | None) -> None:
        if run is None:
            return None
        self._word_end(run)
        # NFC writes some characters as two or three (U+0958, U+FB2C): cut again after it.
        text = unicodedata.normalize("NFC", "".join(run["chars"]).strip())[:RUN_TEXT_MAX]
        if not text:
            return None
        if len(self.runs) == RUNS_MAX:
            raise _Stop("runs_limit")
        if len(text) > self.room[0]:
            raise _Stop("text_limit")
        self.room[0] -= len(text)
        font, size, colour, _ = run["key"]
        letters = [ch for ch in text if ch.isalpha()]
        caps = len(letters) >= 2 and not any(ch.islower() for ch in letters)
        caps = caps and any(ch.isupper() for ch in letters)
        baseline = statistics.median(run["baselines"]) if run["baselines"] else 0.0
        spacing = 0
        if run["gaps"] and size:
            spacing = round(statistics.median(run["gaps"]) * 10_000 / size)
            spacing = min(max(spacing, SPACING_LOW), SPACING_HIGH)
        self.runs.append(
            {
                "text": text,
                "box": run["box"],
                "font": font,
                "size": size,
                "colour": colour,
                "baseline": _bounded(baseline, _tenths(self.height)),
                "spacing": spacing,
                "caps": caps,
            }
        )
        for word in run["words"]:
            self.words.append((font, size, "".join(word["chars"]), word["advance"], word["box"]))
        return None

    # --- shapes and images

    def read_objects(self) -> None:
        self._walk(
            pdfium_c.FPDFPage_CountObjects(self.page.raw),
            lambda i: pdfium_c.FPDFPage_GetObject(self.page.raw, i),
            None,
            0,
        )
        if len(self.shapes) > SHAPES_MAX:
            self.notes.add("shapes_limit")
            largest = sorted(self.shapes, key=lambda s: (-s[0], s[1]))[:SHAPES_MAX]
            self.shapes = sorted(largest, key=lambda s: s[1])

    def _walk(self, count: int, get, matrix, depth: int) -> None:
        bounds = [ctypes.c_float() for _ in range(4)]
        references = [ctypes.byref(bound) for bound in bounds]
        for index in range(count):
            if self.looked == OBJECTS_MAX:
                self.notes.add("objects_limit")
                return
            self.looked += 1
            if self.looked % BUDGET_EVERY == 0 and self.budget.spent():
                raise _Stop("time_limit")
            found = get(index)
            if not found:
                continue
            kind = pdfium_c.FPDFPageObj_GetType(found)
            if kind == pdfium_c.FPDF_PAGEOBJ_FORM:
                if depth < FORM_DEPTH_MAX:
                    inner = _matrix(found, matrix)
                    self._walk(
                        pdfium_c.FPDFFormObj_CountObjects(found),
                        lambda i, form=found: pdfium_c.FPDFFormObj_GetObject(form, i),
                        inner,
                        depth + 1,
                    )
                continue
            if kind not in (pdfium_c.FPDF_PAGEOBJ_PATH, pdfium_c.FPDF_PAGEOBJ_IMAGE):
                continue
            if not pdfium_c.FPDFPageObj_GetBounds(found, *references):
                continue
            left, bottom, right, top = _transform(matrix, *(bound.value for bound in bounds))
            box = self.box(left, top, right, bottom)
            if box is None:
                continue
            if kind == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                if len(self.images) == IMAGES_MAX:
                    self.notes.add("images_limit")
                else:
                    self.images.append({"box": box, "round": _round_clip(found)})
                continue
            points, curves = _outline(found, matrix)
            dot = _dot(found, box, curves)
            if dot is not None:
                if len(self.dots) == DOTS_MAX:
                    self.notes.add("dots_limit")
                else:
                    self.dots.append(dot)
                continue
            shape = _shape(found, box, matrix)
            if shape is not None:
                shape["radius"] = _radius(points, curves, left, bottom, right, top)
                if shape["type"] == "fill" and self.background is None and self._covers(box):
                    self.background = shape["colour"]
                width, height = box[2] - box[0], box[3] - box[1]
                self.shapes.append((width * height + max(width, height), len(self.shapes), shape))

    def _covers(self, box: list[int]) -> bool:
        return (box[2] - box[0]) >= BACKGROUND * _tenths(self.width) and (
            box[3] - box[1]
        ) >= BACKGROUND * _tenths(self.height)

    def pitches(self, runs: list[dict]) -> None:
        """Each run's `pitch`: to the nearest baseline below of a run of its font and size in
        its column, within PITCH_LOW_EM..PITCH_HIGH_EM times its size."""
        by_style: dict[tuple[int, int], list[dict]] = {}
        for run in runs:
            by_style.setdefault((run["font"], run["size"]), []).append(run)
        for group in by_style.values():
            group.sort(key=lambda r: r["baseline"])
            for at, run in enumerate(group):
                run["pitch"] = 0
                low, high = PITCH_LOW_EM * run["size"], PITCH_HIGH_EM * run["size"]
                for other in group[at + 1 :]:
                    gap = other["baseline"] - run["baseline"]
                    if gap > high:
                        break
                    same_column = (
                        abs(other["box"][0] - run["box"][0]) <= run["size"]
                        or abs(other["box"][2] - run["box"][2]) <= run["size"]
                    )
                    if gap >= low and same_column:
                        run["pitch"] = min(gap, LENGTH_MAX)
                        break

    def sample(self, index: int, quota: dict[int, int], room: list[int], out: list[dict]) -> None:
        """Crops of the longest words of each font still owed (`quota`), within `room` bytes."""
        if pdfium_c.FPDFPage_GetRotation(self.page.raw) != 0:
            return
        scale = SAMPLE_SCALE * pdf_render.LONG_SIDE / max(self.width, self.height)
        full = (max(1, round(self.width * scale)), max(1, round(self.height * scale)))
        rank = {font: at for at, font in enumerate(quota)}
        for font, size, text, advance, (x0, y0, x1, y1) in sorted(
            self.words, key=lambda w: (rank.get(w[0], len(rank)), -len(w[2]), w[2])
        ):
            if quota.get(font, 0) <= 0 or not size or _bounded(advance, LENGTH_MAX) < 1:
                continue
            if self.budget.spent():
                self.notes.add("samples_limit")
                return
            left = math.floor((x0 - self.left - SAMPLE_PAD) * scale)
            right = math.ceil((x1 - self.left + SAMPLE_PAD) * scale)
            top = math.floor((self.top - y1 - SAMPLE_PAD) * scale)
            bottom = math.ceil((self.top - y0 + SAMPLE_PAD) * scale)
            left, top = max(left, 0), max(top, 0)
            right, bottom = min(right, full[0]), min(bottom, full[1])
            if not (0 < right - left <= SAMPLE_SIDE_MAX and 0 < bottom - top <= SAMPLE_SIDE_MAX):
                continue
            drawn = pdf_render.draw_area(self.page, full, (left, top, right, bottom), self.budget)
            if isinstance(drawn, str):
                self.notes.add("samples_limit")
                return
            png = io.BytesIO()
            drawn.convert("L").save(png, format="PNG")
            if png.tell() > room[0]:
                self.notes.add("samples_limit")
                return
            room[0] -= png.tell()
            quota[font] -= 1
            out.append(
                {
                    "font": font,
                    "page": index,
                    "text": text,
                    "size": size,
                    "width": _bounded(advance, LENGTH_MAX),
                    "crop": base64.b64encode(png.getvalue()).decode("ascii"),
                }
            )

    def covered(self) -> bool:
        area = self.width * self.height * 100
        return any(
            (image["box"][2] - image["box"][0]) * (image["box"][3] - image["box"][1])
            >= COVER * area
            for image in self.images
        )

    def answer(self, index: int) -> dict:
        order = reading_order([run["box"] for run in self.runs])
        runs = [self.runs[i] for i in order]
        self.pitches(runs)
        margins = None
        if runs:
            width, height = _tenths(self.width), _tenths(self.height)
            margins = [
                min(run["box"][1] for run in runs),
                max(width - max(run["box"][2] for run in runs), 0),
                max(height - max(run["box"][3] for run in runs), 0),
                min(run["box"][0] for run in runs),
            ]
        return {
            "index": index,
            "width": _tenths(self.width),
            "height": _tenths(self.height),
            "margins": margins,
            "background": self.background,
            "runs": runs,
            "shapes": [shape for _, _, shape in self.shapes],
            "dots": self.dots,
            "images": self.images,
        }


def _matrix(form, outer):
    """The matrix that takes the content of `form` to the page: its own, then `outer`'s."""
    own = pdfium_c.FS_MATRIX()
    if not pdfium_c.FPDFPageObj_GetMatrix(form, ctypes.byref(own)):
        return outer
    mine = (own.a, own.b, own.c, own.d, own.e, own.f)
    if outer is None:
        return mine
    a, b, c, d, e, f = outer
    m = mine
    return (
        a * m[0] + c * m[1],
        b * m[0] + d * m[1],
        a * m[2] + c * m[3],
        b * m[2] + d * m[3],
        a * m[4] + c * m[5] + e,
        b * m[4] + d * m[5] + f,
    )


def _transform(matrix, left: float, bottom: float, right: float, top: float):
    if matrix is None:
        return left, bottom, right, top
    a, b, c, d, e, f = matrix
    xs, ys = [], []
    for x, y in ((left, bottom), (left, top), (right, bottom), (right, top)):
        xs.append(a * x + c * y + e)
        ys.append(b * x + d * y + f)
    return min(xs), min(ys), max(xs), max(ys)


def _segments(count: int, get) -> tuple[list[tuple[float, float]], int]:
    """The points of SEGMENTS_MAX segments at most, and how many are Bezier points."""
    x, y = ctypes.c_float(), ctypes.c_float()
    points, curves = [], 0
    for at in range(min(count, SEGMENTS_MAX)):
        segment = get(at)
        if not segment or not pdfium_c.FPDFPathSegment_GetPoint(
            segment, ctypes.byref(x), ctypes.byref(y)
        ):
            continue
        if not (math.isfinite(x.value) and math.isfinite(y.value)):
            continue
        points.append((x.value, y.value))
        curves += pdfium_c.FPDFPathSegment_GetType(segment) == pdfium_c.FPDF_SEGMENT_BEZIERTO
    return points, curves


def _outline(found, outer) -> tuple[list[tuple[float, float]], int]:
    """The points of a path on the page (its own matrix, then the forms around it) and how many
    of them are Bezier points; none past SEGMENTS_MAX segments."""
    count = pdfium_c.FPDFPath_CountSegments(found)
    if not 0 < count <= SEGMENTS_MAX:
        return [], 0
    points, curves = _segments(count, lambda at: pdfium_c.FPDFPath_GetPathSegment(found, at))
    own = pdfium_c.FS_MATRIX()
    if not pdfium_c.FPDFPageObj_GetMatrix(found, ctypes.byref(own)):
        return [], 0
    matrices = [(own.a, own.b, own.c, own.d, own.e, own.f)] + ([outer] if outer else [])
    for a, b, c, d, e, f in matrices:
        points = [(a * x + c * y + e, b * x + d * y + f) for x, y in points]
    return points, curves


def _square(width: float, height: float) -> bool:
    return width > 0 and height > 0 and abs(width - height) <= ROUND * max(width, height)


def _dot(found, box: list[int], curves: int) -> dict | None:
    """A small filled circle drawn with curves: a bullet."""
    width, height = (box[2] - box[0]) / 10, (box[3] - box[1]) / 10
    if curves < CURVES_MIN or not (DOT_LOW <= width <= DOT_HIGH and _square(width, height)):
        return None
    mode, stroke = ctypes.c_int(), ctypes.c_int()
    if not pdfium_c.FPDFPath_GetDrawMode(found, ctypes.byref(mode), ctypes.byref(stroke)):
        return None
    colour = _paint(found, pdfium_c.FPDFPageObj_GetFillColor) if mode.value else None
    return None if colour is None else {"box": box, "colour": colour}


def _radius(points, curves: int, left: float, bottom: float, right: float, top: float) -> int:
    """The corner radius of a path with curves, in tenths of a point (see the docstring)."""
    if curves < CURVES_MIN or not points:
        return 0
    found = 0.0
    for x, y in points:
        for edge, lo, hi, along in (
            (abs(y - bottom), left, right, x),
            (abs(y - top), left, right, x),
            (abs(x - left), bottom, top, y),
            (abs(x - right), bottom, top, y),
        ):
            if edge <= EDGE_NEAR and lo - EDGE_NEAR <= along <= hi + EDGE_NEAR:
                found = max(found, min(along - lo, hi - along))
    shorter = min(right - left, top - bottom)
    return _bounded(min(found, shorter / 2), LENGTH_MAX)


def _round_clip(found) -> bool:
    """Whether one path of the image's clip is a circle (or an ellipse as wide as high)."""
    clip = pdfium_c.FPDFPageObj_GetClipPath(found)
    if not clip:
        return False
    for path in range(min(pdfium_c.FPDFClipPath_CountPaths(clip), 4)):
        count = pdfium_c.FPDFClipPath_CountPathSegments(clip, path)
        if not 0 < count <= SEGMENTS_MAX:
            continue
        points, curves = _segments(
            count, lambda at, path=path: pdfium_c.FPDFClipPath_GetPathSegment(clip, path, at)
        )
        if curves < CURVES_MIN or not points:
            continue
        xs, ys = [x for x, _ in points], [y for _, y in points]
        if _square(max(xs) - min(xs), max(ys) - min(ys)):
            return True
    return False


def _paint(found, getter) -> str | None:
    red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
    if not getter(found, *(ctypes.byref(part) for part in (red, green, blue, alpha))):
        return None
    return _colour(red.value, green.value, blue.value) if alpha.value else None


def _scale(matrix) -> float:
    """How much `matrix` (a, b, c, d, ...) scales a length, on average over directions."""
    a, b, c, d = matrix[:4]
    return math.sqrt(abs(a * d - b * c))


def _shape(found, box: list[int], outer) -> dict | None:
    width, height = (box[2] - box[0]) / 10, (box[3] - box[1]) / 10
    if max(width, height) < SHAPE_MIN:
        return None
    mode, stroke = ctypes.c_int(), ctypes.c_int()
    if not pdfium_c.FPDFPath_GetDrawMode(found, ctypes.byref(mode), ctypes.byref(stroke)):
        return None
    if mode.value:
        colour = _paint(found, pdfium_c.FPDFPageObj_GetFillColor)
        if colour is not None:
            return {"type": "fill", "box": box, "colour": colour, "width": 0, "radius": 0}
    if stroke.value:
        colour = _paint(found, pdfium_c.FPDFPageObj_GetStrokeColor)
        thickness, own = ctypes.c_float(), pdfium_c.FS_MATRIX()
        pdfium_c.FPDFPageObj_GetStrokeWidth(found, ctypes.byref(thickness))
        if not pdfium_c.FPDFPageObj_GetMatrix(found, ctypes.byref(own)):
            return None
        scale = _scale((own.a, own.b, own.c, own.d)) * (1.0 if outer is None else _scale(outer))
        drawn = thickness.value * scale
        if colour is not None and drawn > 0:
            kind = "rule" if min(width, height) <= RULE_MAX else "outline"
            return {
                "type": kind,
                "box": box,
                "colour": colour,
                "width": _bounded(drawn, STROKE_MAX),
                "radius": 0,
            }
    return None


# --- fonts --------------------------------------------------------------------------------------


class _Fonts:
    """The fonts of the runs, each once, in the order they are met: FONTS_MAX at most."""

    def __init__(self) -> None:
        self.by_handle: dict[int, int] = {}
        self.found: list[dict] = []

    def index(self, font) -> int | None:
        handle = ctypes.cast(font, ctypes.c_void_p).value if font else 0
        if handle in self.by_handle:
            return self.by_handle[handle]
        entry = _describe(font) if font else _UNKNOWN
        for number, known in enumerate(self.found):  # one font loaded twice is one font
            if known == entry:
                self.by_handle[handle] = number
                return number
        if len(self.found) == FONTS_MAX:
            return None
        self.found.append(entry)
        self.by_handle[handle] = len(self.found) - 1
        return self.by_handle[handle]


_UNKNOWN = {
    "name": "",
    "weight": 0,
    "italic": False,
    "serif": False,
    "monospace": False,
    "symbolic": False,
    "embedded": False,
}


def _describe(font) -> dict:
    size = pdfium_c.FPDFFont_GetBaseFontName(font, None, 0)
    name = ""
    if 0 < size <= 4 * FONT_NAME_MAX + 1:
        buffer = ctypes.create_string_buffer(size)
        pdfium_c.FPDFFont_GetBaseFontName(font, buffer, size)
        name = _clean_name(buffer.raw)
    flags = pdfium_c.FPDFFont_GetFlags(font)
    flags = flags if flags > 0 else 0
    weight = pdfium_c.FPDFFont_GetWeight(font)
    angle = ctypes.c_int()
    slanted = bool(pdfium_c.FPDFFont_GetItalicAngle(font, ctypes.byref(angle)) and angle.value)
    return {
        "name": name,
        "weight": weight if 100 <= weight <= 900 else 0,
        "italic": bool(flags & _ITALIC) or slanted,
        "serif": bool(flags & _SERIF),
        "monospace": bool(flags & _FIXED),
        "symbolic": bool(flags & _SYMBOLIC),
        "embedded": pdfium_c.FPDFFont_GetIsEmbedded(font) == 1,
    }


# --- the document -------------------------------------------------------------------------------


def read(pdf, total: int, budget, contents=None) -> dict:
    fonts, room = _Fonts(), [TEXT_MAX]
    pages: list[dict] = []
    samples: list[dict] = []
    quota: dict[int, int] = {}
    crop_room = [SAMPLE_BYTES]
    notes: set[str] = set()
    covered = False
    stopped = None
    skipped = 0
    for index in range(min(total, PAGES_READ)):
        if budget.spent():
            stopped = "time_limit"
            break
        if contents is not None:
            contents.weigh(index)
        try:
            page = pdf[index]
        except MemoryError:
            raise
        except Exception:  # PDFium could not load it
            skipped += 1
            continue
        try:
            width, height = page.get_size()
            if not pdf_render.renderable(width, height) or min(_tenths(width), _tenths(height)) < 1:
                skipped += 1
                continue
            read_page = _Page(page, fonts, room, budget)
            for step in (read_page.read_text, read_page.read_objects):
                try:
                    step()
                except _Stop as stop:
                    read_page.notes.add(stop.code)
                    if stop.code == "time_limit":
                        stopped = stop.code
                        break
            if not stopped:
                if not quota:  # the fonts with most characters are sampled first
                    weight: dict[int, int] = {}
                    for run in read_page.runs:
                        weight[run["font"]] = weight.get(run["font"], 0) + len(run["text"])
                    quota = dict.fromkeys(sorted(weight, key=lambda f: -weight[f]), 0)
                for font in {word[0] for word in read_page.words}:
                    quota.setdefault(font, 0)
                for font in quota:
                    taken = sum(1 for sample in samples if sample["font"] == font)
                    quota[font] = SAMPLES_PER_FONT - taken
                read_page.sample(index, quota, crop_room, samples)
            notes |= read_page.notes
            covered = covered or read_page.covered()
            pages.append(read_page.answer(index))
        finally:
            page.close()
        if stopped:
            break
    if stopped and not pages:
        raise pdf_render._Refused("too_slow")
    if stopped:
        notes.add(stopped)
    found = [{"code": code} for code in sorted(notes)]
    if skipped:
        found.append({"code": "pages_not_read", "count": skipped})
    return {
        "ok": True,
        "kind": "design_pdf",
        "pages": min(total, PAGES_MAX),
        "scanned": covered and not any(page["runs"] for page in pages),
        "truncated": bool(notes),
        "notes": found,
        "fonts": fonts.found,
        "read": pages,
        "samples": samples,
    }


def handle(data: bytes, options: dict, budget) -> dict:
    """The layout of the first PAGES_READ pages of `data`. `options` must be empty."""
    if options:
        return {"ok": False, "failure": "server", "code": "handler_error"}
    if not data.startswith(b"%PDF-"):
        return pdf_render.failure("unreadable")
    try:
        pdf, total = pdf_render.open_document(data)
        try:
            return read(pdf, total, budget, pdf_render._Contents(data))
        finally:
            pdf.close()
    except pdf_render._Refused as refused:
        return pdf_render.failure(refused.code, **refused.values)
    except MemoryError:
        return pdf_render.failure("bomb")
