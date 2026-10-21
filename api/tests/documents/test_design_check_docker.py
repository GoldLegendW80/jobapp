"""D25b's render check on the real renderer (renderer/server.py, POST /check, Chromium in its
container): every bundled template but elegant (its fixed frame) passes it untouched, and each
way a style sheet can hide a field is refused with the field and the code (issue #220: the
residues of #427's static filter, judged where the browser paints).

Marked `docker`: the stack of test_renderer_container.py, an image built under a tag of its own
unless RENDERER_TEST_IMAGE names one.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from app.documents import design_capture, design_check, render_client, template_rules, templates
from app.documents.render_client import Checked, RenderError
from app.ingestion.parser_client import DesignPdf
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
