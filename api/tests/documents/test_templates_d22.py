"""The edge cases of templates 17 to 20 (task D22), beside the table driven tests of
test_templates.py that every folder passes: `plain`, strict ats (no colour, no column, no photo,
the standard headings, its PDF text in the order of the page); `portrait` without a photo (the
initials in the frame, never an empty frame); `columns-header` with five contact lines in its
three columns; a long name and long contact lines in all four (a line wraps between contact
lines, never inside a name, an address or a link); a heading never left alone at a page's end;
the contrast of every text colour on what it is painted on, no dash printed as a separator,
truthful tags, and each of the four distinct in layout from every other template.
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
from app.documents.templates import thumbnails
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
    Glyphs,
    a_photo,
    bottom,
    colours,
    contrast,
    top,
)

D22 = ("nordic", "columns-header", "portrait", "plain")
DASHES = ("—", "–", " - ")
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


def test_plain_is_black_on_white_without_column_or_photo() -> None:
    found = template("plain")
    css = re.sub(r"/\*.*?\*/", "", found.files["style.css"], flags=re.S)
    painted = {
        colour_
        for _, value in re.findall(rf"(?<![-\w]){COLOUR_PROPERTIES}\s*:\s*([^;}}]+)", css)
        for colour_ in colours(value)
    }
    borders = set(re.findall(r"#[0-9a-fA-F]{6}\b", css))
    assert found.accent == "#000000"
    assert painted | borders == {"#000000"}, painted | borders
    for refused in ("grid", "column-count", "columns:", "float", "position", "background"):
        assert refused not in css, refused
    assert found.photo == "none"
    for kind in templates.KINDS:
        content = d1("cv-typical" if kind == "cv" else "letter-en")[2]
        html = templates.build_html(found, kind, content, d1_view(), "en", 0, ON, photo=a_photo())
        assert "<img" not in html


@pytest.mark.parametrize("language", templates.LANGUAGES)
def test_plain_prints_the_standard_headings_in_the_standard_order(language: str) -> None:
    _, _, content = d1("cv-typical")
    html = templates.build_html(template("plain"), "cv", content, d1_view(), language, 0, ON)
    labels = templates.labels()[language]
    assert headings(html) == [labels[key] for key in PLAIN_ORDER]
    body = assert_authoring(html).body
    assert ", ".join(content.competencies) in body


# --- static: portrait ---------------------------------------------------------------------------

FRAME = re.compile(r'<div class="frame( initials)?">(.*?)</div>', re.S)


@pytest.mark.parametrize(
    ("name", "initials"),
    [
        ("Alex Example", "AE"),
        ("alex example", "AE"),
        ("Alex", "A"),
        ("Émilie de la Fontaine", "ÉF"),
        ("  Alex   Example  ", "AE"),
        ("Øyvind Ålesund Ødegård", "ØØ"),
        ("<b>Alex</b> Example", "&lt;E"),
    ],
)
@pytest.mark.parametrize("kind", templates.KINDS)
def test_portrait_without_a_photo_puts_the_initials_in_the_frame(
    name: str, initials: str, kind: str
) -> None:
    content = d1("cv-typical" if kind == "cv" else "letter-en")[2]
    html = templates.build_html(
        template("portrait"), kind, content, named(d1_view(), name), "en", 0, ON
    )
    frames = FRAME.findall(html)
    assert frames == [(" initials", initials)], frames
    assert "<img" not in html


@pytest.mark.parametrize("kind", templates.KINDS)
def test_portrait_with_a_photo_puts_the_photo_in_the_frame_and_no_initials(kind: str) -> None:
    content = d1("cv-typical" if kind == "cv" else "letter-en")[2]
    uri = a_photo()
    html = templates.build_html(
        template("portrait"), kind, content, d1_view(), "en", 0, ON, photo=uri
    )
    frames = FRAME.findall(html)
    assert len(frames) == 1
    assert frames[0][0] == ""
    assert '<img class="photo"' in frames[0][1]


@pytest.mark.parametrize("name", ["", "   "])
def test_portrait_without_a_photo_or_a_name_draws_no_frame(name: str) -> None:
    html = templates.build_html(
        template("portrait"), "cv", d1("cv-typical")[2], named(d1_view(), name), "en", 0, ON
    )
    assert 'class="frame' not in html
    assert '<header class="head bare">' in html


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


def test_columns_header_prints_five_contact_lines_under_the_name() -> None:
    view = with_identity(d1_view(), LONG_CONTACT)
    lines = templates.contact_lines(view)
    assert len(lines) == 5
    html = templates.build_html(
        template("columns-header"), "cv", d1("cv-typical")[2], view, "en", 0, ON
    )
    body = assert_authoring(html).body
    assert body.index("Alex Example") < body.index(str(lines[0]["text"]))
    contact = re.search(r'<ul class="contact">(.*?)</ul>', html, re.S)
    assert contact
    assert contact.group(1).count("<li>") == 5


# --- static: every template of the set ----------------------------------------------------------

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def painted_pairs(slug: str) -> set[tuple[str, str]]:
    """Each text colour of the style with the backgrounds it is painted on. A rule that sets both
    a colour and a background is its own pair (a tag, the initials on their tint). A colour set
    alone (the accent included) is read on white and on every background set alone."""
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


@pytest.mark.parametrize("slug", D22)
def test_every_text_colour_reaches_4_5_to_1_on_what_it_is_painted_on(slug: str) -> None:
    pairs = painted_pairs(slug)
    assert len({t for t, _ in pairs}) >= (1 if slug == "plain" else 2), pairs
    low = {pair: round(contrast(*pair), 2) for pair in pairs}
    assert all(ratio >= 4.5 for ratio in low.values()), {k: v for k, v in low.items() if v < 4.5}


def test_the_contrast_pairs_read_a_rule_with_its_own_background() -> None:
    assert ("#4a6273", "#edf1ed") not in painted_pairs("nordic")  # a tag sets its own colour
    assert ("#2d3a42", "#edf1ed") in painted_pairs("nordic")
    assert ("#7a4a22", "#f3e9df") in painted_pairs("portrait")
    assert painted_pairs("plain") == {("#000000", "#ffffff")}


# The text of a CSS `content`, in double or in single quotes.
CONTENT = re.compile(r"""content:\s*(?:"([^"]*)"|'([^']*)')""")


def test_the_dash_test_reads_content_in_both_quotes() -> None:
    css = """a::before { content: "x — y"; } b::after { content: ' - '; }"""
    assert [a or b for a, b in CONTENT.findall(css)] == ["x — y", " - "]


@pytest.mark.parametrize("slug", D22)
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


@pytest.mark.parametrize("slug", D22)
def test_the_tags_say_what_the_template_is(slug: str) -> None:
    found = template(slug)
    tags = set(found.tags)
    assert ("photo" in tags) == (found.photo != "none")
    assert ("no photo" in tags) == (found.photo == "none")
    assert "two columns" not in tags
    assert "one column" in tags
    assert ("ats friendly" in tags) == (slug == "plain")
    assert ("colour" in tags) == (slug != "plain")
    assert ("serif" in tags) == (slug == "portrait")
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


def texts_of(html: str) -> list[str]:
    parser = Texts()
    parser.feed(html)
    return parser.texts


def typical(slug: str, full: bool) -> c.CvContent:
    """D1's typical CV, with every field filled to the template's soft limits when `full`."""
    content = d1("cv-typical")[2]
    return at_soft_limits(content, template(slug).kinds["cv"].soft_limits) if full else content


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("full", [False, True], ids=["typical", "soft-limits"])
def test_plain_reads_in_the_order_of_the_page(renderer: Any, language: str, full: bool) -> None:
    """The PDF text, as an applicant tracking system extracts it with pypdfium2: the name first,
    the headline and every contact line next, the sections in the standard order, the roles in
    order, every bullet whole; on the typical CV and on the typical CV filled to the soft
    limits."""
    content = typical("plain", full)
    result = fitted(renderer, template("plain"), "cv", content, d1_view(), language)
    assert result.fit
    text = " ".join(pdf_text(result.pdf).replace("￾", "-").split())
    assert text.startswith("Alex Example"), text[:60]
    squeezed = "".join(text.split())
    at = len("AlexExample")
    for line in [content.headline, *(ln["text"] for ln in templates.contact_lines(d1_view()))]:
        place = squeezed.find("".join(str(line).split()), at)
        assert place >= 0, line
        at = place
    labels = templates.labels()[language]
    # the headings are set in capitals, which no sentence of the fixtures is
    places = [text.index(labels[key].upper()) for key in PLAIN_ORDER]
    assert places == sorted(places), dict(zip(PLAIN_ORDER, places, strict=True))
    titles = [text.index(employer.title) for employer in d1_view().employers]
    assert titles == sorted(titles), titles
    for role in content.experience:
        for bullet in role.bullets:
            assert "".join(bullet.split()) in squeezed, bullet[:60]


@docker
@pytest.mark.parametrize("slug", D22)
@pytest.mark.parametrize("name", ["cv-typical", "cv-soft-limits", "letter-en"])
def test_the_pdf_text_follows_the_order_of_the_document(
    renderer: Any, slug: str, name: str
) -> None:
    """Every text of the built page is found in the PDF text in the order of the document: no
    part of the layout is painted, and so extracted, out of its place (the portrait's frame is
    placed left of the name by the grid, and still read after it)."""
    if name == "cv-soft-limits":
        kind, language, content = "cv", "en", typical(slug, full=True)
    else:
        kind, language, content = d1(name)
    found = template(slug)
    result = fitted(renderer, found, kind, content, d1_view(), language)
    assert result.fit
    html = templates.build_html(found, kind, content, d1_view(), language, result.step, ON)
    text = pages_text(result.pdf)[0]
    at = 0
    for chunk in texts_of(html):
        place = text.find(chunk, at)
        assert place >= 0, (chunk, text[at : at + 80])
        at = place + len(chunk)


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


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("kind", templates.KINDS)
def test_columns_header_sets_five_short_contact_lines_in_three_aligned_columns(
    renderer: Any, language: str, kind: str
) -> None:
    """Five short contact lines: three on the first row, two on the second, the second row's
    lines under the first two columns, each column 30 % of the width with gaps of 5 %."""
    found = template("columns-header")
    identity = Identity(
        full_name="Alex Example",
        email="e2e-alex@example.org",
        phone="+33 6 00 00 00 00",
        city="Lyon",
        country="France",
        website="https://example.org/alex",
        links=(("Code", "https://code.example.org/alex"),),
    )
    view = with_identity(d1_view(), identity)
    lines = [str(line["text"]) for line in templates.contact_lines(view)]
    assert len(lines) == 5
    content: Any = d1("cv-typical")[2] if kind == "cv" else d1("letter-en")[2]
    result = fitted(renderer, found, kind, content, view, language)
    assert result.fit
    step = found.steps(kind)[result.step]
    width = A4[0] - 2 * step.margin * MM
    spots = [lefts(result.pdf, line) for line in lines]
    rows = [round(y) for _, y in spots]
    assert rows[0] == rows[1] == rows[2] > rows[3] == rows[4], rows
    xs = [x for x, _ in spots]
    # a glyph's box starts up to 1.5 pt right of its pen position (its side bearing)
    assert abs(xs[3] - xs[0]) < 1.5
    assert abs(xs[4] - xs[1]) < 1.5
    for column, x in enumerate(xs[:3]):
        assert abs(x - (step.margin * MM + column * 0.35 * width)) < 1.5, (column, x)


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("kind", templates.KINDS)
def test_columns_header_moves_a_long_contact_line_whole_to_the_next_row(
    renderer: Any, language: str, kind: str
) -> None:
    """The five long lines take wider cells and the rows they need: each stays on one line
    (asserted above), and no two lines overlap on a row."""
    found = template("columns-header")
    view = with_identity(d1_view(), LONG_CONTACT)
    content: Any = d1("cv-typical")[2] if kind == "cv" else d1("letter-en")[2]
    result = fitted(renderer, found, kind, content, view, language)
    assert result.fit
    glyphs = Glyphs(result.pdf)
    boxes = [glyphs.find(str(line["text"])) for line in templates.contact_lines(view)]
    for i, one in enumerate(boxes):
        for other in boxes[i + 1 :]:
            same_row = abs(bottom(one) - bottom(other)) < 1.0
            apart = max(b[2] for b in one) < min(b[0] for b in other) or max(
                b[2] for b in other
            ) < min(b[0] for b in one)
            assert not same_row or apart


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
    if slug == "portrait":
        left += (36 + 7) * MM  # the frame and the gap of the header
    assert min(box[0] for box in boxes) >= left - 0.5, (min(box[0] for box in boxes), left)
    assert max(box[2] for box in boxes) <= right + 0.5, (max(box[2] for box in boxes), right)
    for word in (word for word in name.split() if len(word) > 3):
        ys = baselines(result.pdf, word)
        assert max(ys) - min(ys) < 1.0, word
    assert top(glyphs.find(content.headline[:20])) < bottom(boxes)
    assert "".join(name.split()) in "".join(pdf_text(result.pdf).split())


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


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("with_photo", [False, True], ids=["initials", "photo"])
def test_portrait_fills_its_frame_with_the_photo_or_the_initials(
    renderer: Any, language: str, kind: str, with_photo: bool
) -> None:
    """The frame at the top left, 36 mm square: with a photo, one image fills it and no initials
    are drawn; without, the initials are drawn inside it, centred, and there is no image."""
    found = template("portrait")
    content: Any = d1("cv-typical")[2] if kind == "cv" else d1("letter-en")[2]
    uri = a_photo() if with_photo else None

    def build(step: templates.Step) -> str:
        return templates.build_html(found, kind, content, d1_view(), language, step, ON, photo=uri)

    async def render(html: str) -> Any:
        return await asyncio.to_thread(renderer.render, html)

    result = asyncio.run(fit.fit(found.steps(kind), build, render=render))
    assert result.fit
    step = found.steps(kind)[result.step]
    frame = (step.margin * MM, A4[1] - step.margin * MM - 36 * MM)
    frame_box = (frame[0], frame[1], frame[0] + 36 * MM, frame[1] + 36 * MM)
    glyphs = Glyphs(result.pdf)
    found_images = images(result.pdf)
    initials = [
        boxes
        for boxes in glyphs.find_all("AE")
        if all(frame_box[0] <= b[0] and b[2] <= frame_box[2] for b in boxes)
    ]
    if with_photo:
        assert len(found_images) == 1
        left, low, right, high = found_images[0]
        assert abs(left - frame_box[0]) < 1.5
        assert abs(right - frame_box[2]) < 1.5
        assert high - low == pytest.approx(36 * MM, abs=1.5)
        assert not initials
    else:
        assert found_images == []
        assert len(initials) == 1
        boxes = initials[0]
        middle = (min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2
        assert middle == pytest.approx((frame_box[0] + frame_box[2]) / 2, abs=3)
        assert frame_box[1] < bottom(boxes) < top(boxes) < frame_box[3]


def chunk_pages(html: str, pdf: bytes) -> list[tuple[str, int]]:
    """Each text of the built page with the page it is found on, in the order of the document."""
    pages = pages_text(pdf)
    whole = "".join(pages)
    ends = [sum(len(p) for p in pages[: i + 1]) for i in range(len(pages))]
    out, at = [], 0
    for chunk in texts_of(html):
        place = whole.find(chunk, at)
        assert place >= 0, chunk
        out.append((chunk, next(i for i, end in enumerate(ends) if place < end)))
        at = place + len(chunk)
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
    placed = chunk_pages(html, rendered.pdf)
    assert {page for _, page in placed} == {0, 1}
    labels = {"".join(str(v).split()).casefold() for v in templates.labels()[language].values()}
    titles = {"".join(e.title.split()).casefold() for e in d1_view().employers}
    # a role's title prints with the comma that joins it to the organisation
    seen = 0
    for (chunk, page), (_, following) in pairwise(placed):
        if chunk in labels or chunk.rstrip(",") in titles:
            assert page == following, chunk
            seen += 1
    assert seen >= len(titles), seen
    if slug == "plain":
        by_key = {e.key: e for e in d1_view().employers}
        for role in content.experience:
            title = "".join(by_key[role.employer_key].title.split()).casefold()
            last = "".join(role.bullets[-1].split()).casefold()
            pages = {chunk.rstrip(","): page for chunk, page in reversed(placed)}
            assert pages[title] == pages[last], title


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
