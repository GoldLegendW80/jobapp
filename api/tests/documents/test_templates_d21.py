"""The edge cases of templates 13 to 16 (task D21), beside the table driven tests of
test_templates.py that every folder passes: `cards` with a section that would split across the
end of a page, `bold` with a very long name, `academic` with a profile of three schools (its
education printed before its experience), `elegant`'s frame on every page; the contrast of
every text colour on the backgrounds it is painted on, truthful tags, and each of the four
distinct in layout from every other template (not a recolour).
Marked `docker`: the same cases through the real renderer, with the geometry of the PDF
(pypdfium2 character boxes and path objects) and the text order of the one tagged ats friendly.
"""

import ctypes
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image, ImageChops

from app.documents import content as c
from app.documents import templates
from app.documents.profile_view import Identity, ProfileView, School
from tests.documents.test_templates import (
    ON,
    SLUGS,
    at_soft_limits,
    d1,
    d1_view,
    fitted,
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
from tests.documents.test_templates_d22 import assert_read_in_order

D21 = tuple(sorted(templates.registry()))  # D27: the three
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


def in_words(name: str) -> str:
    return " ".join(f'<span class="word">{word}</span>' for word in name.split())


@pytest.mark.parametrize("slug", D21)
def test_each_word_of_the_name_is_a_box_of_its_own(slug: str) -> None:
    """A box per word (`.word`, inline-block) moves a word whole to the next line: the line breaks
    at a space, never at the hyphen of a double-barrelled name."""
    name = "Anne-Sophie de La Tour-Échantillon"
    view = with_name(d1_view(), name)
    for kind, data in (("cv", d1("cv-typical")[2]), ("letter", d1("letter-en")[2])):
        html = templates.build_html(template(slug), kind, data, view, "en", 0, ON)
        assert re.search(rf'<h1 class="name[^"]*">{re.escape(in_words(name))}</h1>', html), kind
    assert re.search(
        r"\.word\s*\{\s*display:\s*inline-block;\s*max-width:\s*100%;",
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
        if re.search(r"(?<![-\w])content\s*:\s*(\"\"|'')", body):
            continue  # an empty box (a bullet dot) paints no text: not a surface for text
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


def test_the_contrast_pairs_read_a_rule_with_its_own_background() -> None:
    assert ("#f8f8f8", "#262525") in painted_pairs("band")  # the band sets both
    # the round bullets of band and photo-columns are empty boxes: no text is read on them
    assert ("#262525", "#262525") not in painted_pairs("band")
    assert ("#07113c", "#07113c") not in painted_pairs("photo-columns")
    assert {text for text, _ in painted_pairs("ledger")} == {"#000000"}


@pytest.mark.parametrize("slug", D21)
def test_every_text_colour_reaches_4_5_to_1_on_what_it_is_painted_on(slug: str) -> None:
    pairs = painted_pairs(slug)
    assert {t for t, _ in pairs}, pairs
    low = {pair: round(contrast(*pair), 2) for pair in pairs}
    assert all(ratio >= 4.5 for ratio in low.values()), {k: v for k, v in low.items() if v < 4.5}


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


def heading(glyphs: Glyphs, label: str) -> list[Box]:
    """The occurrence of `label` set largest: the heading, not a word inside a sentence."""
    return max(glyphs.find_all(label), key=lambda boxes: top(boxes) - bottom(boxes))


def typical(slug: str, full: bool) -> c.CvContent:
    """D1's typical CV, with every field filled to the template's soft limits when `full`."""
    content = d1("cv-typical")[2]
    return at_soft_limits(content, template(slug).kinds["cv"].soft_limits) if full else content


@docker
@pytest.mark.parametrize("slug", D21)
@pytest.mark.parametrize("name", ["cv-typical", "cv-soft-limits", "letter-en"])
def test_the_pdf_text_follows_the_order_of_the_document(
    renderer: Any, slug: str, name: str
) -> None:
    """The PDF text read as the layout lays it out (D27): the name first, every word in it, and
    each block's own words in their order (a positioned marker once sent the competencies of
    `cards` to the end of the page)."""
    if name == "cv-soft-limits":
        kind, language, content = "cv", "en", typical(slug, full=True)
    else:
        kind, language, content = d1(name)
    found = template(slug)
    result = fitted(renderer, found, kind, content, d1_view(), language)
    assert result.fit
    html = templates.build_html(found, kind, content, d1_view(), language, result.step, ON)
    assert_read_in_order(html, pages_text(result.pdf)[0], d1_view().identity.full_name)


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


# --- docker: a name breaks between its words ----------------------------------------------------

HYPHENATED = [
    "Marie-Christine Dupont-Aignan",  # 29 characters
    "Anne-Sophie de La Tour-Échantillon",  # 34 characters
    "Marie-Christine Exemplaire-Fontaine",  # 35 characters
    "Alexandra Sampleton-Whitfield",  # 29 characters, one hyphen
    "Jean-Baptiste Exemplaire",  # 24 characters, two lines in bold's letter
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
    inset = step.margin * MM
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
