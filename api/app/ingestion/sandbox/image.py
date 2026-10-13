"""An uploaded PNG or JPEG as one normalised page image (task G8 of the ingestion plan). Runs
only inside the locked down child of the parser service: main.py imports this file after the
lockdown and calls `handle`. The bytes are hostile, and so is everything Pillow makes of them.

    handle(data, options, budget) -> the whole answer, as app/ingestion/parser_client.py reads it

What comes back for an image that was read:

    {"ok": true, "kind": "image", "text": "", "pages": 1, "pages_text": 0, "pages_image": 1,
     "truncated": false, "notes": [],
     "images": [{"position": 0, "width": W, "height": H, "jpeg": base64}]}

The same shape as a PDF of one scanned page (pdf_render.py): one empty page slot that waits for
its transcription, and the image of that page at position 0.

What is done, in this order:

1. The format is the one the first bytes say (PNG or JPEG), whatever the name or the declared
   type said, and Pillow is given that one decoder and no other.
2. The header guard, read from the bytes before Pillow sees them: the width and the height the
   header declares. More than PIXELS_MAX pixels, or a side over SIDE_MAX, is `too_many_pixels`
   and nothing is decoded: a PNG of a few kilobytes may declare 30,000 by 30,000 pixels. A
   decoder allocates what the header declares, never what the data holds, so a header that
   lies costs what it says at most; what Pillow reads of the size is checked the same way.
3. The scans of a JPEG are counted from its bytes (`jpeg_scans`), and more than SCANS_MAX is
   a `bomb`: a progressive file is read scan after scan, each one a pass over every block of
   the picture, and nothing in the decoder bounds how many a file holds. 2,000 scans of 42
   bytes each took 16 s of CPU at 25 megapixels (review of PR #157); an encoder writes ten.
   Decoding. A JPEG is decoded at the smallest scale its DCT allows above the target. Only the
   first frame of an animated PNG or of a multi picture JPEG is read. A file cut short is
   `unreadable`: nothing is made up for the missing part.
4. Shrinking, in bands of at most BAND_PIXELS, so that the conversion never holds a second
   copy of a 25 megapixel image: each band becomes RGB (transparency over white, CMYK, 16 bit
   grey, palette) and is reduced by a whole factor; the result is resized to LONG_SIDE on its
   longest side. Nothing is ever enlarged.
5. The EXIF orientation is applied to the pixels, then the image is rebuilt from its pixels
   alone: no EXIF (so no GPS position), no ICC profile, no comment, no thumbnail reach the
   answer. Colours are not converted through the profile: the page is read for its text.
   Pillow never parses a metadata block. Its EXIF reader loads every tag of a directory at
   once, and each tag may point at the same bytes: 64 KB of file cost 356 MiB (review of
   PR #157). So the orientation is read here by hand (`exif_orientation`: one bounded walk of
   the first directory, nothing followed), and while a JPEG is opened Pillow skips its APP1
   (EXIF, XMP), APP2 (profile, multi picture index) and APP13 segments unread (`_unread`).
6. `encode`: a baseline JPEG of at most JPEG_MAX bytes: quality 80, 70, 60, 50, then the
   size reduced by 15 percent a step until it fits.

What comes back otherwise, `{"ok": false, "failure": "file", "code": ...}`:

    unreadable       neither a PNG nor a JPEG, damaged, cut short, or anything Pillow raised
    too_many_pixels  the header guard
    bomb             a JPEG of more than SCANS_MAX scans, or the memory of the child was not
                     enough
    too_slow         the time budget was spent

Nothing is written anywhere: Pillow decodes from memory and encodes to memory. Nothing else
leaves: no sentence, no excerpt of an error.

Only main.py may import this file in production (and pdf_render.py, for `encode` and
`shrink`). The tests import it too, to call `handle` in their own process on files they make.
"""

import base64
import contextlib
import io
import re
import struct

from PIL import Image, ImageFile, JpegImagePlugin, PngImagePlugin

PIXELS_MAX = 25_000_000  # width times height, as the header declares them
SIDE_MAX = 20_000  # one side; Pillow keeps a pointer per row, and a row of every band
LONG_SIDE = 1568  # the longest side of what is stored (the model's client resizes above it)
JPEG_MAX = 300_000  # bytes of one stored image: what one tool result carries (plan, G0)
QUALITIES = (80, 70, 60, 50)
SHRINK = 0.85  # then the size, a step at a time, at the last quality
BAND_PIXELS = 2_000_000  # what is converted at once
SEGMENTS_MAX = 2_000  # segments of a JPEG looked at before its size is found
SCANS_MAX = 64  # scans of one JPEG: a progressive file from an encoder holds about ten
CHUNKS_MAX = 4_096  # chunks of a PNG looked at for its EXIF block
EXIF_MAX = 65_536  # bytes of an EXIF block looked at: a JPEG segment holds no more
EXIF_ENTRIES_MAX = 256  # entries of its first directory looked at; a camera writes a dozen

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
_ALPHA_MODES = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})
_ORIENTATION = 0x0112
# The EXIF orientation, as the transposition that undoes it.
_TRANSPOSE = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}

# Pillow's own guards, set to this module's: it refuses above twice its limit, the header guard
# below refuses first. A file cut short is an error, never a grey area filled in. The text
# chunks of a PNG are inflated when it is opened: 8 MiB of them in all is far more than any
# screenshot carries.
Image.MAX_IMAGE_PIXELS = PIXELS_MAX
ImageFile.LOAD_TRUNCATED_IMAGES = False
PngImagePlugin.MAX_TEXT_MEMORY = 8 * 1024 * 1024


class Refused(Exception):  # noqa: N818, an outcome, not an error of this program
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def failure(code: str) -> dict:
    return {"ok": False, "failure": "file", "code": code}


def sniff(data: bytes) -> str | None:
    """ "PNG" or "JPEG" by the first bytes, None for anything else."""
    if data.startswith(PNG_MAGIC):
        return "PNG"
    if data.startswith(JPEG_MAGIC):
        return "JPEG"
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    # The signature, then the first chunk, which must be IHDR: length 13, width, height.
    if len(data) < 33 or data[8:16] != b"\x00\x00\x00\rIHDR":
        raise Refused("unreadable")
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data: bytes) -> tuple[int, int]:
    # Segment by segment up to the first frame header (SOF): a marker, a length, the content.
    at, end = 2, len(data)
    for _ in range(SEGMENTS_MAX):
        if at < end and data[at] != 0xFF:  # not a marker: damaged
            break
        while at < end and data[at] == 0xFF:  # fill bytes before the code
            at += 1
        if at >= end:
            break
        code = data[at]
        at += 1
        if code == 0x01 or 0xD0 <= code <= 0xD8:  # markers that stand alone
            continue
        if code in (0xD9, 0xDA) or at + 2 > end:  # the end, or the scan before any frame
            break
        (length,) = struct.unpack(">H", data[at : at + 2])
        if length < 2:
            break
        if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
            if length < 7 or at + 7 > end:
                break
            height, width = struct.unpack(">HH", data[at + 3 : at + 7])
            return width, height
        at += length
    raise Refused("unreadable")


# The next marker as the decoder looks for it: 0xFF, then a code. Not 0xFF 0x00 (a 0xFF of the
# compressed data, which is always followed by that zero), not a restart marker (they sit
# inside a scan, thousands of them in some files), not a fill byte.
_MARKER = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")


def jpeg_scans(data: bytes) -> int:
    """How many scans the first picture of a JPEG holds, counted up to SCANS_MAX + 1, without
    any decoder. Marker after marker to the end of the picture: a segment is stepped over by
    its length (an EXIF thumbnail has scans of its own, which are not this picture's), the
    compressed data of a scan by the search for the next marker, and so is anything that
    stands where a marker is due, as the decoder itself resynchronises: bytes put between two
    scans hide none of them. What follows the end of the picture (a second picture, a video)
    is not looked at, since no decoder is given it. Raises Refused for a file with more
    markers than SEGMENTS_MAX, whose scans cannot be counted within the bound."""
    at, end, scans = 2, len(data), 0
    for _ in range(SEGMENTS_MAX):
        found = _MARKER.search(data, at)
        if found is None:
            return scans
        code, at = data[found.start() + 1], found.end()
        if code == 0xD9:  # the end of the picture
            return scans
        if code in (0x01, 0xD8):  # markers that stand alone
            continue
        if at + 2 > end:
            return scans
        (length,) = struct.unpack(">H", data[at : at + 2])
        if length < 2:  # the decoder stops there too
            return scans
        at += length
        if code == 0xDA:
            scans += 1
            if scans > SCANS_MAX:
                return scans
    raise Refused("bomb")


def declared_size(data: bytes, kind: str) -> tuple[int, int]:
    """(width, height) as the header declares them, read without any decoder."""
    return _png_size(data) if kind == "PNG" else _jpeg_size(data)


def guard(width: int, height: int) -> None:
    """The header guard. Raises Refused."""
    if width < 1 or height < 1:
        raise Refused("unreadable")
    if width > SIDE_MAX or height > SIDE_MAX or width * height > PIXELS_MAX:
        raise Refused("too_many_pixels")


def target_size(width: int, height: int, long_side: int = LONG_SIDE) -> tuple[int, int]:
    """The size at which `long_side` holds the longest side, never larger than the original."""
    longest = max(width, height)
    if longest <= long_side:
        return width, height
    return (
        max(1, round(width * long_side / longest)),
        max(1, round(height * long_side / longest)),
    )


def _rgb(band: Image.Image, alpha: bool) -> Image.Image:
    """One band as plain RGB: transparency over white, everything else through Pillow."""
    if alpha:
        band = band.convert("RGBA")
        white = Image.new("RGB", band.size, (255, 255, 255))
        white.paste(band, mask=band.getchannel("A"))
        return white
    if band.mode.startswith("I"):
        # 16 bit grey: scaled to 8 bits first. Pillow's own conversion clips at 255, which
        # turns every sample above a 256th of the range white.
        band = band.point(lambda value: value * (1 / 256)).convert("L")
    return band if band.mode == "RGB" else band.convert("RGB")


def shrink(image: Image.Image, budget=None) -> Image.Image:
    """A new RGB image with its longest side at LONG_SIDE or under, holding nothing of
    `image` but its pixels (no `info`, so no profile and no comment)."""
    width, height = image.size
    alpha = image.mode in _ALPHA_MODES or "transparency" in image.info
    factor = max(1, max(width, height) // LONG_SIDE)
    rows = max(factor, BAND_PIXELS // width // factor * factor)
    reduced = Image.new("RGB", (-(-width // factor), -(-height // factor)))
    for top in range(0, height, rows):
        if budget is not None and budget.spent():
            raise Refused("too_slow")
        band = _rgb(image.crop((0, top, width, min(height, top + rows))), alpha)
        if factor > 1:
            band = band.reduce(factor)
        reduced.paste(band, (0, top // factor))
    size = target_size(*reduced.size)
    if size != reduced.size:
        reduced = reduced.resize(size, Image.Resampling.LANCZOS)
    return reduced


def _jpeg(image: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    # Only what is named here is written: Pillow takes the profile, the EXIF and the density
    # from the arguments of `save`, never from the image.
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def encode(image: Image.Image) -> tuple[bytes, int, int]:
    """(jpeg, width, height) of an RGB image that `shrink` made: at most JPEG_MAX bytes."""
    for quality in QUALITIES:
        data = _jpeg(image, quality)
        if len(data) <= JPEG_MAX:
            return data, image.width, image.height
    while True:
        size = (max(1, int(image.width * SHRINK)), max(1, int(image.height * SHRINK)))
        if size == image.size:  # one pixel, and still too heavy: no such JPEG exists
            raise Refused("unreadable")
        image = image.resize(size, Image.Resampling.LANCZOS)
        data = _jpeg(image, QUALITIES[-1])
        if len(data) <= JPEG_MAX:
            return data, image.width, image.height


def entry(position: int, image: Image.Image) -> dict:
    """One image of the answer."""
    data, width, height = encode(image)
    return {
        "position": position,
        "width": width,
        "height": height,
        "jpeg": base64.b64encode(data).decode("ascii"),
    }


def _jpeg_exif(data: bytes) -> bytes:
    # The content of the first APP1 segment that says "Exif", before the first scan.
    at, end = 2, len(data)
    for _ in range(SEGMENTS_MAX):
        if at + 4 > end or data[at] != 0xFF:
            break
        code = data[at + 1]
        if code == 0xFF:  # a fill byte
            at += 1
            continue
        at += 2
        if code == 0x01 or 0xD0 <= code <= 0xD8:
            continue
        if code in (0xD9, 0xDA):
            break
        (length,) = struct.unpack(">H", data[at : at + 2])
        if length < 2:
            break
        if code == 0xE1 and data[at + 2 : at + 8] == b"Exif\x00\x00":
            return data[at + 8 : min(at + length, end)]
        at += length
    return b""


def _png_exif(data: bytes) -> bytes:
    # The content of the first eXIf chunk: a length, a name, the content, a checksum.
    at, end = len(PNG_MAGIC), len(data)
    for _ in range(CHUNKS_MAX):
        if at + 8 > end:
            break
        length, name = struct.unpack(">I4s", data[at : at + 8])
        if name == b"eXIf":
            return data[at + 8 : min(at + 8 + length, at + 8 + EXIF_MAX, end)]
        if name == b"IEND":
            break
        at += 12 + length
    return b""


def exif_orientation(block: bytes) -> int | None:
    """The orientation (1 to 8) an EXIF block declares, None when it declares none or is odd
    in any way. `block` starts at the byte order mark. Only the entries of the first directory
    are looked at, EXIF_ENTRIES_MAX of them at most, twelve bytes each: no offset is followed
    (the value of this tag lies in its entry), no other directory is opened, nothing is
    copied. So the cost is the same whatever the block claims."""
    if block[:4] == b"II*\x00":
        order = "<"
    elif block[:4] == b"MM\x00*":
        order = ">"
    else:
        return None
    if len(block) < 8:
        return None
    (at,) = struct.unpack(order + "I", block[4:8])
    if at < 8 or at + 2 > len(block):
        return None
    (entries,) = struct.unpack(order + "H", block[at : at + 2])
    at += 2
    for _ in range(min(entries, EXIF_ENTRIES_MAX)):
        if at + 12 > len(block):
            break
        tag, kind, count, value = struct.unpack(order + "HHIH", block[at : at + 10])
        if tag == _ORIENTATION:
            # One unsigned short, as the standard says, and one of the eight it names.
            return value if kind == 3 and count == 1 and 1 <= value <= 8 else None
        at += 12
    return None


def orientation_of(data: bytes, kind: str) -> int | None:
    """The EXIF orientation of a PNG or a JPEG, read from its bytes without any decoder."""
    return exif_orientation(_jpeg_exif(data) if kind == "JPEG" else _png_exif(data))


# The segments of a JPEG Pillow would read into `info` and parse: APP1 (EXIF: it asks the
# directory for the resolution when the file names none, and loads every tag for it; XMP),
# APP2 (the colour profile, and the multi picture index, a second directory read the same
# way), APP13 (Photoshop's blocks). APP0 and APP14 stay: the second says how four components
# are to be read.
_UNREAD = (0xFFE1, 0xFFE2, 0xFFED)


@contextlib.contextmanager
def _unread():
    """While a JPEG is opened, Pillow skips the segments of _UNREAD without looking inside."""
    kept = {marker: JpegImagePlugin.MARKER[marker] for marker in _UNREAD}
    for marker, (name, description, _) in kept.items():
        JpegImagePlugin.MARKER[marker] = (name, description, JpegImagePlugin.Skip)
    try:
        yield
    finally:
        JpegImagePlugin.MARKER.update(kept)


def normalise(data: bytes, budget=None) -> Image.Image:
    """`data` as an upright RGB image, its longest side at LONG_SIDE or under. Raises
    Refused."""
    kind = sniff(data)
    if kind is None:
        raise Refused("unreadable")
    guard(*declared_size(data, kind))
    if kind == "JPEG" and jpeg_scans(data) > SCANS_MAX:
        raise Refused("bomb")
    orientation = orientation_of(data, kind)
    try:
        with _unread():
            opened = Image.open(io.BytesIO(data), formats=[kind])
        guard(*opened.size)  # what the decoder will allocate
        if kind == "JPEG":
            opened.draft(None, target_size(*opened.size))
        opened.load()  # the first frame only: nothing here ever seeks another
        guard(*opened.size)
        image = shrink(opened, budget)
        opened.close()
    except (Refused, MemoryError):
        raise
    except Exception:  # whatever Pillow raised: DecompressionBombError, OSError, SyntaxError...
        raise Refused("unreadable") from None
    transposition = _TRANSPOSE.get(orientation)
    return image.transpose(transposition) if transposition is not None else image


def handle(data: bytes, options: dict, budget) -> dict:
    """Read `data` as an image. `options` is not used: this reader takes none."""
    try:
        image = normalise(data, budget)
        images = [entry(0, image)]
    except Refused as refused:
        return failure(refused.code)
    except MemoryError:
        return failure("bomb")
    return {
        "ok": True,
        "kind": "image",
        "text": "",
        "pages": 1,
        "pages_text": 0,
        "pages_image": 1,
        "truncated": False,
        "notes": [],
        "images": images,
    }
