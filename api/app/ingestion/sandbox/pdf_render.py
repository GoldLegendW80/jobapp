"""The pages of a PDF that are scans, rendered to normalised page images, and the text layer of
a PDF that only PDFium can open (task G8 of the ingestion plan). Runs only inside the locked
down child of the parser service: main.py imports this file after the lockdown and calls
`handle`. The bytes are hostile, and so is everything PDFium makes of them.

    handle(data, options, budget) -> the whole answer, as app/ingestion/parser_client.py reads it

Two jobs, chosen by `options` (written by the worker, app/ingestion/jobs.py, never by a user):

1. `{"pages": [...], "check": [...], "limit": N}`: render. `pages` are zero based page indexes
   in ascending order. Each is rendered, except a page also named in `check`, which is rendered
   only when it carries an image that covers at least half of it (the second trigger of the
   plan: a scan under a footer or a watermark; the worker names there the pages whose text is
   short). The child stops after `limit` images (BATCH at most: eight images of 300,000 bytes
   are 3.2 MB of base64, under the 4 MiB of an answer), and the worker asks again for the
   pages after the last image it got. The answer:

       {"ok": true, "kind": "pdf_render", "pages": N, "pages_text": 0, "pages_image": I,
        "truncated": bool, "notes": [...],
        "images": [{"position": page index, "width": W, "height": H, "jpeg": base64}]}

   - A page is drawn on white at the scale that puts its longest side at LONG_SIDE pixels, with
     its own rotation and its annotations, then goes through the same JPEG encoding as an
     uploaded image (image.encode).
   - A page larger than PAGE_MAX_INCHES on a side, or narrower than a tenth of its length, is
     not rendered, nor is a page PDFium cannot load or draw: the note `pages_not_rendered`
     counts them. The size of the bitmap never depends on the size of the page, so a page of
     5 metres costs what an A4 costs.
   - The time budget is asked before each page, and by PDFium itself while it draws one
     (`FPDF_RenderPageBitmap_Start` with a pause callback): when it is spent, the page being
     drawn is dropped, what was rendered is returned with the note `time_limit` and
     `truncated`, or `too_slow` when that is nothing. PDFium asks between two objects of a
     page: one object that takes for ever (a single stream) is stopped by the limits of the
     child, as a failure of the server.

2. `{"text": true}`: the text of each page, for a file that pdf_text.py answered `encryption`
   for (AES: the parser never loads `cryptography`, and PDFium decrypts by itself). The answer
   has the shape of pdf_text.py's, with `kind` "pdf_render", and the same rules by
   construction: this path imports pdf_text.py and uses its `_Read`, `clean` and `answer`, so
   the page cap, the character cap, the scan trigger, the "Links: " line and the notes are the
   same objects. The time budget is asked between two pages only: PDFium cannot be interrupted
   inside one.

What comes back otherwise, `{"ok": false, "failure": "file", "code": ...}`:

    unreadable  not a PDF, damaged, no page, or anything PDFium refused
    protected   it asks for a password (the empty one is tried by PDFium itself)
    encryption  a protection PDFium does not know
    bomb        the memory of the child was not enough
    too_slow    the budget was spent before the first image
    no_text     (the text job) no page has text: `count` pages, all of them scans

PDFium is C++ behind ctypes: a file that makes it crash, or ask for more memory than the child
has in a place where it does not check, kills the child. The server then answers `crashed` or
`memory_limit`, the service lives, and the job fails the document as a failure of the server.
PDFium starts no thread and no process, never executes a script (this build has no V8 and no
XFA), and writes nothing: the document is opened from memory.

Only main.py may import this file in production. The tests import it too, to call `handle` in
their own process on files they make.
"""

import ctypes

import image as pictures  # sandbox/image.py, beside this file
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

BATCH = 8  # images of one answer
PAGES_MAX = 200  # page indexes a job may name: the pages pdf_text.py reads at most
PAGE_MAX_INCHES = 200
PAGE_MAX_POINTS = PAGE_MAX_INCHES * 72
NARROW = 10  # a page narrower than a tenth of its length is not rendered
COVER = 0.5  # the share of the page an image must cover for the second trigger
OBJECTS_MAX = 2_000  # objects of one page looked at for that image
LONG_SIDE = pictures.LONG_SIDE
_FLAGS = pdfium_c.FPDF_ANNOT
_WHITE = 0xFFFFFFFF
_PAUSE_TYPE = dict(pdfium_c.IFSDK_PAUSE._fields_)["NeedToPauseNow"]
_DECLINED = "declined"  # a page of `check` with no image over half of it: it stays a text
_SKIPPED = "skipped"
_OUT_OF_TIME = "out_of_time"


class _Refused(Exception):  # noqa: N818, an outcome, not an error of this program
    def __init__(self, code: str, **values: int) -> None:
        super().__init__(code)
        self.code, self.values = code, values


class _BadOptionsError(Exception):
    """The worker asked for something this file does not do: the server's failure."""


def failure(code: str, **values: int) -> dict:
    return {"ok": False, "failure": "file", "code": code, **values}


def _indexes(value: object, most: int) -> list[int]:
    if not isinstance(value, list) or len(value) > most:
        raise _BadOptionsError
    if any(type(item) is not int or not 0 <= item < PAGES_MAX for item in value):
        raise _BadOptionsError
    if sorted(set(value)) != value:
        raise _BadOptionsError
    return value


def read_options(options: dict) -> tuple[bool, list[int], frozenset[int], int]:
    """(text job, pages, check, limit). Raises _BadOptionsError."""
    if options == {"text": True}:
        return True, [], frozenset(), 0
    if not set(options) <= {"pages", "check", "limit"} or "pages" not in options:
        raise _BadOptionsError
    pages = _indexes(options["pages"], PAGES_MAX)
    check = frozenset(_indexes(options.get("check", []), PAGES_MAX))
    limit = options.get("limit", BATCH)
    if type(limit) is not int or not 1 <= limit <= BATCH or not pages or not check <= set(pages):
        raise _BadOptionsError
    return False, pages, check, limit


def open_document(data: bytes):
    """The document and how many pages it has. Raises _Refused."""
    try:
        pdf = pdfium.PdfDocument(data)
        total = len(pdf)
    except MemoryError:
        raise
    except pdfium.PdfiumError as error:
        code = getattr(error, "err_code", None)
        if code == pdfium_c.FPDF_ERR_PASSWORD:
            raise _Refused("protected") from None
        if code == pdfium_c.FPDF_ERR_SECURITY:
            raise _Refused("encryption") from None
        raise _Refused("unreadable") from None
    except Exception:
        raise _Refused("unreadable") from None
    if total < 1:
        raise _Refused("unreadable")
    return pdf, total


# --- rendering ----------------------------------------------------------------------------------


def renderable(width: float, height: float) -> bool:
    """The page size guards: a real size, PAGE_MAX_INCHES a side at most, and not a strip."""
    longest, shortest = max(width, height), min(width, height)
    return shortest > 0 and longest <= PAGE_MAX_POINTS and shortest * NARROW >= longest


def covered(page) -> bool:
    """True when one image of the page covers at least COVER of it. PDFium reports each object
    of a page with its type and its bounds in page units (`FPDFPageObj_GetBounds`); the first
    OBJECTS_MAX objects are looked at, and an image inside a form is not."""
    left, bottom, right, top = page.get_bbox()
    area = (right - left) * (top - bottom)
    if area <= 0:
        return False
    bounds = [ctypes.c_float() for _ in range(4)]
    references = [ctypes.byref(bound) for bound in bounds]
    for index in range(min(pdfium_c.FPDFPage_CountObjects(page.raw), OBJECTS_MAX)):
        found = pdfium_c.FPDFPage_GetObject(page.raw, index)
        if not found or pdfium_c.FPDFPageObj_GetType(found) != pdfium_c.FPDF_PAGEOBJ_IMAGE:
            continue
        if not pdfium_c.FPDFPageObj_GetBounds(found, *references):
            continue
        its_left, its_bottom, its_right, its_top = (bound.value for bound in bounds)
        width = min(its_right, right) - max(its_left, left)
        height = min(its_top, top) - max(its_bottom, bottom)
        if width > 0 and height > 0 and width * height >= COVER * area:
            return True
    return False


def draw(page, width: float, height: float, budget) -> Image.Image | str:
    """The page as an RGB picture with its longest side at LONG_SIDE, or why there is none."""
    scale = LONG_SIDE / max(width, height)
    columns, rows = max(1, round(width * scale)), max(1, round(height * scale))
    bitmap = pdfium_c.FPDFBitmap_CreateEx(columns, rows, pdfium_c.FPDFBitmap_BGRx, None, 0)
    if not bitmap:
        raise MemoryError
    try:
        pdfium_c.FPDFBitmap_FillRect(bitmap, 0, 0, columns, rows, _WHITE)
        pause = pdfium_c.IFSDK_PAUSE()
        pause.version = 1
        # Kept in a name for as long as PDFium may call it.
        ask = _PAUSE_TYPE(lambda _pause: 1 if budget.spent() else 0)
        pause.NeedToPauseNow = ask
        status = pdfium_c.FPDF_RenderPageBitmap_Start(
            bitmap, page.raw, 0, 0, columns, rows, 0, _FLAGS, ctypes.byref(pause)
        )
        while status == pdfium_c.FPDF_RENDER_TOBECONTINUED and not budget.spent():
            status = pdfium_c.FPDF_RenderPage_Continue(page.raw, ctypes.byref(pause))
        pdfium_c.FPDF_RenderPage_Close(page.raw)
        if status == pdfium_c.FPDF_RENDER_TOBECONTINUED:
            return _OUT_OF_TIME
        if status != pdfium_c.FPDF_RENDER_DONE:
            return _SKIPPED
        stride = pdfium_c.FPDFBitmap_GetStride(bitmap)
        pixels = ctypes.string_at(pdfium_c.FPDFBitmap_GetBuffer(bitmap), stride * rows)
    finally:
        pdfium_c.FPDFBitmap_Destroy(bitmap)
    return Image.frombuffer("RGB", (columns, rows), pixels, "raw", "BGRX", stride, 1)


def render_page(pdf, index: int, checked: bool, budget) -> Image.Image | str:
    try:
        page = pdf[index]
    except MemoryError:
        raise
    except Exception:  # PDFium could not load it
        return _SKIPPED
    try:
        width, height = page.get_size()
        if not renderable(width, height):
            return _SKIPPED
        if checked and not covered(page):
            return _DECLINED
        return draw(page, width, height, budget)
    finally:
        page.close()


def render(pdf, total: int, pages: list[int], check: frozenset[int], limit: int, budget) -> dict:
    images: list[dict] = []
    skipped, stopped = 0, False
    for index in pages:
        if len(images) == limit:
            break
        if budget.spent():
            stopped = True
            break
        made = render_page(pdf, index, index in check, budget) if index < total else _SKIPPED
        if made == _OUT_OF_TIME:
            stopped = True
            break
        if made == _SKIPPED:
            skipped += index not in check  # a page that stays a text is not a page left out
        elif made != _DECLINED:
            images.append(pictures.entry(index, made))
    if stopped and not images:
        raise _Refused("too_slow")
    notes: list[dict] = [{"code": "time_limit"}] if stopped else []
    if skipped:
        notes.append({"code": "pages_not_rendered", "count": skipped})
    return {
        "ok": True,
        "kind": "pdf_render",
        "pages": total,
        "pages_text": 0,
        "pages_image": len(images),
        "truncated": stopped,
        "notes": notes,
        "images": images,
    }


# --- the text layer, for a file only PDFium opens -----------------------------------------------


def links_of(pdf, page, reader) -> list[str]:
    """The addresses of the page's link annotations, in order, each once, under the caps of
    pdf_text.py. A link that cannot be read is skipped: links are a bonus."""
    found: list[str] = []
    position, link = ctypes.c_int(0), pdfium_c.FPDF_LINK()
    try:
        for _ in range(20 * reader.LINKS_MAX):
            if not pdfium_c.FPDFLink_Enumerate(
                page.raw, ctypes.byref(position), ctypes.byref(link)
            ):
                break
            action = pdfium_c.FPDFLink_GetAction(link)
            if not action or pdfium_c.FPDFAction_GetType(action) != pdfium_c.PDFACTION_URI:
                continue
            size = pdfium_c.FPDFAction_GetURIPath(pdf.raw, action, None, 0)
            if not 1 < size <= 8 * reader.LINK_MAX_CHARS:
                continue
            buffer = ctypes.create_string_buffer(size)
            pdfium_c.FPDFAction_GetURIPath(pdf.raw, action, buffer, size)
            address = buffer.raw[: size - 1].decode("utf-8", "replace")
            address = reader._BLANK.sub("", reader.clean(address))[: reader.LINK_MAX_CHARS]
            if address and address not in found:
                found.append(address)
                if len(found) == reader.LINKS_MAX:
                    break
    except MemoryError:
        raise
    except Exception:  # noqa: S110, a page never fails for its links
        pass
    return found


def page_text(pdf, index: int, read, reader) -> str:
    """The text of one page as pdf_text.py would keep it, '' for a page that counts as an
    image."""
    try:
        page = pdf[index]
        try:
            layer = page.get_textpage()
            try:
                # Every character of the page, wherever it is drawn: what pypdf reads too.
                text = reader.clean(layer.get_text_range())
            finally:
                layer.close()
            if len(reader._BLANK.sub("", text)) < reader.SCAN_MIN_CHARS:
                return ""
            links = links_of(pdf, page, reader)
        finally:
            page.close()
    except MemoryError:
        raise
    except Exception:
        read.broken += 1
        return ""
    return text + "\n" + reader.LINKS_LABEL + " ".join(links) if links else text


def read_text(pdf, total: int, budget) -> dict:
    import pdf_text as reader  # only this job needs it, and it brings pypdf along

    read = reader._Read()
    read.total = total
    for index in range(min(total, reader.PAGES_MAX)):
        if budget.spent():
            read.stopped = "time_limit"
            break
        if not read.add(page_text(pdf, index, read, reader)):
            read.stopped = "text_limit"
            break
    else:
        if total > reader.PAGES_MAX:
            read.stopped = "pages_not_read"
    result = reader.answer(read)
    if result["ok"]:
        result["kind"] = "pdf_render"
    return result


def handle(data: bytes, options: dict, budget) -> dict:
    """Render pages of `data`, or read its text layer, as `options` say."""
    try:
        text_job, pages, check, limit = read_options(options)
    except _BadOptionsError:
        return {"ok": False, "failure": "server", "code": "handler_error"}
    if not data.startswith(b"%PDF-"):
        return failure("unreadable")
    try:
        pdf, total = open_document(data)
        try:
            if text_job:
                return read_text(pdf, total, budget)
            return render(pdf, total, pages, check, limit, budget)
        finally:
            pdf.close()
    except _Refused as refused:
        return failure(refused.code, **refused.values)
    except pictures.Refused as refused:
        return failure(refused.code)
    except MemoryError:
        return failure("bomb")
