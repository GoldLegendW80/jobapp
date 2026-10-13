"""Scanned PDFs, honest and hostile, built in code for the tests of task G8 (no binary file is
committed): tests/ingestion/parser/test_pdf_render.py and test_image_container.py, and the
worker's tests/ingestion/test_jobs_images.py. They are written object by object with the
`Built` of tests/ingestion/pdf_fixtures.py; the pictures come from image_fixtures.py.

Every text in them is made up: Jane Example, of Example Street.
"""

import io
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image

from tests.ingestion import image_fixtures as images
from tests.ingestion import pdf_fixtures as pdf

MIB = pdf.MIB
A4 = (595.0, 842.0)
INCH = 72.0
FOOTER = "Jane Example, curriculum vitae, page 2 of 2, printed from the files of Example Street"
SHORT = "Jane Example. Engineer, Example Street.\nReach me at jane@example.org or 555 0100.\n" * 2
LONG = "\n".join(pdf.SENTENCE for _ in range(6))


@dataclass
class Picture:
    """An image as a PDF holds it: the encoded stream and what its dictionary says."""

    data: bytes
    width: int
    height: int
    entries: bytes = b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode"


@dataclass
class Sheet:
    """One page: its size in points, the picture drawn on it (over the whole page unless `box`
    says where: x, y, width, height), and the text written over it."""

    picture: Picture | None = None
    text: str = ""
    size: tuple[float, float] = A4
    box: tuple[float, float, float, float] | None = None
    rotate: int = 0
    draws: int = 1  # how many times the picture is drawn
    links: Sequence[str] = ()


def jpeg_picture(width: int = 620, height: int = 877, quality: int = 70) -> Picture:
    """A page of a CV as a scanner would store it."""
    return Picture(images.saved(images.page(width, height), "JPEG", quality=quality), width, height)


def grey_scan(width: int, height: int, quality: int = 25) -> Picture:
    """A grey scan with the grain of paper: it does not compress to nothing."""
    grain = Image.effect_noise((width // 4, height // 4), 30).resize((width, height))
    out = io.BytesIO()
    grain.save(out, format="JPEG", quality=quality)
    return Picture(
        out.getvalue(),
        width,
        height,
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /DCTDecode",
    )


def flate_picture(width: int, height: int) -> Picture:
    """An uncompressed looking RGB picture behind FlateDecode: all zero, so a few kilobytes
    that inflate to width x height x 3 bytes."""
    packer = zlib.compressobj(1)
    row = bytes(width * 3)
    data = b"".join(packer.compress(row) for _ in range(height)) + packer.flush()
    return Picture(
        data, width, height, b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"
    )


def broken_picture(filter_name: str) -> Picture:
    """A picture whose stream claims a decoder (JBIG2Decode, JPXDecode) and holds noise."""
    entries = b"/ColorSpace /DeviceGray /BitsPerComponent 1 /Filter /" + filter_name.encode()
    return Picture(bytes(range(256)) * 64, 2480, 3508, entries)


def scan_pdf(sheets: Sequence[Sheet]) -> bytes:
    """A PDF of these pages. Each distinct picture is one object, shared by its pages."""
    built = pdf.Built()
    font = built.add(pdf.FONT)
    tree = built.reserve()
    stored: dict[int, int] = {}
    kids = []
    for sheet in sheets:
        width, height = sheet.size
        content, resources = b"", b"/Font << /F1 %d 0 R >>" % font
        if sheet.picture is not None:
            key = id(sheet.picture)
            if key not in stored:
                stored[key] = built.stream(
                    sheet.picture.data,
                    b"/Type /XObject /Subtype /Image /Width %d /Height %d "
                    % (sheet.picture.width, sheet.picture.height)
                    + sheet.picture.entries,
                )
            x, y, w, h = sheet.box or (0, 0, width, height)
            content = b" ".join(
                [b"q %.2f 0 0 %.2f %.2f %.2f cm /Im0 Do Q" % (w, h, x, y)] * sheet.draws
            )
            resources += b" /XObject << /Im0 %d 0 R >>" % stored[key]
        if sheet.text:
            content += b" " + pdf.lines(*sheet.text.split("\n")).replace(
                b"56 780 Td", b"56 %d Td" % max(20, min(780, int(height) - 62))
            )
        annotations = b""
        if sheet.links:
            numbers = [
                built.add(
                    b"<< /Type /Annot /Subtype /Link /Rect [56 700 300 720] /Border [0 0 0] "
                    b"/A << /S /URI /URI (" + pdf.escape(link) + b") >> >>"
                )
                for link in sheet.links
            ]
            annotations = b" /Annots [" + b" ".join(b"%d 0 R" % n for n in numbers) + b"]"
        body = built.stream(content)
        kids.append(
            built.add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Rotate %d "
                % (tree, width, height, sheet.rotate)
                + b"/Resources << "
                + resources
                + b" >> /Contents %d 0 R" % body
                + annotations
                + b" >>"
            )
        )
    built.objects[tree] = (
        b"<< /Type /Pages /Count %d /Kids [" % len(kids)
        + b" ".join(b"%d 0 R" % kid for kid in kids)
        + b"] >>"
    )
    root = built.add(b"<< /Type /Catalog /Pages %d 0 R >>" % tree)
    return built.write(root)


def scan(pages: int = 1) -> bytes:
    """A CV scanned page by page: no text layer at all."""
    picture = jpeg_picture()
    return scan_pdf([Sheet(picture) for _ in range(pages)])


def mixed() -> bytes:
    """Five pages: a text, a scan, a text, a scan under a footer of 80 characters, and a short
    text beside a small logo. Pages 1 and 3 (zero based) are the images."""
    picture = jpeg_picture()
    return scan_pdf(
        [
            Sheet(text=LONG),
            Sheet(picture),
            Sheet(text=LONG, links=["https://www.example.org/jane"]),
            Sheet(picture, text=FOOTER),
            Sheet(picture, text=SHORT, box=(400, 700, 120, 80)),
        ]
    )


def sized(width: float, height: float) -> bytes:
    """One scanned page of that size, in points."""
    return scan_pdf([Sheet(jpeg_picture(), size=(width, height))])


def heavy_scans(pages: int = 6, side: tuple[int, int] = (4960, 7016)) -> bytes:
    """A4 pages scanned in grey at 600 dpi, each page its own picture: 17.5 MiB for 6."""
    one = grey_scan(*side)
    copies = [Picture(one.data, one.width, one.height, one.entries) for _ in range(pages)]
    return scan_pdf([Sheet(copy) for copy in copies])


def many_pictures(draws: int = 5000) -> bytes:
    """One page that draws the same picture thousands of times."""
    return scan_pdf([Sheet(jpeg_picture(), draws=draws)])


def with_broken_picture(filter_name: str) -> bytes:
    return scan_pdf([Sheet(broken_picture(filter_name))])


def inflating_picture(side: int = 12_000) -> bytes:
    """A page whose picture is a few kilobytes that inflate to `side` squared times 3 bytes."""
    return scan_pdf([Sheet(flate_picture(side, side))])
