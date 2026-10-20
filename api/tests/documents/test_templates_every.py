"""What every bundled template must do, whatever its set (D2, D19 to D22): print no dash as a
separator, set each word of the name as a box of its own (a no break space joins two words into
one), and say "ats friendly" in the same words in both languages. Marked `docker`: through the
real renderer, CV and letter, English and French, at the loosest step, the fitted one and the
tightest, no name, address or link is broken inside a word; and on a document of two pages every
glyph of every page stands inside the page's frame.
"""

import ctypes
import dataclasses
import re
from html import escape
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest

from app.documents import content as c
from app.documents import templates
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
    template,
)
from tests.documents.test_templates import renderer as renderer
from tests.documents.test_templates_d20 import css_content_strings

DASHES = ("—", "–", " - ")
MM = 72 / 25.4  # points in a millimetre
A4 = (210 * MM, 297 * MM)
NBSP = " "

# Hyphens at a line's reach, and a no break space that joins "de la" into one word.
NAME = f"Jean-Baptiste Exemplaire-Échantillon de{NBSP}la Fontaine-Rivière"
LONG_CONTACT = Identity(
    full_name=NAME,
    email="e2e-alexandra.example-sampleton@example.org",  # 43 characters
    phone="+33 6 00 00 00 00",
    city="Saint-Rémy-de-Provence",
    country="France",
    website="https://example.org/people/alexandra-example-sampleton/portfolio",
    links=(("Code", "https://code.example.org/alexandra-example-sampleton"),),
)
# By the longest contact line: a 43 character address beside a 56 character link, then the
# lengths where a narrow contact column changes its setting (sidebar, split-right): 25 and 26
# characters, 31 and 32 (in sidebar's letter 23 and 24, 28 and 29); wide letters (m, w) and a
# place with a space and hyphens.
CONTACTS = {
    "56": LONG_CONTACT,
    "32": dataclasses.replace(
        LONG_CONTACT,
        email="e2e-william.mwangimw@example.org",
        website="https://mwangi.example.org",
        links=(),
    ),
    "31": dataclasses.replace(
        LONG_CONTACT,
        email="e2e-william.mwangim@example.org",
        website="https://mwangi.example.org",
        links=(),
    ),
    "28": Identity(
        full_name=NAME,
        email="e2e-wim.mwangimw@example.org",
        phone="+33 6 00 00 00 00",
        city="Saint-Rémy-de-Provence",
    ),
    "26": Identity(
        full_name=NAME,
        email="e2e-maxime.wum@example.org",
        phone="+33 6 00 00 00 00",
        city="Saint-Rémy, France",
        website="https://wm.example.org/maxime",
    ),
    "25": Identity(full_name=NAME, email="e2e-maxime.wu@example.org", city="Saint-Malo"),
    "23": Identity(full_name=NAME, email="e2e-mwmw.wu@example.org", phone="+33 6 00 00 00 00"),
}


def with_identity(view: ProfileView, identity: Identity) -> ProfileView:
    return ProfileView(
        revision=view.revision,
        identity=identity,
        employers=view.employers,
        schools=view.schools,
        languages=view.languages,
    )


def words(name: str) -> list[str]:
    """The words of a name as the templates set them: split at a space, never at a no break
    space."""
    return [word for word in name.split(" ") if word]


# --- static -------------------------------------------------------------------------------------


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


WORD_RULE = re.compile(r"\.(w|word)\s*\{[^}]*display:\s*inline-block;[^}]*max-width:\s*100%")


@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("slug", SLUGS)
def test_each_word_of_the_name_is_a_box_of_its_own(slug: str, kind: str) -> None:
    """Wherever the page prints the name (the heading, a letter's signature), each word is an
    inline block, so a line breaks between two words and never at the hyphen of a
    double-barrelled one; a no break space stays inside its word's box."""
    found = template(slug)
    rule = WORD_RULE.search(found.files["style.css"])
    assert rule, slug
    view = with_identity(d1_view(), LONG_CONTACT)
    content: Any = d1("cv-typical")[2] if kind == "cv" else d1("letter-en")[2]
    html = templates.build_html(found, kind, content, view, "en", 0, ON)
    body = html[html.index("<body") :]
    body = re.sub(r'\salt="[^"]*"', "", body)
    boxes = " ".join(f'<span class="{rule[1]}">{escape(word)}</span>' for word in words(NAME))
    assert boxes in body, slug
    assert escape(NAME) not in body, "the name printed in one run"
    if kind == "letter":
        assert body.count(boxes) == 2, "the heading and the signature"


@pytest.mark.parametrize("slug", SLUGS)
def test_a_description_says_ats_friendly_in_the_same_words(slug: str) -> None:
    """A description that names applicant tracking systems says so in one wording in French,
    "Bien lu par ... les logiciels de recrutement", and only on an ats friendly template."""
    found = template(slug)
    english, french = found.description["en"], found.description["fr"]
    named = "applicant tracking systems" in english
    assert named == ("logiciel" in french), (english, french)
    if named:
        assert "ats friendly" in found.tags
        assert "Bien lu par " in french, french
        assert french.endswith("les logiciels de recrutement."), french


# --- docker: through the real renderer ----------------------------------------------------------

docker = pytest.mark.docker


def lines_of(pdf: bytes, needle: str) -> list[list[float]]:
    """For each place `needle` is printed on page 1 (white space and case ignored, a hyphen that
    ends a line read as one), the baseline of each of its characters."""
    document = pdfium.PdfDocument(pdf)
    try:
        textpage = document[0].get_textpage()
        text = textpage.get_text_range().replace("￾", "-")
        chars = [i for i, ch in enumerate(text) if not ch.isspace()]
        compact = "".join(text[i] for i in chars).casefold()
        target = "".join(needle.split()).casefold()
        found, start = [], compact.find(target)
        while start >= 0:
            ys = []
            for index in chars[start : start + len(target)]:
                x, y = ctypes.c_double(), ctypes.c_double()
                pdfium_c.FPDFText_GetCharOrigin(textpage.raw, index, x, y)
                ys.append(y.value)
            found.append(ys)
            start = compact.find(target, start + len(target))
        return found
    finally:
        document.close()


def assert_whole(pdf: bytes, view: ProfileView, where: str) -> None:
    """Each word of the name and each contact line, wherever page 1 prints them, on one line."""
    name = view.identity.full_name
    printed = lines_of(pdf, name)
    assert printed, (where, "the name is not printed whole")
    for ys in printed:
        at = 0
        for word in words(name):
            size = len("".join(word.split()))
            line = ys[at : at + size]
            at += size
            assert max(line) - min(line) < 1.0, (where, word, sorted({round(y) for y in line}))
    for contact in templates.contact_lines(view):
        printed = lines_of(pdf, str(contact["text"]))
        assert printed, (where, contact["text"])
        for ys in printed:
            assert max(ys) - min(ys) < 1.0, (where, contact["text"], sorted({round(y) for y in ys}))


def full_heading(found: templates.Template, kind: str, language: str) -> Any:
    """The CV filled to every soft limit, or D1's letter in the language with its headline at the
    soft limit: the fullest heading the writer can produce."""
    if kind == "cv":
        return at_soft_limits(d1("cv-typical")[2], found.kinds["cv"].soft_limits)
    letter = d1(f"letter-{language}")[2]
    limit = found.kinds["letter"].soft_limits.headline or 90
    headline = (letter.headline + ", ") * 10
    return letter.model_copy(update={"headline": headline[:limit].rstrip(", ")})


@docker
@pytest.mark.parametrize("who", CONTACTS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("slug", SLUGS)
def test_no_name_address_or_link_breaks_inside_a_word(
    renderer: Any, slug: str, kind: str, language: str, who: str
) -> None:
    """The reviews of D20 to D22: an address broken over two lines in a narrow column, a name
    broken at its hyphen. A long double-barrelled name and contact lines of 25 to 56 characters
    under the fullest heading, at the loosest step, the fitted one and the tightest: each word
    of the name and each contact line stand on one line."""
    found = template(slug)
    view = with_identity(d1_view(), CONTACTS[who])
    assert max(len(str(line["text"])) for line in templates.contact_lines(view)) == int(who)
    content = full_heading(found, kind, language)
    result = fitted(renderer, found, kind, content, view, language)
    assert result.fit, result.pages
    assert_whole(result.pdf, view, f"step {result.step}")
    last = len(found.steps(kind)) - 1
    for step in sorted({0, last} - {result.step}):
        html = templates.build_html(found, kind, content, view, language, step, ON)
        assert_whole(renderer.render(html).pdf, view, f"step {step}")


def glyph_boxes(pdf: bytes) -> list[list[tuple[str, tuple[float, float, float, float]]]]:
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


def two_page_letter(language: str) -> c.LetterContent:
    """D1's letter with five paragraphs near the content's cap: two pages at the loosest step."""
    letter = d1(f"letter-{language}")[2]
    text = " ".join(letter.paragraphs) + " "
    paragraph = (text * (900 // len(text) + 1))[:900].rsplit(" ", 1)[0] + "."
    return letter.model_copy(update={"paragraphs": [paragraph] * 5})


@docker
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("slug", SLUGS)
def test_every_glyph_of_every_page_stands_inside_the_page_frame(
    renderer: Any, slug: str, kind: str, language: str
) -> None:
    """The reviews of D21 and D22: with a page margin of 0, a padding applied once printed page
    2's first line on the paper's edge. A document of two pages or more at the loosest step (D1's
    long CV, a letter of five long paragraphs): every glyph of every page lies inside the step's
    margin, give or take 0.5 mm for the ink of a glyph that overhangs its advance."""
    found = template(slug)
    content: Any = d1("cv-long")[2] if kind == "cv" else two_page_letter(language)
    margin = found.steps(kind)[0].margin * MM
    rendered = renderer.render(
        templates.build_html(found, kind, content, d1_view(), language, 0, ON)
    )
    assert rendered.pages >= 2, rendered.pages
    slack = 0.5 * MM
    low, high = margin - slack, (A4[0] - margin + slack, A4[1] - margin + slack)
    pages = glyph_boxes(rendered.pdf)
    assert len(pages) == rendered.pages
    for number, chars in enumerate(pages, 1):
        assert chars, number
        for char, (x0, y0, x1, y1) in chars:
            inside = low <= x0 and low <= y0 and x1 <= high[0] and y1 <= high[1]
            assert inside, (number, char, [round(v / MM, 2) for v in (x0, y0, x1, y1)])
