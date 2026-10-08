"""The profile photo pipeline: every image that reaches the database goes through `normalize`.

Uploads (PUT /api/profile/photo), imports (POST /api/profile/import, the CLI) and the importer of
Harald's engine files all call it, and the service only stores a `Photo`, which nothing else
builds, so an image as received is never stored.

Steps, in order:
1. Size: empty or over PHOTO_MAX_BYTES is refused before anything is parsed.
2. Type by magic bytes only (`sniff_photo`): PNG or JPEG, never a file name or a declared type.
   Pillow is then opened with that one format allowed, so no other decoder (GIF, WebP, TIFF,
   EPS, ...) is ever reached, whatever the bytes claim later on.
3. Decompression bomb guard: the pixel count read from the header must stay under MAX_PIXELS
   (Pillow's own `Image.MAX_IMAGE_PIXELS` is set to the same value as a second guard), and at
   most DECODE_SLOTS images are decoded at once, so concurrent uploads cannot add up past the
   api container's memory. A JPEG is decoded straight at a reduced scale (libjpeg DCT scaling).
4. Full decode: a truncated or damaged file fails here (Pillow never fills a truncated image).
5. Orientation from EXIF applied to the pixels, colours converted to sRGB when the file carries
   an ICC profile, then: PNG keeps its alpha channel when it has one (RGBA) else RGB, 16-bit
   greyscale is scaled (not clipped) to 8 bits, JPEG is RGB (CMYK and greyscale converted).
6. Downscale so the longest side is at most MAX_SIDE, then re-encode from a fresh image that
   holds pixels only: no EXIF (GPS, camera, author), no XMP, no ICC, no text chunks, no
   comments, nothing appended after the image. PNG stays PNG (optimised), JPEG stays JPEG
   (quality 85, progressive, optimised). A result over STORED_MAX_BYTES (pure noise, never a
   real photo) is shrunk until it fits, so an exported profile always fits the import limit.

Errors are `PhotoError` with an HTTP status and one sentence for the user, never the bytes.
"""

import io
import threading
from dataclasses import dataclass
from hashlib import sha256

from PIL import Image, ImageCms, ImageOps

from app.profile.schemas import PHOTO_MAX_BYTES, sniff_photo

MAX_SIDE = 1024  # longest side of a stored photo, in pixels
MAX_PIXELS = 25_000_000  # width x height of an accepted upload (a 24 MP camera photo fits)
STORED_MAX_BYTES = 1024 * 1024  # a re-encoded photo is at most this, base64 included in an export
DECODE_SLOTS = 2  # images decoded at the same time in one process
JPEG_QUALITY = 85

# Pillow warns above this many pixels and refuses above twice as many, at open time.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG"}
_decoding = threading.BoundedSemaphore(DECODE_SLOTS)

ONLY_PNG_JPEG = "Only PNG and JPEG images are accepted."
EMPTY = "The file is empty."
UNREADABLE = "The image could not be read: the file is damaged, truncated or not a real image."
TOO_MANY_PIXELS = (
    f"The image is too large: at most {MAX_PIXELS // 1_000_000} megapixels are accepted."
)
TOO_LARGE = f"The image is larger than {PHOTO_MAX_BYTES // (1024 * 1024)} MB."


class PhotoError(ValueError):
    """An image refused, with the HTTP status of the upload route and a sentence for the user."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Photo:
    """A re-encoded photo, safe to store and serve."""

    data: bytes
    mime: str
    width: int
    height: int

    @property
    def etag(self) -> str:
        return etag(self.data)


def etag(data: bytes) -> str:
    """The strong ETag of stored photo bytes: their SHA-256, quoted."""
    return f'"{sha256(data).hexdigest()}"'


def normalize(data: bytes) -> Photo:
    """The stored form of an uploaded image (steps in the module docstring).

    Raises PhotoError: 413 over PHOTO_MAX_BYTES, 415 for anything that is not a complete PNG or
    JPEG (empty, another type, a polyglot that does not decode, a truncated file), 422 for an
    image over MAX_PIXELS.
    """
    if not data:
        raise PhotoError(415, EMPTY)
    if len(data) > PHOTO_MAX_BYTES:
        raise PhotoError(413, TOO_LARGE)
    mime = sniff_photo(data)
    if mime is None:
        raise PhotoError(415, ONLY_PNG_JPEG)
    with _decoding:
        image = _decode(data, _FORMATS[mime])
        return _encode(image, mime)


def _decode(data: bytes, fmt: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data), formats=[fmt]) as source:
            if source.width * source.height > MAX_PIXELS:
                raise PhotoError(422, TOO_MANY_PIXELS)
            if fmt == "JPEG":
                # Decode at the smallest DCT scale still at least MAX_SIDE on each side.
                source.draft(None, (MAX_SIDE, MAX_SIDE))
            source.load()
            image = _upright(source)
            image = _to_srgb(image, source.info.get("icc_profile"))
            return _to_output_mode(image, fmt, _has_alpha(source))
    except PhotoError:  # the pixel limit above
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise PhotoError(422, TOO_MANY_PIXELS) from None
    except Exception:  # hostile input can raise anything from inside a decoder
        raise PhotoError(415, UNREADABLE) from None


def _upright(image: Image.Image) -> Image.Image:
    """The pixels turned as the EXIF orientation says; a broken EXIF block is ignored."""
    try:
        turned = ImageOps.exif_transpose(image)
    except Exception:
        return image
    return turned if turned is not None else image


def _to_srgb(image: Image.Image, icc: bytes | None) -> Image.Image:
    """Colours converted from the embedded ICC profile to sRGB (a Display P3 phone photo, a CMYK
    print file), since the profile itself is not kept. An unusable profile is ignored."""
    if not icc or image.mode not in ("RGB", "RGBA", "CMYK"):
        return image
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        target = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        output = "RGBA" if image.mode == "RGBA" else "RGB"
        converted = ImageCms.profileToProfile(image, source, target, outputMode=output)
    except Exception:
        return image
    return converted if converted is not None else image


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA", "La", "RGBa") or "transparency" in image.info


def _to_output_mode(image: Image.Image, fmt: str, alpha: bool) -> Image.Image:
    if image.mode.startswith("I"):
        # 16-bit (or 32-bit) greyscale: scale to 8 bits; a plain convert("L") would clip.
        image = image.convert("I").point(lambda value: value * (1 / 256)).convert("L")
    return image.convert("RGBA" if fmt == "PNG" and alpha else "RGB")


def _encode(image: Image.Image, mime: str) -> Photo:
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    while True:
        data = _save(image, mime)
        if len(data) <= STORED_MAX_BYTES or max(image.size) <= 64:
            return Photo(data=data, mime=mime, width=image.width, height=image.height)
        size = (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4))
        image = image.resize(size, Image.Resampling.LANCZOS)


def _save(image: Image.Image, mime: str) -> bytes:
    # A fresh image from the raw pixels: no info, EXIF, ICC or text can be carried over.
    clean = Image.frombytes(image.mode, image.size, image.tobytes())
    out = io.BytesIO()
    if mime == "image/png":
        clean.save(out, format="PNG", optimize=True)
    else:
        clean.save(out, format="JPEG", quality=JPEG_QUALITY, progressive=True, optimize=True)
    return out.getvalue()
