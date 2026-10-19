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
               "runs": [{"text": ..., "box": [x0, y0, x1, y1], "font": index of `fonts`,
                         "size": tenths of a point, "colour": "#rrggbb"}],
               "shapes": [{"type": "fill" | "rule" | "outline", "box": [...],
                           "colour": "#rrggbb", "width": stroke width}],
               "images": [{"box": [...]}]}]}

- A run is the characters PDFium's text page gives in a row with one font, size and colour on
  one line; a jump back, a gap wider than GAP_EM times the size, or another line ends it.
  Invisible text (render mode 3: the text layer of an OCR'd scan), transparent text and text
  outside the page are left out and never reach the model. The runs of a page are put in
  reading order by `reading_order`.
- `margins` are those of the text: where the runs of the page start and end.
- A shape is a path painted with a colour that is not transparent: a fill, or a stroke that is a
  rule (thinner than RULE_MAX on one side) or an outline. Shading, clipping and the pixels of an
  image are never read; an image is its box only.
- `scanned` is true when no page read has a run and an image covers at least half of one of
  them: the design is then in the picture alone, and the extraction says so.

Caps: RUNS_MAX runs, SHAPES_MAX shapes (the largest kept), IMAGES_MAX images and OBJECTS_MAX
objects looked at per page, CHARS_MAX characters per page, TEXT_MAX characters and FONTS_MAX
fonts per answer. Each one met is a note and `truncated`. Together they hold the answer under
parser_client.DESIGN_MAX_ANSWER (tests/ingestion/parser/test_design_pdf.py builds the largest).

What comes back otherwise, `{"ok": false, "failure": "file", "code": ...}`, is pdf_render.py's:
`unreadable`, `protected`, `encryption`, `bomb` (a page whose content decodes to more than
pdf_render.CONTENT_MAX, weighed before PDFium loads it, or the memory of the child), `too_slow`.

Only main.py may import this file in production. The tests import it too, to call `handle` in
their own process on files they make.
"""

import ctypes
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
INVISIBLE = 3  # FPDF_TEXTRENDERMODE_INVISIBLE
GUTTER = 12.0  # points: the narrowest gap between two columns
ORDER_DEPTH_MAX = 8
COLUMN_BANDS_MIN = 3  # bands a block must span to be read as columns
# PDF font descriptor flags (ISO 32000, 9.8.2).
_FIXED, _SERIF, _SYMBOLIC, _ITALIC = 1, 2, 4, 64


class _Stop(Exception):  # noqa: N818, an outcome, not an error of this program
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _tenths(value: float) -> int:
    return round(value * 10)


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
        if reach is None or core_top >= reach:
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
        self.looked = 0

    def box(self, left: float, top: float, right: float, bottom: float) -> list[int] | None:
        """[x0, y0, x1, y1] in tenths of a point from the top left corner, clipped to the page,
        or None when nothing of it is on the page."""
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
                continue
            ch = _kept(code)
            found = pdfium_c.FPDFText_GetTextObject(text_page, index)
            if not ch or not found:
                continue
            if pdfium_c.FPDFTextObj_GetTextRenderMode(found) == INVISIBLE:
                continue
            if not pdfium_c.FPDFText_GetFillColor(text_page, index, *paint) or not alpha.value:
                continue
            if not pdfium_c.FPDFText_GetLooseCharBox(text_page, index, ctypes.byref(rect)):
                continue
            box = self.box(rect.left, rect.top, rect.right, rect.bottom)
            if box is None:
                continue
            font = self.fonts.index(pdfium_c.FPDFTextObj_GetFont(found))
            if font is None:
                self.notes.add("fonts_limit")
                continue
            size = pdfium_c.FPDFText_GetFontSize(text_page, index)
            key = (font, _tenths(size), _colour(red.value, green.value, blue.value))
            if run is not None and not self._continues(run, key, box, size, ch):
                run = self._close(run)
            if run is None:
                run = {"key": key, "chars": [], "box": list(box), "right": box[0]}
            run["chars"].append(ch)
            if not ch.isspace():
                run["box"] = [
                    min(run["box"][0], box[0]),
                    min(run["box"][1], box[1]),
                    max(run["box"][2], box[2]),
                    max(run["box"][3], box[3]),
                ]
                run["right"] = box[2]
        self._close(run)

    @staticmethod
    def _continues(run: dict, key: tuple, box: list[int], size: float, ch: str) -> bool:
        if run["key"] != key or len(run["chars"]) >= RUN_TEXT_MAX:
            return False
        if ch.isspace():
            return True
        top, bottom = run["box"][1], run["box"][3]
        overlap = min(bottom, box[3]) - max(top, box[1])
        if overlap * 2 < min(bottom - top, box[3] - box[1]):
            return False
        em = max(size, 1.0) * 10
        return run["right"] - em / 2 <= box[0] <= run["right"] + GAP_EM * em

    def _close(self, run: dict | None) -> None:
        if run is None:
            return None
        text = unicodedata.normalize("NFC", "".join(run["chars"]).strip())
        if not text:
            return None
        if len(self.runs) == RUNS_MAX:
            raise _Stop("runs_limit")
        if len(text) > self.room[0]:
            raise _Stop("text_limit")
        self.room[0] -= len(text)
        font, size, colour = run["key"]
        self.runs.append(
            {"text": text, "box": run["box"], "font": font, "size": size, "colour": colour}
        )
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
                    self.images.append({"box": box})
                continue
            shape = _shape(found, box)
            if shape is not None:
                width, height = box[2] - box[0], box[3] - box[1]
                self.shapes.append((width * height + max(width, height), len(self.shapes), shape))

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
            "runs": runs,
            "shapes": [shape for _, _, shape in self.shapes],
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


def _paint(found, getter) -> str | None:
    red, green, blue, alpha = (ctypes.c_uint() for _ in range(4))
    if not getter(found, *(ctypes.byref(part) for part in (red, green, blue, alpha))):
        return None
    return _colour(red.value, green.value, blue.value) if alpha.value else None


def _shape(found, box: list[int]) -> dict | None:
    width, height = (box[2] - box[0]) / 10, (box[3] - box[1]) / 10
    if max(width, height) < SHAPE_MIN:
        return None
    mode, stroke = ctypes.c_int(), ctypes.c_int()
    if not pdfium_c.FPDFPath_GetDrawMode(found, ctypes.byref(mode), ctypes.byref(stroke)):
        return None
    if mode.value:
        colour = _paint(found, pdfium_c.FPDFPageObj_GetFillColor)
        if colour is not None:
            return {"type": "fill", "box": box, "colour": colour, "width": 0}
    if stroke.value:
        colour = _paint(found, pdfium_c.FPDFPageObj_GetStrokeColor)
        thickness = ctypes.c_float()
        pdfium_c.FPDFPageObj_GetStrokeWidth(found, ctypes.byref(thickness))
        if colour is not None and thickness.value > 0:
            kind = "rule" if min(width, height) <= RULE_MAX else "outline"
            return {
                "type": kind,
                "box": box,
                "colour": colour,
                "width": min(_tenths(thickness.value), 1000),
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
            if not pdf_render.renderable(width, height):
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
        "pages": total,
        "scanned": covered and not any(page["runs"] for page in pages),
        "truncated": bool(notes),
        "notes": found,
        "fonts": fonts.found,
        "read": pages,
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
