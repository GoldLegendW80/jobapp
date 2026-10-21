"""The text of a PDF, page by page (task G2b of the ingestion plan). Runs only inside the locked
down child of the parser service: main.py imports this file after the lockdown and calls
`handle`. The bytes are hostile, and so is everything pypdf makes of them.

    handle(data, options, budget) -> the whole answer, as app/ingestion/parser_client.py reads it

What comes back for a PDF that was read:

    {"ok": true, "kind": "pdf_text", "text": ..., "pages": N, "pages_text": T, "pages_image": I,
     "truncated": bool, "notes": [{"code": ..., "from": ..., "to": ..., "count": ...}]}

- `text` holds one slot per page read, in order, joined by form feeds (U+000C): the text of a
  page that has some, the empty string for a page that has none. So it holds exactly
  `pages_text + pages_image - 1` form feeds, and no other: a form feed inside a page becomes a
  line feed. `pages` is how many pages the file has, read or not.
- A page with fewer than SCAN_MIN_CHARS characters that are not whitespace is counted as an
  image page and its slot is empty: a scan, or a scan under a page number. G8 renders these
  pages; until then the note `scans_not_read` counts them. A page pypdf cannot read is counted
  the same way: the renderer may fare better with it.
- The addresses of a page's link annotations follow its text on a line "Links: ...": a CV
  often shows "LinkedIn" and keeps the address in the annotation.
- Reading stops at PAGES_MAX pages (`pages_not_read`, from and to), at TEXT_MAX_CHARS characters
  of text (`text_limit`), or when the budget is spent (`time_limit`). What was read until then
  is returned, `truncated` is true, and the note names the pages that were not read. One
  system timer checks the budget four times a second and interrupts pypdf in the middle of a
  page: a single page written to take a minute does not cost the pages before it. The
  interrupt is only raised where the reading can take it (see _Watch).

What comes back for a PDF that was not read, `{"ok": false, "failure": "file", "code": ...}`:

    unreadable  not a PDF, damaged, cut short, no page, or anything pypdf raised opening it
    protected   it asks for a password (the empty one is tried first: a file that only forbids
                copying or printing opens with it)
    encryption  it is protected with AES. The parser never loads `cryptography` (main.py blocks
                it), so pypdf reads RC4 and nothing else. G8 opens these with PDFium.
    bomb        a limit on the work one file may ask for was reached: a stream that inflates
                past INFLATED_MAX, a chain of filters, a page tree too deep or too wide, or the
                memory of the child
    no_text     no page has text: scans, read once G8 has shipped (`count` pages). When a
                page among them could not be read at all, the answer is `unreadable`, and when
                one reached a limit, `bomb`
    too_slow    the budget was spent before the first page was read

Nothing else leaves: no sentence, no excerpt of an error. The code is all the worker gets, and
the worker writes the sentence. pypdf's own warnings are switched off: nothing reads them (the
child's standard error is closed) and a damaged file can produce thousands.

Scripts, attached files and form fields are never looked at: only the content stream of each
page and its link annotations are read, and nothing is ever executed.

Only main.py may import this file in production. The tests import it too, to call `handle` in
their own process on files they make.
"""

import io
import logging
import re
import signal
import sys
import unicodedata
import weakref

import pypdf
from pypdf import errors

PAGES_MAX = 200  # pages of one PDF read as text (SPEC-ingestion.md, Limits)
CLAIMED_MAX = 100_000  # parser_client.MAX_PAGES: a file that claims more is given as this
TEXT_MAX_CHARS = 500_000  # ingestion_documents.text, form feeds included
SCAN_MIN_CHARS = 50  # fewer characters that are not whitespace: an image page
LINKS_MAX = 50  # addresses kept per page
LINK_MAX_CHARS = 2_000
LINKS_LABEL = "Links: "
PAGE_BREAK = "\f"
TICK_S = 0.25  # how often the budget is checked while pypdf works
RETRY_S = 0.01  # how soon the timer rings again after a ring that could not be delivered

# What one stream may become once decoded. pypdf's own default is 75 MB each, which the child
# cannot hold several times over (RLIMIT_AS, main.py). A page of text is a few kilobytes of
# operators; 32 MB is already a page nobody wrote by hand.
INFLATED_MAX = 32_000_000
LIMITS = {
    "maximum_declared_stream_length": 32_000_000,  # a file is at most 20 MiB
    "array_based_stream_maximum_output_length": INFLATED_MAX,
    "jbig2_maximum_output_length": INFLATED_MAX,
    "lzw_maximum_output_length": INFLATED_MAX,
    "run_length_maximum_output_length": INFLATED_MAX,
    "zlib_maximum_output_length": INFLATED_MAX,
    "brotli_maximum_output_length": INFLATED_MAX,
    "image_maximum_buffer_size": INFLATED_MAX,
    "stream_decoding_work_maximum_length": 2 * INFLATED_MAX,
    # As many pages as the worker lets a file claim (parser_client.MAX_PAGES).
    "page_tree_maximum_entries": 100_000,
    # No other program: pypdf would start `jbig2dec` for some images (execve is denied here
    # anyway, and no image is decoded for text).
    "jbig2dec_binary": None,
    # The old module constants are not consulted: nothing here sets them.
    "disable_legacy_handling": True,
}

_LINE_BREAKS = re.compile("\r\n?|[\x0b\x0c\x85\u2028\u2029]")
# Control characters, and the two things Postgres does not store: U+0000 (in the first range)
# and half a surrogate pair.
_DROPPED = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f\ud800-\udfff]")
_BLANK = re.compile(r"\s+")
_BLANK_LINES = re.compile(r"\n{3,}")


class _OutOfTimeError(BaseException):
    """The budget is spent. A BaseException: pypdf catches Exception in many places and would
    go on."""


class _Refused(Exception):  # noqa: N818, an outcome, not an error of this program
    def __init__(self, code: str, **values: int) -> None:
        super().__init__(code)
        self.code, self.values = code, values


class _Watch:
    """While it is open, a timer interrupts the reading with _OutOfTimeError once the budget is
    spent. Signals only reach the main thread of a process: anywhere else, and where the system
    has no such timer, the budget is still checked between two pages.

    The timer rings wherever the program happens to be, and an exception raised there is only
    worth something where it can travel up to `handle`. So the ring raises in one case only:
    `read_pages` is running, and nothing between it and the interrupted line is a destructor or
    the callback of a weak reference. Python does not let an exception leave those (the memory
    manager called them, there is nobody to raise to): it hands the exception to
    `sys.unraisablehook` and goes on, the interrupt is lost and the callback is cut short. They
    run at any moment, when an object dies or the collector passes, and they may belong to any
    library loaded in the process.

    - Before and after `read_pages` the ring raises nothing: the loop asks the budget before
      each page, and a reading that has ended has nothing left to interrupt.
    - In a `__del__`, or in the code of the standard weak containers, the ring is put off: the
      timer rings again RETRY_S later, when the callback has returned.
    - In a callback this cannot recognise, the exception is raised and lost. The hook set for
      the time of the watch takes it, keeps it from being reported, and rings again the same
      way. Any other unraisable exception goes to the hook that was there.

    A ring put off is never dropped: the budget stays spent, so the next one raises."""

    def __init__(self, budget) -> None:
        self.budget, self.armed, self.installed = budget, False, False
        self.previous, self.hook = None, None
        self.put_off = 0  # rings that raised nothing, or whose exception was lost

    def _again(self) -> None:
        self.put_off += 1
        signal.setitimer(signal.ITIMER_REAL, RETRY_S, TICK_S)

    def _ring(self, _number: object = None, frame=None) -> None:
        if not (self.armed and self.budget.spent()):
            return
        while frame is not None:
            code = frame.f_code
            if code is read_pages.__code__:
                raise _OutOfTimeError
            if (
                code.co_name == "__del__"
                or code.co_filename in _WEAK_FILES
                or code is _Watch._lost.__code__
            ):
                self._again()
                return
            frame = frame.f_back

    def _lost(self, unraisable) -> None:
        """sys.unraisablehook while the watch is open."""
        if unraisable.exc_type is _OutOfTimeError:
            if self.armed:
                self._again()
        elif self.hook is not None:
            self.hook(unraisable)

    def __enter__(self) -> "_Watch":
        try:
            self.previous = signal.signal(signal.SIGALRM, self._ring)
        except (ValueError, OSError, AttributeError):  # not the main thread, or no SIGALRM
            return self
        self.hook, sys.unraisablehook = sys.unraisablehook, self._lost
        self.installed = self.armed = True
        signal.setitimer(signal.ITIMER_REAL, TICK_S, TICK_S)
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def stop(self) -> None:
        """No ring after this, and the signal and the hook as they were. Safe to call twice."""
        self.armed = False
        if self.installed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous or signal.SIG_DFL)
            sys.unraisablehook = self.hook
            self.installed = False


# Where the standard library keeps the callbacks of its weak dictionaries and of its weak sets.
_WEAK_FILES = frozenset(
    kind.__init__.__code__.co_filename for kind in (weakref.WeakKeyDictionary, weakref.WeakSet)
)


class _Read:
    """What has been read so far: a slot per page, and why the reading stopped."""

    def __init__(self) -> None:
        self.total = 0
        self.slots: list[str] = []
        self.chars = 0  # of the slots, and of the form feeds between them
        self.images = 0
        self.limit_hits = 0
        self.broken = 0  # pages pypdf could not read at all
        self.stopped: str | None = None  # the code of the note

    def add(self, text: str) -> bool:
        """Keep the text of the next page ('' for an image page). False when the document is
        full: the page was cut at what still fitted, or left out when nothing did."""
        separator = 1 if self.slots else 0
        room = TEXT_MAX_CHARS - self.chars - separator
        whole = len(text) <= room
        if not whole:
            text = text[: max(room, 0)].rstrip()
            if not text:
                return False
        elif not text:
            self.images += 1
        self.chars += len(text) + separator
        self.slots.append(text)
        return whole


def clean(text: str) -> str:
    """The text of one page as it is stored: NFC, every line break a line feed (a form feed
    too: the form feed is the page separator and nothing else), no control character, no half
    surrogate, no blank at the end of a line, at most one empty line in a row."""
    text = unicodedata.normalize("NFC", _DROPPED.sub("", _LINE_BREAKS.sub("\n", text)))
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def links_of(page) -> list[str]:
    """The addresses of the page's link annotations, in order, each once. Whatever is wrong
    with an annotation, it is skipped: links are a bonus, never a reason to lose the page."""
    found: list[str] = []
    try:
        annotations = page.get("/Annots")
        annotations = annotations.get_object() if annotations is not None else []
        for reference in list(annotations)[: 20 * LINKS_MAX]:
            try:
                annotation = reference.get_object()
                if annotation.get("/Subtype") != "/Link":
                    continue
                action = annotation.get("/A")
                action = action.get_object() if action is not None else None
                address = action.get("/URI") if action is not None else None
                if address is None:
                    continue
                address = address.get_object()
                if isinstance(address, bytes):
                    address = address.decode("utf-8", "replace")
                if not isinstance(address, str):  # a number, a dictionary: not an address
                    continue
                address = _BLANK.sub("", clean(address))[:LINK_MAX_CHARS]
            except MemoryError:
                raise
            except Exception:  # noqa: S112, a broken annotation is skipped
                continue
            if address and address not in found:
                found.append(address)
                if len(found) == LINKS_MAX:
                    break
    except MemoryError:
        raise
    except Exception:  # noqa: S110, as above: a page never fails for its links
        pass
    return found


def page_text(page, read: _Read) -> str:
    """The text of one page, '' for a page that counts as an image."""
    try:
        text = clean(page.extract_text())
    except errors.DependencyError:
        raise _Refused("encryption") from None
    except errors.FileNotDecryptedError:
        raise _Refused("protected") from None
    except errors.LimitReachedError:
        read.limit_hits += 1
        return ""
    except MemoryError:
        raise
    except Exception:
        # Whatever pypdf raised on this page (RecursionError included): the others may still
        # be read, and G8's renderer gets this one.
        read.broken += 1
        return ""
    if len(_BLANK.sub("", text)) < SCAN_MIN_CHARS:
        return ""
    links = links_of(page)
    return text + "\n" + LINKS_LABEL + " ".join(links) if links else text


def open_reader(data: bytes):
    """The reader of a PDF that opens, and how many pages it has. Raises _Refused."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        total = min(len(reader.pages), CLAIMED_MAX)  # /Count is whatever the file says
    except errors.DependencyError:  # AES, and no `cryptography` here
        raise _Refused("encryption") from None
    except (errors.FileNotDecryptedError, errors.WrongPasswordError):
        raise _Refused("protected") from None
    except errors.LimitReachedError:
        raise _Refused("bomb") from None
    except MemoryError:
        raise
    except Exception:  # RecursionError included: arrays or dictionaries nested without end
        raise _Refused("unreadable") from None
    if total < 1:
        raise _Refused("unreadable")
    return reader, total


def read_pages(data: bytes, budget, read: _Read) -> None:
    reader, read.total = open_reader(data)
    for index in range(min(read.total, PAGES_MAX)):
        if budget.spent():
            read.stopped = "time_limit"
            return
        try:
            page = reader.pages[index]
        except MemoryError:
            raise
        except Exception:
            page = None
            read.broken += 1
        text = page_text(page, read) if page is not None else ""
        if not read.add(text):
            read.stopped = "text_limit"
            return
    if read.total > PAGES_MAX:
        read.stopped = "pages_not_read"


def failure(code: str, **values: int) -> dict:
    return {"ok": False, "failure": "file", "code": code, **values}


def answer(read: _Read) -> dict:
    """The answer for what was read, or the reason why that is nothing."""
    done = len(read.slots)
    texts = done - read.images
    if texts == 0:
        if read.stopped == "time_limit":
            return failure("too_slow")
        if read.limit_hits:
            return failure("bomb")
        if read.broken or not done:
            return failure("unreadable")
        return failure("no_text", count=read.images)
    notes = []
    if read.stopped is not None and done < read.total:
        notes.append({"code": read.stopped, "from": done + 1, "to": read.total})
    elif read.stopped is not None:
        notes.append({"code": read.stopped})  # the last page was cut, none was left out
    if read.images:
        notes.append({"code": "scans_not_read", "count": read.images})
    return {
        "ok": True,
        "kind": "pdf_text",
        "text": PAGE_BREAK.join(read.slots),
        "pages": read.total,
        "pages_text": texts,
        "pages_image": read.images,
        "truncated": read.stopped is not None,
        "notes": notes,
    }


def handle(data: bytes, options: dict, budget) -> dict:
    """Read `data` as a PDF. `options` is not used: this reader takes none."""
    if not data.startswith(b"%PDF-"):
        return failure("unreadable")
    # Every logger of pypdf takes its level from this one: no warning is even formatted.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    read = _Read()
    watch = _Watch(budget)
    try:
        with pypdf.apply_configuration(**LIMITS), watch:
            try:
                read_pages(data, budget, read)
            except _OutOfTimeError:
                read.stopped = "time_limit"
    except _OutOfTimeError:  # not the timer's doing any more (see _Watch): kept as a last net
        read.stopped = "time_limit"
    except _Refused as refused:
        return failure(refused.code, **refused.values)
    except MemoryError:
        return failure("bomb")
    finally:
        watch.stop()  # whatever happened above, nothing rings while the answer is written
    return answer(read)
