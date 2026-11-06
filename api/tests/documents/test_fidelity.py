"""The fidelity measure of a template written from an upload (documents D28, fidelity.py): a
template against its own render scores at the top with offsets near 0, a shifted copy reports
its shift in a sentence, the lines of our own PDF are read by PDFium. Pages and lines are made
here; the texts are invented."""

import io

import pytest
from PIL import Image, ImageDraw

from app.documents import fidelity
from tests.ingestion import design_fixtures as fx

LINES = [
    fidelity.Line("ALEX EXAMPLE", 56.0, 70.0, 20.0),
    fidelity.Line("Lead engineer at Example Robotics", 56.0, 92.0, 11.0),
    fidelity.Line("EXPERIENCE", 56.0, 140.0, 12.0),
    fidelity.Line("Example Robotics, built the planner", 56.0, 158.0, 10.0),
    fidelity.Line("2021 to 2024", 460.0, 158.0, 9.0),
]


def page(shift: int = 0) -> bytes:
    image = Image.new("RGB", (600, 850), (253, 251, 247))
    draw = ImageDraw.Draw(image)
    for line in LINES:
        draw.rectangle((line.x, line.y - line.size + shift, line.x + 8 * len(line.text) // 2,
                        line.y + shift), fill=(30, 30, 30))  # fmt: skip
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def shifted(dx: float, dy: float, dsize: float = 0.0) -> list[fidelity.Line]:
    return [fidelity.Line(l.text, l.x + dx, l.y + dy, l.size + dsize) for l in LINES]  # noqa: E741


def test_a_template_on_its_own_render_scores_at_the_top_with_no_offset() -> None:
    found = fidelity.measure_lines(LINES, LINES, fidelity.page_score(page(), page()))
    assert found.page == pytest.approx(1.0)
    assert (found.lines, found.matched, found.in_place) == (5, 5, 5)
    assert found.score == pytest.approx(1.0)
    assert found.max_dx == found.max_dy == 0
    assert found.sentences() == []


def test_a_shifted_copy_says_its_shift_in_plain_words() -> None:
    found = fidelity.measure_lines(LINES, shifted(18.9, 4.8), fidelity.page_score(page(), page(5)))
    assert found.page < 0.9
    assert found.in_place == 0
    assert found.matched == 5
    assert found.mean_dy == pytest.approx(4.8)
    assert found.sentences()[0] == (
        'The line "ALEX EXAMPLE" is 4.8pt lower and 18.9pt further right than in the CV.'
    )
    smaller = fidelity.measure_lines(LINES, shifted(0, -1.0, -0.5), 1.0)
    assert smaller.in_place == 5
    assert "1.0pt higher and 0.5pt smaller" in smaller.sentences()[0]
    assert found.score < smaller.score


def test_a_line_is_matched_by_its_text_once_and_not_at_all_below_the_ratio() -> None:
    candidate = [LINES[1], LINES[0], fidelity.Line("Something else entirely", 0, 0, 10)]
    pairs = fidelity.match(LINES, candidate)
    assert [(a.text, b.text) for a, b in pairs] == [(LINES[0].text, LINES[0].text),
                                                    (LINES[1].text, LINES[1].text)]  # fmt: skip


def test_the_lines_of_a_pdf_are_read_with_their_baselines_and_sizes() -> None:
    lines = fidelity.candidate_lines(fx.measured())
    by_text = {line.text: line for line in lines}
    assert by_text["ALEX EXAMPLE"].y == pytest.approx(842 - 760, abs=0.2)
    assert by_text["ALEX EXAMPLE"].size == pytest.approx(20)
    assert by_text["Wrote their software"].x == pytest.approx(72, abs=0.2)
    first, second = by_text["Planned the robots of Example"], by_text["Wrote their software"]
    assert second.y - first.y == pytest.approx(14, abs=0.2)


# #757 round 1 (B3): Chromium sets its text through a 0.75 matrix; the font size alone read
# every rendered line 4/3 of its size, and those phantom sizes ranked first.
def test_the_size_of_a_line_is_read_through_its_text_matrix() -> None:
    by_text = {line.text: line for line in fidelity.candidate_lines(fx.scaled())}
    assert by_text["ALEX EXAMPLE"].size == pytest.approx(18, abs=0.05)
    assert by_text["EXPERIENCE"].size == pytest.approx(12, abs=0.05)
    assert by_text["Wrote their software"].size == pytest.approx(10, abs=0.05)
    assert by_text["ALEX EXAMPLE"].x == pytest.approx(60, abs=0.2)
    assert by_text["ALEX EXAMPLE"].y == pytest.approx(842 - 750, abs=0.2)
    assert fidelity.text_height(0.75, 0, 0, 0.75) == pytest.approx(0.75)
    assert fidelity.text_height(0, 2, -2, 0) == pytest.approx(2)  # turned a quarter
    assert fidelity.text_height(1, 0, 0.3, 1) == pytest.approx(1)  # slanted


@pytest.mark.parametrize("built", [fx.scaled, fx.measured], ids=["scaled", "identity"])
def test_a_pdf_measured_against_itself_has_no_offset_and_no_sentence(built) -> None:
    """The original's lines as design_capture gives them (design_pdf, then `_pdf_layout`)
    against the candidate's (candidate_lines) of the very same PDF."""
    from app.documents import design_capture
    from app.ingestion import parser_client as pc
    from tests.ingestion.parser.helpers import Plenty, as_in_the_child, sandbox_handler
    from tests.ingestion.parser.helpers import sandbox_module

    data = built()
    with as_in_the_child():
        main, design_pdf = sandbox_module("main"), sandbox_handler("design_pdf")
        read = pc.check_design_pdf(pc.loads(main.encode(design_pdf.handle(data, {}, Plenty()))))
    original = fidelity.original_lines(design_capture._pdf_layout(read))
    candidate = fidelity.candidate_lines(data)
    assert len(original) >= 3
    found = fidelity.measure_lines(original, candidate, 1.0)
    assert found.matched == found.lines == len(original)
    assert found.in_place == found.lines
    assert found.max_dx == found.max_dy == 0
    assert all(abs(a.size - b.size) < 0.25 for a, b in fidelity.match(original, candidate))
    assert found.sentences() == []


def test_the_overlay_is_a_jpeg_of_the_originals_size() -> None:
    jpeg = fidelity.overlay(page(), page(5))
    with Image.open(io.BytesIO(jpeg)) as image:
        assert (image.format, image.size) == ("JPEG", (600, 850))


# #757 round 1 (follow up): a US Letter original is no longer stretched onto the A4 candidate.
def _sheet(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((56, 60, 300, 80), fill=(20, 20, 20))  # a name, 1px a point
    draw.rectangle((56, 700, 500, 712), fill=(20, 20, 20))  # a line low on both pages
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_a_us_letter_original_is_letterboxed_not_stretched() -> None:
    letter, a4 = _sheet(612, 792), _sheet(595, 842)  # both at one pixel a point
    assert fidelity.candidate_width(612, 612.0) == 595
    assert fidelity.candidate_width(612, None) == 612
    assert fidelity.candidate_width(1_568, 1.0) == 2 * 1_568  # a tiny page claimed: bounded
    assert fidelity.page_score(letter, a4) == pytest.approx(1.0)
    image = Image.open(io.BytesIO(fidelity.overlay(letter, a4)))
    assert image.size == (612, 792)
