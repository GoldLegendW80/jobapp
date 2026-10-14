"""The edge cases of templates 5 to 8 (task D19), beside the table driven tests of
test_templates.py that every folder passes: `timeline` with an employer that has no end and a
school with no dates, `split-right` with a long skills list, `minimal` without highlights, and
the contrast of every text colour of the four on every background they paint. Marked `docker`:
the same cases through the real renderer (test_templates.py's helpers and renderer stack), plus
the geometry of the PDF (pypdfium2 character boxes): a school's dates in `timeline`'s rail never
reach the next entry, `minimal`'s headings stay in its margin, and a CV with a photo fits.
"""

import asyncio
import io
import re
from html import unescape
from typing import Any

import pypdfium2 as pdfium
import pytest
from PIL import Image

from app.documents import content as c
from app.documents import fit, photo, templates
from app.documents.profile_view import Employer, Identity, ProfileView, School
from tests.documents.test_templates import (
    ON,
    SLUGS,
    assert_authoring,
    d1,
    d1_view,
    fitted,
    pdf_text,
    template,
)
from tests.documents.test_templates import renderer as renderer

D19 = ("serif", "timeline", "split-right", "minimal")


def view_with_open_end_and_undated_school() -> ProfileView:
    return ProfileView(
        revision=1,
        identity=Identity(full_name="Alex Example", email="e2e-alex@example.org"),
        employers=(
            Employer("example-corp", "Example Corp", "Platform Engineer", "2021-03", None, ""),
            Employer("sample-labs", "Sample Labs", "Developer", "2018", "2021", "Lyon, France"),
        ),
        schools=(
            School("Undated School", "Certificate in Testing", None, None, ""),
            School("Institut Échantillon", "BSc Mathematics", "2015", "2018", ""),
        ),
    )


def timeline_content() -> c.CvContent:
    return c.CvContent.model_validate(
        {
            "schema": 1,
            "headline": "Platform engineer",
            "summary": "Engineer who runs calm releases.",
            "experience": [
                {"employer_key": "example-corp", "bullets": ["Ran the platform."]},
                {"employer_key": "sample-labs", "bullets": []},
            ],
        }
    )


def long_skills(content: c.CvContent, groups: int = 4, per_group: int = 18) -> c.CvContent:
    skills = [
        {"label": f"Group {g}", "items": [f"Tool{g}x{i}" for i in range(per_group)]}
        for g in range(groups)
    ]
    return c.CvContent.model_validate(content.model_dump() | {"skills": skills})


def without_highlights(content: c.CvContent) -> c.CvContent:
    return c.CvContent.model_validate(content.model_dump() | {"highlights": []})


def rail_of(html: str, label: str) -> str:
    """The HTML of the rail under the heading `label`."""
    start = html.index(f'<h2 class="label">{label}</h2>')
    rail = html.index('<div class="rail">', start)
    return html[rail : html.index("</section>", rail)]


# --- static: the built HTML ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "present"),
    [("en", "March 2021 to present"), ("fr", "mars 2021 à aujourd&#39;hui")],
)
def test_timeline_puts_an_open_end_and_an_undated_school_on_the_rail(
    language: str, present: str
) -> None:
    labels = templates.labels()[language]
    html = templates.build_html(
        template("timeline"),
        "cv",
        timeline_content(),
        view_with_open_end_and_undated_school(),
        language,
        0,
        ON,
    )
    assert_authoring(html)
    experience = rail_of(html, labels["experience"])
    assert f'<p class="period">{present}</p>' in experience
    assert experience.index("Platform Engineer") < experience.index(present)
    # A role without bullets still prints its title and its period.
    assert "Developer" in experience
    assert "2018 to 2021" in experience or "2018 à 2021" in experience
    education = rail_of(html, labels["education"])
    undated = education.split('<div class="school">')[1]  # the first school
    assert "Undated School" in undated
    assert "Certificate in Testing" in undated
    assert 'class="period"' not in undated
    assert '<p class="period">2015' in education


LOCATIONS = {
    "en": ("Clermont-Ferrand, France", "Boston, Massachusetts, United States"),
    "fr": ("Saint-Étienne, France", "Boston, Massachusetts, États-Unis"),
}


def view_with_schools_with_long_locations(language: str) -> ProfileView:
    """Three schools dated by month (the last by year), each with a city and a country: the
    dates of a school wrap to several lines in `timeline`'s 24 mm rail."""
    city, far = LOCATIONS[language]
    return ProfileView(
        revision=1,
        identity=Identity(full_name="Alex Example", email="e2e-alex@example.org"),
        employers=(
            Employer("example-corp", "Example Corp", "Platform Engineer", "2021-03", None, ""),
            Employer("sample-labs", "Sample Labs", "Developer", "2019-09", "2021-02", "Lyon"),
        ),
        schools=(
            School("Institut Exemple", "Master of Science", "2016-09", "2019-06", city),
            School("Collège Échantillon", "Bachelor of Science", "2016-09", "2019-06", city),
            School("Sample Harbor College", "Associate Degree", "2014", "2017", far),
        ),
    )


def timeline_content_with_skills() -> c.CvContent:
    data = timeline_content().model_dump()
    skills = [{"label": "Cloud", "items": ["Kubernetes", "Terraform"]}]
    return c.CvContent.model_validate(data | {"skills": skills})


def test_timeline_prints_only_the_period_in_the_rail_and_the_location_in_the_text() -> None:
    html = templates.build_html(
        template("timeline"),
        "cv",
        timeline_content_with_skills(),
        view_with_schools_with_long_locations("en"),
        "en",
        0,
        ON,
    )
    education = rail_of(html, templates.labels()["en"]["education"])
    assert education.count('<p class="period">September 2016 to June 2019</p>') == 2
    assert '<p class="period">2014 to 2017</p>' in education
    assert education.count('<p class="place">Clermont-Ferrand, France</p>') == 2
    assert '<p class="place">Boston, Massachusetts, United States</p>' in education


def test_split_right_prints_a_long_skills_list_in_the_panel_after_the_story() -> None:
    _, language, content = d1("cv-typical")
    content = long_skills(content)
    html = templates.build_html(template("split-right"), "cv", content, d1_view(), language, 0, ON)
    page = assert_authoring(html)
    main, side = html.index('<main class="main">'), html.index('<aside class="side">')
    assert main < side
    for g in range(4):
        assert f"Group {g}" in html[side:]
        assert ", ".join(f"Tool{g}x{i}" for i in range(18)) in page.body


@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_minimal_without_highlights_prints_no_highlights_heading(language: str) -> None:
    labels = templates.labels()[language]
    _, _, content = d1("cv-typical")
    html = templates.build_html(
        template("minimal"), "cv", without_highlights(content), d1_view(), language, 0, ON
    )
    body = assert_authoring(html).body
    assert labels["highlights"] not in body
    for key in ("summary", "competencies", "experience", "education", "skills", "languages"):
        assert labels[key] in body, key


# --- static: contrast ---------------------------------------------------------------------------

NAMED = {
    "black": "#000000",
    "white": "#ffffff",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "navy": "#000080",
    "maroon": "#800000",
    "purple": "#800080",
    "teal": "#008080",
    "olive": "#808000",
    "orange": "#ffa500",
    "yellow": "#ffff00",
    "dimgray": "#696969",
    "darkgray": "#a9a9a9",
    "lightgray": "#d3d3d3",
}
# Values that paint no colour of their own (a keyword, or a rule's `none`).
NO_COLOUR = {"inherit", "initial", "unset", "transparent", "none", "currentcolor"}
COLOUR_PROPERTIES = r"(color|background|background-color)"


def colour(token: str) -> str | None:
    """`#rrggbb` of a CSS colour (3 or 6 digit hex, `rgb()`, `rgba()` over white, a named colour),
    None for a keyword that paints nothing. An unknown form fails the test: it must not escape."""
    token = token.strip().lower()
    if token in NO_COLOUR:
        return None
    if token in NAMED:
        return NAMED[token]
    if re.fullmatch(r"#[0-9a-f]{6}", token):
        return token
    if re.fullmatch(r"#[0-9a-f]{3}", token):
        return "#" + "".join(ch * 2 for ch in token[1:])
    match = re.fullmatch(
        r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)\s*(?:[,/]\s*([\d.]+%?)\s*)?\)", token
    )
    if match:
        red, green, blue = (int(g) for g in match.groups()[:3])
        alpha_text = match.group(4) or "1"
        alpha = float(alpha_text[:-1]) / 100 if alpha_text.endswith("%") else float(alpha_text)
        mixed = [round(v * alpha + 255 * (1 - alpha)) for v in (red, green, blue)]
        return "#" + "".join(f"{v:02x}" for v in mixed)
    raise AssertionError(f"unknown colour form {token!r}: teach the contrast test about it")


def colours(value: str) -> set[str]:
    """The colours of one declaration's value: its tokens outside parentheses."""
    tokens = re.findall(r"(?:[^\s(]+\([^)]*\)|\S+)", value.strip())
    return {found for token in tokens if (found := colour(token))}


def luminance(colour_: str) -> float:
    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour_[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#fff", "#ffffff"),
        ("#1A2b3C", "#1a2b3c"),
        ("rgb(0, 0, 0)", "#000000"),
        ("rgba(0,0,0,0.5)", "#808080"),
        ("rgb(255 255 255 / 50%)", "#ffffff"),
        ("Gray", "#808080"),
        ("transparent", None),
        ("inherit", None),
    ],
)
def test_the_contrast_test_reads_every_colour_form(value: str, expected: str | None) -> None:
    assert colour(value) == expected


@pytest.mark.parametrize(
    "value", ["hsl(0 0% 0%)", "color-mix(in srgb, red, blue)", "#12345", "notacolour"]
)
def test_the_contrast_test_fails_on_a_colour_form_it_does_not_know(value: str) -> None:
    with pytest.raises(AssertionError, match="unknown colour form"):
        colour(value)


@pytest.mark.parametrize("slug", D19)
def test_every_text_colour_reaches_4_5_to_1_on_every_background(slug: str) -> None:
    """Every `color` of the style (the accent included) against white and every `background`:
    WCAG AA for body text. Rules (borders) are decoration and are not text. A colour written in a
    form the test cannot read fails it."""
    found = template(slug)
    css = re.sub(r"/\*.*?\*/", "", found.files["style.css"], flags=re.S)
    css = css.replace("var(--accent)", found.accent)
    texts = {found.accent}
    backgrounds = {"#ffffff"}
    for prop, value in re.findall(rf"(?<![-\w]){COLOUR_PROPERTIES}\s*:\s*([^;}}]+)", css):
        (texts if prop == "color" else backgrounds).update(colours(value))
    assert len(texts) >= 3, texts
    low = {(t, b): round(contrast(t, b), 2) for t in texts for b in backgrounds}
    assert all(ratio >= 4.5 for ratio in low.values()), {k: v for k, v in low.items() if v < 4.5}


# --- docker: through the real renderer ----------------------------------------------------------

docker = pytest.mark.docker


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_timeline_with_an_open_end_and_an_undated_school_renders_one_page(
    renderer: Any, language: str
) -> None:
    result = fitted(
        renderer,
        template("timeline"),
        "cv",
        timeline_content(),
        view_with_open_end_and_undated_school(),
        language,
    )
    assert result.fit, result.pages
    assert result.step == 0, result.step
    text = " ".join(pdf_text(result.pdf).split())
    assert "present" in text or "aujourd" in text
    assert "Undated School" in text


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_split_right_with_a_long_skills_list_fits_one_page(renderer: Any, language: str) -> None:
    _, _, content = d1("cv-typical")
    result = fitted(
        renderer, template("split-right"), "cv", long_skills(content), d1_view(), language
    )
    assert result.fit, result.pages
    text = "".join(pdf_text(result.pdf).split())
    assert all(f"Tool{g}x17" in text for g in range(4))


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_minimal_without_highlights_fits_no_later_than_with_them(
    renderer: Any, language: str
) -> None:
    _, _, content = d1("cv-typical")
    found, view = template("minimal"), d1_view()
    plain = fitted(renderer, found, "cv", without_highlights(content), view, language)
    full = fitted(renderer, found, "cv", content, view, language)
    assert plain.fit, plain.pages
    assert plain.step <= full.step, (plain.step, full.step)


# --- docker: the geometry of the PDF -------------------------------------------------------------


Box = tuple[float, float, float, float]


class Glyphs:
    """The characters of page 1 of a PDF with their boxes (points, y up), searched by text with
    the white space and the case ignored. pdfium lists the text in its own order (a line's left
    margin before its text, the headings apart), so a search returns every occurrence."""

    def __init__(self, pdf: bytes) -> None:
        document = pdfium.PdfDocument(pdf)
        try:
            textpage = document[0].get_textpage()
            text = textpage.get_text_range()
            self.chars = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
            self.boxes = {i: textpage.get_charbox(i) for i, _ in self.chars}
        finally:
            document.close()
        self.compact = "".join(ch for _, ch in self.chars).upper()

    def find_all(self, needle: str) -> list[list[Box]]:
        target = "".join(needle.split()).upper()
        found, start = [], self.compact.find(target)
        while start >= 0:
            found.append([self.boxes[i] for i, _ in self.chars[start : start + len(target)]])
            start = self.compact.find(target, start + len(target))
        return found

    def find(self, needle: str) -> list[Box]:
        found = self.find_all(needle)
        assert found, f"{needle!r} not found"
        return found[0]


def top(boxes: list[Box]) -> float:
    return max(box[3] for box in boxes)


def bottom(boxes: list[Box]) -> float:
    return min(box[1] for box in boxes)


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("loosest", [True, False])
def test_timeline_dates_of_a_school_never_reach_the_next_entry(
    renderer: Any, language: str, loosest: bool
) -> None:
    """The review of PR #286: a school dated by month with a city and a country took four rail
    lines and printed over the next school's dates, the last one over the Skills heading."""
    found = template("timeline")
    step = 0 if loosest else len(found.steps("cv")) - 1
    html = templates.build_html(
        found,
        "cv",
        timeline_content_with_skills(),
        view_with_schools_with_long_locations(language),
        language,
        step,
        ON,
    )
    rendered = renderer.render(html)
    assert rendered.pages == 1
    labels = templates.labels()[language]
    education = rail_of(html, labels["education"])
    periods = [unescape(p) for p in re.findall(r'<p class="period">(.*?)</p>', education)]
    degrees = ["Master of Science", "Bachelor of Science", "Associate Degree"]
    assert len(periods) == len(degrees) == 3
    glyphs = Glyphs(rendered.pdf)
    titles = [glyphs.find(degree) for degree in degrees]
    heading = glyphs.find(labels["skills"])
    # The rail: what stands left of the titles, under the Education heading, in reading order.
    column = min(box[0] for title in titles for box in title) - 1
    below = bottom(glyphs.find(labels["education"]))
    rail = [
        (ch, glyphs.boxes[i])
        for i, ch in glyphs.chars
        if glyphs.boxes[i][2] <= column and glyphs.boxes[i][3] <= below
    ]
    letters = "".join(ch for ch, _ in rail).upper()
    dates, at = [], 0
    for period in periods:
        target = "".join(period.split()).upper()
        start = letters.find(target, at)
        assert start >= 0, f"{period!r} is not whole in the rail: {letters!r}"
        at = start + len(target)
        dates.append([box for _, box in rail[start:at]])
    for index in range(len(degrees) - 1):
        next_top = max(top(titles[index + 1]), top(dates[index + 1]))
        assert bottom(dates[index]) > next_top, (language, step, index)
    assert bottom(dates[-1]) > top(heading), (language, step)


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_minimal_headings_stay_in_the_left_margin(renderer: Any, language: str) -> None:
    """Every heading ends before the text column that starts right of it (at the loosest step,
    where the type is largest)."""
    _, _, content = d1("cv-short")
    labels = templates.labels()[language]
    html = templates.build_html(template("minimal"), "cv", content, d1_view(), language, 0, ON)
    rendered = renderer.render(html)
    assert rendered.pages == 1
    glyphs = Glyphs(rendered.pdf)
    summary = glyphs.find(content.summary[:12])
    column = min(box[0] for box in summary)
    for key in ("summary", "highlights", "competencies", "experience", "education"):
        heading = min(glyphs.find_all(labels[key]), key=lambda boxes: boxes[0][0])  # the leftmost
        assert max(box[2] for box in heading) <= column + 0.01, (language, key)


def a_photo() -> str:
    gradient = Image.linear_gradient("L").resize((400, 400)).convert("RGB")
    out = io.BytesIO()
    gradient.save(out, format="JPEG", quality=80)
    return photo.embed(out.getvalue())


def fitted_with_photo(
    renderer: Any, found: templates.Template, content: Any, language: str, uri: str | None
) -> Any:
    async def render(html: str) -> Any:
        return await asyncio.to_thread(renderer.render, html)

    def build(step: templates.Step) -> str:
        return templates.build_html(found, "cv", content, d1_view(), language, step, ON, photo=uri)

    return asyncio.run(fit.fit(found.steps("cv"), build, render=render))


PHOTO_SLUGS = [slug for slug in SLUGS if template(slug).photo != "none"]


@docker
@pytest.mark.parametrize("slug", PHOTO_SLUGS)
@pytest.mark.parametrize("name", ["cv-short", "cv-typical", "cv-long", "cv-tightest"])
def test_a_cv_with_a_photo_fits_and_prints_the_photo(renderer: Any, slug: str, name: str) -> None:
    """Every template whose manifest allows a photo, with one, on D1's CVs: the CV fits one page
    (at the same step as without the photo or later), and the photo is in the PDF."""
    found = template(slug)
    _, language, content = d1(name)
    without = fitted_with_photo(renderer, found, content, language, None)
    with_photo = fitted_with_photo(renderer, found, content, language, a_photo())
    assert with_photo.fit, f"{slug} {name}: {with_photo.pages} pages at the tightest"
    assert with_photo.step >= without.step
    document = pdfium.PdfDocument(with_photo.pdf)
    try:
        images = [
            obj for obj in document[0].get_objects() if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE
        ]
    finally:
        document.close()
    assert len(images) == 1, (slug, name)
