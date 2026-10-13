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


# --- the EXIF orientation, read by hand (review of PR #157, B1) ---------------------------------


@pytest.mark.parametrize("orientation", range(1, 9))
@pytest.mark.parametrize("kind", ["PNG", "JPEG"])
def test_each_exif_orientation_gives_the_pixels_pillow_gives(kind: str, orientation: int) -> None:
    from PIL import ImageOps

    data = fx.oriented(orientation, kind)
    assert image.orientation_of(data, kind) == orientation
    with Image.open(io.BytesIO(data)) as opened:
        expected = ImageOps.exif_transpose(opened).convert("RGB")
    upright = image.normalise(data)
    assert upright.size == expected.size == ((300, 200) if orientation < 5 else (200, 300))
    assert upright.tobytes() == expected.tobytes()


@pytest.mark.parametrize(
    "block",
    [
        b"",
        b"II*\x00",
        b"II*\x00\x08\x00\x00\x00",  # the header and no directory
        b"MM\x00*\x00\x00\x00\x08\xff\xff",  # 65,535 entries declared, none there
        fx.exif_block([(0x0112, 3, 1, 6)], first=4),  # a directory inside the header
        fx.exif_block([(0x0112, 3, 1, 6)], first=4000)[:64],  # a directory past the end
        fx.exif_block([(0x0112, 3, 1, 0)]),  # not one of the eight
        fx.exif_block([(0x0112, 3, 1, 9)]),
        fx.exif_block([(0x0112, 3, 2, 6)]),  # two values: an offset would have to be followed
        fx.exif_block([(0x0112, 7, 60_000, 0)]),  # the tag of the review, as bytes at offset 0
        fx.exif_block([(0x0112, 4, 1, 6)]),  # not the type the standard gives it
        fx.exif_block([(0x8769, 4, 1, 8), (0x8825, 4, 1, 8)]),  # only directories, all itself
        fx.exif_block([(0x9000 + n, 7, 60_000, 0) for n in range(256)] + [(0x0112, 3, 1, 6)]),
        b"ZZ*\x00" + fx.exif_block([(0x0112, 3, 1, 6)])[4:],
    ],
)
def test_an_odd_exif_block_declares_no_orientation(block: bytes) -> None:
    assert image.exif_orientation(block) is None


def test_the_orientation_is_found_in_either_byte_order_among_other_tags() -> None:
    little = fx.exif_block([(0x010F, 2, 60_000, 0), (0x0112, 3, 1, 8), (0x8769, 4, 1, 8)])
    assert image.exif_orientation(little) == 8
    big = b"MM\x00*\x00\x00\x00\x08\x00\x01" + bytes.fromhex("0112 0003 00000001 0003 0000")
    assert image.exif_orientation(big) == 3
    for kind in ("JPEG", "PNG"):
        assert image.orientation_of(fx.with_exif(little, kind), kind) == 8
        assert image.orientation_of(fx.saved(fx.page(64, 64), kind), kind) is None


@pytest.mark.parametrize(("name", "make"), fx.HOSTILE_READ, ids=[row[0] for row in fx.HOSTILE_READ])
def test_hostile_metadata_is_never_parsed_and_the_picture_is_read(name: str, make: Any) -> None:
    """Fails without the fix: Pillow's EXIF reader copied 318 MB for the first file."""
    import tracemalloc

    data = make()
    tracemalloc.start()
    try:
        answer = image.handle(data, {}, Plenty())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert answer["ok"] is True, answer
    assert (answer["images"][0]["width"], answer["images"][0]["height"]) == (64, 64)
    assert peak < 8 * 1024 * 1024, peak


def test_pillow_never_loads_a_directory_of_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """EXIF, the multi picture index and the profile of a JPEG, the eXIf chunk of a PNG: none
    is handed to the reader that loads every tag, neither on opening nor after."""
    from PIL import TiffImagePlugin

    loaded: list[int] = []
    load = TiffImagePlugin.ImageFileDirectory_v2.load

    def counted(self: Any, fp: Any) -> None:
        loaded.append(1)
        load(self, fp)

    monkeypatch.setattr(TiffImagePlugin.ImageFileDirectory_v2, "load", counted)
    with Image.open(io.BytesIO(fx.rotated_jpeg())) as opened:
        opened.getexif()
    assert loaded, "the counter sees what Pillow does on its own"
    del loaded[:]
    for data in (
        fx.rotated_jpeg(),
        fx.multi_picture_jpeg(),
        fx.icc_jpeg(),
        fx.oriented(6, "PNG"),
        fx.exif_bomb(),
    ):
        assert image.handle(data, {}, Plenty())["ok"] is True
    assert loaded == []
    # And Pillow is itself again outside: the table of its JPEG reader is as it was.
    with Image.open(io.BytesIO(fx.rotated_jpeg())) as opened:
        assert opened.getexif()[0x0112] == 6


# --- the scans of a JPEG are counted before it is decoded (review of PR #157, B3) ---------------


def test_an_honest_jpeg_has_few_scans_and_is_read() -> None:
    baseline, progressive = fx.photo_jpeg(), fx.progressive_jpeg(640, 480)
    assert image.jpeg_scans(baseline) == len(fx.scans_of(baseline)) == 1
    assert image.jpeg_scans(progressive) == len(fx.scans_of(progressive)) == 10
    assert image.jpeg_scans(fx.cmyk_jpeg()) == 1
    for data in (baseline, progressive):
        assert image.handle(data, {}, Plenty())["ok"] is True


def test_64_scans_are_read_and_65_are_refused() -> None:
    assert image.SCANS_MAX == 64
    most, one_more = fx.many_scans_jpeg(64), fx.many_scans_jpeg(65)
    assert (image.jpeg_scans(most), image.jpeg_scans(one_more)) == (64, 65)
    assert image.handle(most, {}, Plenty())["ok"] is True
    assert refusal(one_more) == "bomb"


def test_only_the_scans_of_the_picture_are_counted() -> None:
    """0xFF 0xDA in a segment or after the end of the picture is no scan, and neither is what
    the compressed data holds: a stuffed 0xFF followed by 0xDA, a restart marker."""
    data = fx.jpeg_with_markers_in_its_data()
    assert data.count(b"\xff\xda") == 601  # what counting the two bytes would say
    assert image.jpeg_scans(data) == 1
    assert image.handle(data, {}, Plenty())["ok"] is True
    stuffed = fx.jpeg_with_markers_in_its_data(
        b"\xff\x00\xda\xff\xd0\xff\x00\xda\xff\xd7\xff\xff\x00"
    )
    assert b"\xff\x00\xda" in stuffed
    assert image.jpeg_scans(stuffed) == 1


def test_bytes_between_two_scans_hide_none_of_them() -> None:
    """The decoder skips what stands where a marker is due and reads on: so does the count."""
    for between in (b"\x00\x01\x02", b"\xff\x00\xff\x00", b"\xff\xff\xff", b"\xff\xd3"):
        assert image.jpeg_scans(fx.many_scans_jpeg(70, between=between)) == 65, between
        assert refusal(fx.many_scans_jpeg(70, between=between)) == "bomb"
    assert image.jpeg_scans(fx.many_scans_jpeg(30, between=b"\x00\x01\x02")) == 30


def test_a_file_cut_anywhere_is_counted_without_an_error() -> None:
    data = fx.many_scans_jpeg(12)
    for keep in range(2, len(data), 7):
        assert 0 <= image.jpeg_scans(data[:keep]) <= 12


def test_the_many_scans_file_of_the_review_is_refused_before_any_decoding() -> None:
    import time

    data = fx.many_scans_jpeg(2000, 5000)
    assert len(data) < 200_000
    started = time.process_time()
    assert refusal(data) == "bomb"
    assert time.process_time() - started < 1  # 16 s of CPU when it was decoded
