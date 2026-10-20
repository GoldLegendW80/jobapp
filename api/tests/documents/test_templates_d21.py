"""The edge cases of templates 13 to 16 (task D21), beside the table driven tests of
test_templates.py that every folder passes: `cards` with a section that would split across the
end of a page, `bold` with a very long name, `academic` with a profile of three schools (its
education printed before its experience), `elegant`'s frame on every page; the contrast of
every text colour on the backgrounds it is painted on, no dash printed as a separator, truthful
tags, and each of the four distinct in layout from every other template (not a recolour).
Marked `docker`: the same cases through the real renderer, with the geometry of the PDF
(pypdfium2 character boxes and path objects) and the text order of the one tagged ats friendly.
"""

import asyncio
import ctypes
import re
from html import unescape
from html.parser import HTMLParser
from itertools import pairwise
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageChops

from app.documents import content as c
from app.documents import fit, templates
from app.documents.profile_view import Identity, ProfileView, School
from app.documents.templates import thumbnails
from tests.documents.conftest import view_of, written
from tests.documents.test_templates import (
    ON,
    SLUGS,
    assert_authoring,
    at_soft_limits,
    d1,
    d1_view,
    fitted,
    pdf_text,
    template,
)
from tests.documents.test_templates import renderer as renderer
from tests.documents.test_templates_d19 import (
    COLOUR_PROPERTIES,
    Box,
    Glyphs,
    a_photo,
    bottom,
    colours,
    contrast,
    top,
)

D21 = ("elegant", "bold", "cards", "academic")
DASHES = ("—", "–", " - ")
MM = 72 / 25.4  # points in a millimetre
A4 = (210 * MM, 297 * MM)

# `academic` prints its sections in this order; the other templates print education after
# experience.
ACADEMIC_ORDER = (
    "summary",
    "education",
    "experience",
    "highlights",
    "competencies",
    "skills",
    "languages",
)


def headings(html: str) -> list[str]:
    return [unescape(h) for h in re.findall(r'<h2 class="label">(.*?)</h2>', html)]


# --- static: academic ---------------------------------------------------------------------------


def three_schools_view(language: str) -> ProfileView:
    """Alex with three schools: one dated by month with a long place, one by year, one undated."""
    far = "Boston, Massachusetts, United States" if language == "en" else "Boston, États-Unis"
    view = d1_view()
    return ProfileView(
        revision=view.revision,
        identity=view.identity,
        employers=view.employers,
        schools=(
            School("Institut Exemple", "Doctorate in Applied Statistics", "2019-09", "2023-06", ""),
            School("Université Échantillon", "MSc Computer Science", "2017", "2019", "Lyon"),
            School("Sample Harbor College", "Summer School in Optimisation", None, None, far),
        ),
        languages=view.languages,
    )


@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_academic_prints_the_education_before_the_experience(language: str) -> None:
    _, _, content = d1("cv-typical")
    html = templates.build_html(
        template("academic"), "cv", content, three_schools_view(language), language, 0, ON
    )
    body = assert_authoring(html).body
    labels = templates.labels()[language]
    assert headings(html) == [labels[key] for key in ACADEMIC_ORDER]
    degrees = [school.degree for school in three_schools_view(language).schools]
    places = [body.index(degree) for degree in degrees]
    assert places == sorted(places)
    assert body.index(labels["education"]) < places[0]
    assert places[-1] < body.index(labels["experience"])
    assert "September 2019 to June 2023" in body or "septembre 2019 à juin 2023" in body
    assert "Boston" in body


def test_every_other_template_of_the_set_prints_the_experience_first() -> None:
    _, language, content = d1("cv-typical")
    labels = templates.labels()[language]
    for slug in ("elegant", "bold", "cards"):
        found = headings(
            templates.build_html(template(slug), "cv", content, d1_view(), language, 0, ON)
        )
        assert found.index(labels["experience"]) < found.index(labels["education"]), slug


# --- static: cards ------------------------------------------------------------------------------


class Cards(HTMLParser):
    """The text of each card of a built page (an element whose class names `card`), in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[list[str]] = []
        self._depth = 0  # inside a card: the depth of open elements, else 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("img", "br", "meta"):
            return
        classes = (dict(attrs).get("class") or "").split()
        if self._depth:
            self._depth += 1
        elif "card" in classes:
            self.cards.append([])
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in ("img", "br", "meta"):
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth and data.strip():
            self.cards[-1].append(data)


def cards_of(html: str) -> list[str]:
    parser = Cards()
    parser.feed(html)
    return ["".join(" ".join(texts).split()) for texts in parser.cards]


def test_cards_keep_a_card_whole_and_put_each_role_on_its_own() -> None:
    css = template("cards").files["style.css"]
    rule = re.search(r"(?m)^\.card\s*\{([^}]*)\}", css)
    assert rule
    assert "break-inside: avoid" in rule.group(1)
    for language in templates.LANGUAGES:
        _, _, content = d1("cv-long")
        view = d1_view()
        html = templates.build_html(template("cards"), "cv", content, view, language, 0, ON)
        texts = cards_of(html)
        # The heading card, one card for each of the six other sections and one per role (the
        # first role's card carries the Experience heading).
        assert len(texts) == 1 + 6 + len(content.experience)
        labels = {
            key: "".join(text.split())
            for key, text in templates.labels()[language].items()
            if isinstance(text, str)
        }
        for key in ("summary", "highlights", "competencies", "experience", "education", "skills"):
            assert sum(text.startswith(labels[key]) for text in texts) == 1, key
        by_key = {employer.key: employer for employer in view.employers}
        roles = [text.removeprefix(labels["experience"]) for text in texts]
        for role in content.experience:
            title = "".join(by_key[role.employer_key].title.split())
            assert sum(text.startswith(title) for text in roles) == 1, title


# --- static: bold -------------------------------------------------------------------------------

LONG_NAMES = [
    "Maximiliane Alexandrine Exemplaire de la Fontaine Échantillon",  # 61 characters
    "Exemplairissimeauxlongsnomsquiseprolongentsansfin Example",  # a word of 49 letters
    " ".join(["Bartholomew Example"] * 11)[:200],  # the profile's cap, 200 characters
]


def with_name(view: ProfileView, name: str) -> ProfileView:
    identity = Identity(
        full_name=name,
        email=view.identity.email,
        phone=view.identity.phone,
        city=view.identity.city,
        country=view.identity.country,
        website=view.identity.website,
        links=view.identity.links,
    )
    return ProfileView(
        revision=view.revision,
        identity=identity,
        employers=view.employers,
        schools=view.schools,
        languages=view.languages,
    )


@pytest.mark.parametrize(
    ("name", "size"),
    [("Alex Example", ""), (LONG_NAMES[1], " long"), (LONG_NAMES[0], " longer")],
)
def test_bold_sets_a_long_name_smaller(name: str, size: str) -> None:
    _, language, content = d1("cv-typical")
    view = with_name(d1_view(), name)
    for kind, data in (("cv", content), ("letter", d1("letter-en")[2])):
        html = templates.build_html(template("bold"), kind, data, view, language, 0, ON)
        assert f'<h1 class="name{size}">{in_words(name)}</h1>' in html, kind


def in_words(name: str) -> str:
    return " ".join(f'<span class="w">{word}</span>' for word in name.split())


@pytest.mark.parametrize("slug", D21)
def test_each_word_of_the_name_is_a_box_of_its_own(slug: str) -> None:
    """A box per word (`.w`, inline-block) moves a word whole to the next line: the line breaks
    at a space, never at the hyphen of a double-barrelled name."""
    name = "Anne-Sophie de La Tour-Échantillon"
    view = with_name(d1_view(), name)
    for kind, data in (("cv", d1("cv-typical")[2]), ("letter", d1("letter-en")[2])):
        html = templates.build_html(template(slug), kind, data, view, "en", 0, ON)
        assert re.search(rf'<h1 class="name[^"]*">{re.escape(in_words(name))}</h1>', html), kind
    assert re.search(
        r"\.w\s*\{\s*display:\s*inline-block;\s*max-width:\s*100%;",
        template(slug).files["style.css"],
    )


# --- static: every template of the set ----------------------------------------------------------

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def painted_pairs(slug: str) -> set[tuple[str, str]]:
    """Each text colour of the style with the backgrounds it is painted on. A rule that sets both
    a colour and a background is its own pair (white capitals on a black bar). A colour set
    alone (the accent included) is read on white and on every background set alone (a card, a
    pill), since such a rule may hold any text."""
    found = template(slug)
    css = re.sub(r"/\*.*?\*/", "", found.files["style.css"], flags=re.S)
    css = css.replace("var(--accent)", found.accent)
    pairs: set[tuple[str, str]] = set()
    texts, surfaces = {found.accent}, {"#ffffff"}
    for _, body in RULE.findall(css):
        fore: set[str] = set()
        back: set[str] = set()
        for prop, value in re.findall(rf"(?<![-\w]){COLOUR_PROPERTIES}\s*:\s*([^;}}]+)", body):
            (fore if prop == "color" else back).update(colours(value))
        if fore and back:
            pairs |= {(f, b) for f in fore for b in back}
        else:
            texts |= fore
            surfaces |= back
    return pairs | {(t, s) for t in texts for s in surfaces}


@pytest.mark.parametrize("slug", D21)
def test_every_text_colour_reaches_4_5_to_1_on_what_it_is_painted_on(slug: str) -> None:
    pairs = painted_pairs(slug)
    assert len({t for t, _ in pairs}) >= 2, pairs
    low = {pair: round(contrast(*pair), 2) for pair in pairs}
    assert all(ratio >= 4.5 for ratio in low.values()), {k: v for k, v in low.items() if v < 4.5}


def test_the_contrast_pairs_read_a_rule_with_its_own_background_alone() -> None:
    pairs = painted_pairs("bold")
    assert ("#ffffff", "#16171b") in pairs
    assert ("#ffffff", "#ffffff") not in pairs
    assert ("#16171b", "#ffffff") in pairs
    assert ("#1c5d99", "#dce8f4") in painted_pairs("cards")


# The text of a CSS `content`, in double or in single quotes.
CONTENT = re.compile(r"""content:\s*(?:"([^"]*)"|'([^']*)')""")


def test_the_dash_test_reads_content_in_both_quotes() -> None:
    css = """a::before { content: "x \u2014 y"; } b::after { content: ' - '; }"""
    assert [a or b for a, b in CONTENT.findall(css)] == ["x \u2014 y", " - "]


@pytest.mark.parametrize("slug", D21)
def test_no_dash_is_printed_as_a_separator(slug: str) -> None:
    """The text a template prints of its own: its manifest's names and descriptions, the text of
    its CSS `content`, and the page it builds from the sample in both languages."""
    found = template(slug)
    own = [*found.name.values(), *found.description.values()]
    own += [a or b for a, b in CONTENT.findall(found.files["style.css"])]
    for kind in templates.KINDS:
        for language in templates.LANGUAGES:
            sample = thumbnails.sample()[kind]
            view = thumbnails.sample_view()
            html = templates.build_html(found, kind, sample, view, language, 0, ON)
            own.append(assert_authoring(html).body)
    for text in own:
        assert not any(dash in text for dash in DASHES), (slug, text[:120])


@pytest.mark.parametrize("slug", D21)
def test_the_tags_say_what_the_template_is(slug: str) -> None:
    found = template(slug)
    tags = set(found.tags)
    assert ("photo" in tags) == (found.photo != "none")
    assert ("no photo" in tags) == (found.photo == "none")
    assert ("two columns" in tags) == (slug == "cards")
    assert ("one column" in tags) == (slug != "cards")
    assert ("ats friendly" in tags) == (slug == "academic")
    serif = {"elegant": True, "bold": False, "cards": False, "academic": True}[slug]
    assert ("serif" in tags) == serif
    assert ("sans" in tags) == (not serif)


def normalised(found: templates.Template) -> str:
    """The CV template and the style without comments, colours and white space."""
    text = found.files["cv.html.j2"] + found.files["style.css"]
    text = re.sub(r"\{#.*?#\}|/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"#[0-9a-fA-F]{3,6}\b|rgba?\([^)]*\)", "", text)
    return "".join(text.split())


def ink(slug: str) -> Image.Image:
    with Image.open(templates.ROOT / slug / "thumbnail.png") as image:
        return image.convert("L").point(lambda value: 255 if value < 200 else 0)


@pytest.mark.parametrize("slug", D21)
def test_each_template_differs_in_layout_from_every_other(slug: str) -> None:
    """Not a recolour: the files differ once colours are taken out, and the CV thumbnails, reduced
    to ink and paper (any colour darker than a light grey is ink), differ on at least 5 % of their
    pixels from every other folder. Measured on 2026-10-19 with D20's four beside them: the
    closest pairs are `cards` and `accent` (10.3 %), `academic` and `cards` (10.4 %)."""
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


def pages_text(pdf: bytes) -> list[str]:
    """The text of each page without white space, case folded (the headings are set in
    capitals); a hyphen that ends a line read as one."""
    document = pdfium.PdfDocument(pdf)
    try:
        texts = [page.get_textpage().get_text_range() for page in document]
    finally:
        document.close()
    return ["".join(text.replace("\ufffe", "-").split()).casefold() for text in texts]


def fills(pdf: bytes, colour: tuple[int, int, int]) -> list[int]:
    """The page of every filled path of `colour`, one per box Chromium painted."""
    document = pdfium.PdfDocument(pdf)
    found = []
    try:
        for index, page in enumerate(document):
            for obj in page.get_objects():
                if obj.type != pdfium_c.FPDF_PAGEOBJ_PATH:
                    continue
                mode, stroke = ctypes.c_int(), ctypes.c_int()
                pdfium_c.FPDFPath_GetDrawMode(obj.raw, mode, stroke)
                rgba = [ctypes.c_uint() for _ in range(4)]
                pdfium_c.FPDFPageObj_GetFillColor(obj.raw, *rgba)
                if mode.value and tuple(v.value for v in rgba[:3]) == colour:
                    found.append(index)
    finally:
        document.close()
    return found


CARD = (0xEE, 0xF4, 0xFA)
HEAD_CARD = (0xDC, 0xE8, 0xF4)


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("name", ["cv-long", "cv-tightest"])
def test_cards_move_whole_to_the_next_page_and_are_never_cut(
    renderer: Any, language: str, name: str
) -> None:
    """At the loosest step these CVs take two pages: a card that would cross the end of page 1
    moves whole to page 2. Every card is painted once (a cut card is painted on both pages) and
    its text stands whole on one page."""
    _, _, content = d1(name)
    html = templates.build_html(template("cards"), "cv", content, d1_view(), language, 0, ON)
    rendered = renderer.render(html)
    assert rendered.pages == 2, rendered.pages
    texts = cards_of(html)
    painted = fills(rendered.pdf, CARD) + fills(rendered.pdf, HEAD_CARD)
    assert len(painted) == len(texts), (len(painted), len(texts))
    assert set(painted) == {0, 1}  # the case is real: cards stand on both pages
    pages = pages_text(rendered.pdf)
    for text in texts:
        assert sum(text.casefold() in page for page in pages) == 1, text[:60]


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_cards_fitted_long_cv_paints_each_card_once_on_one_page(
    renderer: Any, language: str
) -> None:
    _, _, content = d1("cv-long")
    found = template("cards")
    result = fitted(renderer, found, "cv", content, d1_view(), language)
    assert result.fit
    assert result.pages == 1
    html = templates.build_html(found, "cv", content, d1_view(), language, result.step, ON)
    painted = fills(result.pdf, CARD) + fills(result.pdf, HEAD_CARD)
    assert len(painted) == len(cards_of(html))


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("name", LONG_NAMES, ids=["words", "one-word", "the-cap"])
@pytest.mark.parametrize("with_photo", [False, True], ids=["no-photo", "photo"])
def test_bold_keeps_a_very_long_name_on_the_page(
    renderer: Any, language: str, name: str, with_photo: bool
) -> None:
    """The typical CV with a very long name: one page, the name whole in the PDF text, every one
    of its glyphs inside the page's margins (it wraps, it never runs off the page or under the
    photo), and the headline below its last line."""
    found = template("bold")
    _, _, content = d1("cv-typical")
    view = with_name(d1_view(), name)
    uri = a_photo() if with_photo else None

    def build(step: templates.Step) -> str:
        return templates.build_html(found, "cv", content, view, language, step, ON, photo=uri)

    async def render(html: str) -> Any:
        return await asyncio.to_thread(renderer.render, html)

    result = asyncio.run(fit.fit(found.steps("cv"), build, render=render))
    assert result.fit, result.pages
    step = found.steps("cv")[result.step]
    glyphs = Glyphs(result.pdf)
    boxes = glyphs.find(name)
    left, right = step.margin * MM, A4[0] - step.margin * MM
    if with_photo:
        right -= (30 + 6) * MM  # the photo and the gap of the heading
    assert min(box[0] for box in boxes) >= left - 0.5
    assert max(box[2] for box in boxes) <= right + 0.5, (max(box[2] for box in boxes), right)
    headline = glyphs.find(content.headline[:20])
    assert top(headline) < bottom(boxes)
    assert "".join(name.split()) in "".join(pdf_text(result.pdf).split())


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_bold_letter_with_a_very_long_name_fits(renderer: Any, language: str) -> None:
    kind, _, content = d1("letter-en")
    view = with_name(d1_view(), LONG_NAMES[2])
    result = fitted(renderer, template("bold"), kind, content, view, language)
    assert result.fit
    assert result.pages == 1


def heading(glyphs: Glyphs, label: str) -> list[Box]:
    """The occurrence of `label` set largest: the heading, not a word inside a sentence."""
    return max(glyphs.find_all(label), key=lambda boxes: top(boxes) - bottom(boxes))


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("who", ["three", "dana"])
def test_academic_with_three_schools_fits_with_the_education_above_the_experience(
    renderer: Any, language: str, who: str
) -> None:
    """Three schools (D1's typical CV with three, and D2's Dana, whose three are dated by month
    with a city and a country): one page, the Education heading above every school, every school
    above the Experience heading, in the order of the profile."""
    if who == "three":
        view, content = three_schools_view(language), d1("cv-typical")[2]
    else:
        view, content = view_of("dana"), c.CvContent.model_validate(written("cv_typical"))
    result = fitted(renderer, template("academic"), "cv", content, view, language)
    assert result.fit, result.pages
    assert result.pages == 1
    labels = templates.labels()[language]
    glyphs = Glyphs(result.pdf)
    education = heading(glyphs, labels["education"])
    experience = heading(glyphs, labels["experience"])
    degrees = [glyphs.find(school.degree) for school in view.schools]
    assert len(degrees) == 3
    assert bottom(education) > top(degrees[0])
    for upper, lower in pairwise(degrees):
        assert bottom(upper) > top(lower)
    assert bottom(degrees[-1]) > top(experience)


def typical(slug: str, full: bool) -> c.CvContent:
    """D1's typical CV, with every field filled to the template's soft limits when `full`."""
    content = d1("cv-typical")[2]
    return at_soft_limits(content, template(slug).kinds["cv"].soft_limits) if full else content


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("full", [False, True], ids=["typical", "soft-limits"])
def test_academic_reads_in_the_order_of_the_page(renderer: Any, language: str, full: bool) -> None:
    """The PDF text, as an applicant tracking system extracts it: the name first, the sections in
    the order of the page (the education before the experience), the roles in order, every
    bullet whole; on the typical CV and on the typical CV filled to the soft limits."""
    content = typical("academic", full)
    result = fitted(renderer, template("academic"), "cv", content, d1_view(), language)
    assert result.fit
    text = " ".join(pdf_text(result.pdf).replace("\ufffe", "-").split())
    assert text.startswith("Alex Example"), text[:60]
    labels = templates.labels()[language]
    places = [text.index(labels[key]) for key in ACADEMIC_ORDER]
    assert places == sorted(places), dict(zip(ACADEMIC_ORDER, places, strict=True))
    titles = [text.index(employer.title) for employer in d1_view().employers]
    assert titles == sorted(titles), titles
    squeezed = "".join(text.split())
    for role in content.experience:
        for bullet in role.bullets:
            assert "".join(bullet.split()) in squeezed, bullet[:60]


class Texts(HTMLParser):
    """The text nodes of a built page's body, in the order of the document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self._body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._body = self._body or tag == "body"

    def handle_data(self, data: str) -> None:
        if self._body and data.strip():
            self.texts.append("".join(data.split()).casefold())


@docker
@pytest.mark.parametrize("slug", D21)
@pytest.mark.parametrize("name", ["cv-typical", "cv-soft-limits", "letter-en"])
def test_the_pdf_text_follows_the_order_of_the_document(
    renderer: Any, slug: str, name: str
) -> None:
    """Every text of the built page is found in the PDF text in the order of the document: no
    part of the layout is painted, and so extracted, out of its place (a positioned marker once
    sent the competencies of `cards` and the bullets of `bold` to the end of the page)."""
    if name == "cv-soft-limits":
        kind, language, content = "cv", "en", typical(slug, full=True)
    else:
        kind, language, content = d1(name)
    found = template(slug)
    result = fitted(renderer, found, kind, content, d1_view(), language)
    assert result.fit
    html = templates.build_html(found, kind, content, d1_view(), language, result.step, ON)
    parser = Texts()
    parser.feed(html)
    text = pages_text(result.pdf)[0]
    at = 0
    for chunk in parser.texts:
        place = text.find(chunk, at)
        assert place >= 0, (chunk, text[at : at + 80])
        at = place + len(chunk)


LONG_CONTACT = Identity(
    full_name="Alex Example",
    email="e2e-alexandra.example-sampleton@example.org",
    phone="+33 6 00 00 00 00",
    city="Saint-Rémy-de-Provence",
    country="France",
    website="https://example.org/people/alexandra-example-sampleton/portfolio",
    links=(("Code", "https://code.example.org/alexandra-example-sampleton"),),
)


def heading_content(slug: str, kind: str) -> Any:
    """The CV filled to every soft limit, or D1's letter with its headline at the soft limit."""
    if kind == "cv":
        return typical(slug, full=True)
    letter = d1("letter-en")[2]
    limit = template(slug).kinds["letter"].soft_limits.headline or 90
    headline = (letter.headline + ", ") * 10
    return letter.model_copy(update={"headline": headline[:limit].rstrip(", ")})


def baselines(pdf: bytes, needle: str) -> list[float]:
    """The baseline of each character of the first `needle` of page 1 (white space and case
    ignored; a hyphen that ends a line read as one): one value when it stands on one line."""
    document = pdfium.PdfDocument(pdf)
    try:
        textpage = document[0].get_textpage()
        text = textpage.get_text_range().replace("\ufffe", "-")
        chars = [i for i, ch in enumerate(text) if not ch.isspace()]
        compact = "".join(text[i] for i in chars).casefold()
        target = "".join(needle.split()).casefold()
        start = compact.find(target)
        assert start >= 0, needle
        found = []
        for index in chars[start : start + len(target)]:
            x, y = ctypes.c_double(), ctypes.c_double()
            pdfium_c.FPDFText_GetCharOrigin(textpage.raw, index, x, y)
            found.append(y.value)
        return found
    finally:
        document.close()


@docker
@pytest.mark.parametrize("slug", D21)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("who", ["alex", "long"])
def test_the_heading_wraps_between_contact_lines_never_inside_one(
    renderer: Any, slug: str, kind: str, language: str, who: str
) -> None:
    """The review of D20: a header that could not wrap split the name and broke an e-mail
    address in two. Here the headline is at its soft limit (the CV filled to every soft limit),
    with D1's contact lines and with long ones (a 43 character address, a 64 character link):
    the document fits, the name stands on one line, and so does each contact line."""
    found = template(slug)
    view = d1_view()
    if who == "long":
        view = ProfileView(
            revision=view.revision,
            identity=LONG_CONTACT,
            employers=view.employers,
            schools=view.schools,
            languages=view.languages,
        )
    result = fitted(renderer, found, kind, heading_content(slug, kind), view, language)
    assert result.fit, result.pages
    for needle in ["Alex Example", *(line["text"] for line in templates.contact_lines(view))]:
        ys = baselines(result.pdf, str(needle))
        assert max(ys) - min(ys) < 1.0, (needle, sorted({round(y) for y in ys}))


def frames(pdf: bytes) -> list[list[float]]:
    """Per page, the bounds of the paths that span nearly the whole sheet."""
    document = pdfium.PdfDocument(pdf)
    try:
        out = []
        for page in document:
            spans = []
            for obj in page.get_objects():
                if obj.type == pdfium_c.FPDF_PAGEOBJ_PATH:
                    box = obj.get_bounds()
                    if box[2] - box[0] > A4[0] - 40 * MM and box[3] - box[1] > A4[1] - 40 * MM:
                        spans.append([round(v, 1) for v in box])
            out.append(spans)
        return out
    finally:
        document.close()


@docker
@pytest.mark.parametrize("kind", templates.KINDS)
def test_elegant_draws_its_frame_on_every_page(renderer: Any, kind: str) -> None:
    """The frame stands 6 mm inside the sheet's edge, on page 1 of a fitted document and on each
    page of a two page one (D1's long CV at the loosest step)."""
    found = template("elegant")
    name = "cv-long" if kind == "cv" else "letter-en"
    _, language, content = d1(name)
    loosest = renderer.render(
        templates.build_html(found, kind, content, d1_view(), language, 0, ON)
    )
    fitted_ = fitted(renderer, found, kind, content, d1_view(), language)
    assert fitted_.pages == 1
    assert loosest.pages == (2 if kind == "cv" else 1)
    expected = [6 * MM, 6 * MM, A4[0] - 6 * MM, A4[1] - 6 * MM]
    for pdf, pages in ((fitted_.pdf, 1), (loosest.pdf, loosest.pages)):
        per_page = frames(pdf)
        assert len(per_page) == pages
        for spans in per_page:
            assert 1 <= len(spans) <= 2, spans  # a double border: its two lines, or one path
            outer = min(spans)
            assert all(abs(a - b) <= 1.5 for a, b in zip(outer, expected, strict=True)), spans


# --- docker: a name breaks between its words ----------------------------------------------------

HYPHENATED = [
    "Marie-Christine Dupont-Aignan",  # 29 characters
    "Anne-Sophie de La Tour-Échantillon",  # 34 characters
    "Marie-Christine Exemplaire-Fontaine",  # 35 characters
]


@docker
@pytest.mark.parametrize("slug", D21)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("name", HYPHENATED)
def test_a_name_breaks_only_between_its_words(
    renderer: Any, slug: str, kind: str, language: str, name: str
) -> None:
    """The review of D21: a double-barrelled name printed `Dupont-` on one line and `Aignan` on
    the next. At the loosest step and at the fitted one, with the heading full (the CV at every
    soft limit, the letter's headline at its limit), each word of the name stands on one line."""
    found = template(slug)
    view = with_name(d1_view(), name)
    content = heading_content(slug, kind)
    loosest = renderer.render(templates.build_html(found, kind, content, view, language, 0, ON))
    result = fitted(renderer, found, kind, content, view, language)
    assert result.fit, result.pages
    for pdf in (loosest.pdf, result.pdf):
        ys = baselines(pdf, name)
        at = 0
        for word in name.split():
            line = ys[at : at + len(word)]
            at += len(word)
            assert max(line) - min(line) < 1.0, (word, sorted({round(y) for y in line}))


# --- docker: every glyph inside the page's frame ------------------------------------------------


def glyph_boxes(pdf: bytes) -> list[list[tuple[str, Box]]]:
    """Per page, every character that is drawn (not white space, not generated) with its box."""
    document = pdfium.PdfDocument(pdf)
    try:
        out = []
        for page in document:
            textpage = page.get_textpage()
            chars = []
            for index in range(textpage.count_chars()):
                char = chr(pdfium_c.FPDFText_GetUnicode(textpage.raw, index))
                if pdfium_c.FPDFText_IsGenerated(textpage.raw, index) or char.isspace():
                    continue
                chars.append((char, textpage.get_charbox(index)))
            out.append(chars)
        return out
    finally:
        document.close()


def two_page_letter() -> c.LetterContent:
    """D1's English letter with five paragraphs at the soft limit: two pages at the loosest step."""
    letter = d1("letter-en")[2]
    text = " ".join(letter.paragraphs) + " "
    paragraph = (text * (900 // len(text) + 1))[:900].rsplit(" ", 1)[0] + "."
    return letter.model_copy(update={"paragraphs": [paragraph] * 5})


@docker
@pytest.mark.parametrize("slug", D21)
@pytest.mark.parametrize("kind", templates.KINDS)
def test_every_glyph_of_every_page_stays_inside_the_frame(
    renderer: Any, slug: str, kind: str
) -> None:
    """A document of two pages at the loosest step: on each page, every glyph lies inside the
    page's margin, in `elegant` inside the inner edge of its frame (6 mm and a 2.8 pt border).
    The review of D21: `elegant`'s padding stood only at the top of page 1, and page 2's first
    line 3.2 mm from the paper's edge, across the frame."""
    found = template(slug)
    if kind == "cv":
        _, language, content = d1("cv-long")
    else:
        language, content = "en", two_page_letter()
    step = found.steps(kind)[0]
    rendered = renderer.render(
        templates.build_html(found, kind, content, d1_view(), language, 0, ON)
    )
    assert rendered.pages == 2, rendered.pages
    inset = (6 * MM + 2.8) if slug == "elegant" else step.margin * MM
    pages = glyph_boxes(rendered.pdf)
    assert len(pages) == 2
    if kind == "letter":  # page 1 starts the letter, it does not hold the heading alone
        assert "".join(content.salutation.split()).casefold() in pages_text(rendered.pdf)[0]
    # a glyph's ink may pass its advance by a fraction of a millimetre (an italic g, a figure)
    slack = 0.5 * MM
    low, high = inset - slack, (A4[0] - inset + slack, A4[1] - inset + slack)
    for number, chars in enumerate(pages, 1):
        assert chars, number
        for char, (x0, y0, x1, y1) in chars:
            inside = low <= x0 and low <= y0 and x1 <= high[0] and y1 <= high[1]
            assert inside, (number, char, [round(v / MM, 2) for v in (x0, y0, x1, y1)])
