"""The edge cases of templates 17 to 20 (task D22), beside the table driven tests of
test_templates.py that every folder passes: `plain`, strict ats (no colour, no column, no photo,
the standard headings, its PDF text in the order of the page); `portrait` without a photo (the
initials in the frame, never an empty frame); `columns-header` with five contact lines in its
three columns; a long name and long contact lines in all four (a line wraps between contact
lines, never inside a name, an address or a link); a heading never left alone at a page's end;
the contrast of every text colour on what it is painted on, truthful tags, and each of the four
distinct in layout from every other template.
Marked `docker`: the same cases through the real renderer, with the geometry of the PDF
(pypdfium2 character boxes and image objects).
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
from app.documents.profile_view import Identity, ProfileView
from tests.documents.test_templates import (
    ON,
    SLUGS,
    at_soft_limits,
    d1,
    d1_view,
    fitted,
    pdf_text,
    template,
)
from tests.documents.test_templates import renderer as renderer
from tests.documents.test_templates_d19 import (
    Glyphs,
    a_photo,
    bottom,
    top,
)

D22 = tuple(sorted(templates.registry()))  # D27: the three
MM = 72 / 25.4  # points in a millimetre
A4 = (210 * MM, 297 * MM)

# The standard order of an ats CV, the one `plain` prints.
PLAIN_ORDER = (
    "summary",
    "highlights",
    "competencies",
    "experience",
    "education",
    "skills",
    "languages",
)


def headings(html: str) -> list[str]:
    return [unescape(h) for h in re.findall(r'<h2 class="label">(.*?)</h2>', html)]


def with_identity(view: ProfileView, identity: Identity) -> ProfileView:
    return ProfileView(
        revision=view.revision,
        identity=identity,
        employers=view.employers,
        schools=view.schools,
        languages=view.languages,
    )


def named(view: ProfileView, name: str) -> ProfileView:
    old = view.identity
    return with_identity(
        view,
        Identity(
            full_name=name,
            email=old.email,
            phone=old.phone,
            city=old.city,
            country=old.country,
            website=old.website,
            links=old.links,
        ),
    )


# --- static: plain ------------------------------------------------------------------------------


# --- static: portrait ---------------------------------------------------------------------------

FRAME = re.compile(r'<div class="frame( initials)?">(.*?)</div>', re.S)


# --- static: columns-header ---------------------------------------------------------------------

LONG_CONTACT = Identity(
    full_name="Alex Example",
    email="e2e-alexandra.example-sampleton@example.org",
    phone="+33 6 00 00 00 00",
    city="Saint-Rémy-de-Provence",
    country="France",
    website="https://example.org/people/alexandra-example-sampleton/portfolio",
    links=(("Code", "https://code.example.org/alexandra-example-sampleton"),),
)


# --- static: every template of the set ----------------------------------------------------------


@pytest.mark.parametrize("slug", D22)
def test_the_tags_say_what_the_template_is(slug: str) -> None:
    found = template(slug)
    tags = set(found.tags)
    assert ("photo" in tags) == (found.photo != "none")
    assert ("no photo" in tags) == (found.photo == "none")
    columns = "two columns" if slug == "photo-columns" else "one column"
    assert columns in tags
    assert len(tags & {"one column", "two columns"}) == 1
    assert ("ats friendly" in tags) == (slug != "photo-columns")  # two columns read out of order
    assert "colour" not in tags  # black, grey and one navy: no colour of its own
    assert "serif" not in tags
    assert "sans" in tags


def normalised(found: templates.Template) -> str:
    """The CV template and the style without comments, colours and white space."""
    text = found.files["cv.html.j2"] + found.files["style.css"]
    text = re.sub(r"\{#.*?#\}|/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"#[0-9a-fA-F]{3,6}\b|rgba?\([^)]*\)", "", text)
    return "".join(text.split())


def ink(slug: str) -> Image.Image:
    with Image.open(templates.ROOT / slug / "thumbnail.png") as image:
        return image.convert("L").point(lambda value: 255 if value < 200 else 0)


@pytest.mark.parametrize("slug", D22)
def test_each_template_differs_in_layout_from_every_other(slug: str) -> None:
    """Not a recolour: the files differ once colours are taken out, and the CV thumbnails, reduced
    to ink and paper (any colour darker than a light grey is ink), differ on at least 5 % of their
    pixels from every other folder."""
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
    """The text of each page without white space, case folded (headings are set in capitals);
    a hyphen that ends a line read as one."""
    document = pdfium.PdfDocument(pdf)
    try:
        texts = [page.get_textpage().get_text_range() for page in document]
    finally:
        document.close()
    return ["".join(text.replace("￾", "-").split()).casefold() for text in texts]


# A block of the page: a heading, a paragraph, a list item, a section. The PDF text follows each
# layout's own lines (D27, the orchestrator's decision): band's label column and body column, and
# photo-columns' two columns, are read line by line side by side, so two blocks may interleave;
# the words of one block never leave their order.
BLOCKS = frozenset(("h1", "h2", "h3", "p", "li", "section", "header", "div"))


def words_of(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.replace("\ufffe", "-").casefold()) if w]


def in_order(flat: str, words: list[str]) -> bool:
    at = 0
    for word in words:
        at = flat.find(word, at)
        if at < 0:
            return False
        at += len(word)
    return True


def words_in(html: str, slug: str) -> list[list[str]]:
    """The words of each text of texts_of(html, slug), in the same order."""
    parser = Texts()
    parser.feed(html)
    by_text = dict(zip(parser.texts, parser.words, strict=False))
    return [by_text[text] for text in texts_of(html, slug)]


def assert_read_in_order(html: str, pdf_text: str, name: str) -> None:
    """What an applicant tracking system reads: the name first; every word of the built page in
    the PDF text; within each block (a heading, a line, a bullet), its words in order."""
    parser = Texts()
    parser.feed(html)
    flat = "".join(words_of(pdf_text))
    assert flat.startswith("".join(name.split()).casefold()), flat[:60]
    by_block: dict[int, list[str]] = {}
    for words, block in zip(parser.words, parser.blocks, strict=True):
        by_block.setdefault(block, []).extend(words)
    for words in by_block.values():
        at = 0
        for word in words:  # a word, not the whole block: a two line label wraps beside the body
            place = flat.find(word, at)
            assert place >= 0, (word, " ".join(words)[:80], flat[at : at + 80])
            at = place + len(word)


# The order each template prints its contact lines in (CSS `order`, the original's: the owner's
# look, D27), which the PDF text follows rather than the order of the document.
CONTACT_ORDER = {
    "band": ("phone", "email", "place", "link"),
    "photo-columns": ("email", "phone", "link", "place"),
    "ledger": ("phone", "email", "place", "link"),
}


class Texts(HTMLParser):
    """The text nodes of a built page's body, in the order of the document, each with the kind of
    the contact line it belongs to (None outside the contact lines)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.kinds: list[str | None] = []
        self.blocks: list[int] = []
        self.words: list[list[str]] = []
        self._body = False
        self._kind: str | None = None
        self._block = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._body = self._body or tag == "body"
        if tag in BLOCKS:
            self._block += 1
        if tag == "li":
            kind = dict(attrs).get("class") or ""
            self._kind = kind if kind in ("email", "phone", "place", "link") else None

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCKS:
            self._block += 1
        if tag == "li":
            self._kind = None

    def handle_data(self, data: str) -> None:
        if self._body and data.strip():
            self.texts.append("".join(data.split()).casefold())
            self.words.append(words_of(data))
            self.kinds.append(self._kind)
            self.blocks.append(self._block)


def texts_of(html: str, slug: str | None = None) -> list[str]:
    """The texts in the order the page prints them: the document's, but for the contact lines,
    which take the order of CONTACT_ORDER[slug] (a sort that keeps two links in their order)."""
    parser = Texts()
    parser.feed(html)
    if slug is None:
        return parser.texts
    rank = {kind: index for index, kind in enumerate(CONTACT_ORDER[slug])}
    lines = [i for i, kind in enumerate(parser.kinds) if kind is not None]
    ordered = sorted(lines, key=lambda i: rank[parser.kinds[i] or ""])
    texts = list(parser.texts)
    for place, index in zip(lines, ordered, strict=True):
        texts[place] = parser.texts[index]
    return texts


def test_the_contact_order_of_each_template_is_its_css_order() -> None:
    """The table is what each style sheet says, so a change of either shows here."""
    assert set(CONTACT_ORDER) == set(templates.registry())
    for slug, kinds in CONTACT_ORDER.items():
        css = template(slug).files["style.css"]
        declared = {
            kind: int(n) for kind, n in re.findall(r"\.contact \.(\w+) \{ order: (\d+); \}", css)
        }
        assert tuple(sorted(declared, key=declared.__getitem__)) == kinds, slug


def typical(slug: str, full: bool) -> c.CvContent:
    """D1's typical CV, with every field filled to the template's soft limits when `full`."""
    content = d1("cv-typical")[2]
    return at_soft_limits(content, template(slug).kinds["cv"].soft_limits) if full else content


@docker
@pytest.mark.parametrize("slug", D22)
@pytest.mark.parametrize("name", ["cv-typical", "cv-soft-limits", "letter-en"])
def test_the_pdf_text_follows_the_order_of_the_document(
    renderer: Any, slug: str, name: str
) -> None:
    """The PDF text read as the layout lays it out (D27): the name first, every word of the built
    page in it, and each block's own words in their order; two blocks side by side (a label
    column, two columns) may interleave line by line."""
    if name == "cv-soft-limits":
        kind, language, content = "cv", "en", typical(slug, full=True)
    else:
        kind, language, content = d1(name)
    found = template(slug)
    result = fitted(renderer, found, kind, content, d1_view(), language)
    assert result.fit
    html = templates.build_html(found, kind, content, d1_view(), language, result.step, ON)
    assert_read_in_order(html, pages_text(result.pdf)[0], d1_view().identity.full_name)


def baselines(pdf: bytes, needle: str) -> list[float]:
    """The baseline of each character of the first `needle` of page 1 (white space and case
    ignored; a hyphen that ends a line read as one): one value when it stands on one line."""
    document = pdfium.PdfDocument(pdf)
    try:
        textpage = document[0].get_textpage()
        text = textpage.get_text_range().replace("￾", "-")
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


def letter_at_its_limit(found: templates.Template) -> c.LetterContent:
    letter = d1("letter-en")[2]
    limit = found.kinds["letter"].soft_limits.headline or 90
    headline = (letter.headline + ", ") * 10
    return letter.model_copy(update={"headline": headline[:limit].rstrip(", ")})


@docker
@pytest.mark.parametrize("slug", D22)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("who", ["alex", "long"])
@pytest.mark.parametrize("step", ["fitted", "loosest"])
def test_the_heading_wraps_between_contact_lines_never_inside_one(
    renderer: Any, slug: str, kind: str, language: str, who: str, step: str
) -> None:
    """The review of D20: a header that could not wrap split the name and broke an e-mail
    address in two. Here the headline is at its soft limit (the CV filled to every soft limit),
    with D1's contact lines and with five long ones (a 43 character address, a 64 character
    link), at the fitted step and at the loosest: the name stands on one line, and so does
    each contact line."""
    found = template(slug)
    view = d1_view() if who == "alex" else with_identity(d1_view(), LONG_CONTACT)
    content: Any = typical(slug, full=True) if kind == "cv" else letter_at_its_limit(found)
    if step == "fitted":
        result = fitted(renderer, found, kind, content, view, language)
        assert result.fit, result.pages
        pdf = result.pdf
    else:
        pdf = renderer.render(templates.build_html(found, kind, content, view, language, 0, ON)).pdf
    for needle in ["Alex Example", *(line["text"] for line in templates.contact_lines(view))]:
        ys = baselines(pdf, str(needle))
        assert max(ys) - min(ys) < 1.0, (needle, sorted({round(y) for y in ys}))


def lefts(pdf: bytes, needle: str) -> tuple[float, float]:
    """The left edge and the baseline of the first character of `needle` on page 1."""
    boxes = Glyphs(pdf).find(needle)
    return boxes[0][0], baselines(pdf, needle)[0]


LONG_NAMES = [
    "Maximiliane Alexandrine Exemplaire de la Fontaine Échantillon",  # 61 characters
    "Exemplairissimeauxlongsnoms Example",  # a word of 27 letters
]


@docker
@pytest.mark.parametrize("slug", D22)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("name", LONG_NAMES, ids=["words", "long-word"])
@pytest.mark.parametrize("with_photo", [False, True], ids=["no-photo", "photo"])
def test_a_long_name_stays_on_the_page_and_whole(
    renderer: Any, slug: str, language: str, name: str, with_photo: bool
) -> None:
    """The typical CV with a long name: one page, the name whole in the PDF text, every one of
    its glyphs inside the margins and right of the portrait's frame, no word of it cut, and the
    headline below its last line."""
    found = template(slug)
    if with_photo and found.photo == "none":
        pytest.skip("no photo slot")
    _, _, content = d1("cv-typical")
    view = named(d1_view(), name)
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
    assert min(box[0] for box in boxes) >= left - 0.5, (min(box[0] for box in boxes), left)
    assert max(box[2] for box in boxes) <= right + 0.5, (max(box[2] for box in boxes), right)
    for word in (word for word in name.split() if len(word) > 3):
        ys = baselines(result.pdf, word)
        assert max(ys) - min(ys) < 1.0, word
    assert top(glyphs.find(content.headline[:20])) < bottom(boxes)
    assert "".join(name.split()).casefold() in "".join(pdf_text(result.pdf).split()).casefold()


def images(pdf: bytes) -> list[tuple[float, float, float, float]]:
    document = pdfium.PdfDocument(pdf)
    try:
        return [
            obj.get_bounds()
            for obj in document[0].get_objects()
            if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE
        ]
    finally:
        document.close()


def chunk_pages(html: str, pdf: bytes, slug: str) -> list[tuple[str, set[int]]]:
    """Each text of the built page, in the order of the document, with the pages that hold its
    words in their order. No order across texts is assumed: the PDF text follows each layout's
    own lines (D27), so a label wrapped beside its body is read between the body's words."""
    pages = pages_text(pdf)
    out = []
    for chunk, words in zip(texts_of(html, slug), words_in(html, slug), strict=True):
        found = {number for number, page in enumerate(pages) if in_order(page, words)}
        assert found, chunk
        out.append((chunk, found))
    return out


@docker
@pytest.mark.parametrize("slug", D22)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("name", ["cv-long", "cv-tightest"])
def test_a_page_end_never_leaves_a_heading_or_a_role_title_alone(
    renderer: Any, slug: str, language: str, name: str
) -> None:
    """At the loosest step these CVs take two pages. Each section heading and each role's title
    stands on the page of the text that follows it; in `plain` each role is kept whole on one
    page."""
    _, _, content = d1(name)
    found = template(slug)
    html = templates.build_html(found, "cv", content, d1_view(), language, 0, ON)
    rendered = renderer.render(html)
    assert rendered.pages == 2, rendered.pages
    placed = chunk_pages(html, rendered.pdf, slug)
    assert set().union(*(pages for _, pages in placed)) == {0, 1}
    shared = templates.labels()[language] | found.labels.get(language, {})
    labels = {"".join(str(v).split()).casefold() for v in shared.values()}
    titles = {"".join(e.title.split()).casefold() for e in d1_view().employers}
    # a role's title prints with the comma that joins it to the organisation
    seen = 0
    for (chunk, pages), (_, following) in pairwise(placed):
        if chunk in labels or chunk.rstrip(",") in titles:
            assert pages & following, (chunk, pages, following)  # on a page with what follows it
            seen += 1
    assert seen >= len(titles), seen


HYPHENATED = [
    "Marie-Christine Dupont-Aignan",
    "Jean-Baptiste Exemplaire-Échantillon de la Fontaine-Rivière",
]


@docker
@pytest.mark.parametrize("slug", D22)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("name", HYPHENATED, ids=["short", "long"])
@pytest.mark.parametrize("step", ["fitted", "loosest"])
def test_a_double_barrelled_name_never_breaks_at_its_hyphen(
    renderer: Any, slug: str, kind: str, language: str, name: str, step: str
) -> None:
    """The review of D21: "Dupont-Aignan" printed as "Dupont-" then "Aignan". Each word of the
    name, in the heading and in a letter's signature, stands on one line, inside the margins."""
    found = template(slug)
    view = named(d1_view(), name)
    content: Any = d1("cv-typical")[2] if kind == "cv" else d1("letter-en")[2]
    if step == "fitted":
        result = fitted(renderer, found, kind, content, view, language)
        assert result.fit, result.pages
        pdf, at = result.pdf, found.steps(kind)[result.step]
    else:
        at = found.steps(kind)[0]
        pdf = renderer.render(
            templates.build_html(found, kind, content, view, language, at, ON)
        ).pdf
    glyphs = Glyphs(pdf)
    for occurrence in glyphs.find_all(name):
        assert min(b[0] for b in occurrence) >= at.margin * MM - 0.5
        assert max(b[2] for b in occurrence) <= A4[0] - at.margin * MM + 0.5
    assert glyphs.find_all(name), name
    for word in name.split():
        if "-" in word:
            ys = baselines(pdf, word)
            assert max(ys) - min(ys) < 1.0, (word, sorted({round(y) for y in ys}))


def two_page_letter() -> c.LetterContent:
    letter = d1("letter-en")[2]
    text = " ".join(["I ran the deployment platform and wrote its operating guides."] * 28)
    return letter.model_copy(update={"paragraphs": [text[: c.PARAGRAPH_MAX]] * 3})


def every_glyph(pdf: bytes) -> list[list[tuple[float, float, float, float]]]:
    """Per page, the box of every character that is not white space."""
    document = pdfium.PdfDocument(pdf)
    try:
        out = []
        for page in document:
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            out.append([textpage.get_charbox(i) for i, ch in enumerate(text) if not ch.isspace()])
        return out
    finally:
        document.close()


@docker
@pytest.mark.parametrize("slug", D22)
@pytest.mark.parametrize("kind", templates.KINDS)
def test_every_glyph_of_every_page_stands_inside_the_page_margins(
    renderer: Any, slug: str, kind: str
) -> None:
    """The review of D21: with a page margin of 0 the body's padding applied once and page 2's
    first line touched the top edge. Here a two page CV (D1's long one) and a two page letter at
    the loosest step: every glyph of every page lies inside the step's margins."""
    found = template(slug)
    content: Any = d1("cv-long")[2] if kind == "cv" else two_page_letter()
    step = found.steps(kind)[0]
    rendered = renderer.render(templates.build_html(found, kind, content, d1_view(), "en", 0, ON))
    assert rendered.pages == 2, rendered.pages
    # 1 pt for the ink of a glyph that overhangs its advance (an italic, a justified line)
    low, high = (
        step.margin * MM - 1,
        (A4[0] - step.margin * MM + 1, A4[1] - step.margin * MM + 1),
    )
    for number, boxes in enumerate(every_glyph(rendered.pdf)):
        assert boxes, number
        assert min(b[0] for b in boxes) >= low, (number, min(b[0] for b in boxes))
        assert min(b[1] for b in boxes) >= low, (number, min(b[1] for b in boxes))
        assert max(b[2] for b in boxes) <= high[0], (number, max(b[2] for b in boxes))
        assert max(b[3] for b in boxes) <= high[1], (number, max(b[3] for b in boxes))
