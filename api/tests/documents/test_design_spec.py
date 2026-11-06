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
