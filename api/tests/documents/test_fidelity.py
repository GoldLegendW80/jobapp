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


def test_the_overlay_is_a_jpeg_of_the_originals_size() -> None:
    jpeg = fidelity.overlay(page(), page(5))
    with Image.open(io.BytesIO(jpeg)) as image:
        assert (image.format, image.size) == ("JPEG", (600, 850))
