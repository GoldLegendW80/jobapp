"""The edge cases of templates 9 to 12 (task D20), beside the table driven tests of
test_templates.py that every folder passes: `grid` with competencies and skills of very different
lengths, `mono` with accented text in its monospace runs, `executive` with its small caps headings
in French; the contrast of every text colour of the four, no dash printed as a separator, and each
of the four distinct in layout from every other template (not a recolour). Marked `docker`: the
same cases through the real renderer, with the geometry and the fonts of the PDF (pypdfium2
character boxes and font names), a long name and long contact lines kept whole in the header,
and the text order of the two tagged ats friendly.

`grid` runs with eight competencies, the content's cap (`content.COMPETENCIES_MAX`, D2), beside
twelve skills of the same spread of lengths, all in the grid of tags.
"""

import ctypes
import dataclasses
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
    at_soft_limits,
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


CSS_CONTENT = re.compile(
    r"""(?<![-\w])content\s*:\s*((?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^;}"'])*)""", re.S
)
CSS_STRING = re.compile(r""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'""", re.S)
CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n]?|\\(.)", re.S)


def css_escape(match: re.Match[str]) -> str:
    if match[1] is None:
        return match[2]
    code = int(match[1], 16)
    return chr(code) if 0 < code <= 0x10FFFF else "\ufffd"


def css_content_strings(css: str) -> list[str]:
    """Every string of every CSS `content` declaration, in double or single quotes, with its
    escapes decoded (`\\2013` is an en dash); comments are not read."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        CSS_ESCAPE.sub(css_escape, double or single)
        for value in CSS_CONTENT.findall(css)
        for double, single in CSS_STRING.findall(value)
    ]


@pytest.mark.parametrize(
    ("css", "strings"),
    [
        ('a::before { content: "\u2013"; }', ["\u2013"]),
        ("a::before { content: '\u2013'; }", ["\u2013"]),
        ("a::before{content:'\u2014'}", ["\u2014"]),
        ('a::before { content: "\\2013"; }', ["\u2013"]),
        ("a::before { content: '\\2014 x'; }", ["\u2014x"]),
        ("a::after { content: ' - ' attr(title); }", [" - "]),
        ("a::after { content: \"it's\" ' \\\" '; }", ["it's", ' " ']),
        ("a::after { content: 'a' \" - \"; }", ["a", " - "]),
        ('a::before { content: ""; } b { color: red; }', [""]),
        ("a::before { content: counter(item); }", []),
        ('/* content: "\u2013" */ a { color: red; }', []),
        ("a { justify-content: space-between; align-content: center; }", []),
    ],
)
def test_css_content_strings_reads_both_quotes_and_escapes(css: str, strings: list[str]) -> None:
    assert css_content_strings(css) == strings


@pytest.mark.parametrize("slug", SLUGS)
def test_no_dash_is_printed_as_a_separator(slug: str) -> None:
    """The text a template prints of its own: its manifest's names and descriptions, the text of
    its CSS `content` (either quotes, escapes decoded), and the page it builds from the sample in
    both languages."""
    found = template(slug)
    own = [*found.name.values(), *found.description.values()]
    own += css_content_strings(found.files["style.css"])
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


def baselines(pdf: bytes, needle: str) -> list[set[float]]:
    """For each place `needle` is printed on page 1 (white space ignored), the baselines its
    glyphs stand on: one baseline when it is whole on a line. pdfium reads a hyphen that ends a
    line as U+FFFE, read here as the hyphen it was."""
    document = pdfium.PdfDocument(pdf)
    try:
        textpage = document[0].get_textpage()
        text = textpage.get_text_range().replace("\ufffe", "-")
        chars = [i for i, ch in enumerate(text) if not ch.isspace()]
        compact = "".join(text[i] for i in chars)
        target = "".join(needle.split())
        found, start = [], compact.find(target)
        while start >= 0:
            lines = set()
            for i in chars[start : start + len(target)]:
                x, y = ctypes.c_double(), ctypes.c_double()
                pdfium.raw.FPDFText_GetCharOrigin(textpage.raw, i, ctypes.byref(x), ctypes.byref(y))
                lines.add(round(y.value, 1))
            found.append(lines)
            start = compact.find(target, start + len(target))
        return found
    finally:
        document.close()


def assert_whole_on_one_line(pdf: bytes, view: ProfileView) -> None:
    """The name and every contact line are printed whole, each on a single line where it stands
    highest on the page (the header: a letter prints the name again to sign it, a CV may print
    the city again with a role)."""
    for text in [
        view.identity.full_name,
        *(line["text"] for line in templates.contact_lines(view)),
    ]:
        found = baselines(pdf, text)
        assert found, f"{text!r} not printed whole"
        header = max(found, key=max)
        assert len(header) == 1, (text, sorted(header))


# The journey's person (documents-helpers.ts `invented`): a 17 character name and a 43 character
# e-mail with hyphens, here with two addresses of 39 and 34 characters as well.
LONG_CONTACT = Identity(
    full_name="Morgan Exampleton",
    email="e2e-documents-d20-mvfaxmjmk2bzq@example.org",
    phone="+33 6 00 00 00 00",
    city="Nantes",
    country="France",
    website="https://morgan-exampleton.example.org/portfolio",
    links=(("Code", "https://code.example.org/morgan-exampleton"),),
)
SHORT_HEADLINE = "Platform Engineer"

# Every header the review of these templates quoted (the journey's person under its short
# headline, D1's Alex and an ordinary dotted address under the soft limit one), then the
# journey's person under the soft limit headline. Rows are added, never removed.
HEADERS = {
    "journey, short headline": (LONG_CONTACT, False),
    "alex, soft limit headline": (None, True),
    "ordinary address, soft limit headline": (
        dataclasses.replace(LONG_CONTACT, email="morgan.exampleton@example.org"),
        True,
    ),
    "journey, soft limit headline": (LONG_CONTACT, True),
}


def with_headline(found: templates.Template, kind: str, language: str, soft: bool) -> c.Content:
    """D1's typical CV, or its letter in the language, under the short headline or under the
    typical CV's headline filled to the template's soft limit."""
    _, _, typical = d1("cv-typical")
    limit = found.kinds["cv"].soft_limits.headline
    headline = at_soft_limits(typical, found.kinds["cv"].soft_limits).headline
    assert len(headline) == limit
    content = typical if kind == "cv" else d1(f"letter-{language}")[2]
    data = content.model_dump() | {"headline": headline if soft else SHORT_HEADLINE}
    return type(content).model_validate(data)


@docker
@pytest.mark.parametrize("header", HEADERS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("slug", D20)
def test_a_long_name_and_long_contact_lines_stay_whole_in_the_header(
    renderer: Any, slug: str, kind: str, language: str, header: str
) -> None:
    """A long name beside long contact lines, under a short headline or one at the soft limit,
    at the step the fit chooses and at the loosest step (the largest type): the name stays on
    one line and each contact line whole, so an applicant tracking system reads the e-mail
    address and the links as they are."""
    found = template(slug)
    identity, soft = HEADERS[header]
    view = d1_view()
    if identity is not None:
        view = dataclasses.replace(view, identity=identity)
    content = with_headline(found, kind, language, soft)
    result = fitted(renderer, found, kind, content, view, language)
    assert result.fit, result.pages
    assert_whole_on_one_line(result.pdf, view)
    if result.step:
        loosest = found.steps(kind)[0]
        html = templates.build_html(found, kind, content, view, language, loosest, ON)
        assert_whole_on_one_line(renderer.render(html).pdf, view)


@docker
@pytest.mark.parametrize("filled", [False, True], ids=["typical", "at its soft limits"])
@pytest.mark.parametrize("slug", ATS)
def test_an_ats_friendly_template_reads_in_the_order_of_the_page(
    renderer: Any, slug: str, filled: bool
) -> None:
    """The PDF text, as an applicant tracking system extracts it: the name first, the name and
    each contact line whole on a line, the sections in the order of the page, the roles in
    order, and every bullet whole on its way; for D1's typical CV and for it filled to the
    template's soft limits."""
    _, language, content = d1("cv-typical")
    found = template(slug)
    if filled:
        content = at_soft_limits(content, found.kinds["cv"].soft_limits)
    result = fitted(renderer, found, "cv", content, d1_view(), language)
    assert result.fit
    assert_whole_on_one_line(result.pdf, d1_view())
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
