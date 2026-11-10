"""Browser B23 (issue 780): a screenshot as an agent's model gets it (app/browser/screenshot.py),
a JPEG of at most SHOT_SIDE pixels on its longest side and SHOT_BYTES bytes, made of browserd's
image whatever it holds: the hardest page to compress (every pixel random), a page of text with a
photo whose PNG was over the SDK's 1 MiB as base64, a window wider than the model reads, a
transparent PNG, a small element. What is no image, or larger than SHOT_PIXELS, gives none.
"""

import base64
import io
import random

import pytest
from PIL import Image, ImageDraw

from app.browser import screenshot

ONE_MIB = 1024 * 1024


def png(image: Image.Image) -> str:
    out = io.BytesIO()
    image.save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


def noise(width: int, height: int, seed: int = 780) -> Image.Image:
    pixels = random.Random(seed).randbytes(width * height * 3)  # noqa: S311  a fixed page
    return Image.frombytes("RGB", (width, height), pixels)


def page_with_a_photo() -> Image.Image:
    """A results page as a person sees it: lines of text, and a photo beside them."""
    page = Image.new("RGB", (1440, 900), "white")
    draw = ImageDraw.Draw(page)
    for y in range(0, 900, 18):
        draw.text((20, y), "Robotics Engineer, Example Robotics, Lyon, posted today " * 3, "black")
    page.paste(noise(600, 400), (800, 300))
    return page


def read(data: str) -> Image.Image:
    raw = base64.b64decode(data, validate=True)
    assert len(raw) <= screenshot.SHOT_BYTES
    image = Image.open(io.BytesIO(raw))
    assert image.format == "JPEG"
    assert max(image.size) <= screenshot.SHOT_SIDE
    return image


@pytest.mark.parametrize(
    ("image", "size"),
    [
        (noise(1440, 900), (1440, 900)),  # the browser's window, at its own size
        (page_with_a_photo(), (1440, 900)),
        (noise(3136, 1800), (1568, 900)),  # wider than the model reads: scaled down
        (noise(120, 80).convert("RGBA"), (120, 80)),  # a small element, transparent
    ],
)
def test_the_image_is_a_bounded_jpeg_the_model_reads(image: Image.Image, size: tuple) -> None:
    shot = screenshot.for_model(png(image))
    assert shot is not None
    assert read(shot).size == size


def test_a_page_over_the_sdk_default_comes_back_far_under_it() -> None:
    data = png(page_with_a_photo())
    assert len(data) > ONE_MIB  # what ended the run: a PNG over 1 MiB as base64
    shot = screenshot.for_model(data)
    assert shot is not None
    assert len(shot) < ONE_MIB // 2


def test_an_image_no_quality_fits_is_made_smaller_until_it_fits() -> None:
    image = noise(1568, 1568)  # random pixels, square: at the full size no quality fits
    out = io.BytesIO()
    image.save(out, "JPEG", quality=screenshot.QUALITIES[-1])
    assert out.tell() > screenshot.SHOT_BYTES
    shot = screenshot.for_model(png(image))
    assert shot is not None
    assert max(read(shot).size) < 1568


@pytest.mark.parametrize(
    "data",
    [
        "",
        "not base64 at all!",
        base64.b64encode(b"<html>a page, not an image</html>").decode(),
        png(Image.new("1", (5_500, 4_000))),  # 22 million pixels, over SHOT_PIXELS
    ],
    ids=["empty", "not_base64", "html", "too_many_pixels"],
)
def test_what_is_no_readable_image_gives_none(data: str) -> None:
    assert screenshot.for_model(data) is None
