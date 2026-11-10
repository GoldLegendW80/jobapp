"""A screenshot as an agent's model gets it (browser B23, issue 780): browserd's image of the
visible page, made a JPEG of at most SHOT_SIDE pixels on its longest side and SHOT_BYTES bytes,
so the tool answer that carries it stays far under the line bound of the Claude Agent SDK
(sdk_options.MAX_BUFFER_SIZE) and the model still reads the page. A PNG of a busy page reached
the SDK's default bound of 1 MiB as base64 and ended the search agent's run (CLIJSONDecodeError).
browser_take_screenshot of the search agent (finder.py) and of the apply agent (apply/applier.py)
answers it.
"""

import base64
import binascii
import io

from PIL import Image

# The longest side the model reads without the API scaling the image down first.
SHOT_SIDE = 1568
# About 533,000 characters as base64.
SHOT_BYTES = 400_000
# A decoded image larger than this is not read (the visible page is 1440 by 900), under the
# 25,000,000 pixels profile/photo.py sets as Pillow's own bound for the whole process.
SHOT_PIXELS = 20_000_000
QUALITIES = (80, 65, 50, 35)
SMALLEST_SIDE = 320
MIME = "image/jpeg"
# The tool's answer when for_model gives no image.
UNREAD = "The screenshot could not be read: read the page with browser_snapshot."


def for_model(data: str) -> str | None:
    """The base64 of a JPEG within SHOT_SIDE and SHOT_BYTES made of browserd's base64 image (PNG
    or JPEG): the full size first at falling qualities, then a quarter smaller each round. None
    when it is no image Pillow reads, is larger than SHOT_PIXELS, or fits at no side down to
    SMALLEST_SIDE."""
    try:
        raw = base64.b64decode(data, validate=True)
        with Image.open(io.BytesIO(raw)) as found:
            if found.width * found.height > SHOT_PIXELS:
                return None
            image = found.convert("RGB")
    except (
        binascii.Error,
        ValueError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return None
    scale = min(1.0, SHOT_SIDE / max(image.size))
    while True:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        frame = image if size == image.size else image.resize(size, Image.Resampling.LANCZOS)
        for quality in QUALITIES:
            out = io.BytesIO()
            frame.save(out, "JPEG", quality=quality, optimize=True)
            if out.tell() <= SHOT_BYTES:
                return base64.b64encode(out.getvalue()).decode("ascii")
        if max(size) <= SMALLEST_SIDE:
            return None
        scale *= 0.75
