"""Images, honest and hostile, built in code for the tests of task G8 (no binary file is
committed): tests/ingestion/parser/test_image.py and test_image_container.py, the worker's
tests/ingestion/test_jobs_images.py, and the scanned pages of tests/ingestion/scan_fixtures.py.

Every picture is synthetic: blocks, bars and noise, and made up words (`example.org`).
"""

import io
import random
import struct
import zlib
from collections.abc import Callable

from PIL import Image, ImageDraw

MIB = 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
GPS_MARK = b"GPS-MARK-48.8566N-2.3522E"  # stands in for a position, searched for in the output
COMMENT_MARK = b"comment written by a camera of example.org"


def page(width: int = 1200, height: int = 800, mode: str = "RGB") -> Image.Image:
    """Something like a screenshot of a CV: a white page, a dark title bar, grey lines of
    'text', and one red block in the top left corner that tells where the top is."""
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width // 4, height // 10), fill=(200, 0, 0))
    draw.rectangle((width // 3, height // 40, width - 20, height // 12), fill=(20, 20, 60))
    for line in range(8):
        top = height // 5 + line * height // 12
        right = max(60, width - 60 - width // 30 * line)
        draw.rectangle((40, top, right, top + height // 40), fill=(90, 90, 90))
    draw.text((50, height - 40), "Camille Durand, camille.durand@example.org", fill=(0, 0, 0))
    return image if mode == "RGB" else image.convert(mode)


def saved(image: Image.Image, kind: str, **options: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=kind, **options)
    return out.getvalue()


def screenshot_png(width: int = 1200, height: int = 800) -> bytes:
    return saved(page(width, height), "PNG")


def photo_jpeg(width: int = 1200, height: int = 800, **options: object) -> bytes:
    return saved(page(width, height), "JPEG", quality=90, **options)


def cmyk_jpeg() -> bytes:
    return saved(page(mode="CMYK"), "JPEG", quality=90)


def alpha_png() -> bytes:
    """Black 'text' on a fully transparent background, whose hidden colour is black: read
    without its alpha it would be a black page."""
    image = Image.new("RGBA", (600, 400), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((100, 100, 500, 140), fill=(0, 0, 0, 255))
    return saved(image, "PNG")


def palette_png() -> bytes:
    """A palette image whose index 0 is transparent."""
    image = Image.new("P", (600, 400), 0)
    image.putpalette([0, 0, 0, 255, 0, 0])
    ImageDraw.Draw(image).rectangle((100, 100, 500, 140), fill=1)
    return saved(image, "PNG", transparency=0)


def sixteen_bit_png() -> bytes:
    """16 bit grey: the left half at a quarter of the range, the right half at three quarters.
    A reader that clips instead of scaling gives white for both."""
    width, height = 400, 200
    row = b"".join(struct.pack(">H", 0x4000 if x < width // 2 else 0xC000) for x in range(width))
    return png_of(width, height, [row] * height, bit_depth=16, colour=0)


def exif_bytes(orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010E] = GPS_MARK.decode()  # ImageDescription: a text a camera could write
    exif[0x8825] = {1: "N", 2: (48.0, 51.0, 24.0), 3: "E", 4: (2.0, 21.0, 8.0)}  # the GPS IFD
    return exif.tobytes()


def rotated_jpeg(orientation: int = 6) -> bytes:
    """A photo taken with the phone turned: stored lying down, with the EXIF orientation that
    says how to stand it up, a position and a comment."""
    upright = page(800, 1200)
    # Orientation 6 means "turn the stored pixels 90 degrees clockwise to see it upright".
    stored = upright.transpose(Image.Transpose.ROTATE_90) if orientation == 6 else upright
    return saved(stored, "JPEG", quality=90, exif=exif_bytes(orientation), comment=COMMENT_MARK)


def animated_png() -> bytes:
    """Two frames: the page, then a black frame. Only the first may be read."""
    first = page(600, 400)
    return saved(
        first, "PNG", save_all=True, append_images=[Image.new("RGB", first.size)], duration=100
    )


def multi_picture_jpeg() -> bytes:
    """An MPO, as some cameras write: two JPEG pictures in one file."""
    first = page(600, 400)
    return saved(first, "MPO", save_all=True, append_images=[Image.new("RGB", first.size)])


def truncated(data: bytes, keep: float = 0.5) -> bytes:
    return data[: int(len(data) * keep)]


def noise_png(side: int = 1568) -> bytes:
    """Pure noise: no JPEG quality brings it under 300,000 bytes at this size."""
    pixels = random.Random(8).randbytes(side * side * 3)  # noqa: S311, a fixture
    return saved(Image.frombytes("RGB", (side, side), pixels), "PNG", compress_level=1)


def icc_jpeg(profile_bytes: int = 3 * MIB) -> bytes:
    """A photo carrying megabytes of 'colour profile'."""
    return photo_jpeg(icc_profile=random.Random(3).randbytes(profile_bytes))  # noqa: S311


def progressive_jpeg(width: int, height: int) -> bytes:
    """A progressive JPEG with no subsampling: the decoder keeps every coefficient of every
    component for the whole picture."""
    image = Image.effect_noise((width // 8, height // 8), 40).resize((width, height))
    return saved(image.convert("RGB"), "JPEG", quality=30, progressive=True, subsampling=0)


def _chunk(kind: bytes, content: bytes) -> bytes:
    return (
        struct.pack(">I", len(content))
        + kind
        + content
        + struct.pack(">I", zlib.crc32(kind + content))
    )


def png_of(
    width: int,
    height: int,
    rows: list[bytes] | None = None,
    *,
    bit_depth: int = 8,
    colour: int = 0,
    declared: tuple[int, int] | None = None,
    extra: bytes = b"",
) -> bytes:
    """A PNG written chunk by chunk. `rows` are the raw rows (all zero when None, which
    deflates to almost nothing: a 25 megapixel picture is a few kilobytes); `declared` is what
    the header says instead of the truth; `extra` goes between the header and the data."""
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    stride = width * channels * bit_depth // 8
    packer = zlib.compressobj(1)
    parts = []
    for index in range(height):
        row = rows[index] if rows is not None else bytes(stride)
        parts.append(packer.compress(b"\x00" + row))
    parts.append(packer.flush())
    header = struct.pack(">IIBBBBB", *(declared or (width, height)), bit_depth, colour, 0, 0, 0)
    return (
        PNG_MAGIC
        + _chunk(b"IHDR", header)
        + extra
        + _chunk(b"IDAT", b"".join(parts))
        + _chunk(b"IEND", b"")
    )


def huge_png(width: int = 30_000, height: int = 30_000) -> bytes:
    """A header that declares width by height, over a few bytes of data."""
    return png_of(4, 4, declared=(width, height))


def blank_png(width: int, height: int, colour: int = 0) -> bytes:
    """A real picture of that size, all zero (black, or fully transparent for colour 6)."""
    return png_of(width, height, colour=colour)


def jpeg_declaring(width: int, height: int) -> bytes:
    """A small progressive JPEG whose frame header is rewritten to declare another size."""
    data = bytearray(saved(page(64, 64), "JPEG", progressive=True))
    at = data.index(b"\xff\xc2")
    data[at + 5 : at + 9] = struct.pack(">HH", height, width)
    return bytes(data)


def gif() -> bytes:
    return saved(page(200, 100), "GIF")


def oriented(orientation: int, kind: str = "PNG") -> bytes:
    """A small picture with nothing symmetric in it, stored with this EXIF orientation."""
    return saved(page(300, 200), kind, exif=exif_bytes(orientation))


def exif_block(entries: list[tuple[int, int, int, int]], first: int = 8, size: int = 0) -> bytes:
    """An EXIF block written by hand, little endian: the byte order mark, the offset of the
    first directory, its entries (tag, type, count, value or offset), the offset of the next
    directory (the first again: a loop), then zeros up to `size` bytes."""
    block = b"II*\x00" + struct.pack("<I", first) + bytes(max(0, first - 8))
    block += struct.pack("<H", len(entries))
    block += b"".join(struct.pack("<HHII", *entry) for entry in entries)
    block += struct.pack("<I", first)
    return block + bytes(max(0, size - len(block)))


def with_exif(block: bytes, kind: str = "JPEG") -> bytes:
    """A 64 by 64 picture carrying this EXIF block: an APP1 segment, or an eXIf chunk."""
    data = saved(page(64, 64), kind)
    if kind == "JPEG":
        content = b"Exif\x00\x00" + block
        return data[:2] + b"\xff\xe1" + struct.pack(">H", len(content) + 2) + content + data[2:]
    return data[:33] + _chunk(b"eXIf", block) + data[33:]


def exif_bomb(kind: str = "JPEG") -> bytes:
    """The file of the review of PR #157 (B1): every tag of the directory is 60,000 bytes of
    type 7 at offset 0, the same bytes each time. 5,300 tags fill one JPEG segment and made
    Pillow's EXIF reader copy 318 MB out of a 64 KB file (304 MiB traced here before the fix).
    The PNG carries the same trick in an eXIf chunk, 60,000 tags over one megabyte: Pillow
    12.3 held 10 MiB for it, and it stays a row so that a later Pillow cannot change that
    unseen."""
    if kind == "JPEG":
        return with_exif(exif_block([(0x9000 + n, 7, 60_000, 0) for n in range(5300)]))
    tags = [(0x1000 + n, 7, MIB, 0) for n in range(60_000)]
    return with_exif(exif_block(tags, size=MIB), "PNG")


def exif_loop(kind: str = "JPEG") -> bytes:
    """An EXIF block that refers to itself: its EXIF, GPS and interoperability directories
    and its next directory are all the first one again."""
    loop = [(0x8769, 4, 1, 8), (0x8825, 4, 1, 8), (0xA005, 4, 1, 8)]
    return with_exif(exif_block(loop), kind)


def scans_of(data: bytes) -> list[bytes]:
    """The scans of a JPEG that Pillow wrote, each from its marker to the next one (no segment
    of these files holds the two bytes of the marker by chance; the fixtures below check)."""
    cuts = [at for at in range(len(data) - 1) if data[at : at + 2] == b"\xff\xda"]
    return [data[a:b] for a, b in zip(cuts, [*cuts[1:], len(data) - 2], strict=True)]


def many_scans_jpeg(scans: int, side: int = 64, between: bytes = b"") -> bytes:
    """A progressive JPEG of a flat grey square holding exactly `scans` scans: the ones the
    encoder wrote, then its last one (a refinement of 42 bytes at any size) again and again,
    `between` before each. The file of the review of PR #157 (B3) is 2,000 scans at 5,000 a
    side: 16 s of CPU in 182 KB, and nothing in the decoder to stop it."""
    data = saved(Image.new("L", (side, side), 128), "JPEG", progressive=True)
    written = scans_of(data)
    assert 1 < len(written) <= scans
    more = (between + written[-1]) * (scans - len(written))
    return data[:-2] + more + b"\xff\xd9"


def jpeg_with_markers_in_its_data(extra: bytes = b"") -> bytes:
    """An honest photo, one scan, in which the two bytes of the scan marker occur 600 times
    where they mark nothing: in an application segment (as in an EXIF thumbnail) and after the
    end of the picture (as in a second picture or a video a phone appends)."""
    data = photo_jpeg(200, 100)
    segment = b"\xff\xe5" + struct.pack(">H", 2 + 600) + b"\xff\xda" * 300
    return data[:2] + segment + data[2:-2] + extra + b"\xff\xd9" + b"\xff\xda\x00\x02" * 300


# Hostile files that are simply read: (name, the file). What made them hostile is metadata,
# and no metadata is parsed. Run by the handler in the test process and by the real child.
HOSTILE_READ: list[tuple[str, Callable[[], bytes]]] = [
    ("a JPEG whose 5,300 EXIF tags are the same 60,000 bytes", exif_bomb),
    ("a PNG whose 60,000 EXIF tags are the same megabyte", lambda: exif_bomb("PNG")),
    ("a JPEG whose EXIF directories are one another", exif_loop),
    ("a PNG whose EXIF directories are one another", lambda: exif_loop("PNG")),
]

# (name, the file, the code it must end as), run by the handler in the test process and by the
# real child in the container of the service.
HOSTILE: list[tuple[str, Callable[[], bytes], str]] = [
    ("a PNG of 30,000 by 30,000 pixels in a few kilobytes", huge_png, "too_many_pixels"),
    ("a PNG one row over 25 megapixels", lambda: blank_png(5000, 5001), "too_many_pixels"),
    ("a PNG 20,001 pixels long", lambda: huge_png(1, 20_001), "too_many_pixels"),
    (
        "a progressive JPEG that declares 65,000 a side",
        lambda: jpeg_declaring(65_000, 65_000),
        "too_many_pixels",
    ),
    (
        "a PNG whose header declares more rows than it holds",
        lambda: png_of(100, 100, declared=(2000, 2000)),
        "unreadable",
    ),
    ("a PNG whose header declares no pixel", lambda: png_of(4, 4, declared=(0, 4)), "unreadable"),
    ("a truncated JPEG", lambda: truncated(photo_jpeg()), "unreadable"),
    ("a truncated PNG", lambda: truncated(screenshot_png()), "unreadable"),
    ("a JPEG cut inside its header", lambda: photo_jpeg()[:12], "unreadable"),
    ("a GIF named like a PNG", gif, "unreadable"),
    ("a PNG signature and nothing else", lambda: PNG_MAGIC, "unreadable"),
    ("a progressive JPEG of 2,000 scans", lambda: many_scans_jpeg(2000, 5000), "bomb"),
    ("a JPEG of 65 scans", lambda: many_scans_jpeg(65), "bomb"),
    (
        "a JPEG whose scans are hidden behind 3,000 other markers",
        lambda: many_scans_jpeg(20, between=b"\xff\xfe\x00\x02" * 300),
        "bomb",
    ),
    (
        "a PNG with megabytes of inflated text chunks",
        lambda: png_of(
            4, 4, extra=_chunk(b"zTXt", b"k\x00\x00" + zlib.compress(bytes(16 * MIB))) * 3
        ),
        "unreadable",
    ),
]
