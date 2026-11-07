"""The fonts of an upload chosen by measurement (documents D28, font_measure.py): the control of
D27 (words drawn in Raleway rank raleway first, Inter Tight's rank inter-tight first, with their
names hidden), a family of the library taken by its name, and the name alone without samples.
The samples are made here: invented words drawn with the bundled faces, as design_pdf crops
them (a grey PNG at four times the page image's scale, a point of margin)."""

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.documents import font_map, font_measure, templates
from app.ingestion.parser_client import DesignFont, DesignPage, DesignPdf, TextRun, WordSample

WORDS = (
    "Example", "Robotics", "engineer", "planning", "software", "delivered", "customers",
    "Analysed", "warehouse", "quarterly", "strategy", "Languages", "Experience", "Education",
)  # fmt: skip
SCALE = font_measure.CROP_LONG_SIDE / 842  # pixels per point on an A4 page


def _path(family: str, weight: int = 400) -> str:
    return next(
        str(face.path)
        for face in templates.fonts()[family].faces
        if face.weight == weight
        and face.style == "normal"
        and face.path.name == f"latin-{weight}-normal.woff2"
    )


def sample(family: str, word: str, size: float = 10.0, *, font: int = 0, weight: int = 400):
    """`word` as design_pdf gives it: its advances' width, and its crop drawn in `family`."""
    path = _path(family, weight)
    measure = ImageFont.truetype(path, 100, layout_engine=ImageFont.Layout.BASIC)
    width = sum(measure.getlength(ch) for ch in word) * size / 100
    pixels = round(size * SCALE)
    drawn = ImageFont.truetype(path, pixels, layout_engine=ImageFont.Layout.BASIC)
    pad = round(SCALE)
    canvas = Image.new("L", (round(drawn.getlength(word)) + 2 * pad, round(pixels * 1.3)), 247)
    ImageDraw.Draw(canvas).text((pad, round(pixels * 0.1)), word, font=drawn, fill=30)
    png = io.BytesIO()
    canvas.save(png, format="PNG")
    return WordSample(font, 0, word, round(size * 10), round(width * 10), png.getvalue())


def design(fonts: list[DesignFont], samples: list[WordSample]) -> DesignPdf:
    runs = tuple(
        TextRun("Alex Example worked here", (0, 0, 10, 10), i, 100, "#000000")
        for i in range(len(fonts))
    )
    page = DesignPage(0, 5950, 8420, (0, 0, 0, 0), runs, (), ())
    return DesignPdf(1, False, False, (), tuple(fonts), (page,), samples=tuple(samples))


def hidden(name: str = "ABCDEF+Font0", weight: int = 400) -> DesignFont:
    return DesignFont(name, weight, False, False, False, False, True)


@pytest.mark.parametrize("family", ["raleway", "inter-tight", "gantari", "montserrat"])
def test_words_drawn_in_a_family_rank_that_family_first_with_its_name_hidden(family) -> None:
    words = font_measure.words_of([sample(family, w) for w in WORDS], {0: SCALE})
    ranked = font_measure.rank(words, 400, False)
    assert ranked[0].family == family, [(s.family, round(s.total, 3)) for s in ranked[:4]]
    assert abs(ranked[0].width) < 0.01
    assert ranked[0].shape > 0.8
    assert ranked[0].total - ranked[1].total > 0.01


def test_the_measure_reaches_the_mapped_face_with_its_weight_and_a_note() -> None:
    found = font_measure.faces(
        design([hidden(weight=700)], [sample("inter-tight", w, weight=700) for w in WORDS])
    )
    assert (found[0].family, found[0].weight, found[0].italic) == ("inter-tight", 700, False)
    assert found[0].notes[0].startswith("measured: Inter Tight, score ")


def test_a_family_of_the_library_is_taken_by_its_name_even_when_measured_otherwise() -> None:
    # The words are Inter Tight's, the name says Raleway: the name of a bundled family wins.
    found = font_measure.faces(
        design([hidden("ABCDEF+Raleway-SemiBold")], [sample("inter-tight", w) for w in WORDS])
    )
    assert (found[0].family, found[0].weight) == ("raleway", 600)
    assert found[0].notes == ("exact: Raleway",)


def test_without_samples_a_font_is_mapped_by_its_name_as_before() -> None:
    found = font_measure.faces(design([hidden("ABCDEF+Calibri-Bold")], []))
    assert (found[0].family, found[0].weight) == ("carlito", 700)
    few = font_measure.faces(
        design([hidden()], [sample("raleway", w) for w in WORDS[: font_measure.SAMPLES_MIN - 1]])
    )
    assert few[0].family == "arimo"  # an unknown name, too few words: the default


def test_a_crop_that_does_not_read_is_left_out_alone() -> None:
    good = [sample("raleway", w) for w in WORDS[:5]]
    broken = WordSample(0, 0, "Broken", 100, 300, b"\x89PNG\r\n\x1a\nnot a png")
    blank = WordSample(0, 0, "Blank", 100, 300, _blank())
    assert len(font_measure.words_of([*good, broken, blank], {0: SCALE})) == 5


def _blank() -> bytes:
    png = io.BytesIO()
    Image.new("L", (40, 20), 255).save(png, format="PNG")
    return png.getvalue()


def test_the_crop_scale_is_the_sandboxs() -> None:
    from tests.ingestion.parser.helpers import as_in_the_child, sandbox_handler

    with as_in_the_child():
        design_pdf = sandbox_handler("design_pdf")
        long_side = design_pdf.pdf_render.LONG_SIDE
        assert design_pdf.SAMPLE_SCALE * long_side == font_measure.CROP_LONG_SIDE


@pytest.mark.parametrize("family", sorted(font_map.FAMILIES))
def test_each_family_is_measured_with_a_face_that_draws_latin_letters(family: str) -> None:
    """The review of D28: the face measured was the latin-ext file, which holds no basic letter,
    so every family drew the same empty box for each letter and the measure chose at random."""
    weight, italic = font_map.nearest_face(family, 400, False)
    path = font_measure._face_path(family, weight, italic)
    assert path is not None
    assert "latin-ext" not in path, path
    face = ImageFont.truetype(path, 100, layout_engine=ImageFont.Layout.BASIC)
    shapes = {bytes(face.getmask(letter)) for letter in "Iamo"}
    assert len(shapes) == 4, f"{family}: its letters are drawn alike"


def _striped(width: int, height: int) -> bytes:
    """A crop of one pixel stripes: as much ink as a crop can hold, in a few kilobytes."""
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 2):
        draw.line((x, 0, x, height), fill=0)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


# #757 round 1 (B5): crops from a sandbox the file may have taken over held a worker core for
# minutes (30 striped 2,400 px crops: 80 s of `rank`). By size, not by time: what is compared.
def test_the_pixels_compared_are_bounded_whatever_the_crops(monkeypatch) -> None:
    allowed = _striped(2_400, 166)  # sides and area within the client's caps
    square = _striped(2_400, 2_400)  # the area past them: never decoded
    assert 2_400 * 166 <= font_measure.CROP_AREA < 2_400 * 2_400
    # claimed sizes and texts as large as the client lets through: 1,440pt, 40 wide letters
    long = "W" * 40
    samples = [WordSample(0, 0, "Example", 300, 1_200, allowed)] * 15
    samples += [WordSample(0, 0, long, 14_400, 144_000, allowed)] * 15
    samples += [WordSample(0, 0, "Robotics", 300, 1_200, square)] * 30
    words = font_measure.words_of(samples, {0: SCALE})
    assert [word.text for word in words] == ["Example"] * 15 + [long] * 15
    for word in words:
        assert word.ink.width <= font_measure.INK_WIDTH
        assert word.ink.height <= font_measure.INK_HEIGHT
        assert word.scale < SCALE  # drawn at the bounded ink's height
    compared: list[tuple[tuple[int, int], tuple[int, int]]] = []
    real = font_measure._iou

    def iou(first, second):
        compared.append((first.size, second.size))
        return real(first, second)

    monkeypatch.setattr(font_measure, "_iou", iou)
    assert font_measure.rank(words, 400, False)
    assert len(compared) >= 30 * len(font_map.FAMILIES) // 2
    drawn = font_measure.DRAWN_MAX
    for (ink_width, ink_height), (drawn_width, drawn_height) in compared:
        assert ink_width <= font_measure.INK_WIDTH
        assert ink_height <= font_measure.INK_HEIGHT
        assert drawn_width <= font_measure.DRAWN_WIDTH + 2 * drawn
        assert drawn_height <= 3 * drawn


def test_a_small_crop_keeps_its_ink_and_its_scale() -> None:
    crop = sample("raleway", "Example")
    (word,) = font_measure.words_of([crop], {0: SCALE})
    assert word.ink.height <= font_measure.INK_HEIGHT
    assert word.scale == SCALE
