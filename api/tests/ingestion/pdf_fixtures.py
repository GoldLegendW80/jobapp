"""PDF files made in code for the tests of the PDF reader (ingestion G2b): no binary is committed.

`build` writes a PDF by hand, object by object, so that a test can also write one wrong: a page
tree that loops, a stream that lies about its length, a file cut in the middle. The helpers
below it are the fixtures the plan names, each a few lines over `build`. Every text in them is
made up: Jane Example, of Example Street.

The encrypted ones are the only ones that need a library to make: pypdf's own writer, in the
test process, where `cryptography` is installed (the parser blocks it; that is what they test).
"""

import io
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field

MIB = 1024 * 1024
FONT = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
# A font whose letter A is written U+1F600 by its /ToUnicode map: one byte in the file, one
# character outside the basic plane in the text.
ASTRAL = "\U0001f600"
_ASTRAL_CMAP = (
    b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
    b"/CMapName /Adobe-Identity-UCS def /CMapType 2 def "
    b"1 begincodespacerange <00> <FF> endcodespacerange "
    b"1 beginbfchar <41> <D83DDE00> endbfchar "
    b"endcmap CMapName currentdict /CMap defineresource pop end end"
)


def escape(text: str) -> bytes:
    """`text` as the inside of a PDF literal string, in WinAnsi (Latin-1 is close enough for
    the letters these fixtures use)."""
    raw = text.encode("latin-1")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def lines(*texts: str, font: str = "F1") -> bytes:
    """A content stream that writes each text on a line of its own."""
    shown = b" T* ".join(b"(" + escape(text) + b") Tj" for text in texts)
    return b"BT /" + font.encode() + b" 11 Tf 14 TL 56 780 Td " + shown + b" ET"


@dataclass
class Page:
    """One page: its content stream, the addresses of its link annotations, and what else
    its dictionary says."""

    content: bytes = b""
    links: Sequence[str] = ()
    extra: bytes = b""
    compress: bool = False


@dataclass
class Built:
    """A PDF being written: numbered objects, then the cross reference table."""

    objects: dict[int, bytes] = field(default_factory=dict)

    def add(self, body: bytes) -> int:
        number = len(self.objects) + 1
        self.objects[number] = body
        return number

    def reserve(self) -> int:
        return self.add(b"null")

    def stream(self, data: bytes, entries: bytes = b"", *, compress: bool = False) -> int:
        if compress:
            data, entries = zlib.compress(data, 9), entries + b" /Filter /FlateDecode"
        head = b"<< /Length " + str(len(data)).encode() + b" " + entries + b" >>"
        return self.add(head + b"\nstream\n" + data + b"\nendstream")

    def write(self, root: int, trailer: bytes = b"", header: bytes = b"%PDF-1.7\n") -> bytes:
        out = io.BytesIO()
        out.write(header + b"%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for number, body in self.objects.items():
            offsets[number] = out.tell()
            out.write(str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n")
        start = out.tell()
        count = len(self.objects) + 1
        out.write(b"xref\n0 " + str(count).encode() + b"\n0000000000 65535 f \n")
        for number in range(1, count):
            out.write(f"{offsets[number]:010d} 00000 n \n".encode())
        out.write(
            b"trailer\n<< /Size " + str(count).encode() + b" /Root " + str(root).encode()
            + b" 0 R " + trailer + b" >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n"
        )  # fmt: skip
        return out.getvalue()


def build(
    pages: Sequence[Page | str | bytes],
    *,
    catalog: bytes = b"",
    trailer: bytes = b"",
    fonts: bytes | None = None,
    extra_objects: Sequence[bytes] = (),
) -> bytes:
    """A PDF of these pages. A str is a page that shows that text, a line per line of it; bytes
    are a content stream as they are. `catalog` and `trailer` are added to those dictionaries,
    `extra_objects` are written first, as objects 1, 2 and so on, for `catalog` to point at."""
    pdf = Built()
    for body in extra_objects:
        pdf.add(body)
    font = pdf.add(FONT)
    astral = pdf.add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /ToUnicode "
        + str(len(pdf.objects) + 2).encode()
        + b" 0 R >>"
    )
    pdf.stream(_ASTRAL_CMAP)
    resources = fonts or (
        b"<< /Font << /F1 "
        + str(font).encode()
        + b" 0 R /F2 "
        + str(astral).encode()
        + b" 0 R >> >>"
    )
    tree = pdf.reserve()
    kids = []
    for page in pages:
        if isinstance(page, str):
            page = Page(lines(*page.split("\n")))
        elif isinstance(page, bytes):
            page = Page(page)
        content = pdf.stream(page.content, compress=page.compress)
        annotations = b""
        if page.links:
            numbers = [
                pdf.add(
                    b"<< /Type /Annot /Subtype /Link /Rect [56 700 300 720] /Border [0 0 0] "
                    b"/A << /S /URI /URI (" + escape(link) + b") >> >>"
                )
                for link in page.links
            ]
            annotations = b" /Annots [" + b" ".join(b"%d 0 R" % n for n in numbers) + b"]"
        kids.append(
            pdf.add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources " % tree
                + resources
                + b" /Contents %d 0 R" % content
                + annotations
                + b" "
                + page.extra
                + b" >>"
            )
        )
    pdf.objects[tree] = (
        b"<< /Type /Pages /Count " + str(len(kids)).encode() + b" /Kids ["
        + b" ".join(b"%d 0 R" % kid for kid in kids) + b"] >>"
    )  # fmt: skip
    root = pdf.add(b"<< /Type /Catalog /Pages " + str(tree).encode() + b" 0 R " + catalog + b" >>")
    return pdf.write(root, trailer)


# --- the ordinary ones --------------------------------------------------------------------------

SENTENCE = "Jane Example led the migration of the billing service to Postgres in 2021."


def page_text(number: int, rows: int = 40) -> str:
    """A page of a made up CV: `rows` lines, each ending with its page number."""
    return "\n".join(f"{SENTENCE} Line {row} of page {number}." for row in range(1, rows + 1))


def text_pdf(pages: int = 1, rows: int = 40, *, compress: bool = False) -> bytes:
    """`pages` pages of text, `rows` lines each (about 90 characters a line)."""
    return build(
        [
            Page(lines(*page_text(n, rows).split("\n")), compress=compress)
            for n in range(1, pages + 1)
        ]
    )


def scanned_page() -> Page:
    """A page that is a picture and nothing else: one grey pixel stretched over it."""
    return Page(b"q 595 0 0 842 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI Q")


def mixed_pdf() -> bytes:
    """Text, a scan, text, a scan with a page number under it, text: five pages."""
    return build(
        [
            page_text(1, 12),
            scanned_page(),
            page_text(3, 12),
            Page(scanned_page().content + b" " + lines("Page 4 of 5")),
            page_text(5, 12),
        ]
    )


def astral_pdf(characters: int, per_page: int = 2500) -> bytes:
    """`characters` characters outside the basic plane, `per_page` a page, 50 to a line."""
    pages, left = [], characters
    while left > 0:
        here = min(per_page, left)
        rows = ["A" * min(50, here - done) for done in range(0, here, 50)]
        pages.append(Page(lines(*rows, font="F2"), compress=True))
        left -= here
    return build(pages)


def padded_pdf(size: int) -> bytes:
    """One page of text in a file of exactly `size` bytes: the rest is an image nothing shows,
    bytes that do not compress."""
    import random

    def made(noise: bytes) -> bytes:
        image = (
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Length %d >>\nstream\n" % len(noise) + noise + b"\nendstream"
        )
        return build([page_text(1, 20)], extra_objects=[image])

    # The numbers that grow with the file (/Length, startxref) take a few bytes more: left as
    # room, and filled with blanks after the end of file marker.
    room = size - len(made(b"")) - 32
    whole = made(random.Random(7).randbytes(room))  # noqa: S311, a filler, not a secret
    assert len(whole) <= size, "the padding went over the size asked"
    return whole + b"\n" * (size - len(whole))  # blanks after the end of file marker


# --- protected ----------------------------------------------------------------------------------


def encrypted(plain: bytes, algorithm: str, user_password: str = "") -> bytes:
    """`plain` protected with `algorithm` ("RC4-128", "AES-128", "AES-256"). An empty user
    password is a file anyone opens and nobody may copy from: how most CVs are exported."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(plain)))
    writer.encrypt(user_password, owner_password="owner-of-the-fixture", algorithm=algorithm)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# --- hostile ------------------------------------------------------------------------------------


def truncated_pdf() -> bytes:
    """A real file cut at 60 percent: no cross reference table, the last object half written."""
    whole = text_pdf(3)
    return whole[: len(whole) * 6 // 10]


def cyclic_tree_pdf() -> bytes:
    """A page tree whose node lists itself among its kids."""
    pdf = Built()
    font = pdf.add(FONT)
    tree = pdf.reserve()
    content = pdf.stream(lines("A page under a tree that loops.", SENTENCE))
    page = pdf.add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R "
        b">> >> /Contents %d 0 R >>" % (tree, font, content)
    )
    pdf.objects[tree] = b"<< /Type /Pages /Count 3 /Kids [%d 0 R %d 0 R %d 0 R] >>" % (
        page,
        tree,
        tree,
    )
    return pdf.write(pdf.add(b"<< /Type /Catalog /Pages %d 0 R >>" % tree))


def nested_tree_pdf(depth: int) -> bytes:
    """One page under `depth` page tree nodes, each the only kid of the one above."""
    pdf = Built()
    font = pdf.add(FONT)
    content = pdf.stream(lines("A page at the bottom of a deep tree.", SENTENCE))
    nodes = [pdf.reserve() for _ in range(depth)]
    page = pdf.add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R "
        b">> >> /Contents %d 0 R >>" % (nodes[-1], font, content)
    )
    for index, node in enumerate(nodes):
        kid = nodes[index + 1] if index + 1 < depth else page
        parent = b" /Parent %d 0 R" % nodes[index - 1] if index else b""
        pdf.objects[node] = b"<< /Type /Pages /Count 1 /Kids [%d 0 R]%s >>" % (kid, parent)
    return pdf.write(pdf.add(b"<< /Type /Catalog /Pages %d 0 R >>" % nodes[0]))


def nested_arrays_pdf(depth: int) -> bytes:
    """A page whose content stream opens `depth` arrays inside one another, and a catalog
    entry that does the same with dictionaries."""
    deep = b"[" * depth + b"]" * depth
    return build(
        [Page(b"BT /F1 11 Tf 56 780 Td " + deep + b" TJ (After the arrays.) Tj ET")],
        catalog=b"/Deep " + b"<< /D " * depth + b"null" + b" >>" * depth,
    )


def inflating_pdf(inflated: int = 1024 * MIB, declared: int | None = None) -> bytes:
    """A page whose content stream is `inflated` bytes once inflated (zeros: a megabyte of
    file for a gigabyte of content), and says it is `declared` bytes long."""
    squeezer = zlib.compressobj(9)
    block = bytes(MIB)
    packed = b"".join(squeezer.compress(block) for _ in range(inflated // MIB)) + squeezer.flush()
    pdf = Built()
    font = pdf.add(FONT)
    tree = pdf.reserve()
    length = len(packed) if declared is None else declared
    content = pdf.add(
        b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % length + packed + b"\nendstream"
    )
    page = pdf.add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R "
        b">> >> /Contents %d 0 R >>" % (tree, font, content)
    )
    pdf.objects[tree] = b"<< /Type /Pages /Count 1 /Kids [%d 0 R] >>" % page
    return pdf.write(pdf.add(b"<< /Type /Catalog /Pages %d 0 R >>" % tree))


def filter_chain_pdf(filters: int = 40) -> bytes:
    """A content stream wrapped in `filters` layers of FlateDecode."""
    data = lines("Under many layers.", SENTENCE)
    for _ in range(filters):
        data = zlib.compress(data)
    names = b"[" + b" ".join([b"/FlateDecode"] * filters) + b"]"
    pdf = Built()
    font = pdf.add(FONT)
    tree = pdf.reserve()
    content = pdf.add(
        b"<< /Length %d /Filter %s >>\nstream\n" % (len(data), names) + data + b"\nendstream"
    )
    page = pdf.add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R "
        b">> >> /Contents %d 0 R >>" % (tree, font, content)
    )
    pdf.objects[tree] = b"<< /Type /Pages /Count 1 /Kids [%d 0 R] >>" % page
    return pdf.write(pdf.add(b"<< /Type /Catalog /Pages %d 0 R >>" % tree))


def lying_count_pdf(count: int = 50_000_000) -> bytes:
    """One page, in a tree that claims `count` of them."""
    whole = text_pdf(1, 5)
    return whole.replace(b"/Count 1 ", b"/Count %d " % count, 1)


def looping_xref_pdf() -> bytes:
    """A cross reference table whose /Prev points at itself."""
    whole = text_pdf(1, 5)
    start = int(whole.rsplit(b"startxref\n", 1)[1].split(b"\n", 1)[0])
    return build([page_text(1, 5)], trailer=b"/Prev %d" % start)


SCRIPT_MARKER = "app.alert; script-marker-7f3a"
ATTACHED_MARKER = "attached-marker-91c2"
FIELD_MARKER = "field-marker-4d18"


def active_pdf() -> bytes:
    """A page of text in a file that also carries a script run at opening, an attached file and
    a form field with a value: none of the three is the text of a page."""
    script = b"<< /S /JavaScript /JS (" + escape(SCRIPT_MARKER) + b") >>"
    attached = (
        b"<< /Type /EmbeddedFile /Length %d >>\nstream\n" % len(ATTACHED_MARKER)
        + ATTACHED_MARKER.encode()
        + b"\nendstream"
    )
    filespec = b"<< /Type /Filespec /F (notes.txt) /EF << /F 2 0 R >> >>"
    form_field = (
        b"<< /FT /Tx /T (salary) /V (" + escape(FIELD_MARKER) + b") /Ff 0 /Rect [0 0 0 0] >>"
    )
    return build(
        [page_text(1, 6)],
        extra_objects=[script, attached, filespec, form_field],
        catalog=(
            b"/OpenAction 1 0 R /Names << /EmbeddedFiles << /Names [(notes.txt) 3 0 R] >> "
            b"/JavaScript << /Names [(run) 1 0 R] >> >> /AcroForm << /Fields [4 0 R] >>"
        ),
    )


def pdf_with_zip_tail() -> bytes:
    """Five bytes of PDF, then a ZIP archive: a file that is really another type."""
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
    return b"%PDF-1.7\n" + out.getvalue()


def slow_pdf(pages: int, operators: int) -> bytes:
    """`pages` pages that all show the same content stream of `operators` text operators,
    stored once and deflated: a small file that takes its time, page after page."""
    pdf = Built()
    font = pdf.add(FONT)
    tree = pdf.reserve()
    body = b"BT /F1 6 Tf 56 780 Td " + b"(slow) Tj 1 0 Td " * operators + b"ET"
    content = pdf.stream(body, compress=True)
    kids = [
        pdf.add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 "
            b"%d 0 R >> >> /Contents %d 0 R >>" % (tree, font, content)
        )
        for _ in range(pages)
    ]
    pdf.objects[tree] = (
        b"<< /Type /Pages /Count %d /Kids [" % pages
        + b" ".join(b"%d 0 R" % k for k in kids)
        + b"] >>"
    )
    return pdf.write(pdf.add(b"<< /Type /Catalog /Pages %d 0 R >>" % tree))
