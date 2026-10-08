"""photo.normalize on real images generated with Pillow and on hostile files (no database)."""

import io
import struct
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from PIL import Image

from app.profile import photo as photos
from app.profile.photo import (
    MAX_PIXELS,
    MAX_SIDE,
    STORED_MAX_BYTES,
    PhotoError,
    normalize,
)
from app.profile.schemas import PHOTO_MAX_BYTES
from tests.profile import images

RED = (220, 20, 20)
BLUE = (20, 20, 220)


def close(pixel: Any, colour: tuple[int, ...], tolerance: int = 40) -> bool:
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(pixel, colour, strict=False))


def refused(data: bytes) -> PhotoError:
    with pytest.raises(PhotoError) as caught:
        normalize(data)
    return caught.value


# --- accepted images ----------------------------------------------------------------------------


def test_plain_png_stays_png() -> None:
    photo = normalize(images.png(40, 30))
    image = images.opened(photo.data)
    assert (photo.mime, image.format, image.mode, image.size) == (
        "image/png",
        "PNG",
        "RGB",
        (40, 30),
    )
    assert (photo.width, photo.height) == (40, 30)
    assert close(image.getpixel((5, 5)), RED, 0)
    assert close(image.getpixel((35, 5)), BLUE, 0)
    assert images.metadata(photo.data) == {}


def test_plain_jpeg_stays_jpeg_progressive() -> None:
    photo = normalize(images.jpeg(40, 30))
    image = images.opened(photo.data)
    assert (photo.mime, image.format, image.mode, image.size) == (
        "image/jpeg",
        "JPEG",
        "RGB",
        (40, 30),
    )
    assert image.info.get("progressive") == 1
    assert close(image.getpixel((5, 5)), RED)
    assert images.metadata(photo.data) == {}


def test_png_alpha_is_kept_and_text_exif_icc_are_gone() -> None:
    source = images.png_with_text_and_alpha()
    found = images.metadata(source)
    assert {"icc_profile", "exif", "text"} <= set(found)
    photo = normalize(source)
    image = images.opened(photo.data)
    assert (image.format, image.mode, image.size) == ("PNG", "RGBA", (40, 30))
    assert image.getpixel((5, 5)) == (*RED, 255)
    assert image.getpixel((35, 5))[3] == 0  # still transparent
    assert images.metadata(photo.data) == {}
    for secret in (b"Ada Example", b"44.8378", b"tEXt", b"iTXt", b"eXIf", b"iCCP"):
        assert secret not in photo.data


def test_jpeg_exif_gps_and_orientation_6() -> None:
    """Stored sideways (40x30, red left half) with orientation 6: the stored photo is upright
    (30x40, red top half) and carries no EXIF, GPS, XMP, ICC or comment."""
    source = images.jpeg_with_gps(orientation=6)
    exif = images.opened(source).getexif()
    assert exif[images.ORIENTATION] == 6
    assert exif.get_ifd(images.GPS_IFD)[images.GPS_LATITUDE_REF] == "N"
    photo = normalize(source)
    image = images.opened(photo.data)
    assert image.size == (30, 40)
    assert close(image.getpixel((15, 5)), RED)
    assert close(image.getpixel((15, 35)), BLUE)
    assert images.metadata(photo.data) == {}
    assert len(image.getexif()) == 0
    assert image.getexif().get_ifd(images.GPS_IFD) == {}
    for secret in (b"Exif", b"GPS", b"SecretCam", b"Ada Example", b"Bordeaux", b"secret comment"):
        assert secret not in photo.data
    assert b"ICC_PROFILE" not in photo.data
    assert b"http://ns.adobe.com/xap" not in photo.data


@pytest.mark.parametrize(
    ("orientation", "size", "top_left"),
    [
        (1, (40, 30), RED),
        (2, (40, 30), BLUE),  # mirrored
        (3, (40, 30), BLUE),  # 180 degrees
        (4, (40, 30), RED),  # flipped vertically
        (5, (30, 40), RED),  # transposed
        (6, (30, 40), RED),
        (7, (30, 40), BLUE),
        (8, (30, 40), BLUE),
    ],
)
def test_every_exif_orientation(orientation: int, size: tuple[int, int], top_left: Any) -> None:
    image = images.opened(normalize(images.jpeg_with_gps(orientation=orientation)).data)
    assert image.size == size
    assert close(image.getpixel((2, 2)), top_left)


# Pillow only warns about a corrupt EXIF block, and the app keeps the default warning filters, so
# the photo is accepted; the test suite turns warnings into errors, hence the filter here.
@pytest.mark.filterwarnings("ignore:Corrupt EXIF data:UserWarning")
def test_broken_exif_block_is_ignored() -> None:
    source = images.save(images.halves(40, 30), "JPEG", exif=b"Exif\x00\x00MM\x00*garbage")
    photo = normalize(source)
    assert images.opened(photo.data).size == (40, 30)
    assert images.metadata(photo.data) == {}


def test_broken_icc_profile_is_ignored() -> None:
    source = images.save(images.halves(40, 30), "JPEG", icc_profile=b"not a profile" * 10)
    photo = normalize(source)
    assert images.metadata(photo.data) == {}
    assert close(images.opened(photo.data).getpixel((5, 5)), RED)


def test_cmyk_jpeg_becomes_rgb() -> None:
    source = images.cmyk_jpeg()
    assert images.opened(source).mode == "CMYK"
    image = images.opened(normalize(source).data)
    assert (image.format, image.mode, image.size) == ("JPEG", "RGB", (40, 30))
    assert close(image.getpixel((5, 5)), RED, 60)
    assert close(image.getpixel((35, 5)), BLUE, 60)


def test_greyscale_jpeg_becomes_rgb() -> None:
    image = images.opened(normalize(images.jpeg(40, 30, mode="L")).data)
    assert image.mode == "RGB"


def test_16_bit_greyscale_png_is_scaled_not_clipped() -> None:
    source = images.png16_grey(64, 64)
    assert images.opened(source).mode.startswith("I")
    image = images.opened(normalize(source).data)
    assert (image.format, image.mode, image.size) == ("PNG", "RGB", (64, 64))
    assert image.getpixel((0, 0))[0] <= 1
    assert 120 <= image.getpixel((32, 0))[0] <= 135
    assert image.getpixel((63, 0))[0] >= 254


def test_48_bit_rgb_png() -> None:
    image = images.opened(normalize(images.png48(20, 10)).data)
    assert (image.mode, image.size) == ("RGB", (20, 10))
    assert close(image.getpixel((3, 3)), (128, 128, 128), 1)


def test_palette_png_with_and_without_transparency() -> None:
    opaque = images.save(images.halves(16, 16).convert("P"), "PNG")
    assert images.opened(normalize(opaque).data).mode == "RGB"
    palette = images.halves(16, 16).convert("P")
    with_key = images.save(palette, "PNG", transparency=palette.getpixel((12, 4)))
    image = images.opened(normalize(with_key).data)
    assert image.mode == "RGBA"
    assert image.getpixel((12, 4))[3] == 0  # the right half is see-through
    assert image.getpixel((3, 4))[3] == 255


def test_grey_with_alpha_png_becomes_rgba() -> None:
    image = images.opened(normalize(images.png(10, 10, mode="LA")).data)
    assert image.mode == "RGBA"


def test_animated_png_keeps_the_first_frame_only() -> None:
    frames = [Image.new("RGB", (20, 20), RED), Image.new("RGB", (20, 20), BLUE)]
    out = io.BytesIO()
    frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:], duration=100)
    source = out.getvalue()
    assert getattr(images.opened(source), "n_frames", 1) == 2
    photo = normalize(source)
    assert b"acTL" not in photo.data
    image = images.opened(photo.data)
    assert getattr(image, "n_frames", 1) == 1
    assert image.getpixel((5, 5)) == RED


@pytest.mark.parametrize(
    ("size", "fmt", "expected"),
    [
        ((3000, 1500), "JPEG", (1024, 512)),
        ((1500, 3000), "PNG", (512, 1024)),
        ((1024, 1024), "PNG", (1024, 1024)),
        ((1025, 10), "PNG", (1024, 10)),
        ((5000, 2), "PNG", (1024, 1)),  # never rounded down to zero
        ((1, 1), "PNG", (1, 1)),
        ((1, 1), "JPEG", (1, 1)),
    ],
)
def test_longest_side_is_at_most_1024(
    size: tuple[int, int], fmt: str, expected: tuple[int, int]
) -> None:
    photo = normalize(images.save(images.halves(*size), fmt))
    assert images.opened(photo.data).size == expected
    assert (photo.width, photo.height) == expected
    assert max(expected) <= MAX_SIDE


def test_png_re_encoding_is_idempotent() -> None:
    once = normalize(images.png_with_text_and_alpha()).data
    assert normalize(once).data == once


def test_type_comes_from_the_bytes_only() -> None:
    """A PNG whose bytes say PNG is a PNG, whatever it was called; and PNG magic in front of a
    JPEG body is no PNG at all (only the PNG decoder is tried)."""
    assert normalize(images.png()).mime == "image/png"
    assert normalize(images.jpeg()).mime == "image/jpeg"
    hybrid = b"\x89PNG\r\n\x1a\n" + images.jpeg()
    assert refused(hybrid).status == 415


# --- size limits ----------------------------------------------------------------------------------


def padded_jpeg(total: int) -> bytes:
    """A real JPEG followed by filler up to `total` bytes (data after the end marker)."""
    jpeg = images.jpeg(64, 48)
    return jpeg + b"\x00" * (total - len(jpeg))


def test_exactly_two_megabytes_is_accepted_and_the_trailer_dropped() -> None:
    source = padded_jpeg(PHOTO_MAX_BYTES)
    assert len(source) == PHOTO_MAX_BYTES
    photo = normalize(source)
    assert images.opened(photo.data).size == (64, 48)
    assert len(photo.data) < 10_000


def test_one_byte_over_two_megabytes_is_413() -> None:
    error = refused(padded_jpeg(PHOTO_MAX_BYTES + 1))
    assert (error.status, error.message) == (413, photos.TOO_LARGE)
    assert error.message == "The image is larger than 2 MB."


def test_stored_photo_stays_under_a_megabyte_even_for_noise() -> None:
    """Random pixels are the worst case of both encoders; the result is shrunk until it fits."""
    for mode, mime in (("RGBA", "image/png"), ("RGB", "image/jpeg")):
        photo = photos._encode(images.noise(1024, 1024, mode), mime)
        assert len(photo.data) <= STORED_MAX_BYTES
        if mode == "RGBA":  # 4 MB of noise as PNG: shrunk until it fits
            assert max(photo.width, photo.height) < 1024
        assert images.opened(photo.data).size == (photo.width, photo.height)


def test_shrinking_loop_through_the_public_function(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(photos, "STORED_MAX_BYTES", 20_000)
    source = images.save(images.noise(400, 300), "PNG")
    photo = normalize(source)
    assert len(photo.data) <= 20_000
    assert photo.width < 400
    assert photo.width / photo.height == pytest.approx(4 / 3, rel=0.02)


# --- decompression bombs ----------------------------------------------------------------------


def test_pillow_limit_is_set() -> None:
    assert Image.MAX_IMAGE_PIXELS == MAX_PIXELS


def test_20000_by_20000_png_is_refused_without_decoding() -> None:
    source = images.bomb_png(20000)
    assert len(source) < PHOTO_MAX_BYTES  # small on the wire, 400 megapixels in memory
    started = time.monotonic()
    error = refused(source)
    assert (error.status, error.message) == (422, photos.TOO_MANY_PIXELS)
    assert time.monotonic() - started < 1  # refused from the header, never decoded


def test_between_the_limit_and_twice_the_limit_is_refused() -> None:
    error = refused(images.bomb_png(6000))  # 36 megapixels
    assert error.status == 422


def test_over_the_limit_with_production_warning_filters() -> None:
    """In production Pillow only warns between the limit and twice the limit (the test suite
    turns that warning into an error); the explicit pixel check refuses it all the same."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        error = refused(images.bomb_png(6000))
    assert (error.status, error.message) == (422, photos.TOO_MANY_PIXELS)


def test_zero_width_png_is_refused() -> None:
    assert refused(images.raw_png(0, 10, 8, 0, b"\x00")).status == 415


def test_failing_orientation_step_keeps_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(image: Image.Image) -> Image.Image:
        raise ValueError("bad EXIF")

    monkeypatch.setattr(photos.ImageOps, "exif_transpose", broken)
    photo = normalize(images.jpeg_with_gps(orientation=6))
    assert (photo.width, photo.height) == (40, 30)  # not turned, but stored and stripped
    assert images.metadata(photo.data) == {}


def test_exactly_the_pixel_limit_is_accepted() -> None:
    side = int(MAX_PIXELS**0.5)
    assert side * side == MAX_PIXELS
    photo = normalize(images.bomb_png(side))
    assert (photo.width, photo.height) == (MAX_SIDE, MAX_SIDE)


def jpeg_claiming(width: int, height: int) -> bytes:
    """A small real JPEG whose frame header (SOF) claims other dimensions."""
    data = bytearray(images.jpeg(16, 16))
    for marker in (b"\xff\xc0", b"\xff\xc2"):
        at = data.find(marker)
        if at != -1:
            data[at + 5 : at + 9] = struct.pack(">HH", height, width)
            return bytes(data)
    raise AssertionError("no SOF marker")


def test_jpeg_header_claiming_65000_by_65000_is_refused() -> None:
    error = refused(jpeg_claiming(65000, 65000))
    assert error.status == 422


def test_jpeg_header_claiming_a_larger_size_than_its_data() -> None:
    """libjpeg fills the missing blocks with grey rather than failing: harmless, since only the
    decoded pixels are kept, and the result is within the size bounds like any other photo."""
    photo = normalize(jpeg_claiming(3000, 3000))
    assert (photo.width, photo.height) == (MAX_SIDE, MAX_SIDE)
    assert images.metadata(photo.data) == {}


def test_decodes_are_limited_to_a_few_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    running = 0
    peak = 0
    lock = threading.Lock()
    real = photos._decode

    def counted(data: bytes, fmt: str) -> Image.Image:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            time.sleep(0.05)
            return real(data, fmt)
        finally:
            with lock:
                running -= 1

    monkeypatch.setattr(photos, "_decode", counted)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(normalize, [images.jpeg(200, 100)] * 8))
    assert all(r.width == 200 for r in results)
    assert peak == photos.DECODE_SLOTS


# --- hostile and wrong files --------------------------------------------------------------------

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect width="9" height="9"/></svg>'
)
HTML = b"<!doctype html><html><script>alert(document.cookie)</script></html>"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", photos.EMPTY),
        (images.gif(), photos.ONLY_PNG_JPEG),
        (images.webp(), photos.ONLY_PNG_JPEG),
        (SVG, photos.ONLY_PNG_JPEG),
        (b"\xef\xbb\xbf" + SVG, photos.ONLY_PNG_JPEG),
        (PDF, photos.ONLY_PNG_JPEG),
        (HTML, photos.ONLY_PNG_JPEG),
        (HEIC, photos.ONLY_PNG_JPEG),
        (images.save(images.halves(8, 8), "BMP"), photos.ONLY_PNG_JPEG),
        (images.save(images.halves(8, 8), "TIFF"), photos.ONLY_PNG_JPEG),
        (b" " + images.png(), photos.ONLY_PNG_JPEG),  # magic not at the start
        (b"\x89PNG\r\n\x1a\n", photos.UNREADABLE),  # magic only
        (b"\xff\xd8\xff", photos.UNREADABLE),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, photos.UNREADABLE),
        (b"\xff\xd8\xff\xe0" + HTML, photos.UNREADABLE),  # JPEG magic, then HTML
        (b"\x89PNG\r\n\x1a\n" + HTML, photos.UNREADABLE),
        (images.png(200, 200)[:-40], photos.UNREADABLE),  # truncated PNG (IDAT cut)
        (images.png(200, 200)[:60], photos.UNREADABLE),
        (images.jpeg(200, 200)[:-300], photos.UNREADABLE),  # truncated JPEG
        (images.jpeg(200, 200)[:200], photos.UNREADABLE),
    ],
    ids=[
        "empty", "gif", "webp", "svg", "svg-bom", "pdf", "html", "heic", "bmp", "tiff",
        "late-magic", "png-magic-only", "jpeg-magic-only", "png-zeros", "jpeg-then-html",
        "png-then-html", "png-truncated", "png-header-only", "jpeg-truncated", "jpeg-cut-early",
    ],
)  # fmt: skip
def test_refused_with_415(data: bytes, message: str) -> None:
    error = refused(data)
    assert (error.status, error.message) == (415, message)


def test_corrupted_png_crc_is_refused() -> None:
    data = bytearray(images.png(50, 50))
    at = data.find(b"IDAT")
    data[at + 10] ^= 0xFF  # flip bits inside the compressed pixels
    assert refused(bytes(data)).status == 415


def test_valid_jpeg_with_html_appended_is_stored_clean() -> None:
    """A polyglot that is a real JPEG and also carries HTML after its end marker: the picture is
    kept, the HTML is not (the stored file is re-encoded from pixels)."""
    source = images.jpeg(40, 30) + HTML
    photo = normalize(source)
    assert HTML not in photo.data
    assert b"<script>" not in photo.data
    assert photo.data.endswith(b"\xff\xd9")


def test_valid_png_with_html_in_a_text_chunk_and_a_zip_appended() -> None:
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", HTML.decode())
    source = images.save(images.halves(40, 30), "PNG", pnginfo=info) + b"PK\x03\x04" + b"\x00" * 30
    photo = normalize(source)
    assert b"<script>" not in photo.data
    assert b"PK\x03\x04" not in photo.data
    assert photo.data.endswith(b"IEND\xaeB`\x82")


def test_error_message_never_echoes_the_bytes() -> None:
    error = refused(b"\xff\xd8\xff\xe0" + HTML)
    assert "script" not in str(error)
    assert "alert" not in str(error)


def test_etag_is_the_quoted_sha256_of_the_bytes() -> None:
    import hashlib

    photo = normalize(images.png())
    assert photo.etag == f'"{hashlib.sha256(photo.data).hexdigest()}"'
    assert photos.etag(photo.data) == photo.etag
