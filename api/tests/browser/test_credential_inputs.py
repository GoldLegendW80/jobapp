"""browserd's read of credential inputs without a browser (browser B5, detect.CREDENTIAL_FN): the
snapshot says whether each text entry is one, and a typing into one is refused, whoever asks and
whatever the element's label. The read on a real Chromium is test_credential_container.py."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    not (ROOT / "browser/browserd").is_dir(), reason="the repository root is not mounted here"
)
sys.path.insert(0, str(ROOT / "browser"))

from browserd import detect, pages  # noqa: E402

from tests.browser.test_b5_actions import Page, acted, call, facts, snapshot_stub  # noqa: E402

SECRET_WORD = facts("Secret word", kind="password", field="w", credential=True)
SILENT = {k: v for k, v in facts("Email", kind="email").items() if k != "credential"}


@pytest.mark.parametrize(
    "box",
    [
        SECRET_WORD,
        facts("Enter the code", field="one-time-code", credential=True),
        facts("Email", kind="email", field="username email", credential=True),
        SILENT,  # facts that say nothing of it: fail closed
    ],
)
def test_a_typing_into_a_credential_input_is_refused_and_nothing_is_typed(box: dict) -> None:
    page = Page()
    page.add("e5", box)
    result = call(page, "browser_type", {"ref": "e5", "text": "anything"})
    assert (result["ok"], result["error"]) == (False, "credential")
    assert acted(page) == []


def test_a_plain_entry_is_typed_and_a_credential_input_is_still_clicked() -> None:
    page = Page()
    page.add("e5", facts("Email", kind="email", field="email"))
    page.add("e6", SECRET_WORD)
    assert call(page, "browser_type", {"ref": "e5", "text": "a"})["ok"]
    assert call(page, "browser_click", {"ref": "e6"})["ok"]


def test_the_point_a_typing_lands_on_is_judged_as_a_credential_too() -> None:
    plain = facts("Email", kind="email", field="email")
    point = {**SECRET_WORD, "inside": True}
    assert detect.verdict({**plain, "point": point}, action="type") == "credential"
    assert detect.verdict({**plain, "point": {**plain, "inside": True}}, action="type") is None


def test_the_facts_and_the_snapshot_read_the_same_credential_function() -> None:
    assert detect.CREDENTIAL_FN in detect.FACTS_JS
    assert "credential: isCredential(control)" in detect.FACTS_JS
    assert detect.CREDENTIAL_FN in pages.CREDENTIAL_JS
    assert str(detect.CREDENTIAL_SCAN) in detect.CREDENTIAL_FN


class GoneError(Exception):
    """Playwright's answer at once for a ref that no longer resolves."""


class ReadPage(Page):
    """`eval_on_selector` as Playwright answers it for the credential read."""

    def __init__(self, read: dict[str, Any]) -> None:
        super().__init__()
        self.read, self.refs = read, []

    async def eval_on_selector(self, selector: str, script: str) -> Any:
        assert script == pages.CREDENTIAL_JS
        ref = selector.removeprefix("aria-ref=")
        self.refs.append(ref)
        found = self.read.get(ref, GoneError(ref))
        if isinstance(found, BaseException):
            raise found
        return found


SNAPSHOT = """- generic [ref=e1]:
  - textbox "Email" [ref=e2]: someone@example.org
  - textbox "Secret word" [ref=e3]
  - spinbutton "Enter the code" [ref=e4]
  - combobox "Country" [ref=e5]:
    - option "France" [selected]
  - searchbox "Search" [ref=f1e6]
  - textbox "Gone" [ref=e7]
  - textbox "Odd" [ref=e8]
  - button "Next" [ref=e9]
  - heading "Sign in" [level=1] [ref=e10]
  - text: textbox "Not a line" [ref=e11]
  - textbox "Value naming a ref" [ref=e12]: x [ref=e9999]
"""


def test_the_read_answers_each_text_entry_and_leaves_out_what_it_cannot_hold() -> None:
    page = ReadPage(
        {"e2": False, "e3": True, "e4": True, "e5": False, "f1e6": False, "e8": "yes", "e12": None}
    )
    found = asyncio.run(pages.credentials(page, SNAPSHOT))
    assert found == {"e2": False, "e3": True, "e4": True, "e5": False, "f1e6": False}
    assert page.refs == ["e2", "e3", "e4", "e5", "f1e6", "e7", "e8", "e12"]


def test_entries_past_the_cap_are_left_unread() -> None:
    count = pages.CREDENTIAL_REFS_MAX + 5
    text = "\n".join(f'- textbox "Box {i}" [ref=e{i}]' for i in range(count))
    page = ReadPage({f"e{i}": True for i in range(count)})
    found = asyncio.run(pages.credentials(page, text))
    assert len(found) == len(page.refs) == pages.CREDENTIAL_REFS_MAX


def test_the_snapshot_answers_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_stub(monkeypatch)  # one text entry, e5
    page = ReadPage({"e5": True})
    result = call(page, "browser_snapshot", {})
    assert (result["ok"], result["credentials"]) == (True, {"e5": True})
