"""The text of a Word document (task G7 of the ingestion plan). Runs only inside the locked down
child of the parser service: main.py imports this file after the lockdown and calls `handle`.
The bytes are hostile, and so is every name and every attribute found in them.

    handle(data, options, budget) -> the whole answer, as app/ingestion/parser_client.py reads it

A DOCX is a ZIP that holds `[Content_Types].xml` and `word/document.xml`. It is opened under
the caps of zipcaps.py, and three kinds of entries are read, nothing else: the document, its
headers and footers (`word/header*.xml`, `word/footer*.xml`), and the relationship file of each
of them (where the address of a link is kept). Images, embedded objects, styles, comments and
notes are never inflated. The XML is parsed by `defusedxml`'s parser, which refuses a DTD, an
entity and an external reference, and it is walked as it streams: no tree is built, so a part
of 20 MiB costs no more memory than a small one.

What comes back for a document that was read:

    {"ok": true, "kind": "docx", "text": ..., "truncated": bool, "notes": [{"code": ...}]}

No page count: a DOCX has no pages until something lays it out, so the text holds no form feed
(a page break is a line break). The text is the headers (each distinct one once), the body,
then the footers, in the order of the document: the text of `w:t`, a tab for `w:tab`, a line
break for `w:br` and `w:cr`, a line per paragraph, the cells of a table row on one line
separated by tabs. Three rules:

1. A `w:hyperlink` whose relationship has an external target is written "text (address)", so
   a CV that shows "LinkedIn" keeps the address. An internal anchor, a missing relationship or
   an address that is not http, https or mailto leaves the text alone.
2. Of an `mc:AlternateContent` only the first `mc:Choice` is read: a text box is stored twice,
   as a drawing and as its fallback, and its text is wanted once.
3. Tracked changes are read as accepted: what was inserted (`w:ins`) is in, what was deleted
   (`w:del`, `w:delText`, `w:moveFrom`) is out. Field codes (`w:instrText`) are never text.

Reading stops at TEXT_MAX_CHARS characters (`text_limit`) or when the budget is spent
(`time_limit`): what was read is returned and `truncated` is true.

What comes back for a file that was not read, `{"ok": false, "failure": "file", "code": ...}`:

    not_docx        a ZIP that is not a Word document: the worker then asks linkedin_zip.py
    macros          a `.docm`, or any document carrying `vbaProject.bin`. Macros are never run
                    and never read; such a file is refused whole
    unreadable      a Word document without its `word/document.xml`, or with XML that does not
                    parse, and every `unreadable` of zipcaps.py
    bomb            a DTD or an entity in the XML (an entity bomb, an external entity), a
                    part with more than NAMES_MAX distinct names or open more than DEPTH_MAX
                    deep (what grows the parser without growing the file), the memory of the
                    child when a file met its limit all the same, and every `bomb` of
                    zipcaps.py
    nested_archive, protected   as zipcaps.py says
    no_text         a document without a word: images only
    too_slow        the budget was spent before a first character was read

Only main.py may import this file in production. The tests import it too, to call `handle` in
their own process on files they make.
"""

import re
import unicodedata

import zipcaps
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

TEXT_MAX_CHARS = 500_000  # ingestion_documents.text
LINK_MAX_CHARS = 2_000
PARTS_MAX = 60  # headers and footers read; Word writes at most three of each per section
FEED_BYTES = 64 * 1024  # how much XML is parsed between two looks at the budget
# What a part may cost the parser beyond its own bytes. The parser keeps every distinct name it
# meets (of an element, of an attribute) for as long as it lives, and every element that is
# open: 19 MiB of new names cost it more than 500 MiB. Word writes a fixed vocabulary, a few
# hundred names in a document, and nests a text box in a table in a text box some tens deep.
NAMES_MAX = 4_096
DEPTH_MAX = 512
_NO_MEMORY = 1  # expat's XML_ERROR_NO_MEMORY, the `code` of the ParseError it becomes

CONTENT_TYPES = "[Content_Types].xml"
DOCUMENT = "word/document.xml"
MACROS_NAME = "vbaproject.bin"
MACROS_TYPE = b"macroEnabled"

# WordprocessingML, transitional and strict.
_W = frozenset(
    {
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    }
)
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_R_ID = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
    "{http://purl.oclc.org/ooxml/officeDocument/relationships}id",
)
_RELATIONSHIP = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_LINK_SCHEMES = ("http://", "https://", "mailto:")
# Read as nothing, with all that is inside: what was deleted or moved away, and the second
# copy of an alternate content.
_SKIPPED_W = frozenset({"del", "moveFrom", "delText", "instrText"})
_BREAKS_W = frozenset({"br", "cr"})
_HEADER = re.compile(r"word/header(\d*)\.xml")
_FOOTER = re.compile(r"word/footer(\d*)\.xml")

_LINE_BREAKS = re.compile("\r\n?|[\x0b\x0c\x85\u2028\u2029]")
# Control characters but the tab and the line feed, and the two things Postgres does not store:
# U+0000 (in the first range) and half a surrogate pair.
_DROPPED = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f\ud800-\udfff]")
_BLANK_LINES = re.compile(r"\n{3,}")
# An address is written on the line of its text, "text (address)", so nothing in it may end a
# line or turn the line around: no blank, no control character (the tab, the line feed and the
# form feed are among them, and U+0085), no line or paragraph separator (which clean() would
# make a line feed), no mark or override of direction, no half surrogate.
_ADDRESS_DROPPED = re.compile(
    "[\x00-\x20\x7f-\x9f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069\ud800-\udfff]"
)


class _Full(Exception):  # noqa: N818, an outcome, not an error of this program
    """TEXT_MAX_CHARS characters were read: the parsing stops there."""


def failure(code: str) -> dict:
    return {"ok": False, "failure": "file", "code": code}


def clean(text: str) -> str:
    """Text as it is stored: NFC, every line break a line feed (a form feed too: a DOCX has no
    pages), no control character, no half surrogate, no blank at the end of a line, at most one
    empty line in a row. Tabs stay: they are the cells of a table."""
    text = unicodedata.normalize("NFC", _DROPPED.sub("", _LINE_BREAKS.sub("\n", text)))
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _split(tag: str) -> tuple[str, str]:
    """(namespace, local name) of a tag as the parser writes it, `{namespace}name`."""
    if tag[:1] == "{":
        namespace, _, name = tag[1:].partition("}")
        return namespace, name
    return "", tag


class _Bounded:
    """What both targets count of a part: its distinct names and how deep it is open. Past
    NAMES_MAX or DEPTH_MAX the part is a `bomb` and the parsing stops there, so the parser's
    tables stay small whatever the part holds. Only the hash of a name is kept, never the name:
    two names sharing one only make the count lower."""

    def __init__(self) -> None:
        self.names: set[int] = set()
        self.depth = 0

    def opened(self, tag: str, attributes: dict[str, str]) -> None:
        self.depth += 1
        if len(attributes) > NAMES_MAX or self.depth > DEPTH_MAX:
            raise zipcaps.Refused("bomb")
        names = self.names
        names.add(hash(tag))
        for name in attributes:
            names.add(hash(name))
        if len(names) > NAMES_MAX:
            raise zipcaps.Refused("bomb")

    def start_ns(self, prefix: str, uri: str) -> None:
        """A namespace declared: its prefix is one more name the parser keeps."""
        self.names.add(hash(("prefix", prefix)))
        if len(self.names) > NAMES_MAX:
            raise zipcaps.Refused("bomb")


class Walker(_Bounded):
    """The target of the parser for one part: it is told each start tag, each end tag and each
    run of characters, in the order of the document, and keeps the text. `links` maps the
    relationship ids of this part to the external addresses they stand for. `room` is how many
    characters may still be kept."""

    def __init__(self, links: dict[str, str], room: int) -> None:
        super().__init__()
        self.links = links
        self.room = room
        self.pieces: list[str] = []
        self.skip_from = 0  # depth of the element being skipped, 0 when none is
        self.in_text = 0  # depth of the open w:t, 0 when none is
        self.choices: list[int] = []  # per open mc:AlternateContent: the choices seen so far
        self.hyperlinks: list[tuple[int, str]] = []  # (where its text starts, its address)

    def _emit(self, text: str) -> None:
        if not text:
            return
        if len(text) >= self.room:
            self.pieces.append(text[: self.room])
            self.room = 0
            raise _Full
        self.room -= len(text)
        self.pieces.append(text)

    def _end_cell(self, separator: str, strip: str) -> None:
        """The line break that ended the last paragraph of a cell becomes a tab, and the tab
        that ended the last cell of a row a line break."""
        if self.pieces and self.pieces[-1] == strip:
            self.pieces.pop()
            self.room += len(strip)
        self._emit(separator)

    def start(self, tag: str, attributes: dict[str, str]) -> None:
        self.opened(tag, attributes)
        if self.skip_from:
            return
        namespace, name = _split(tag)
        if namespace == _MC:
            if name == "AlternateContent":
                self.choices.append(0)
            elif name == "Choice" and self.choices:
                self.choices[-1] += 1
                if self.choices[-1] > 1:
                    self.skip_from = self.depth
            elif name == "Fallback":
                self.skip_from = self.depth
            return
        if namespace not in _W:
            return
        if name in _SKIPPED_W:
            self.skip_from = self.depth
        elif name == "t":
            self.in_text = self.depth
        elif name == "tab":
            self._emit("\t")
        elif name in _BREAKS_W:
            self._emit("\n")
        elif name == "noBreakHyphen":
            self._emit("-")
        elif name == "hyperlink":
            relationship = next((attributes[key] for key in _R_ID if key in attributes), "")
            self.hyperlinks.append((len(self.pieces), self.links.get(relationship, "")))

    def end(self, tag: str) -> None:
        depth, self.depth = self.depth, self.depth - 1
        if self.skip_from:
            if depth == self.skip_from:
                self.skip_from = 0
            return
        namespace, name = _split(tag)
        if namespace == _MC:
            if name == "AlternateContent" and self.choices:
                self.choices.pop()
            return
        if namespace not in _W:
            return
        if name == "t":
            self.in_text = 0
        elif name == "p":
            self._emit("\n")
        elif name == "tc":
            self._end_cell("\t", "\n")
        elif name == "tr":
            self._end_cell("\n", "\t")
        elif name == "hyperlink" and self.hyperlinks:
            begun, address = self.hyperlinks.pop()
            shown = "".join(self.pieces[begun:]).strip()
            if address and shown and address.removeprefix("mailto:") not in shown:
                self._emit(f" ({address})")

    def data(self, text: str) -> None:
        if self.in_text and not self.skip_from:
            self._emit(text)

    def close(self) -> None:
        return None


class _Links(_Bounded):
    """The target of the parser for a relationship file: id -> external address."""

    def __init__(self) -> None:
        super().__init__()
        self.found: dict[str, str] = {}

    def start(self, tag: str, attributes: dict[str, str]) -> None:
        self.opened(tag, attributes)
        if tag != _RELATIONSHIP or attributes.get("TargetMode") != "External":
            return
        address = _ADDRESS_DROPPED.sub("", attributes.get("Target", ""))
        if len(address) <= LINK_MAX_CHARS and address.lower().startswith(_LINK_SCHEMES):
            self.found[attributes.get("Id", "")] = address

    def end(self, tag: str) -> None:
        self.depth -= 1

    def data(self, text: str) -> None:
        return None

    def close(self) -> None:
        return None


def parse(content: bytes, target, budget) -> bool:
    """Feed `content` to defusedxml's parser, which tells `target` what it finds. False when the
    budget was spent before the end. Raises what the target raises (_Full, a Refused past
    NAMES_MAX or DEPTH_MAX) and what the parser raises: a DefusedXmlException for a DTD or an
    entity, a ParseError for XML that is not, a MemoryError when the address space limit was
    met, whichever of Python and expat met it. One start tag is told to the target only when
    it is complete, so a single tag of 19 MiB of new attribute names is counted too late:
    that one the limit stops."""
    parser = ElementTree.DefusedXMLParser(
        target=target, forbid_dtd=True, forbid_entities=True, forbid_external=True
    )
    try:
        for at in range(0, len(content), FEED_BYTES):
            if budget.spent():
                return False
            parser.feed(content[at : at + FEED_BYTES])
        parser.close()
    except ElementTree.ParseError as error:
        if getattr(error, "code", None) == _NO_MEMORY:
            raise MemoryError from None
        raise
    finally:
        del parser  # what it holds is given back before an answer is written
    return True


def links_of(archive: zipcaps.Archive, part: str, budget) -> dict[str, str]:
    """The external addresses of `part` ("word/document.xml"), from the relationship file
    beside it. Empty when the file is missing or cannot be parsed: the text is read without
    its addresses. A DTD or an entity in it refuses the document all the same."""
    folder, _, name = part.rpartition("/")
    relationships = f"{folder}/_rels/{name}.rels"
    if relationships not in archive.names:
        return {}
    target = _Links()
    try:
        parse(archive.read(relationships), target, budget)
    except ElementTree.ParseError:
        return {}
    return target.found


def _numbered(archive: zipcaps.Archive, pattern: re.Pattern[str]) -> list[str]:
    found = []
    for name in archive.names:
        match = pattern.fullmatch(name)
        if match:
            found.append((int(match.group(1) or 0), name))
    return [name for _, name in sorted(found)[:PARTS_MAX]]


def read(archive: zipcaps.Archive, budget) -> dict:
    """The answer for an archive that is a Word document."""
    room = TEXT_MAX_CHARS
    stopped = None
    texts: list[str] = []
    headers = _numbered(archive, _HEADER)
    footers = _numbered(archive, _FOOTER)
    for part in (*headers, DOCUMENT, *footers):
        walker = Walker(links_of(archive, part, budget), room)
        try:
            if not parse(archive.read(part), walker, budget):
                stopped = "time_limit"
        except _Full:
            stopped = "text_limit"
        except ElementTree.ParseError:
            if part == DOCUMENT:
                return failure("unreadable")
            continue  # a damaged header does not cost the document
        text = clean("".join(walker.pieces))
        # Word writes the same header for the first, the even and the other pages.
        if text and (part == DOCUMENT or text not in texts):
            texts.append(text)
            room -= len(text) + 2
        if stopped or room <= 0:
            break
    text = "\n\n".join(texts)[:TEXT_MAX_CHARS]
    if not text:
        return failure("too_slow" if stopped == "time_limit" else "no_text")
    return {
        "ok": True,
        "kind": "docx",
        "text": text,
        "truncated": stopped is not None,
        "notes": [{"code": stopped}] if stopped else [],
    }


def handle(data: bytes, options: dict, budget) -> dict:
    """Read `data` as a Word document. `options` is not used: this reader takes none."""
    try:
        archive = zipcaps.Archive(data)
        lowered = {name.lower() for name in archive.names}
        if any(name.rsplit("/", 1)[-1] == MACROS_NAME for name in lowered):
            return failure("macros")
        if CONTENT_TYPES not in archive.names:
            return failure("not_docx")
        if DOCUMENT not in archive.names:
            # A Word document that lost its body, or another Office file (a workbook, slides).
            damaged = any(name.startswith("word/") for name in lowered)
            return failure("unreadable" if damaged else "not_docx")
        if MACROS_TYPE in archive.read(CONTENT_TYPES):
            return failure("macros")
        return read(archive, budget)
    except zipcaps.Refused as refused:
        return failure(refused.code)
    except (DefusedXmlException, MemoryError):
        return failure("bomb")
