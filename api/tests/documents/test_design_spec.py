"""The measured spec the design writer reads (documents D28, design_spec.py): styles clustered
with their guessed roles, the original's headings as the model read them (scrubbed), and the
shapes of each page in points. The layout is invented, made here."""

from app.documents import design_spec, font_map
from app.ingestion.parser_client import (
    DesignFont,
    DesignPage,
    DesignPdf,
    Dot,
    Shape,
    TextRun,
)

FONTS = (
    DesignFont("ABCDEF+Raleway-Bold", 700, False, False, False, False, True),
    DesignFont("ABCDEF+Raleway-Regular", 400, False, False, False, False, True),
)
FACES = [font_map.Mapped("raleway", 700, False), font_map.Mapped("raleway", 400, False)]


def run(text, y, size, font=1, caps=False, spacing=0, colour="#1a1917", x=567, pitch=0):
    return TextRun(
        text, (x, y - size, x + 3000, y + 30), font, size, colour, y, spacing, caps, pitch
    )


RUNS = (
    run("ALEX EXAMPLE", 900, 260, font=0, caps=True, spacing=150),
    run("Robotics engineer", 1250, 120),
    run("EXPERIENCE", 2000, 110, font=0, caps=True, spacing=100),
    run("Example Robotics, Lead engineer", 2200, 100, font=0),
    *(
        run(f"Built the planner of the fleet, part {n}", 2400 + 140 * n, 100, pitch=140)
        for n in range(6)
    ),
    run("EDUCATION", 3500, 110, font=0, caps=True, spacing=100),
    run("Example University", 3700, 100, font=0),
    run("2016 to 2020", 3850, 80, colour="#6b6b6b"),
    run("zq7w4k9secret", 4000, 110, font=0, caps=True, spacing=100),
)
BAND = Shape("fill", (0, 0, 5953, 1500), "#262525", 0, 80)
RULE = Shape("rule", (567, 1900, 5386, 1915), "#a6a6a6", 8)
DOT = Dot((500, 2380, 530, 2410), "#1f4e79")
PAGE = DesignPage(
    0, 5953, 8419, (567, 567, 567, 567), RUNS, (BAND, RULE), ((4500, 300, 5400, 1200),),
    "#f8f8f8", (DOT,), (True,),
)  # fmt: skip
DESIGN = DesignPdf(1, False, False, (), FONTS, (PAGE,))


def texts(redacted: str = "zq7w4k9secret") -> list[list[str]]:
    return [["[redacted]" if r.text == redacted else r.text for r in RUNS]]


def test_styles_are_clustered_with_their_measures_and_a_guessed_role():
    found = design_spec.measured(DESIGN, FACES, texts())
    roles = {(s["size_pt"], s["caps"], s["weight"]): s["role"] for s in found["styles"]}
    assert roles[(26.0, True, 700)] == "name"
    assert roles[(12.0, False, 400)] == "headline"
    assert roles[(11.0, True, 700)] == "section heading"
    assert roles[(10.0, False, 400)] == "body"
    assert roles[(10.0, False, 700)] == "entry title"
    assert roles[(8.0, False, 400)] == "small"
    body = next(s for s in found["styles"] if s["role"] == "body")
    assert body["family"] == "Raleway"
    assert body["line_pitch_pt"] == 14.0
    assert body["x_pt"] == [56.7]
    heading = next(s for s in found["styles"] if s["role"] == "section heading")
    assert heading["letter_spacing_em"] == 0.1


def test_the_headings_are_the_scrubbed_texts_of_the_heading_style_in_reading_order():
    found = design_spec.measured(DESIGN, FACES, texts())
    assert found["headings"] == ["EXPERIENCE", "EDUCATION"]
    assert "zq7w4k9secret" not in str(found)


def test_each_page_gives_its_background_margins_rules_boxes_dots_and_photo_shape():
    (page,) = design_spec.measured(DESIGN, FACES, texts())["pages"]
    assert page["background"] == "#f8f8f8"
    assert page["margins_pt"] == [56.7] * 4
    assert page["rules"] == [
        {"thickness_pt": 0.8, "colour": "#a6a6a6", "box_pt": [56.7, 190.0, 538.6, 191.5]}
    ]
    assert page["boxes"] == [
        {"fill": "#262525", "radius_pt": 8.0, "box_pt": [0.0, 0.0, 595.3, 150.0]}
    ]
    assert page["dots"] == [{"diameter_pt": 3.0, "colour": "#1f4e79", "x_pt": 50.0}]
    assert page["photos"] == [{"box_pt": [450.0, 30.0, 540.0, 120.0], "shape": "round"}]


def test_a_page_without_runs_gives_no_style_and_no_heading():
    empty = DesignPdf(1, True, False, (), (), (DesignPage(0, 5953, 8419, None, (), (), ()),))
    found = design_spec.measured(empty, [], [[]])
    assert (found["styles"], found["headings"]) == ([], [])


# #757 round 1: the roles as the real Chromium prints of the three bundled templates showed
# them. Labels come only from `headings` (design_capture.labels_of), so a heading style must
# hold the section headings and nothing of the header.
def _spec(*runs: TextRun) -> dict:
    page = DesignPage(0, 5953, 8419, (567, 567, 567, 567), runs, (), ())
    design = DesignPdf(1, False, False, (), FONTS, (page,))
    return design_spec.measured(design, FACES, [[r.text for r in runs]])


def _roles(found: dict) -> dict:
    """The role of each (size, capitals, weight), of its widest cluster (a short word whose
    spacing reads otherwise is a cluster of its own)."""
    widest = sorted(found["styles"], key=lambda s: s["characters"])
    return {(s["size_pt"], s["caps"], s["weight"]): s["role"] for s in widest}


BODY = tuple(
    run(f"Built the planner of the fleet, part {n}", 3000 + 140 * n, 100) for n in range(9)
)


def test_headings_larger_than_the_headline_and_one_in_the_header_are_section_headings():
    """photo-columns: a 26pt name, a 12pt headline of two lines, 13pt headings (PROFILE in the
    header, the others below it) and 10pt entry titles in bold capitals."""
    found = _spec(
        run("ALEX EXAMPLE", 900, 260, font=0, caps=True),
        run("PLATFORM ENGINEER: RELIABLE SERVICES", 1250, 120, caps=True),
        run("OPERATIONS, CALM RELEASES", 1400, 120, caps=True),
        run("PROFILE", 2000, 130, font=0, caps=True),
        *BODY,
        run("EXPERIENCE", 4500, 130, font=0, caps=True),
        run("SENIOR PLATFORM ENGINEER", 4700, 100, font=0, caps=True),
        run("SOFTWARE ENGINEER", 5200, 100, font=0, caps=True),
        run("EDUCATION", 6000, 130, font=0, caps=True),
    )
    roles = _roles(found)
    assert roles[(26.0, True, 700)] == "name"
    assert roles[(12.0, True, 400)] == "headline"
    assert roles[(13.0, True, 700)] == "section heading"
    assert roles[(10.0, True, 700)] == "entry title"
    assert found["headings"] == ["PROFILE", "EXPERIENCE", "EDUCATION"]


def test_capitals_win_over_bold_at_one_size_and_wrapped_headings_are_joined():
    """ledger: headings in bold capitals and sub headings in bold, both 10pt; band: a heading
    wrapped onto two lines of a narrow column, one of them read with another spacing."""
    found = _spec(
        run("ABOUT ME", 3000, 100, font=0, caps=True),
        *(run(f"Planned the robots of the fleet, part {n}", 3200 + 120 * n, 80) for n in range(9)),
        run("Recognition", 4500, 100, font=0),
        run("Core Skills", 4700, 100, font=0),
        run("KEY", 5000, 100, font=0, caps=True, spacing=10),
        run("ACHIEVEMENTS", 5134, 100, font=0, caps=True),
        run("EDUCATION", 6000, 100, font=0, caps=True),
    )
    roles = _roles(found)
    assert roles[(10.0, True, 700)] == "section heading"
    assert roles[(10.0, False, 700)] == "entry title"
    assert found["headings"] == ["ABOUT ME", "KEY ACHIEVEMENTS", "EDUCATION"]


def test_contact_lines_of_the_header_and_texts_with_digits_are_no_headings():
    """band: contact lines larger than the body in the header band; a date and an address in a
    heading style are left out of the headings."""
    found = _spec(
        run("Alex Example", 600, 180, font=0),
        run("Platform engineer", 900, 130, font=0),
        run("Lyon, France", 1200, 110, colour="#f8f8f8"),
        run("e2e-alex@example.org", 1350, 110, colour="#f8f8f8"),
        *BODY,
        run("About", 4400, 110, font=0),
        run("Skills", 4800, 110, font=0),
        run("2018 to 2020", 5200, 110, font=0),
        run("example.org/alex", 5600, 110, font=0),
    )
    roles = _roles(found)
    assert roles[(11.0, False, 400)] == "meta line"
    assert roles[(11.0, False, 700)] == "section heading"
    assert found["headings"] == ["About", "Skills"]
