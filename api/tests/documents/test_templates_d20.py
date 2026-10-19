"""The edge cases of templates 9 to 12 (task D20), beside the table driven tests of
test_templates.py that every folder passes: `grid` with competencies and skills of very different
lengths, `mono` with accented text in its monospace runs, `executive` with its small caps headings
in French; the contrast of every text colour of the four, no dash printed as a separator, and each
of the four distinct in layout from every other template (not a recolour). Marked `docker`: the
same cases through the real renderer, with the geometry and the fonts of the PDF (pypdfium2
character boxes and font names), and the text order of the two tagged ats friendly.

The plan asks for `grid` with twelve competencies: the content holds at most eight
(`content.COMPETENCIES_MAX`, D2), so the case runs with the eight it accepts beside twelve skills
of the same spread of lengths, all in the grid of tags.
"""

import ctypes
import re
import unicodedata
from html import unescape
from itertools import combinations
from typing import Any

import pydantic
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageChops

from app.documents import content as c
from app.documents import templates
from app.documents.profile_view import Employer, Identity, Language, ProfileView, School
from app.documents.templates import thumbnails
from tests.documents.test_templates import (
    ON,
    SLUGS,
    assert_authoring,
    d1,
    d1_view,
    fitted,
    pdf_fonts,
    pdf_text,
    template,
)
from tests.documents.test_templates import renderer as renderer
from tests.documents.test_templates_d19 import (
    COLOUR_PROPERTIES,
    Box,
    Glyphs,
    bottom,
    colours,
    contrast,
    top,
)

D20 = ("accent", "executive", "grid", "mono")
ATS = [slug for slug in D20 if "ats friendly" in template(slug).tags]
DASHES = ("—", "–", " - ")

# Eight competencies (the content's cap) from 2 to 98 characters, and twelve skills from 1 to 100.
COMPETENCIES = [
    "UX",
    "Kanban",
    "Observability",
    "Release engineering",
    "Infrastructure as code for regulated workloads",
    "Mentoring",
    "Developer experience and internal platforms for forty product teams",
    "Capacity planning, cost reviews and the quarterly budget of the shared cloud accounts",
]
SKILLS = [
    "C",
    "Zig",
    "Rust",
    "Terraform",
    "OpenTelemetry",
    "Structured logging",
    "PostgreSQL logical replication",
    "Kubernetes operators written for the billing team",
    "Q" + "wertyuiop" * 6 + "z",  # 56 letters and no space: it wraps inside its tag
    "Zero downtime schema migrations on tables of several hundred million rows",
    "Wasm",
    "Profiling of the Python services of the order flow under production load, by flame graphs"
    " and traces",
]


def grid_content() -> c.CvContent:
    _, _, content = d1("cv-typical")
    skills = [{"label": "Tools", "items": SKILLS}]
    return c.CvContent.model_validate(
        content.model_dump() | {"competencies": COMPETENCIES, "skills": skills}
    )


def items_of(html: str, opening: str) -> list[str]:
    """The text of each `<li>` of the first list that starts with `opening`."""
    start = html.index(opening)
    block = html[start : html.index("</ul>", start)]
    return [unescape(text) for text in re.findall(r"<li>(.*?)</li>", block, re.S)]


# --- static: grid -------------------------------------------------------------------------------


def test_twelve_competencies_are_refused_by_the_content_and_eight_accepted() -> None:
    _, _, content = d1("cv-typical")
    data = content.model_dump()
    with pytest.raises(pydantic.ValidationError):
        c.CvContent.model_validate(data | {"competencies": [f"Term {i}" for i in range(12)]})
    assert c.CvContent.model_validate(data | {"competencies": COMPETENCIES}).competencies


@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_grid_prints_each_competency_and_skill_as_its_own_tag(language: str) -> None:
    html = templates.build_html(template("grid"), "cv", grid_content(), d1_view(), language, 0, ON)
    assert_authoring(html)
    assert items_of(html, '<ul class="competencies">') == COMPETENCIES
    assert items_of(html, '<ul class="tags">') == SKILLS
    lengths = sorted(len(term) for term in COMPETENCIES + SKILLS)
    assert (lengths[0], lengths[-1]) == (1, c.TERM_MAX)


# --- static: mono -------------------------------------------------------------------------------


def accented_view() -> ProfileView:
    return ProfileView(
        revision=1,
        identity=Identity(full_name="Zoé Exemplaire", email="e2e-zoe@example.org", city="Besançon"),
        employers=(
            Employer(
                "coop", "Coopérative Échantillon", "Ingénieure plateforme", "2021-02", None, ""
            ),
            Employer("atelier", "Atelier Dvořák", "Développeuse", "2018-08", "2021-01", "Lyon"),
        ),
        schools=(
            School("École Supérieure d'Exemple", "Maîtrise d'informatique", "2016", "2018", ""),
        ),
        languages=(Language("Français", "langue maternelle"),),
    )


MONO_COMPETENCIES = ["Fiabilité des déploiements", "Modélisation des données", "Façades d'API"]
MONO_SKILLS = ["Débogage", "Mise à l'échelle", "Cœur réseau", "Zürich", "Dvořák", "Łódź"]


def accented_content() -> c.CvContent:
    return c.CvContent.model_validate(
        {
            "schema": 1,
            "headline": "Ingénieure plateforme, déploiements sûrs",
            "summary": "Ingénieure qui rend les mises en production prévisibles.",
            "competencies": MONO_COMPETENCIES,
            "experience": [
                {"employer_key": "coop", "bullets": ["A réécrit la chaîne de déploiement."]},
                {"employer_key": "atelier", "bullets": ["A conçu les écrans de réglage."]},
            ],
            "skills": [{"label": "Systèmes", "items": MONO_SKILLS}],
        }
    )


# The runs `mono` sets in its monospace face, with accents: the name, an organisation, a period,
# a heading, a competency, a skill label and skills of Latin 1 and Latin Extended A.
MONO_RUNS = [
    "Zoé Exemplaire",
    "Coopérative Échantillon",
    "Atelier Dvořák",
    "février 2021 à aujourd'hui",
    "août 2018 à janvier 2021",
    "Compétences clés",
    *MONO_COMPETENCIES,
    "Systèmes",
    *MONO_SKILLS,
]


def test_mono_prints_the_accented_runs() -> None:
    html = templates.build_html(
        template("mono"), "cv", accented_content(), accented_view(), "fr", 0, ON
    )
    body = assert_authoring(html).body
    for run in MONO_RUNS:
        assert run in body, run


# --- static: executive --------------------------------------------------------------------------


def rule_of(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match, selector
    return match.group(1)


def test_executive_sets_its_headings_in_small_caps_in_both_languages() -> None:
    css = template("executive").files["style.css"]
    assert "font-variant: small-caps" in rule_of(css, ".label")
    _, _, content = d1("cv-typical")
    for language in templates.LANGUAGES:
        html = templates.build_html(
            template("executive"), "cv", content, d1_view(), language, 0, ON
        )
        headings = [unescape(h) for h in re.findall(r'<h2 class="label">(.*?)</h2>', html)]
        labels = templates.labels()[language]
        assert headings == [
            labels[key]
            for key in (
                "summary",
                "highlights",
                "competencies",
                "experience",
                "education",
                "skills",
                "languages",
            )
        ]


# --- static: every template of the set ----------------------------------------------------------


@pytest.mark.parametrize("slug", D20)
def test_every_text_colour_reaches_4_5_to_1_on_every_background(slug: str) -> None:
    """As D19's test: every `color` of the style (the accent included) against white and every
    `background`, WCAG AA for body text; rules are decoration, not text."""
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


@pytest.mark.parametrize("slug", SLUGS)
def test_no_dash_is_printed_as_a_separator(slug: str) -> None:
    """The text a template prints of its own: its manifest's names and descriptions, the text of
    its CSS `content`, and the page it builds from the sample in both languages."""
    found = template(slug)
    own = [*found.name.values(), *found.description.values()]
    own += re.findall(r"content:\s*\"([^\"]*)\"", found.files["style.css"])
    for kind in templates.KINDS:
        for language in templates.LANGUAGES:
            sample = thumbnails.sample()[kind]
            view = thumbnails.sample_view()
            html = templates.build_html(found, kind, sample, view, language, 0, ON)
            own.append(assert_authoring(html).body)
    for text in own:
        assert not any(dash in text for dash in DASHES), (slug, text[:120])


def normalised(found: templates.Template) -> str:
    """The CV template and the style without comments, colours and white space."""
    text = found.files["cv.html.j2"] + found.files["style.css"]
    text = re.sub(r"\{#.*?#\}|/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"#[0-9a-fA-F]{3,6}\b|rgba?\([^)]*\)", "", text)
    return "".join(text.split())


def ink(slug: str) -> Image.Image:
    with Image.open(templates.ROOT / slug / "thumbnail.png") as image:
        return image.convert("L").point(lambda value: 255 if value < 200 else 0)


@pytest.mark.parametrize("slug", D20)
def test_each_template_differs_in_layout_from_every_other(slug: str) -> None:
    """Not a recolour: the files differ once colours are taken out, and the CV thumbnails, reduced
    to ink and paper (any colour darker than a light grey is ink), differ on at least 5 % of their
    pixels. Measured on 2026-10-19: the closest pair of the twelve, `grid` and `mono`, 9.3 %."""
    mine = ink(slug)
    for other in SLUGS:
        if other == slug:
            continue
        assert normalised(template(slug)) != normalised(template(other)), other
        theirs = ink(other)
        height = min(mine.height, theirs.height)
        box = (0, 0, mine.width, height)
        diff = ImageChops.difference(mine.crop(box), theirs.crop(box))
        share = 1 - diff.histogram()[0] / (mine.width * height)
        assert share >= 0.05, (slug, other, round(share, 3))


# --- docker: through the real renderer ----------------------------------------------------------

docker = pytest.mark.docker


def overlaps(a: list[Box], b: list[Box]) -> bool:
    return any(x[0] < y[2] and y[0] < x[2] and x[1] < y[3] and y[1] < x[3] for x in a for y in b)


def span(boxes: list[Box]) -> float:
    return max(box[2] for box in boxes) - min(box[0] for box in boxes)


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_grid_tags_of_very_different_lengths_stay_in_their_cells(
    renderer: Any, language: str
) -> None:
    """One page; each competency wraps inside its cell of the grid four across (its glyphs span
    at most a cell), each skill inside its tag, and no two tags share a glyph's space."""
    found = template("grid")
    result = fitted(renderer, found, "cv", grid_content(), d1_view(), language)
    assert result.fit, result.pages
    step = found.steps("cv")[result.step]
    content_width = (210 - 2 * step.margin) * 72 / 25.4
    cell = (content_width - 3 * 4) / 4  # four columns, three gaps of 4pt
    glyphs = Glyphs(result.pdf)
    competencies = [glyphs.find(term) for term in COMPETENCIES]
    for term, boxes in zip(COMPETENCIES, competencies, strict=True):
        assert span(boxes) <= cell + 0.5, (term, span(boxes), cell)
    skills = [glyphs.find(term) for term in SKILLS]
    for term, boxes in zip(SKILLS, skills, strict=True):
        assert span(boxes) <= content_width, term
    for (a, first), (b, second) in combinations(
        zip(COMPETENCIES + SKILLS, competencies + skills, strict=True), 2
    ):
        assert not overlaps(first, second), (a, b)


def font_of(textpage: Any, index: int) -> str:
    size = pdfium.raw.FPDFText_GetFontInfo(textpage.raw, index, None, 0, None)
    buffer = ctypes.create_string_buffer(size)
    pdfium.raw.FPDFText_GetFontInfo(textpage.raw, index, buffer, size, None)
    return buffer.value.decode("utf-8", "replace")


@docker
def test_mono_draws_accented_runs_in_its_monospace_face(renderer: Any) -> None:
    """French, accents of Latin 1 and Latin Extended A in every monospace run: one page, each run
    whole in the PDF text, every one of its characters drawn by IBM Plex Mono (never a fallback
    family), and no fallback family embedded at all."""
    result = fitted(renderer, template("mono"), "cv", accented_content(), accented_view(), "fr")
    assert result.fit
    assert result.pages == 1
    fonts = pdf_fonts(result.pdf)
    assert not [font for font in fonts if "Noto" in font], fonts
    document = pdfium.PdfDocument(result.pdf)
    try:
        textpage = document[0].get_textpage()
        text = textpage.get_text_range()
        squeezed = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
        flat = unicodedata.normalize("NFC", "".join(ch for _, ch in squeezed))
        assert len(flat) == len(squeezed), "the text holds decomposed accents"
        for run in MONO_RUNS:
            target = "".join(run.split())
            start = flat.find(target)
            assert start >= 0, run
            names = {font_of(textpage, squeezed[k][0]) for k in range(start, start + len(target))}
            assert all("IBMPlexMono" in name for name in names), (run, names)
    finally:
        document.close()


@docker
def test_executive_small_caps_in_french_keep_their_accents_and_text(renderer: Any) -> None:
    """Every French heading of `executive` is in the PDF text with its accents, drawn in Source
    Serif 4 alone, and set in small caps: in "Expérience" the x stands lower than the capital E
    (not upper case) and the p sits on the baseline with no descender (not lower case)."""
    _, _, content = d1("cv-typical")
    result = fitted(renderer, template("executive"), "cv", content, d1_view(), "fr")
    assert result.fit, result.pages
    fonts = pdf_fonts(result.pdf)
    assert fonts
    assert all("SourceSerif4" in font for font in fonts), fonts
    glyphs = Glyphs(result.pdf)
    labels = templates.labels()["fr"]
    for key in ("summary", "highlights", "competencies", "experience", "education", "skills"):
        assert glyphs.find_all(labels[key]), key
    heading = min(glyphs.find_all("Expérience"), key=lambda boxes: -top(boxes))  # the heading
    capital, small_x, small_p = heading[0], heading[1], heading[2]
    assert small_x[3] < capital[3] - 0.5, (small_x, capital)
    assert small_p[1] >= capital[1] - 0.4, (small_p, capital)
    assert bottom(heading) >= capital[1] - 0.4


@docker
@pytest.mark.parametrize("slug", ATS)
def test_an_ats_friendly_template_reads_in_the_order_of_the_page(renderer: Any, slug: str) -> None:
    """The PDF text, as an applicant tracking system extracts it: the name first, the sections
    in the order of the page, the roles in order, and every bullet whole on its way."""
    _, language, content = d1("cv-typical")
    result = fitted(renderer, template(slug), "cv", content, d1_view(), language)
    assert result.fit
    # pdfium reads a hyphen that ends a line as U+FFFE, in every template.
    text = " ".join(pdf_text(result.pdf).replace("\ufffe", "-").split())
    assert text.startswith("Alex Example"), text[:60]
    labels = templates.labels()[language]
    keys = ("summary", "highlights", "competencies", "experience", "education", "skills")
    folded = text.casefold()  # a heading in small caps may be extracted in capitals
    places = [folded.index(labels[key].casefold()) for key in keys]
    assert places == sorted(places), dict(zip(keys, places, strict=True))
    titles = [text.index(employer.title) for employer in d1_view().employers]
    assert titles == sorted(titles), titles
    # A long word wraps where the line ends: white space is not compared.
    squeezed = "".join(text.split())
    for role in content.experience:
        for bullet in role.bullets:
            assert "".join(bullet.split()) in squeezed, bullet[:60]
