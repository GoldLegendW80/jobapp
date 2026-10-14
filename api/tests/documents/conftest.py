"""The scaffolding of the documents module's tests (D2). Later tasks extend it only by adding
files: fixtures under `fixtures/` (D1 owns `fixtures/content/`) and helpers of their own.

Everything here is invented: "Alex Example" (user A, e2e-alex@example.org) works at "Example
Corp" and worked at "Sample Labs"; "Blake Sample" (user B, e2e-blake@example.org) has an empty
profile and a base CV converted from an upload; the postings are of invented companies. Nothing
comes from a real person's profile or documents.

Fixtures a test gets by naming them:

- `alex_profile`, `blake_profile`: `(sections, revision)` as the profile stores them;
- `alex_base`, `blake_base`: the base CVs (`BaseCvContent`);
- `alex_upload`, `blake_upload`: the text of the CV each base CV was converted from;
- `lumen`, `brise`: two postings (`PostingView`), English and French, with their text apart
  (`posting_text`), which is never a source;
- `alex_view`, `alex_sources`: the profile view and the fact check sources of Alex writing for
  Lumen from his base CV;
- `written`: the CVs and letters of `fixtures/written/`, as JSON data, by name.

`profile_casey`, `base_cv_casey` and `posting_brume` are the invented profile, base CV and
posting of the review probes (`test_review_probes.py`), read with `view_of`, `base_cv`, `posting`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.documents import content as c
from app.documents import profile_view
from app.documents.sources import PostingView, sources_for
from app.mail.factcheck_rules import Sources

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    path = FIXTURES / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text) if path.suffix == ".json" else text


def profile(name: str) -> tuple[dict[str, Any], int]:
    data = load(f"profile_{name}.json")
    return data["sections"], data["revision"]


def base_cv(name: str) -> c.BaseCvContent:
    return c.parse("base", load(f"base_cv_{name}.json"))  # type: ignore[return-value]


def posting(name: str) -> PostingView:
    data = load(f"posting_{name}.json")
    return PostingView(company=data["company"], title=data["title"])


def posting_text(name: str) -> str:
    return load(f"posting_{name}.json")["text"]


def written(name: str) -> dict[str, Any]:
    return load(f"written/{name}.json")


def view_of(name: str, base: c.BaseCvContent | None = None) -> profile_view.ProfileView:
    sections, revision = profile(name)
    return profile_view.build(sections, revision, base)


@pytest.fixture
def alex_profile() -> tuple[dict[str, Any], int]:
    return profile("alex")


@pytest.fixture
def blake_profile() -> tuple[dict[str, Any], int]:
    return profile("blake")


@pytest.fixture
def alex_base() -> c.BaseCvContent:
    return base_cv("alex")


@pytest.fixture
def blake_base() -> c.BaseCvContent:
    return base_cv("blake")


@pytest.fixture
def alex_upload() -> str:
    return load("upload_alex.txt")


@pytest.fixture
def blake_upload() -> str:
    return load("upload_blake.txt")


@pytest.fixture
def lumen() -> PostingView:
    return posting("lumen")


@pytest.fixture
def brise() -> PostingView:
    return posting("brise")


@pytest.fixture
def alex_view(alex_base: c.BaseCvContent) -> profile_view.ProfileView:
    return view_of("alex", alex_base)


@pytest.fixture
def alex_sources(
    alex_view: profile_view.ProfileView,
    alex_base: c.BaseCvContent,
    alex_upload: str,
    lumen: PostingView,
) -> Sources:
    return sources_for(alex_view, alex_base, alex_upload, lumen)
