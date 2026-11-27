"""Fake Chromium pages for the code the person gives in the app (apply A25), the shapes of the
review of PR 849, round 2, each as hard as Chromium where the code goes: every navigation makes a
new document (the old one's handles raise, as Playwright's do), a box keeps its own value and
its maxlength, the page's own script answers each key and each emptying (`fill`), and the
snapshot is written as Playwright's AI snapshot writes it (`- role "name" [ref=eN]: text`).

Shapes: `slots` (one box under six slots that mirror it, shadcn's InputOTP), `stuck` (the same
whose slots never follow an emptying), `six` (six boxes of one character, the focus moving on),
`auto` (a form that sends itself at the last character), `wrong` (the box kept, "That code is
wrong"), `get` (a GET form: the answer page's address and link hold the code), `hide` (one
document that hides the box), `later` (a page that shows the code again at the fourth read, the
model's second snapshot after the code)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd import detect
from browserd.tools import actions, pacing

HOST = "jobs.example.org"
VERIFY = f"https://{HOST}/verify"
SHAPES = ("slots", "stuck", "six", "auto", "wrong", "get", "hide", "later")
SENDS = {"slots", "stuck", "six", "wrong", "get", "hide", "later"}  # a Verify button sends it


def box_facts(name: str, max_length: int, otp: bool) -> dict[str, Any]:
    return {"tag": "input", "type": "text", "autocomplete": ["one-time-code"] if otp else [],
            "names": [name], "max": max_length, "readonly": False, "disabled": False,
            "top": True, "credential": otp}  # fmt: skip


class El:
    """An element of one document, its locator and its handle at once."""

    def __init__(self, doc: Doc, role: str, name: str = "", **kw: Any) -> None:
        self.doc, self.role, self.name = doc, role, name
        self.value, self.text, self.shown = "", kw.get("text", ""), True
        self.facts, self.max = kw.get("facts"), kw.get("max", -1)
        self.kind = kw.get("kind", "button")
        self.ref = f"e{len(doc.els) + 2}"

    def _live(self) -> None:
        if not self.doc.live:
            raise RuntimeError("Execution context was destroyed")

    async def count(self) -> int:
        return 1 if self.doc.live and self in self.doc.els else 0

    async def evaluate(self, script: str, arg: Any = None, **_: Any) -> Any:
        self._live()
        here = self in self.doc.els
        if script == actions.VALUE_JS:
            return self.value if here else None
        if script == actions.SAME_JS:
            return arg is self
        if script == actions.SHOWN_JS:
            return here and self.shown
        if script == actions.FOCUSED_JS:
            return self.doc.focus is self
        if script == actions.FOCUS_TAG_JS:
            return "INPUT" if self.facts else "BUTTON"
        if script == detect.CODE_BOX_JS:
            return {**self.facts, "host": HOST} if self.facts else {"tag": "button", "names": []}
        if script == detect.FACTS_JS:
            own = {"names": [self.name], "tag": "button", "type": self.kind, "role": "",
                   "in_form": True}  # fmt: skip
            return {**own, "point": {**own, "inside": True}}
        raise AssertionError(script[:40])

    async def focus(self, **_: Any) -> None:
        self._live()
        self.doc.focus = self

    async def fill(self, text: str, **_: Any) -> None:
        self._live()
        self.doc.focus, self.value = self, text
        self.doc.page.changed(self)

    async def click(self, **_: Any) -> None:
        self._live()
        self.doc.page.clicked(self)

    async def element_handle(self) -> El:
        return self

    def line(self) -> str:
        if self.facts:
            return f'- textbox "{self.name}" [ref={self.ref}]: {self.value}'
        if self.role == "button":
            return f'- button "{self.name}" [ref={self.ref}]'
        if self.role == "link":
            return f'- link "{self.name}" [ref={self.ref}]:\n  - /url: {self.text}'
        return f'- generic [ref={self.ref}]: "{self.text}"' if self.text else ""


class Doc:
    def __init__(self, page: FakePage, url: str) -> None:
        self.page, self.url, self.live = page, url, True
        self.els: list[El] = []
        self.focus: El | None = None

    def add(self, role: str, name: str = "", **kw: Any) -> El:
        el = El(self, role, name, **kw)
        self.els.append(el)
        return el

    def by_ref(self, ref: str) -> El | None:
        return next((e for e in self.els if e.ref == ref and e.shown), None)


class Keyboard:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def type(self, char: str) -> None:
        box = self.page.doc.focus
        if box is not None and box.facts and (box.max < 0 or len(box.value) < box.max):
            box.value += char
            self.page.changed(box, key=True)

    async def press(self, key: str) -> None:
        box = self.page.doc.focus
        if box is not None and box.facts and key == "Backspace":
            box.value = box.value[:-1]
            self.page.changed(box)


class FakePage:
    def __init__(self, shape: str, code_length: int = 6) -> None:
        self.shape, self.length, self.keyboard = shape, code_length, Keyboard(self)
        self.reads, self.code_seen = 0, ""
        self.doc = Doc(self, "about:blank")
        self.open(VERIFY)

    @property
    def url(self) -> str:
        return self.doc.url

    @property
    def context(self) -> Any:
        return SimpleNamespace(pages=[self])

    def is_closed(self) -> bool:
        return False

    async def title(self) -> str:
        return "Verify your e-mail" if urlsplit(self.url).path == "/verify" else "Welcome"

    def open(self, url: str) -> None:
        self.doc.live = False
        self.doc = doc = Doc(self, url)
        if urlsplit(url).path != "/verify":
            said = parse_qs(urlsplit(url).query).get("otp")
            doc.add("text", text="Your application goes on.")
            if said:  # the GET form's answer says the code back, and links it
                doc.add("text", text=f"You entered {said[0]}")
                doc.add("link", "Not you? Start again", text=f"/verify?otp={said[0]}")
            doc.add("button", "Continue")
            return
        doc.add("text", text="Enter the code we sent")
        if self.shape == "six":
            names = [f"Digit {i + 1}" for i in range(self.length)]
            self.boxes = [
                doc.add("box", name, facts=box_facts(name, 1, i == 0), max=1)
                for i, name in enumerate(names)
            ]
        else:
            self.slots = [doc.add("slot") for _ in range(self.length)] if self.shape in (
                "slots", "stuck") else []  # fmt: skip
            self.boxes = [doc.add("box", "Verification code",
                                  facts=box_facts("Verification code", self.length, True),
                                  max=self.length)]  # fmt: skip
        self.verify = doc.add("button", "Verify", kind="submit") if self.shape in SENDS else None

    def changed(self, box: El, key: bool = False) -> None:
        """The page's own script after a key or an emptying of `box`."""
        if self.shape == "slots" or (self.shape == "stuck" and box.value):
            for i, slot in enumerate(self.slots):
                slot.text = box.value[i] if i < len(box.value) else ""
        if self.shape == "six" and key and box.value:
            at = self.boxes.index(box)
            self.doc.focus = self.boxes[at + 1] if at + 1 < len(self.boxes) else box
        if self.shape == "auto" and key and len(box.value) == self.length:
            self.code_seen = box.value
            self.open(f"https://{HOST}/welcome")

    def clicked(self, el: El) -> None:
        if el is not self.verify:
            return
        value = "".join(b.value for b in self.boxes)
        self.code_seen = value
        if self.shape in ("wrong", "stuck"):
            self.doc.add("text", text="That code is wrong. Try again.")
        elif self.shape == "hide":
            for box in self.boxes:
                box.shown = False
            self.doc.add("text", text="Code accepted.")
        elif self.shape == "get":
            self.open(f"https://{HOST}/welcome?{urlencode({'otp': value})}")
        else:
            self.open(f"https://{HOST}/welcome")

    def snapshot(self) -> str:
        self.reads += 1
        lines = [el.line() for el in self.doc.els if el.shown]
        if self.shape == "later" and self.code_seen and self.reads >= 4:
            lines.append(f'- generic [ref=e99]: "Your code {self.code_seen} expires soon"')
        return "\n".join(line for line in lines if line) or "- generic [ref=e1]"

    async def evaluate_handle(self, _: str) -> Any:
        return self.doc.focus or SimpleNamespace(evaluate=self._body)

    async def _body(self, script: str, *_: Any, **__: Any) -> Any:
        return "BODY" if script == actions.FOCUS_TAG_JS else None

    async def eval_on_selector(self, selector: str, script: str, arg: Any = None) -> Any:
        found = self.doc.by_ref(selector.removeprefix("aria-ref="))
        if found is None:
            raise RuntimeError("No element matches the selector")
        return await found.evaluate(script, arg)

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None


def tab_on(page: FakePage, monkeypatch: Any, purpose: str = "apply_agent",
           gate: bool = False) -> actions.Tab:  # fmt: skip
    """An apply agent lease's tab on `page`, with browserd's own tools; only Playwright's reach
    of the page is the fake's."""

    async def nothing(*_: Any, **__: Any) -> None:
        return None

    tab = actions.Tab(
        request=SimpleNamespace(purpose=purpose, hosts=(), submit_selectors=()),
        state=actions.State(),
        browser=None,
        pacer=pacing.Pacer(sleep=nothing),
        gate=gate,
    )
    tab.state.page = page

    async def the_page(*_: Any, **__: Any) -> FakePage:
        return page

    async def element(args: dict[str, Any]) -> tuple[Any, Any]:
        found = page.doc.by_ref(actions.policy.ref_of(args))
        return (found, None) if found is not None else (None, actions.refused("stale_ref"))

    async def settle(result: dict[str, Any]) -> dict[str, Any]:
        return result

    async def home() -> Any:
        return page.context

    async def go(_: Any, args: dict[str, Any]) -> dict[str, Any]:
        page.open(args["url"])
        return await actions._here(tab)

    async def text(p: Any) -> str:
        return p.snapshot()

    async def none(*_: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(tab, "page", the_page)
    monkeypatch.setattr(tab, "element", element)
    monkeypatch.setattr(tab, "approach", nothing)
    monkeypatch.setattr(tab, "settle", settle)
    monkeypatch.setattr(tab, "home", home)
    monkeypatch.setattr(actions.pages, "snapshot_for_ai", text)
    monkeypatch.setattr(actions.pages, "native_files", none)
    monkeypatch.setattr(actions.pages, "credentials", none)
    monkeypatch.setitem(actions.TOOLS, "browser_navigate", go)
    return tab


def give(tab: actions.Tab, code: str) -> None:
    """routes.give_code's effect on the lease's state."""
    tab.state.code, tab.state.code_host = code, HOST
    assert tab.state.shown.add(code)
