"""The method of D28 checked end to end on the real renderer (renderer/server.py, Chromium in
its container): each of D27's three templates, printed with the invented sample at step 0, is
read back as an upload would be (the sandbox's design_pdf on that PDF, in this process), its
fonts are chosen (font_measure: by name, as the PDF embeds the library's own families), its
layout made (design_capture._pdf_layout), and the same template printed again lies on it at the
top of the measure, while a copy shifted by 5pt says so. A guard that the measure measures.

The bundled templates are not passed through the upload rules (template_rules refuses their
own constructs, test_template_rules.REPOSITORY), so no writer submit is scripted here: the
capture's machinery around the measure is test_design_capture's.

Marked `docker`: the stack of test_renderer_container.py, an image built under a tag of its own
unless RENDERER_TEST_IMAGE names one.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from app.documents import content as c
from app.documents import design_capture, fidelity, font_measure, render_client, templates
from app.documents.templates import thumbnails
from app.ingestion import parser_client as pc
from tests.documents.test_renderer_container import Stack, stack
from tests.ingestion.parser.helpers import Plenty, as_in_the_child, sandbox_handler, sandbox_module

__all__ = ["stack"]

pytestmark = [pytest.mark.docker]

FAMILY = {"band": "inter-tight", "ledger": "gantari", "photo-columns": "raleway"}
WIDTH = 1100


def printed(stack: Stack, template: templates.Template, css: str = "") -> bytes:
    data = thumbnails.sample()
    content = c.MODELS["cv"].model_validate(data["cv"])
    if css:
        files = {**template.files, "style.css": template.files["style.css"] + css}
        template = dataclasses.replace(template, files=files)
    html = templates.build_html(
        template, "cv", content, thumbnails.sample_view(), "en", 0,
        date.fromisoformat(data["rendered_on"]),
    )  # fmt: skip
    return stack.render(html).pdf


def extracted(pdf: bytes) -> pc.DesignPdf:
    with as_in_the_child():
        main, design_pdf = sandbox_module("main"), sandbox_handler("design_pdf")
        answer = design_pdf.handle(pdf, {}, Plenty())
        return pc.check_design_pdf(pc.loads(main.encode(answer)))


@pytest.mark.parametrize("slug", sorted(FAMILY))
def test_a_template_lies_on_its_own_print_and_a_shifted_copy_says_so(
    stack: Stack, slug: str
) -> None:
    template = templates.registry()[slug]
    original = printed(stack, template)
    design = extracted(original)
    faces = font_measure.faces(design)
    body = max(design.read[0].runs, key=lambda run: len(run.text))
    assert faces[body.font].family == FAMILY[slug]
    layout = design_capture._pdf_layout(design, faces)
    page = render_client.page1_png(original, WIDTH)
    lines = fidelity.original_lines(layout)
    assert len(lines) >= 20

    again = printed(stack, template)
    same = fidelity.measure(page, lines, again, render_client.page1_png(again, WIDTH))
    assert same.page > 0.98
    assert same.in_place >= 0.95 * same.lines
    assert same.mean_dy < 0.5

    lower = printed(stack, template, "\nbody { padding-top: 5pt; }")
    moved = fidelity.measure(page, lines, lower, render_client.page1_png(lower, WIDTH))
    assert moved.score < same.score
    assert moved.mean_dy == pytest.approx(5, abs=1)
    assert any("pt lower" in sentence for sentence in moved.sentences())
