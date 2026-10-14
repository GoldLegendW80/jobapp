"""The photo of a document (task D4a of the documents plan; spec, "Rendering on the Jetson").

`embed(data) -> str` turns the profile's photo into the `data:` address a template prints, so the
renderer never fetches it:

1. The profile's own pipeline first (`app.profile.photo.normalize`): the size cap, the type read
   from the bytes (PNG or JPEG only, so anything that is not an image is refused), the pixel cap
   against decompression bombs, a full decode, the orientation applied, colours in sRGB.
2. Then at most MAX_SIDE pixels on the long side (the spec's 600), re-encoded from a fresh image
   that holds pixels only: no EXIF (GPS, camera, author), no XMP, no ICC, no text chunks.
3. JPEG at quality 85, lowered until the file is under MAX_BYTES; a PNG with transparency stays
   PNG unless it is too large, then it is flattened on white and written as JPEG.

A refused photo raises `app.profile.photo.PhotoError` (its status and its sentence for the user);
the caller renders the document without the photo, since a photo never blocks a render.
"""

import base64
import io

from PIL import Image

from app.profile import photo as profile_photo
from app.profile.photo import PhotoError

MAX_SIDE = 600  # pixels on the long side, the spec's figure
MAX_BYTES = 300 * 1024  # the encoded photo, before base64
JPEG_QUALITIES = (85, 75, 65, 55, 45)

__all__ = ["MAX_BYTES", "MAX_SIDE", "PhotoError", "embed"]


def _pixels_only(image: Image.Image) -> Image.Image:
    """A new image with the same pixels and nothing else (no info, no EXIF, no ICC)."""
    return Image.frombytes(image.mode, image.size, image.tobytes())


def _jpeg(image: Image.Image) -> bytes:
    if image.mode != "RGB":
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.getchannel("A") if "A" in image.mode else None)
        image = flat
    for quality in JPEG_QUALITIES:
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality, optimize=True)
        if out.tell() <= MAX_BYTES:
            break
    return out.getvalue()


def embed(data: bytes) -> str:
    """The `data:` address of a photo, at most MAX_SIDE pixels on its long side and MAX_BYTES
    encoded, with no metadata. Raises PhotoError for a file that is not a PNG or JPEG image, a
    damaged one, or one over the profile's size or pixel caps."""
    stored = profile_photo.normalize(data)
    fmt = "PNG" if stored.mime == "image/png" else "JPEG"
    with Image.open(io.BytesIO(stored.data), formats=[fmt]) as source:
        source.load()
        image = _pixels_only(source)
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    image = _pixels_only(image)
    if fmt == "PNG":
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        encoded, mime = out.getvalue(), "image/png"
        if len(encoded) > MAX_BYTES:
            encoded, mime = _jpeg(image), "image/jpeg"
    else:
        encoded, mime = _jpeg(image.convert("RGB")), "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(encoded).decode('ascii')}"
