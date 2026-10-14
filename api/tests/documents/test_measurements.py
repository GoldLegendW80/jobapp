"""The false issue count of the fact check on the three invented CVs (D2, "Measurements").

Each CV of `fixtures/written/` is written from Alex's sources only, so every claim the checks
find on it is a false issue. The counts are pinned: a change of M9 or of the sources that moves
them shows here. Measured on 2026-10-14: the only false issues are version numbers in the skills
section ("Python 3.12", "PostgreSQL 16", "React 18"), which M9 reads as numbers no source states.
"""

from __future__ import annotations

import pytest

from app.documents import checks
from app.documents.settings import DocumentSettings
from app.documents.sources import sources_for
from tests.documents.conftest import base_cv, load, posting, view_of, written

FALSE_ISSUES = {"cv_short": [], "cv_typical": ["3.12", "16"], "cv_long": ["3.12", "16", "18"]}


@pytest.mark.parametrize("name", sorted(FALSE_ISSUES))
def test_false_issues_on_the_cv_fixtures(name: str) -> None:
    base = base_cv("alex")
    view = view_of("alex", base)
    sources = sources_for(view, base, load("upload_alex.txt"), posting("lumen"))
    result = checks.run(
        "cv", written(name), sources, DocumentSettings(), employer_keys=view.employer_keys
    )
    assert result.content is not None
    assert result.invalid == ()
    assert [checks.excerpt(result.content, i) for i in result.claims] == FALSE_ISSUES[name]
