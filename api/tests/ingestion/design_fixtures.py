"""Invented CV files made in code for the design capture's sandbox kinds (documents D25a): PDF
pages drawn with fonts, colours, bands, rules, a photo box and a form, and Word documents with a
theme, styles and direct formatting. No binary is committed. Every text is made up: Alex
Example, of Example Robotics.

CANARY is planted in the text of each file: a test asserts where it may appear (a run's text of
a PDF) and where it never does (any DOCX answer, the logs)."""

from collections.abc import Sequence

from tests.ingestion import archive_fixtures as archives
from tests.ingestion import pdf_fixtures as pdf

CANARY = "zq7w4k9canary"
A4 = (595, 842)

_DESCRIPTOR = (
    b"<< /Type /FontDescriptor /FontName /ABCDEF+Calibri-Bold /Flags 32 /FontWeight 700 "
    b"/ItalicAngle 0 /FontBBox [0 -250 1000 750] /Ascent 750 /Descent -250 /CapHeight 700 "
    b"/StemV 80 >>"
)
# F1 to F4: the standard Helvetica Bold, Times Roman and Courier, and a Calibri Bold that names
# a descriptor and is not embedded (PDFium draws a stand in, the name stays as written).
STANDARD = {
    "F1": b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    "F2": b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
    "F4": b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
}


def text(
    words: str,
    x: float,
    y: float,
    *,
    font: str = "F2",
    size: float = 11,
    colour: tuple[float, float, float] = (0, 0, 0),
    mode: int = 0,
) -> bytes:
    """One line of text at (x, y), PDF space (from the bottom left)."""
    red, green, blue = colour
    return b"BT /%s %g Tf %g %g %g rg %d Tr 1 0 0 1 %g %g Tm (%s) Tj ET\n" % (
        font.encode(),
        size,
        red,
        green,
        blue,
        mode,
        x,
        y,
        pdf.escape(words),
    )


def fill(x: float, y: float, width: float, height: float, colour: tuple) -> bytes:
    return b"q %g %g %g rg %g %g %g %g re f Q\n" % (*colour, x, y, width, height)


def rule(x0: float, x1: float, y: float, colour: tuple, width: float = 1.0) -> bytes:
    return b"q %g %g %g RG %g w %g %g m %g %g l S Q\n" % (*colour, width, x0, y, x1, y)


def outline(x: float, y: float, width: float, height: float, colour: tuple) -> bytes:
    return b"q %g %g %g RG 2 w %g %g %g %g re S Q\n" % (*colour, x, y, width, height)


def picture(x: float, y: float, width: float, height: float) -> bytes:
    """The image /Im1 (two by two grey pixels) drawn over that box."""
    return b"q %g 0 0 %g %g %g cm /Im1 Do Q\n" % (width, height, x, y)


# F5: Helvetica whose ToUnicode reads "A" as U+0958 and "B" as U+2ADC, characters PDFium keeps
# and NFC writes as two.
_GROWING = (
    b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Growing def "
    b"1 begincodespacerange <00> <FF> endcodespacerange "
    b"2 beginbfchar <41> <0958> <42> <2ADC> endbfchar "
    b"endcmap CMapName currentdict /CMap defineresource pop end end"
)


def build(
    pages: Sequence[bytes], *, form: bytes = b"", size: tuple = A4, count: int | None = None
) -> bytes:
    """A PDF of these content streams, with the fonts F1 to F5, the image /Im1 and the form
    /Fm1 (whose content is `form`, moved by 100 and 50 points) in every page's resources.
    `count` is the page count the tree claims, the number of pages when None."""
    built = pdf.Built()
    fonts = {name: built.add(body) for name, body in STANDARD.items()}
    fonts["F5"] = built.add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode %d 0 R >>"
        % built.stream(_GROWING)
    )
    descriptor = built.add(_DESCRIPTOR)
    widths = b" ".join(b"%d" % (400 + n % 3 * 100) for n in range(95))
    fonts["F3"] = built.add(
        b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+Calibri-Bold /FirstChar 32 "
        b"/LastChar 126 /Widths [" + widths + b"] /FontDescriptor %d 0 R "
        b"/Encoding /WinAnsiEncoding >>" % descriptor
    )
    image = built.stream(
        b"\x40\x80\xc0\xff",
        b"/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8",
    )
    xobjects = b"/Im1 %d 0 R" % image
    if form:
        xform = built.stream(
            form, b"/Type /XObject /Subtype /Form /BBox [0 0 595 842] /Matrix [1 0 0 1 100 50]"
        )
        xobjects += b" /Fm1 %d 0 R" % xform
    font_list = b" ".join(b"/%s %d 0 R" % (name.encode(), number) for name, number in fonts.items())
    resources = b"<< /Font << " + font_list + b" >> /XObject << " + xobjects + b" >> >>"
    tree = built.reserve()
    kids = []
    for content in pages:
        stream = built.stream(content)
        kids.append(
            built.add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %g %g] /Resources " % (tree, *size)
                + resources
                + b" /Contents %d 0 R >>" % stream
            )
        )
    built.objects[tree] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (
        len(kids) if count is None else count,
        b" ".join(b"%d 0 R" % kid for kid in kids),
    )
    root = built.add(b"<< /Type /Catalog /Pages %d 0 R >>" % tree)
    return built.write(root)


NAVY = (0.12, 0.31, 0.47)  # #1f4f78
RED = (0.8, 0, 0)  # #cc0000


def one_column() -> bytes:
    """A band across the top with the name in white, a rule, two sections, a round photo box,
    a bordered box and a shape inside the form."""
    return build(
        [
            fill(0, 742, 595, 100, NAVY)
            + text("Alex Example", 56, 790, font="F3", size=20, colour=(1, 1, 1))
            + text("Lead engineer at Example Robotics", 56, 765, font="F1", size=11)
            + picture(480, 752, 80, 80)
            + rule(56, 540, 720, RED, 1.5)
            + text("Experience", 56, 700, font="F1", size=12, colour=NAVY)
            + text("Example Robotics, built the " + CANARY + " planner", 56, 684)
            + text("2021 to 2024", 460, 684, font="F4", size=9)
            + outline(50, 600, 300, 60, NAVY)
            + b"q /Fm1 Do Q\n"
        ],
        form=fill(0, 0, 50, 20, RED),
    )


LEFT = ("Skills", "Python", "Rust", "Languages", "French")
RIGHT = ("Experience", "Example Robotics", "Lead engineer", "Example Corp", "Engineer")


def two_columns() -> bytes:
    """A tinted sidebar on the left and a main column, written line by line across both
    columns at the same heights (the order a PDF's stream may hold), under a header that spans
    the page."""
    body = fill(0, 0, 190, 842, (0.93, 0.95, 0.97))
    body += text("Alex Example", 40, 790, font="F1", size=22)
    for row, (left, right) in enumerate(zip(LEFT, RIGHT, strict=True)):
        y = 720 - 18 * row
        body += text(left, 40, y, size=10) + text(right, 220, y, size=10)
    return build([body])


def scanned(*, ocr: str = "") -> bytes:
    """One page that is a picture, with an invisible text layer when `ocr` is given."""
    content = picture(0, 0, 595, 842)
    if ocr:
        content += text(ocr, 56, 700, mode=3)
    return build([content])


def pages(count: int) -> bytes:
    return build([text(f"Page {n + 1}", 56, 780) for n in range(count)])


def outside() -> bytes:
    """A line on the page and one written past its right edge."""
    return build([text("On the page", 56, 780) + text("Past the edge " + CANARY, 700, 760)])


def many_runs(lines: int, per_line: int, size: float = 4.0) -> bytes:
    """Lines of short words that alternate two fonts: a run per word."""
    content = b""
    for line in range(lines):
        y = 830 - line * (size + 1)
        content += b"BT 1 0 0 1 10 %g Tm " % y
        for word in range(per_line):
            content += b"/F%d %g Tf (w%d ) Tj " % (1 + word % 2, size, word)
        content += b"ET\n"
    return build([content])


def long_lines(lines: int, characters: int) -> bytes:
    """Lines of `characters` letters at one point: long runs, cut at the run cap."""
    content = b"".join(text("x" * characters, 5, 835 - line * 1.2, size=1) for line in range(lines))
    return build([content])


def many_shapes(count: int) -> bytes:
    """`count` filled squares of growing sides (3 to 3 + count / 10 points)."""
    content = b"".join(
        fill(5 + (n % 20) * 29, 5 + (n // 20) * 29, 3 + n / 10 % 25, 3 + n / 10 % 25, NAVY)
        for n in range(count)
    )
    return build([content])


# --- Word documents -----------------------------------------------------------------------------

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
THEME = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office">'
    '<a:themeElements><a:clrScheme name="Office">'
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
    '</a:clrScheme><a:fontScheme name="Office">'
    '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/></a:minorFont>'
    "</a:fontScheme></a:themeElements></a:theme>"
)
STYLE_LIST = (
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    "</w:style>"
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240" w:after="0"/>'
    '<w:pBdr><w:bottom w:val="single" w:sz="6" w:color="1F4E79"/></w:pBdr>'
    '<w:outlineLvl w:val="0"/><w:rPr><w:i/></w:rPr></w:pPr>'
    '<w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:hAnsiTheme="majorHAnsi"/><w:b/><w:caps/>'
    '<w:color w:val="2F5496" w:themeColor="accent1" w:themeShade="BF"/><w:sz w:val="32"/>'
    "</w:rPr></w:style>"
    '<w:style w:type="character" w:styleId="Strong"><w:name w:val="Strong"/>'
    '<w:rPr><w:b w:val="0"/><w:i/><w:color w:themeColor="accent2"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="Grid"><w:name w:val="Table Grid"/>'
    '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/></w:rPr></w:tblStylePr></w:style>'
)


def styles(inside: str = STYLE_LIST) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><w:styles {W}><w:docDefaults>'
        '<w:rPrDefault><w:rPr><w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/>'
        '<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
        '<w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        f"</w:docDefaults>{inside}</w:styles>"
    )


def heading(words: str) -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>{archives.run(words)}</w:p>'


def formatted(words: str) -> str:
    return (
        '<w:p><w:r><w:rPr><w:rFonts w:ascii="Georgia" w:hAnsi="Georgia"/><w:sz w:val="20"/>'
        f'<w:color w:val="C00000"/></w:rPr><w:t xml:space="preserve">{words}</w:t></w:r></w:p>'
    )


DRAWING = (
    '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing/></mc:Choice>'
    "<mc:Fallback><w:pict/></mc:Fallback></mc:AlternateContent></w:r></w:p>"
)
SECTION = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1080" w:bottom="1440" w:left="1080" w:header="708"/>'
    '<w:cols w:num="2" w:space="708"/></w:sectPr>'
)
GRID = (
    '<w:tbl><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/>'
    "</w:tblGrid><w:tr><w:tc>" + archives.paragraph(archives.run("Python")) + "</w:tc></w:tr>"
    "</w:tbl>"
)


def styled_docx(*, style_part: str | None = None, theme: str | None = THEME) -> bytes:
    """A CV with a theme, styles, two headings, a run of direct formatting holding the canary,
    a table of three columns, a drawing with its fallback, and a section of two columns."""
    extra = [("word/styles.xml", styles() if style_part is None else style_part)]
    if theme is not None:
        extra.append(("word/theme/theme1.xml", theme))
    return archives.docx(
        heading("Experience"),
        formatted("Built the " + CANARY + " planner at Example Robotics"),
        heading("Skills"),
        GRID,
        DRAWING,
        SECTION,
        extra=extra,
    )
