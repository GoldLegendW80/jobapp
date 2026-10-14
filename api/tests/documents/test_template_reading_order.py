"""The reading order of every template (outside D4b's owned folders, added after the review of
PR #275): applicant tracking systems parse a CV PDF by text order, so the first text of page 1
must be the person's name, whatever the layout puts on the left. Table driven over every folder
of `templates/`, through the real renderer. Marked `docker`, like the fit rule in
test_templates.py whose helpers and renderer stack it reuses.
"""

from typing import Any

import pypdfium2 as pdfium
import pytest

from app.documents import templates
from tests.documents.test_templates import SLUGS, d1, d1_view, fitted, template
from tests.documents.test_templates import renderer as renderer

docker = pytest.mark.docker


def first_page_text(pdf: bytes) -> str:
    document = pdfium.PdfDocument(pdf)
    try:
        return document[0].get_textpage().get_text_range()
    finally:
        document.close()


@docker
@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("language", templates.LANGUAGES)
@pytest.mark.parametrize("name", ["cv-short", "cv-typical", "cv-long"])
def test_a_cv_starts_with_the_name(renderer: Any, slug: str, language: str, name: str) -> None:
    kind, _, content = d1(name)
    result = fitted(renderer, template(slug), kind, content, d1_view(), language)
    assert result.fit
    assert first_page_text(result.pdf).lstrip().startswith("Alex Example"), slug


@docker
@pytest.mark.parametrize("slug", SLUGS)
def test_a_letter_starts_with_the_name(renderer: Any, slug: str) -> None:
    kind, language, content = d1("letter-en")
    result = fitted(renderer, template(slug), kind, content, d1_view(), language)
    assert result.fit
    text = first_page_text(result.pdf).lstrip()
    assert text.startswith("Alex Example"), (slug, text[:80])
