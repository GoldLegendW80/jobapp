"""D25b's render check on the real renderer (renderer/server.py, POST /check, Chromium in its
container): every bundled template but elegant (its fixed frame) passes it untouched, and each
way a style sheet can hide a field is refused with the field and the code (issue #220: the
residues of #427's static filter, judged where the browser paints).

Marked `docker`: the stack of test_renderer_container.py, an image built under a tag of its own
unless RENDERER_TEST_IMAGE names one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re

import pypdfium2 as pdfium
import pytest

from app.documents import design_capture, design_check, render_client, template_rules, templates
from app.documents.render_client import Checked, RenderError
from app.ingestion.parser_client import DesignPdf
from app.profile.schemas import URL_MAX, IdentitySection
from tests.documents.test_renderer_container import Stack, stack

__all__ = ["stack"]

pytestmark = [pytest.mark.docker]

# Posts stdin to the service from inside its own container: the status line, then the body.
POST = (
    "import http.client, sys\n"
    "c = http.client.HTTPConnection('127.0.0.1', 7071, timeout=60)\n"
    "c.request('POST', '/check', sys.stdin.buffer.read(), {'Content-Type': 'application/json'})\n"
    "r = c.getresponse(); b = r.read()\n"
    "sys.stdout.buffer.write(str(r.status).encode() + b'\\n' + b)\n"
)


def checker(stack: Stack) -> design_check.Check:
    async def check(html: str, needles: tuple[str, ...], width: int, name: str) -> Checked:
        body = json.dumps({"html": html, "needles": list(needles), "width": width, "name": name})
        raw = await asyncio.to_thread(
            stack.execute, "renderer", "python", "-I", "-c", POST, stdin=body.encode()
        )
        status, _, data = raw.partition(b"\n")
        if int(status) != 200:
            raise RenderError(render_client.REFUSALS.get(int(status), "render_failed"))
        return render_client.read_checked(data, needles, width)

    return check


def probe(css: str = "", replace: tuple[tuple[str, str], ...] = ()) -> templates.Template:
    """classic, with `css` appended and each `replace` applied to its style sheet."""
    classic = templates.registry()["classic"]
    style = classic.files["style.css"]
    for old, new in replace:
        style = re.sub(old, new, style)
    manifest = json.loads((classic.folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["slug"] = "probe"
    return templates.from_files(manifest, {**classic.files, "style.css": style + css})


# elegant's double frame is a `position: fixed` box over the whole page, transparent inside: the
# check takes any box over a field for a cover, and a user template cannot hold a fixed box
# (template_css), so this bundled one is the only template to meet it.
COVERED_BY_A_FRAME = frozenset(("elegant",))


@pytest.mark.parametrize("slug", sorted(templates.registry()))
def test_every_bundled_template_shows_every_field(stack: Stack, slug: str) -> None:
    template = templates.registry()[slug]
    found = asyncio.run(design_check.check_template(template, checker(stack)))
    if slug in COVERED_BY_A_FRAME:
        assert found
        assert {p.code for p in found} == {"covered"}
    else:
        assert found == [], [(p.code, p.detail) for p in found]


HIDING = [
    ("white_text", ".entry .org { color: #ffffff; }", "contrast", "roles[0].organisation"),
    (
        "box_of_a_parent_over_it",
        ".skill-group { position: relative; }"
        ".skill-group::after { content: ''; position: absolute; left: 0; top: 0; right: 0;"
        " bottom: 0; background: #ffffff; }",
        "covered",
        "content.skills[0].label",
    ),
    (
        "box_of_its_own_element_over_it",
        ".group-label { position: relative; }"
        ".group-label::after { content: ''; position: absolute; left: 0; top: 0; right: 0;"
        " bottom: 0; background: #ffffff; }",
        "contrast",
        "content.skills[0].label",
    ),
    (
        "a_later_sibling_pulled_over_it",
        ".place { margin-top: -3em; background: #ffffff; position: relative; padding: 1em 0; }",
        "covered",
        "roles[0].title",
    ),
    # #427's round 4 residues: a pseudo element pushed by `justify-self` over a skill group's
    # items, and the large white outside marker of a `p` made a list item over the field beside
    (
        "pseudo_element_pushed_by_justify_self",
        ".skill-group { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)"
        " minmax(0, 1fr); } .skill-group::after { content: '\\25A0\\25A0\\25A0\\25A0"
        "\\25A0\\25A0\\25A0\\25A0\\25A0\\25A0\\25A0\\25A0'; color: #ffffff;"
        " font-size: 2em; white-space: nowrap; justify-self: end; }",
        "covered",
        "content.skills[0].items[0]",
    ),
    (
        "marker_of_a_list_item_paragraph",
        ".school { display: flex; } .school .org { display: list-item; list-style-type: square;"
        " } .school .org::marker { color: #ffffff; font-size: 4em; }",
        "covered",
        "schools[0].degree",
    ),
    ("off_the_page", ".languages { margin-left: 300mm; }", "outside", "languages[0].name"),
    ("too_small", ".place { font-size: 0.3em; }", "small", "roles[0].location"),
    ("not_displayed", ".summary { display: none; }", "hidden", "content.summary"),
    ("invisible", ".summary { visibility: hidden; }", "covered", "content.summary"),
    ("faint", ".headline { opacity: 0.1; }", "contrast", "content.headline"),
    (
        "clipped",
        ".entry-title { height: 0.1em; overflow: hidden; }",
        "covered",
        "roles[0].title",
    ),
    # PR #518 round 2: a root margin print adds to the page's, which the check once replaced
    (
        "root_margin",
        "html { margin-left: 120mm; } body { width: 175mm; }",
        "outside",
        "languages[1].level",
    ),
]  # fmt: skip


@pytest.mark.parametrize(("name", "css", "code", "field"), HIDING)
def test_a_style_that_hides_a_field_is_refused_with_its_field(
    stack: Stack, name: str, css: str, code: str, field: str
) -> None:
    found = asyncio.run(design_check.check_template(probe(css), checker(stack)))
    assert any(p.code == code and p.detail.startswith(f"{field} ") for p in found), (name, found)


# PR #518 round 2: what only another content shape shows (both refused statically too, by
# template_css: a height off the photo, a column track under TRACK_MIN).
LONE = " one item per list and long texts"
SHAPE_HIDING = [
    ("box_shorter_than_a_long_summary", ".summary { height: 3.2em; }", "content.summary"),
    (
        "grid_of_a_lone_school",
        ".schools .school:first-child:last-child { display: grid;"
        " grid-template-columns: 0px 0px; grid-auto-flow: column; }",
        "schools[0].",
    ),
    # PR #518 round 3: a line that may not wrap, wider than its box only with a long address
    (
        "address_that_may_not_wrap_in_a_fixed_track",
        ".contact { display: grid; grid-template-columns: 45mm 45mm 45mm; }"
        " .contact li { white-space: nowrap; }",
        "contact[].text",
    ),
]


@pytest.mark.parametrize(("name", "css", "field"), SHAPE_HIDING)
def test_a_style_that_fails_only_another_content_shape_is_refused(
    stack: Stack, name: str, css: str, field: str
) -> None:
    found = asyncio.run(design_check.check_template(probe(css), checker(stack)))
    caught = [p for p in found if p.detail.startswith(field)]
    assert caught, (name, found)
    assert all(p.detail.endswith(LONE) for p in caught), (name, caught)


def test_a_style_that_ignores_the_density_is_refused(stack: Stack) -> None:
    fixed = (
        (r"var\(--body-size\)", "10pt"),
        (r"var\(--line-height\)", "1.4"),
        (r"var\(--section-gap\)", "10pt"),
        (r"var\(--item-gap\)", "5pt"),
    )
    # A fixed width too, so the step's margin no longer changes the lines either.
    template = probe("body { width: 120mm; }", fixed)
    found = asyncio.run(design_check.check_template(template, checker(stack)))
    assert [p.code for p in found if p.code == "density_unread"] == ["density_unread"] * 2


def test_the_service_refuses_a_check_it_cannot_read(stack: Stack) -> None:
    for body in (
        b"{}",
        b"not json",
        json.dumps({"html": "", "needles": [], "width": 600, "name": "jcabcdefgh"}).encode(),
        json.dumps(
            {"html": "<p>x</p>", "needles": ["x" * 2000] * 9, "width": 600, "name": "jcabcdefgh"}
        ).encode(),
    ):
        raw = stack.execute("renderer", "python", "-I", "-c", POST, stdin=body)
        assert raw.split(b"\n", 1)[0] == b"400"


def test_the_recorded_streams_template_passes_the_real_check(stack: Stack) -> None:
    stream = json.loads(
        (agents_root() / "fake_streams" / "documents_design_valid.json").read_text("utf-8")
    )
    (files,) = [
        block["input"]
        for step in stream["steps"]
        for block in (step["message"].get("message") or {}).get("content", [])
        if block.get("name") == "mcp__app__submit_template"
    ]
    inputs = design_capture.Inputs(
        "pdf",
        template_rules.from_pdf(DesignPdf(1, False, False, (), (), ())),
        "optional",
        {},
        (),
        (),
    )
    named = {"cv.html.j2": files["cv_html"], "letter.html.j2": files["letter_html"]}
    found = asyncio.run(
        design_capture.validate({**named, "style.css": files["css"]}, inputs, checker(stack))
    )
    assert found.problems == ()
    assert found.template is not None


def agents_root():
    from pathlib import Path

    import app.agents

    return Path(app.agents.__file__).parent


# PR #518 round 3: the reviewer's sheet, whose 43 character e-mail printed over the phone, and its
# family: boxes that fit the canary's words. The app's rules after the sheet
# (template_css.ENFORCED_CSS) break a word wider than its box, so each passes the check with the
# lone shape's long words and addresses, and the render wraps the address inside its cell.
REVIEWER_SHEET = (
    ".contact { display: grid; grid-template-columns: 45mm 45mm 45mm; }"
    " li, p { overflow-wrap: normal; }"
)
NARROW_BOXES = [
    (
        "contact_grid_that_fits_the_header",
        ".contact { display: grid; grid-template-columns: 40mm 40mm 40mm; }"
        " li, p { overflow-wrap: normal; }",
    ),
    (
        "skill_group_at_track_min",
        ".skill-group { display: grid; grid-template-columns: 20mm 20mm; }"
        " .items { overflow-wrap: normal; word-break: keep-all; }",
    ),
    (
        "shrinking_track_beside_a_wide_one",
        ".schools { grid-template-columns: minmax(0, 1fr) 60mm; }",
    ),
    ("fixed_width", ".entry .entry-title { width: 100mm; } .org { overflow-wrap: normal; }"),
    ("row_flex_basis", ".headtext { flex: 0 0 110mm; } .name { overflow-wrap: break-word; }"),
]


@pytest.mark.parametrize(("name", "css"), NARROW_BOXES)
def test_a_box_narrower_than_a_long_word_breaks_the_word_and_passes(
    stack: Stack, name: str, css: str
) -> None:
    found = asyncio.run(design_check.check_template(probe(css), checker(stack)))
    assert found == [], (name, [(p.code, p.detail) for p in found])


def test_the_reviewers_sheet_is_refused_only_where_its_grid_is_wider_than_the_header(
    stack: Stack,
) -> None:
    # 135 mm of tracks beside the photo: wider than the letter's header at its loosest margin,
    # so its name runs past the page's text; the long addresses wrap inside their cells.
    found = asyncio.run(design_check.check_template(probe(REVIEWER_SHEET), checker(stack)))
    assert found
    assert not [p for p in found if p.detail.startswith("contact[].text")], found


def _glyphs(pdf: bytes) -> list[tuple[str, tuple[float, float, float, float]]]:
    """The printed characters of page 1 and their boxes (left, bottom, right, top), in points."""
    document = pdfium.PdfDocument(pdf)
    try:
        page = document[0].get_textpage()
        found = []
        for n in range(page.count_chars()):
            char = page.get_text_range(n, 1).replace("\ufffe", "-")
            if char.strip():
                found.append((char, page.get_charbox(n)))
        return found
    finally:
        document.close()


def test_the_reviewers_long_email_prints_inside_its_cell_left_of_the_phone(stack: Stack) -> None:
    email, phone = "e2e-alexandra.martin.consulting@example.org", "+33600000000"
    sample = design_check.canary("cv")
    view = dataclasses.replace(
        sample.view, identity=dataclasses.replace(sample.view.identity, email=email)
    )
    html = templates.build_html(
        probe(REVIEWER_SHEET), "cv", sample.content, view, "en", 0, sample.rendered_on
    )
    glyphs = _glyphs(stack.render(html).pdf)
    text = "".join(char for char, _ in glyphs)
    at, phone_at = text.index(email), text.index(phone)
    rightmost = max(box[2] for _, box in glyphs[at : at + len(email)])
    leftmost = min(box[0] for _, box in glyphs[phone_at : phone_at + len(phone)])
    assert rightmost < leftmost


# Issue #575, the approving review's sheet: the 55 character canary fits but real URLs need
# more room. Render the refused variant deliberately too, to prove the PDF bounds detect it.
HEADER_125_SHEET = (
    ".headtext { flex: 0 0 125mm; } .photo { order: 2; }"
    " .contact { display: block; } .contact li { white-space: nowrap; }"
)


def _website_in_125mm_header(
    stack: Stack, kind: str, step: int, length: int, whitespace: str
) -> tuple[list[tuple[float, float, float, float]], float, float, float]:
    printed = "example.org/" + "a" * (length - len("example.org/"))
    address = "https://" + printed
    assert len(printed) == length
    assert IdentitySection(website=address).website == address
    sample = design_check.canary(kind)
    view = dataclasses.replace(
        sample.view, identity=dataclasses.replace(sample.view.identity, website=address)
    )
    # A flex basis alone can grow to its automatic content minimum. Keep the measured box at
    # 125 mm for both variants, so the hostile address cannot move the photo out of its way.
    css = HEADER_125_SHEET.replace("white-space: nowrap", f"white-space: {whitespace}")
    template = probe(css + " .headtext { min-width: 0; }")
    html = templates.build_html(
        template,
        kind,
        sample.content,
        view,
        "en",
        step,
        sample.rendered_on,
        photo=design_check.grey_photo(),
    )
    rendered = stack.render(html)
    glyphs = _glyphs(rendered.pdf)
    text = "".join(char for char, _ in glyphs)
    assert text.count(printed) == 1, (length, whitespace, text)
    at = text.index(printed)
    boxes = [box for _, box in glyphs[at : at + length]]
    document = pdfium.PdfDocument(rendered.pdf)
    try:
        page = document[0]
        images = list(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]))
        assert len(images) == 1
        photo_left = images[0].get_bounds()[0]
    finally:
        document.close()
    left = template.steps(kind)[step].margin * 72 / 25.4
    right = left + 125 * 72 / 25.4
    assert abs(photo_left - right - 14) < 0.5  # the actual photo follows the 14 pt header gap
    return boxes, left, right, photo_left


@pytest.mark.parametrize("kind", ["cv", "letter"])
@pytest.mark.parametrize("step", [0, -1])
@pytest.mark.parametrize("length", [77, 80, URL_MAX - len("https://")])
@pytest.mark.parametrize("whitespace", ["normal", "pre-line", "pre-wrap"])
def test_a_valid_long_website_prints_completely_inside_125mm_before_the_photo(
    stack: Stack, kind: str, step: int, length: int, whitespace: str
) -> None:
    boxes, left, right, photo_left = _website_in_125mm_header(stack, kind, step, length, whitespace)
    assert all(left - 0.5 <= box[0] and box[2] <= right + 0.5 for box in boxes), boxes
    assert all(box[2] < photo_left for box in boxes)
    if step == 0:
        assert boxes[-1][3] < boxes[0][1]  # the end of the address is on a later printed line


@pytest.mark.parametrize("length", [77, 80])
def test_the_pdf_bounds_detect_the_refused_nowrap_website_beyond_125mm(
    stack: Stack, length: int
) -> None:
    boxes, _, right, photo_left = _website_in_125mm_header(stack, "cv", 0, length, "nowrap")
    assert any(box[2] > right + 0.5 for box in boxes)
    if length == 80:
        assert any(box[2] > photo_left for box in boxes)
