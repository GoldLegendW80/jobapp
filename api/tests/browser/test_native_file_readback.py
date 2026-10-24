"""File metadata belongs to current native controls, never the upload response or page text, and
a button the read cannot resolve answers an empty selection instead of refusing the snapshot."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.apply.ats.base import parse_snapshot

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import pages  # noqa: E402
from browserd.tools.policy import SNAPSHOT_MAX  # noqa: E402


class GoneError(Exception):
    """Playwright's answer at once for a ref that no longer resolves."""


class Page:
    """`eval_on_selector` as Playwright answers it: the script's value at the ref, or an error."""

    def __init__(self, current: dict[str, Any] | None = None) -> None:
        self.current = {"e2": ["cv.pdf"], "e3": None, "f1e4": []} if current is None else current
        self.refs: list[str] = []

    async def eval_on_selector(self, selector: str, script: str) -> Any:
        assert script == pages.NATIVE_FILES_JS
        ref = selector.removeprefix("aria-ref=")
        self.refs.append(ref)
        found = self.current.get(ref, GoneError(f'Failed to find element matching "{selector}"'))
        if isinstance(found, BaseException):
            raise found
        return found


def read(page: Page, snapshot: str) -> dict[str, list[str]]:
    return asyncio.run(pages.native_files(page, snapshot))


def buttons(count: int, start: int = 0) -> str:
    return "\n".join(f'- button "Yes" [ref=e{i}]' for i in range(start, start + count))


def test_native_file_metadata_is_fresh_and_includes_empty_selection() -> None:
    page = Page()
    snapshot = """- generic [ref=e1]:
  - button "Resume *" [ref=e2]
  - button "Attach letter" [ref=e3]
  - button "Resume *" [ref=f1e4]
  - text: uploaded cv.pdf [ref=e999]
"""
    assert read(page, snapshot) == {"e2": ["cv.pdf"], "f1e4": []}
    page.current["e2"] = []
    assert read(page, snapshot) == {"e2": [], "f1e4": []}
    page.current["e2"] = ["other.pdf"]
    assert read(page, snapshot) == {"e2": ["other.pdf"], "f1e4": []}
    assert "e999" not in page.refs


# A line of a snapshot and the refs a read takes from it: the one in the attributes between the
# name and the colon, where base.parse_snapshot reads it; a line base does not read gives none.
LINES = [
    ('- button "Resume *" [ref=e2]', ["e2"]),
    ('    - button "Resume *" [ref=f1e4]', ["f1e4"]),
    ("- button [ref=e2]", ["e2"]),
    ('- button "Resume" [ref=e2]   ', ["e2"]),
    ('- button "Upload letter" [ref=e8]: Choose x [ref=e9999]', ["e8"]),
    ('- button "Upload letter" [ref=e8]: [ref=e9999]', ["e8"]),
    ('- button "Upload letter" [ref=e8]: "Choose [ref=e9999]"', ["e8"]),
    ('- button "Upload" [ref=e8]:', ["e8"]),
    ('- button "Upload" [disabled] [ref=e8] [cursor=pointer]: x [ref=e9999]', ["e8"]),
    ('- button "x [ref=e9999]" [ref=e8]', ["e8"]),
    ('- button "x" [ref=e9999]" [ref=e8]', []),
    ('- button "say \\"x\\" [ref=e9999]" [ref=e8]', ["e8"]),
    ('- button "a] [ref=e9999]" [ref=e8]: b] [ref=e9998]', ["e8"]),
    ('- button "x" [ref=e8]:x [ref=e9999]', []),
    ('- button "x" [ref=e8] trailing [ref=e9999]', []),
    ('- button "unclosed [ref=e9999]', []),
    ("- button /Upload/ [ref=e8]", []),
    ("- 'button \"Step: upload\" [ref=e8]'", []),
    ("- text: cv.pdf [ref=e999]", []),
    ('- text: - button "x" [ref=e999]', []),
    ("- generic [ref=e1]: - button [ref=e999]", []),
    ('- link "button [ref=e999]" [ref=e5]', []),
    ('- buttons "x" [ref=e5]', []),
    ('- button-like "x" [ref=e5]', []),
    ('-button "x" [ref=e5]', []),
    ('\t- button "x" [ref=e5]', []),
    ('button "x" [ref=e5]', []),
    ('- button "x" [ref=../bad]', []),
    ('- button "x" [ref=e]', []),
    ('- button "x" [ref=E5]', []),
    ('- button "x" [ref= e5]', []),
    ('- button "x" [ref=e5 ]', []),
    ('- button "x" [ref=e12345678]', []),
    ('- button "x" [ref=f12345e1]', []),
    ('- button "x" [ref=f1]', []),
    ('- button "x" [ref=e5x]', []),
    ("- text: cv.pdf", []),
    ("", []),
]


@pytest.mark.parametrize(("line", "refs"), LINES)
def test_page_text_cannot_supply_a_native_file_ref(line: str, refs: list[str]) -> None:
    assert pages.button_refs(line) == refs
    read_by_base = [n.ref for n in parse_snapshot(line).walk() if n.role == "button"]
    assert refs == [ref for ref in read_by_base if pages.REF.fullmatch(ref)]
    page = Page(dict.fromkeys(["e2", "e5", "e8", "e999", "e9998", "e9999", "f1e4"]))
    assert read(page, line) == {}
    assert page.refs == refs


def test_the_refs_read_are_the_button_refs_base_reads() -> None:
    corpus = sorted((ROOT / "tests" / "ats_forms").rglob("*.txt"))
    assert corpus
    for path in corpus:
        text = path.read_text(encoding="utf-8")
        read_by_base = [n.ref for n in parse_snapshot(text).walk() if n.role == "button" and n.ref]
        assert pages.button_refs(text) == list(dict.fromkeys(read_by_base)), path.name


def test_a_vanished_ref_reads_empty_and_the_snapshot_is_answered() -> None:
    page = Page({"e3": ["cv.pdf"], "e5": None})
    snapshot = '- button "Resume" [ref=e2]\n- button "Letter" [ref=e3]\n- button "Next" [ref=e5]'
    assert read(page, snapshot) == {"e2": [], "e3": ["cv.pdf"]}
    assert page.refs == ["e2", "e3", "e5"]


@pytest.mark.parametrize(
    "error",
    [
        GoneError("Element is not attached to the DOM"),
        TimeoutError("Timeout 30000ms exceeded."),
        RuntimeError("Execution context was destroyed, most likely because of a navigation"),
        ValueError('Invalid frame in aria-ref selector "aria-ref=f1e4"'),
    ],
    ids=lambda error: type(error).__name__,
)
def test_a_read_that_raises_once_resolved_reads_empty(error: Exception) -> None:
    page = Page({"e2": error, "e3": ["cv.pdf"]})
    assert read(page, '- button "Resume" [ref=e2]\n- button "Letter" [ref=e3]') == {
        "e2": [],
        "e3": ["cv.pdf"],
    }


def test_a_stop_is_never_read_as_an_empty_selection() -> None:
    with pytest.raises(asyncio.CancelledError):
        read(Page({"e2": asyncio.CancelledError()}), '- button "Resume" [ref=e2]')


def test_dom_churn_answers_every_read() -> None:
    """The review's page: forty Yes and No buttons re-created between the snapshot and the reads,
    beside a file input. Each read answers; a button gone reads [], the input its names."""
    snapshot = '- button "Resume *" [ref=e1]\n' + buttons(40, start=2)
    for turn in range(40):
        gone = {f"e{i}" for i in range(2, 42) if (i + turn) % 3 == 0}
        page = Page({"e1": ["cv.pdf"], **{f"e{i}": None for i in range(2, 42)}})
        for ref in gone:
            del page.current[ref]
        assert read(page, snapshot) == {"e1": ["cv.pdf"], **{ref: [] for ref in gone}}
        assert len(page.refs) == 41


@pytest.mark.parametrize(
    "answer",
    [
        "cv.pdf",
        3,
        True,
        {"cv.pdf": 1},
        [3],
        [None],
        [["cv.pdf"]],
        ["a", "b", "c", "d"],
        ["a" * (pages.NAME_MAX + 1)],
        datetime(2026, 1, 1),
        [datetime(2026, 1, 1)],
    ],
    ids=repr,
)
def test_an_answer_the_page_bent_reads_empty(answer: Any) -> None:
    assert read(Page({"e2": answer}), '- button "Resume" [ref=e2]') == {"e2": []}


def test_refs_past_the_cap_are_not_read_and_read_empty() -> None:
    cap = pages.NATIVE_REFS_MAX
    page = Page({f"e{i}": None for i in range(cap + 5)} | {f"e{cap + 4}": ["cv.pdf"]})
    assert read(page, buttons(cap + 5)) == {f"e{i}": [] for i in range(cap, cap + 5)}
    assert page.refs == [f"e{i}" for i in range(cap)]


@pytest.mark.parametrize("letter", ["a", "é", "\U0001f600"], ids=["ascii", "latin", "emoji"])
def test_native_file_metadata_cannot_exceed_the_snapshot_budget(letter: str) -> None:
    name = letter * (pages.NAME_MAX - 4) + ".pdf"
    count = pages.NATIVE_REFS_MAX
    page = Page({f"e{i}": [name] * pages.NAMES_MAX for i in range(count)})
    got = read(page, buttons(count))
    named = {ref: names for ref, names in got.items() if names}
    size = 2 + sum(len(json.dumps({ref: names}, ensure_ascii=True)) for ref, names in named.items())
    assert size <= SNAPSHOT_MAX
    assert list(got) == [f"e{i}" for i in range(count)]
    assert list(named) == [f"e{i}" for i in range(len(named))]
    assert 0 < len(named) < count
    assert all(names == [] for ref, names in got.items() if ref not in named)
