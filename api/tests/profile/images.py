"""Real image files for the photo tests, generated with Pillow (nothing binary is checked in)."""

import io
import struct
import zlib
from typing import Any

from PIL import Image, ImageCms

# EXIF tags
ORIENTATION = 0x0112
ARTIST = 0x013B
SOFTWARE = 0x0131
GPS_IFD = 0x8825
GPS_LATITUDE_REF = 1
GPS_LATITUDE = 2


def save(image: Image.Image, fmt: str, **params: Any) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **params)
    return out.getvalue()


def png(width: int = 40, height: int = 30, mode: str = "RGB", **params: Any) -> bytes:
    """A PNG with a left half red, right half blue (so a rotation is visible)."""
    return save(halves(width, height, mode), "PNG", **params)


def jpeg(width: int = 40, height: int = 30, mode: str = "RGB", **params: Any) -> bytes:
    return save(halves(width, height, mode), "JPEG", quality=95, **params)


def halves(width: int, height: int, mode: str = "RGB") -> Image.Image:
    image = Image.new("RGB", (width, height), (220, 20, 20))
    image.paste((20, 20, 220), (width // 2, 0, width, height))
    return image.convert(mode)


def gps_exif(orientation: int | None = None) -> Image.Exif:
    """EXIF with a GPS position, an author and a camera software, and optionally an
    orientation."""
    exif = Image.Exif()
    exif[ARTIST] = "Ada Example"
    exif[SOFTWARE] = "SecretCam 1.0"
    if orientation is not None:
        exif[ORIENTATION] = orientation
    gps = exif.get_ifd(GPS_IFD)
    gps[GPS_LATITUDE_REF] = "N"
    gps[GPS_LATITUDE] = (44.0, 50.0, 15.5)
    return exif


def jpeg_with_gps(orientation: int = 6, width: int = 40, height: int = 30) -> bytes:
    """A JPEG stored sideways (40x30, left half red) with EXIF orientation 6 (rotate 90 degrees
    clockwise to display), GPS, an author, an XMP packet and a comment."""
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/'
        b'22-rdf-syntax-ns#"><rdf:Description photoshop:City="Bordeaux"/></rdf:RDF></x:xmpmeta>'
    )
    return save(
        halves(width, height),
        "JPEG",
        quality=95,
        exif=gps_exif(orientation).tobytes(),
        xmp=xmp,
        comment=b"secret comment",
        icc_profile=srgb_icc(),
    )


def srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def png_with_text_and_alpha(width: int = 40, height: int = 30) -> bytes:
    """An RGBA PNG (transparent right half) with tEXt, iTXt, EXIF and an ICC profile."""
    from PIL import PngImagePlugin

    image = Image.new("RGBA", (width, height), (220, 20, 20, 255))
    image.paste((0, 0, 0, 0), (width // 2, 0, width, height))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Ada Example")
    info.add_itxt("Location", "44.8378 N, 0.5792 W")
    return save(image, "PNG", pnginfo=info, exif=gps_exif().tobytes(), icc_profile=srgb_icc())


def png16_grey(width: int = 64, height: int = 64) -> bytes:
    """A 16-bit greyscale PNG: a horizontal ramp from 0 to 65535."""
    image = Image.new("I;16", (width, height))
    image.putdata([x * 65535 // (width - 1) for _ in range(height) for x in range(width)])
    return save(image, "PNG")


def raw_png(width: int, height: int, bit_depth: int, colour_type: int, row: bytes) -> bytes:
    """A PNG written by hand (for what Pillow cannot write, such as 48-bit RGB or a huge
    canvas): every row is `row`, filter byte included."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, bit_depth, colour_type, 0, 0, 0)
    compressor = zlib.compressobj(9)
    idat = b"".join(compressor.compress(row) for _ in range(height)) + compressor.flush()
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )


def png48(width: int = 20, height: int = 10) -> bytes:
    """A 16-bit per channel RGB PNG, mid grey (0x8000 per channel)."""
    return raw_png(width, height, 16, 2, b"\x00" + b"\x80\x00" * 3 * width)


def bomb_png(side: int = 20000) -> bytes:
    """A side x side greyscale PNG: a few hundred kilobytes, 400 megapixels once decoded."""
    return raw_png(side, side, 8, 0, b"\x00" * (side + 1))


def cmyk_jpeg(width: int = 40, height: int = 30) -> bytes:
    return save(halves(width, height).convert("CMYK"), "JPEG", quality=95)


def noise(width: int, height: int, mode: str = "RGB", seed: int = 7) -> Image.Image:
    """Random pixels (deterministic): the worst case for any compressor."""
    import random

    generator = random.Random(seed)  # noqa: S311 (test pixels, not a secret)
    channels = len(mode)
    return Image.frombytes(mode, (width, height), generator.randbytes(width * height * channels))


def gif() -> bytes:
    return save(halves(8, 8).convert("P"), "GIF")


def webp() -> bytes:
    return save(halves(8, 8), "WEBP")


def opened(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def metadata(data: bytes) -> dict[str, Any]:
    """Everything an image file says besides its pixels, as Pillow reads it."""
    image = opened(data)
    found: dict[str, Any] = {
        key: value
        for key, value in image.info.items()
        # Structural entries of the format, not metadata about the photo or its author.
        if key
        not in {"jfif", "jfif_version", "jfif_unit", "jfif_density", "progressive", "progression"}
    }
    exif = image.getexif()
    if len(exif):
        found["exif"] = dict(exif)
    if getattr(image, "text", None):
        found["text"] = dict(image.text)
    return found
