"""The image reader, sandbox/image.py, called in the test process on pictures made here
(tests/ingestion/image_fixtures.py): what an ordinary picture becomes, each rule of the
normalisation, and each hostile file ending as a code of the closed list, never as an exception.

Every answer is also passed through the worker's own reading of it (main.encode, then
parser_client.loads and check_parsed): what the handler writes is what the client accepts.
The same files against the real child, under its limits and its filter, in the container of the
service: test_image_container.py.
"""

import io
from typing import Any

import pytest
from PIL import Image

from app.ingestion import parser_client as pc
from tests.ingestion import image_fixtures as fx
from tests.ingestion.parser.helpers import Plenty, SpentAfter, sandbox_handler, sandbox_module

main = sandbox_module("main")
image = sandbox_handler("image")


def read(data: bytes, budget: Any = None) -> pc.Parsed:
    """The picture as the worker gets it: the handler's answer through the client's checks."""
    answer = image.handle(data, {}, budget or Plenty())
    return pc.check_parsed(pc.loads(main.encode(answer)), "image")


def picture(data: bytes) -> tuple[pc.PageImage, Image.Image]:
    parsed = read(data)
    assert (parsed.text, parsed.pages, parsed.pages_text, parsed.pages_image) == ("", 1, 0, 1)
    assert not parsed.truncated
    assert parsed.notes == ()
    (stored,) = parsed.images
    assert stored.position == 0
    assert len(stored.jpeg) <= image.JPEG_MAX
    decoded = Image.open(io.BytesIO(stored.jpeg))
    decoded.load()
    assert decoded.format == "JPEG"
    assert decoded.mode == "RGB"
    assert decoded.size == (stored.width, stored.height)
    assert max(decoded.size) <= image.LONG_SIDE
    return stored, decoded


def refusal(data: bytes, budget: Any = None) -> str:
    with pytest.raises(pc.ParserRefusedError) as caught:
        read(data, budget)
    assert caught.value.failure == "file"
    return caught.value.code


def segments(jpeg: bytes) -> list[int]:
    """The marker codes of a JPEG, up to its scan."""
    found, at = [], 2
    while at < len(jpeg):
        assert jpeg[at] == 0xFF
        code = jpeg[at + 1]
        found.append(code)
        if code == 0xDA:
            break
        at += 2 + int.from_bytes(jpeg[at + 2 : at + 4], "big")
    return found


def near(pixel: tuple[int, ...], colour: tuple[int, int, int], by: int = 40) -> bool:
    return all(abs(a - b) <= by for a, b in zip(pixel, colour, strict=True))


def test_the_limits_are_the_ones_of_the_plan() -> None:
    assert image.PIXELS_MAX == 25_000_000
    assert image.LONG_SIDE == 1568
    assert image.JPEG_MAX == 300_000 == pc.MAX_IMAGE_BYTES
    assert image.QUALITIES == (80, 70, 60, 50)
    assert image.LONG_SIDE <= pc.MAX_IMAGE_SIDE
    assert image.PNG_MAGIC == fx.PNG_MAGIC
    assert image.JPEG_MAGIC == pc.JPEG_MAGIC


def test_a_screenshot_png_becomes_one_jpeg_at_position_0() -> None:
    stored, decoded = picture(fx.screenshot_png())
    assert (stored.width, stored.height) == (1200, 800)  # never enlarged
    assert near(decoded.getpixel((20, 20)), (200, 0, 0))  # the red block, top left
    assert near(decoded.getpixel((1190, 790)), (255, 255, 255))


def test_a_large_screenshot_is_brought_to_1568_on_its_longest_side() -> None:
    stored, decoded = picture(fx.screenshot_png(4000, 2500))
    assert (stored.width, stored.height) == (1568, 980)
    assert near(decoded.getpixel((30, 30)), (200, 0, 0))
    tall, _ = picture(fx.photo_jpeg(1000, 6000))
    assert (tall.width, tall.height) == (261, 1568)


def test_a_cmyk_jpeg_is_read_as_rgb() -> None:
    _, decoded = picture(fx.cmyk_jpeg())
    assert near(decoded.getpixel((20, 20)), (200, 0, 0), by=60)
    assert near(decoded.getpixel((1190, 790)), (255, 255, 255))


def test_a_png_with_alpha_is_laid_over_white() -> None:
    _, decoded = picture(fx.alpha_png())
    assert near(decoded.getpixel((10, 10)), (255, 255, 255))  # transparent: white, not black
    assert near(decoded.getpixel((300, 120)), (0, 0, 0))


def test_a_palette_png_with_a_transparent_colour_is_laid_over_white() -> None:
    _, decoded = picture(fx.palette_png())
    assert near(decoded.getpixel((10, 10)), (255, 255, 255))
    assert near(decoded.getpixel((300, 120)), (255, 0, 0))


def test_a_16_bit_png_is_scaled_not_clipped() -> None:
    _, decoded = picture(fx.sixteen_bit_png())
    assert near(decoded.getpixel((100, 100)), (64, 64, 64), by=8)
    assert near(decoded.getpixel((300, 100)), (192, 192, 192), by=8)


def test_exif_rotation_is_applied_and_no_metadata_is_kept() -> None:
    data = fx.rotated_jpeg(6)
    assert fx.GPS_MARK in data
    assert fx.COMMENT_MARK in data
    stored, decoded = picture(data)
    assert (stored.width, stored.height) == (800, 1200)  # upright again
    assert near(decoded.getpixel((20, 20)), (200, 0, 0))  # the red block is back top left
    # No EXIF (APP1), no profile (APP2), no comment (COM): the position never leaves.
    assert not {0xE1, 0xE2, 0xFE} & set(segments(stored.jpeg))
    assert fx.GPS_MARK not in stored.jpeg
    assert fx.COMMENT_MARK not in stored.jpeg
    assert not decoded.getexif()
    assert "icc_profile" not in decoded.info
    assert "comment" not in decoded.info


def test_a_broken_exif_block_costs_the_orientation_only() -> None:
    data = fx.rotated_jpeg(6).replace(b"Exif\x00\x00MM", b"Exif\x00\x00ZZ")
    stored, _ = picture(data)
    assert (stored.width, stored.height) == (1200, 800)  # as stored, not turned


def test_an_animated_png_gives_its_first_frame() -> None:
    _, decoded = picture(fx.animated_png())
    assert near(decoded.getpixel((10, 10)), (200, 0, 0))  # the page, not the black frame


def test_a_multi_picture_jpeg_gives_its_first_picture() -> None:
    _, decoded = picture(fx.multi_picture_jpeg())
    assert near(decoded.getpixel((10, 10)), (200, 0, 0))


def test_a_truncated_jpeg_is_unreadable() -> None:
    assert refusal(fx.truncated(fx.photo_jpeg())) == "unreadable"
    assert refusal(fx.truncated(fx.photo_jpeg(), 0.98)) == "unreadable"


def test_a_25_megapixel_image_is_read_and_one_pixel_more_is_refused() -> None:
    stored, _ = picture(fx.blank_png(5000, 5000))
    assert (stored.width, stored.height) == (1568, 1568)
    assert refusal(fx.blank_png(5000, 5001)) == "too_many_pixels"
    # The rule itself, to the pixel.
    image.guard(12_500, 2_000)
    with pytest.raises(image.Refused) as caught:
        image.guard(25_000_001, 1)
    assert caught.value.code == "too_many_pixels"
    assert image.declared_size(fx.blank_png(5000, 5001), "PNG") == (5000, 5001)
    assert image.declared_size(fx.photo_jpeg(1200, 800), "JPEG") == (1200, 800)


def test_a_png_whose_header_lies_about_its_size_costs_what_it_says_at_most() -> None:
    # More than it holds: refused before any decoding, by the header alone.
    assert refusal(fx.huge_png()) == "too_many_pixels"
    # More rows than it holds, inside the guard: the data ends early.
    assert refusal(fx.png_of(100, 100, declared=(2000, 2000))) == "unreadable"
    # Fewer than it holds: the picture has the declared size, whatever it shows.
    stored, _ = picture(fx.png_of(100, 100, declared=(50, 50)))
    assert (stored.width, stored.height) == (50, 50)


def test_an_image_that_stays_over_300_000_bytes_at_quality_50_is_made_smaller() -> None:
    data = fx.noise_png()
    opened = image.shrink(Image.open(io.BytesIO(data)))
    assert all(len(image._jpeg(opened, quality)) > image.JPEG_MAX for quality in image.QUALITIES)
    stored, _ = picture(data)
    assert len(stored.jpeg) <= image.JPEG_MAX
    assert stored.width == stored.height < 1568


def test_megabytes_of_colour_profile_are_dropped() -> None:
    data = fx.icc_jpeg()
    assert len(data) > 3 * fx.MIB
    stored, decoded = picture(data)
    assert len(stored.jpeg) < 150_000
    assert "icc_profile" not in decoded.info


def test_the_format_is_the_one_the_bytes_say() -> None:
    assert image.sniff(fx.screenshot_png()) == "PNG"
    assert image.sniff(fx.photo_jpeg()) == "JPEG"
    assert image.sniff(fx.gif()) is None
    assert refusal(fx.gif()) == "unreadable"
    assert refusal(b"%PDF-1.7\n") == "unreadable"
    assert refusal(b"") == "unreadable"


@pytest.mark.parametrize(("name", "make", "code"), fx.HOSTILE, ids=[row[0] for row in fx.HOSTILE])
def test_a_hostile_image_ends_as_a_code(name: str, make: Any, code: str) -> None:
    assert code in pc.FILE_CODES
    assert refusal(make()) == code


def test_a_memory_error_anywhere_ends_as_the_code_of_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_memory(*_: object, **__: object) -> None:
        raise MemoryError

    for name in ("shrink", "encode", "declared_size"):
        with monkeypatch.context() as patch:
            patch.setattr(image, name, no_memory)
            assert refusal(fx.screenshot_png()) == "bomb"
    with monkeypatch.context() as patch:
        patch.setattr(image.Image, "open", no_memory)
        assert refusal(fx.screenshot_png()) == "bomb"


def test_the_time_budget_is_asked_between_two_bands() -> None:
    assert refusal(fx.blank_png(3000, 3000), SpentAfter(2)) == "too_slow"


def test_nothing_is_ever_enlarged_and_one_pixel_is_a_picture() -> None:
    stored, _ = picture(fx.blank_png(1, 1))
    assert (stored.width, stored.height) == (1, 1)
    wide, _ = picture(fx.blank_png(20_000, 4))
    assert (wide.width, wide.height) == (1568, 1)
