"""The edge cases of templates 5 to 8 (task D19), beside the table driven tests of
test_templates.py that every folder passes: `timeline` with an employer that has no end and a
school with no dates, `split-right` with a long skills list, `minimal` without highlights, and
the contrast of every text colour of the four on every background they paint. Marked `docker`:
the same cases through the real renderer (test_templates.py's helpers and renderer stack).
"""

import re
from typing import Any

import pytest

from app.documents import content as c
from app.documents import templates
from app.documents.profile_view import Employer, Identity, ProfileView, School
from tests.documents.test_templates import (
    ON,
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
    undated = education[: education.index("Institut")]
    assert "Undated School" in undated
    assert "Certificate in Testing" in undated
    assert 'class="period"' not in undated
    assert '<p class="period">2015' in education


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

HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def luminance(colour: str) -> float:
    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("slug", D19)
def test_every_text_colour_reaches_4_5_to_1_on_every_background(slug: str) -> None:
    """Every `color` of the style (the accent included) against white and every `background`:
    WCAG AA for body text. Rules (borders) are decoration and are not text."""
    found = template(slug)
    css = found.files["style.css"].replace("var(--accent)", found.accent)
    texts = {found.accent}
    backgrounds = {"#ffffff"}
    for prop, value in re.findall(r"(?<![-\w])(color|background)\s*:\s*([^;}]+)", css):
        (texts if prop == "color" else backgrounds).update(HEX.findall(value))
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
