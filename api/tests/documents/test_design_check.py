"""D25b's render check without the renderer: the canary shapes, the page box each page is laid
out in, and what an answer of the renderer means (the real Chromium: test_design_check_docker.py).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json

import pytest
from PIL import Image
from pydantic import ValidationError

from app.documents import content as c
from app.documents import design_check, render_client, templates
from app.documents.render_client import Checked, RenderError, Seen
from app.documents.template_css import Problem
from app.profile.schemas import IdentitySection


def test_the_lone_canary_holds_one_item_per_list_and_texts_at_their_limits() -> None:
    cv = design_check.canary("cv", "lone")
    letter = design_check.canary("letter", "lone")
    content = cv.content
    assert isinstance(content, c.CvContent)
    assert isinstance(letter.content, c.LetterContent)
    lists = (content.highlights, content.competencies, content.skills, content.experience)
    assert [len(found) for found in lists] == [1, 1, 1, 1]
    assert len(content.skills[0].items) == len(content.experience[0].bullets) == 1
    assert len(letter.content.paragraphs) == 1
    assert (len(cv.view.employers), len(cv.view.schools), len(cv.view.languages)) == (1, 1, 1)
    near = (
        (content.headline, c.HEADLINE_MAX),
        (content.summary, c.SUMMARY_MAX),
        (content.highlights[0], c.BULLET_MAX),
        (content.experience[0].bullets[0], c.BULLET_MAX),
        (content.competencies[0], c.TERM_MAX),
        (letter.content.paragraphs[0], design_check.LONE_PARAGRAPH),
        (letter.content.subject, c.LINE_MAX),
        (cv.view.employers[0].organisation, c.LINE_MAX),
        (cv.view.schools[0].school, c.LINE_MAX),
    )
    for text, limit in near:
        assert limit - 20 <= len(text) <= limit, (text, limit)
    sample = design_check.canary("cv")
    assert len(sample.content.highlights) > 1  # the sample shape is untouched


def _one_character_longer(key: str, value: str) -> str:
    if key == "email":  # in its last label, under the 63 of a label
        return value.replace(".example.org", "a.example.org")
    return value + ("0" if key == "phone" else "a")


@pytest.mark.parametrize("kind", templates.KINDS)
def test_the_identity_canary_prints_each_identity_field_at_the_profiles_maximum(kind) -> None:
    sample = design_check.canary(kind, "identity")
    identity = sample.view.identity
    for key, most in design_check.IDENTITY_MAX.items():
        value = getattr(identity, key)
        assert len(value) == most, key
        assert getattr(IdentitySection(**{key: value}), key) == value
        with pytest.raises(ValidationError):
            IdentitySection(**{key: _one_character_longer(key, value)})
    printed = [str(line["text"]) for line in templates.contact_lines(sample.view)]
    assert [len(text) for text in printed] == [254, 40, 402, 492, 492]
    assert all(text in [needle for _, needle in sample.needles] for text in printed)
    view = sample.view
    assert (len(view.employers), len(view.schools), len(view.languages)) == (1, 1, 1)
    if kind == "cv":
        content = sample.content
        assert isinstance(content, c.CvContent)
        lists = (content.highlights, content.competencies, content.skills, content.experience)
        assert [len(found) for found in lists] == [1, 1, 1, 1]
        assert len(content.summary) == len(design_check.canary("cv").content.summary)


@pytest.mark.parametrize("kind", templates.KINDS)
@pytest.mark.parametrize("shape", design_check.SHAPES)
def test_each_needle_names_one_field(kind: str, shape: str) -> None:
    needles = [text.lower() for _, text in design_check.canary(kind, shape).needles]
    for n, one in enumerate(needles):
        assert not any(one in other for m, other in enumerate(needles) if m != n), one
    assert sum(map(len, needles)) < 16_000  # renderer/server.py, CHECK_TEXT


class Recorded:
    def __init__(self, code: str) -> None:
        self.code = code
        self.widths: list[int] = []
        self.htmls: list[str] = []

    async def __call__(self, html: str, needles: tuple[str, ...], width: int, name: str) -> Checked:
        self.widths.append(width)
        self.htmls.append(html)
        raise RenderError(self.code)


def test_every_shape_is_checked_at_both_steps_in_the_page_box_of_the_step() -> None:
    template = templates.registry()["classic"]
    check = Recorded("render_failed")
    found = asyncio.run(design_check.check_template(template, check))
    widths = []
    for kind in templates.KINDS:
        steps = template.steps(kind)
        loose, tight = design_check.text_width(steps[0]), design_check.text_width(steps[-1])
        widths += [loose, tight] * len(design_check.SHAPES)
    assert check.widths == widths
    assert loose < tight < round(210 * 96 / 25.4)
    # nothing in the page sets the root element's margin: print's page box is the width
    assert not any("html{margin" in html for html in check.htmls)
    assert {p.code for p in found} == {"render"}
    assert any("one item per list and long texts" in p.detail for p in found)
    assert any("the name and contact lines at their longest" in p.detail for p in found)


@pytest.mark.parametrize("code", ["renderer_unavailable", "timeout"])
def test_a_renderer_down_busy_or_slow_says_nothing_of_the_template(code: str) -> None:
    template = templates.registry()["classic"]
    with pytest.raises(design_check.CheckUnavailableError):
        asyncio.run(design_check.check_template(template, Recorded(code)))


def _png(width: int, height: int, ink: tuple[int, int, int, int] | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    if ink:
        image.paste((0, 0, 0), ink)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _checked(
    width: int,
    needles: int,
    glyph: tuple[float, float, float, float] = (2.0, 2.0, 4.0, 6.0),
    within: tuple[float, float] | None = None,
    scroll_width: int | None = None,
    shown: int = 12,
) -> Checked:
    """A page of `width` x `shown` CSS pixels where every needle prints one black glyph."""
    seen = Seen(12.0, ((*glyph, True),), within or (0.0, float(width)))
    return Checked(
        width=width,
        scroll_width=width if scroll_width is None else scroll_width,
        height=10_000 // width,  # the tightest step, the wider page, is shorter
        shown=shown,
        scale=1,
        found=tuple((seen,) for _ in range(needles)),
        before=_png(width, shown, (2, 2, 6, 8)),
        after=_png(width, shown),
    )


NEEDLE = (("contact[].text", "QaaaZ"),)


def test_a_glyph_outside_a_box_that_holds_it_overflows() -> None:
    def codes(**changed) -> list[str]:
        found = design_check.problems_of(_checked(40, 1, **changed), NEEDLE, "cv.html.j2", "here")
        return [p.code for p in found]

    assert codes() == []
    assert codes(within=(2.0, 6.0)) == []
    assert codes(within=(2.0, 5.4)) == ["overflow"]  # past the right edge
    assert codes(within=(2.6, 40.0)) == ["overflow"]  # past the left edge
    assert codes(glyph=(38.0, 2.0, 4.0, 6.0)) == ["outside"]  # off the page beats overflow


def test_below_the_measured_height_only_the_identity_shape_judges_widths_alone() -> None:
    low = _checked(40, 1, glyph=(2.0, 14.0, 4.0, 6.0))
    assert [p.code for p in design_check.problems_of(low, NEEDLE, "f", "here")] == ["outside"]
    assert design_check.problems_of(low, NEEDLE, "f", "here", lower=True) == []
    beside = _checked(40, 1, glyph=(2.0, 14.0, 4.0, 6.0), within=(0.0, 5.0))
    found = design_check.problems_of(beside, NEEDLE, "f", "here", lower=True)
    assert [p.code for p in found] == ["overflow"]


class Wide:
    """A renderer whose page is `over` pixels wider than its text, every needle shown."""

    def __init__(self, over: int) -> None:
        self.over = over

    async def __call__(self, html: str, needles: tuple[str, ...], width: int, name: str) -> Checked:
        return _checked(width, len(needles), scroll_width=width + self.over)


def test_a_page_wider_than_its_text_is_scaled_at_every_shape_and_step() -> None:
    template = templates.registry()["classic"]
    assert asyncio.run(design_check.check_template(template, Wide(1))) == []
    found = asyncio.run(design_check.check_template(template, Wide(2)))
    wheres = [
        f"the page at the {label} step{design_check.WHERE[shape]}"
        for _ in templates.KINDS
        for shape in design_check.SHAPES
        for label in ("loosest", "tightest")
    ]
    assert found == list(dict.fromkeys(Problem("style.css", 0, "scaled", w) for w in wheres))


def _answer(**changed) -> bytes:
    answer = {
        "width": 40,
        "scroll_width": 52,
        "height": 9,
        "shown": 12,
        "scale": 1,
        "found": [[[12.0, [[2.0, 2.0, 4.0, 6.0, 1]], [0.0, 40.0]]]],
        "before": base64.b64encode(_png(40, 12)).decode(),
        "after": base64.b64encode(_png(40, 12)).decode(),
    }
    answer.update(changed)
    return json.dumps({key: value for key, value in answer.items() if value is not None}).encode()


def test_the_answer_holds_the_scroll_width_and_the_edges_of_each_occurrence() -> None:
    checked = render_client.read_checked(_answer(), ("QaaaZ",), 40)
    assert checked.scroll_width == 52
    assert checked.found[0][0].within == (0.0, 40.0)
    for wrong in (
        {"scroll_width": None},
        {"scroll_width": 0},
        {"scroll_width": 52.5},
        {"found": [[[12.0, [[2.0, 2.0, 4.0, 6.0, 1]]]]]},
        {"found": [[[12.0, [[2.0, 2.0, 4.0, 6.0, 1]], [0.0]]]]},
        {"found": [[[12.0, [[2.0, 2.0, 4.0, 6.0, 1]], [0.0, "40"]]]]},
    ):
        with pytest.raises(RenderError):
            render_client.read_checked(_answer(**wrong), ("QaaaZ",), 40)
