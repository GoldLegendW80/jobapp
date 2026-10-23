"""File metadata belongs to current native controls, never the upload response or page text."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "browser"))

from browserd import pages  # noqa: E402


class Selection:
    def __init__(self, value: Any, present: bool = True) -> None:
        self.value, self.present = value, present

    async def count(self) -> int:
        return int(self.present)

    async def evaluate(self, script: str, **kwargs: Any) -> Any:
        assert "files" in script
        assert "name" in script
        assert kwargs == {"timeout": 5000}
        return self.value


class Page:
    def __init__(self) -> None:
        self.current = {"e2": ["cv.pdf"], "e3": None, "f1e4": []}
        self.refs: list[str] = []

    def locator(self, selector: str) -> Selection:
        ref = selector.removeprefix("aria-ref=")
        self.refs.append(ref)
        return Selection(self.current.get(ref), ref in self.current)


def test_native_file_metadata_is_fresh_and_includes_empty_selection() -> None:
    page = Page()
    snapshot = """- generic [ref=e1]:
  - button "Resume *" [ref=e2]
  - button "Attach letter" [ref=e3]
  - button "Resume *" [ref=f1e4]
  - text: uploaded cv.pdf [ref=e999]
"""

    async def go() -> None:
        assert await pages.native_files(page, snapshot) == {"e2": ["cv.pdf"], "f1e4": []}
        page.current["e2"] = []
        assert await pages.native_files(page, snapshot) == {"e2": [], "f1e4": []}
        page.current["e2"] = ["other.pdf"]
        assert await pages.native_files(page, snapshot) == {"e2": ["other.pdf"], "f1e4": []}
        assert "e999" not in page.refs

    asyncio.run(go())


@pytest.mark.parametrize("snapshot", ["- text: cv.pdf", '- button "Resume" [ref=../bad]'])
def test_page_text_cannot_supply_a_native_file_ref(snapshot: str) -> None:
    assert asyncio.run(pages.native_files(Page(), snapshot)) == {}


def test_a_changed_ref_refuses_the_snapshot_instead_of_reusing_selection() -> None:
    page = Page()
    del page.current["e2"]
    with pytest.raises(pages.SnapshotUnavailable, match="ref changed"):
        asyncio.run(pages.native_files(page, '- button "Resume" [ref=e2]'))


def test_native_file_metadata_cannot_exceed_the_snapshot_budget() -> None:
    page = Page()
    page.current = {f"e{i}": ["a" * 78 + ".pdf"] * 3 for i in range(300)}
    snapshot = "\n".join(f'- button "Resume" [ref=e{i}]' for i in range(300))
    with pytest.raises(pages.SnapshotUnavailable, match="budget"):
        asyncio.run(pages.native_files(page, snapshot))
