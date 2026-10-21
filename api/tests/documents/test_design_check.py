"""D25b's render check without the renderer: the canary shapes, the page box each page is laid
out in, and what an answer of the renderer means (the real Chromium: test_design_check_docker.py).
"""

from __future__ import annotations

import asyncio

import pytest

from app.documents import content as c
from app.documents import design_check, templates
from app.documents.render_client import Checked, RenderError


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


def test_both_shapes_are_checked_at_both_steps_in_the_page_box_of_the_step() -> None:
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


@pytest.mark.parametrize("code", ["renderer_unavailable", "timeout"])
def test_a_renderer_down_busy_or_slow_says_nothing_of_the_template(code: str) -> None:
    template = templates.registry()["classic"]
    with pytest.raises(design_check.CheckUnavailableError):
        asyncio.run(design_check.check_template(template, Recorded(code)))
