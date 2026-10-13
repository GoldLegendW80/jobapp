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
3. Decoding. A JPEG is decoded at the smallest scale its DCT allows above the target. Only the
   first frame of an animated PNG or of a multi picture JPEG is read. A file cut short is
   `unreadable`: nothing is made up for the missing part.
4. Shrinking, in bands of at most BAND_PIXELS, so that the conversion never holds a second
   copy of a 25 megapixel image: each band becomes RGB (transparency over white, CMYK, 16 bit
   grey, palette) and is reduced by a whole factor; the result is resized to LONG_SIDE on its
   longest side. Nothing is ever enlarged.
5. The EXIF orientation is applied to the pixels, then the image is rebuilt from its pixels
   alone: no EXIF (so no GPS position), no ICC profile, no comment, no thumbnail reach the
   answer. Colours are not converted through the profile: the page is read for its text.
6. `encode`: a baseline JPEG of at most JPEG_MAX bytes: quality 80, 70, 60, 50, then the
   size reduced by 15 percent a step until it fits.

What comes back otherwise, `{"ok": false, "failure": "file", "code": ...}`:

    unreadable       neither a PNG nor a JPEG, damaged, cut short, or anything Pillow raised
    too_many_pixels  the header guard
    bomb             the memory of the child was not enough
    too_slow         the time budget was spent

Nothing is written anywhere: Pillow decodes from memory and encodes to memory. Nothing else
leaves: no sentence, no excerpt of an error.

Only main.py may import this file in production (and pdf_render.py, for `encode` and
`shrink`). The tests import it too, to call `handle` in their own process on files they make.
"""

import base64
import io
import struct

from PIL import Image, ImageFile, PngImagePlugin

PIXELS_MAX = 25_000_000  # width times height, as the header declares them
SIDE_MAX = 20_000  # one side; Pillow keeps a pointer per row, and a row of every band
LONG_SIDE = 1568  # the longest side of what is stored (the model's client resizes above it)
JPEG_MAX = 300_000  # bytes of one stored image: what one tool result carries (plan, G0)
QUALITIES = (80, 70, 60, 50)
SHRINK = 0.85  # then the size, a step at a time, at the last quality
BAND_PIXELS = 2_000_000  # what is converted at once
SEGMENTS_MAX = 2_000  # segments of a JPEG looked at before its size is found

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


def _orientation(opened: Image.Image) -> int | None:
    try:
        value = opened.getexif().get(_ORIENTATION)
    except MemoryError:
        raise
    except Exception:  # a broken EXIF block costs the orientation, never the image
        return None
    return value if isinstance(value, int) else None


def normalise(data: bytes, budget=None) -> Image.Image:
    """`data` as an upright RGB image, its longest side at LONG_SIDE or under. Raises
    Refused."""
    kind = sniff(data)
    if kind is None:
        raise Refused("unreadable")
    guard(*declared_size(data, kind))
    try:
        opened = Image.open(io.BytesIO(data), formats=[kind])
        guard(*opened.size)  # what the decoder will allocate
        if kind == "JPEG":
            opened.draft(None, target_size(*opened.size))
        opened.load()  # the first frame only: nothing here ever seeks another
        guard(*opened.size)
        orientation = _orientation(opened)
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
