"""A fake of the browser's lease tools (browser B5) over a modelled form, for the adapter tests of
apply A4. It renders Playwright's AI snapshot in the shapes the fake ATS forms of A1 show in
Chrome (recorded in tests/ats_forms/a4/snapshots/) and is as hostile as the tool layer: every
snapshot gives every element a new ref, so a ref kept from an earlier snapshot answers
`stale_ref`; a react-select's options exist only while it is open; a typeahead shows none until
two characters are typed, and only after `load_after` more snapshots; a document not given to the
lease answers `file_not_given`; a page may ignore an option click (`ignore_click`), a button may be
one the tool layer refuses as a submit (`refused`), a captcha may show beside or instead of the
form, and a framed form's company page may carry its own newsletter box (`newsletter`, every call
on it recorded in `outside`). Every call is recorded (`calls`), the submit click included, so a
test asserts what the adapter did and never did."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Spec:
    label: str
    kind: str  # text select react typeahead yesno file checkbox, and unsupported: buttons listbox
    required: bool = False
    options: tuple[str, ...] = ()
    aria_only: bool = False  # a name from aria-label, no visible label text
    pressed_state: bool = True  # a yes or no group that shows aria-pressed (Ashby's may not)
    reject_upload: bool = False
    load_after: int = 0  # snapshots before a typeahead's options show
    ignore_click: bool = False  # the page ignores a click on one of its options
    wrapped: bool = False  # `buttons`: each button inside its own generic
    refused: str = ""  # `yesno`: the button the tool layer refuses as a submit
    # state
    value: str = ""
    typed: str = ""
    checked: bool = False
    open: bool = False
    shown_after: int = 0
    error: str = ""


@dataclass
class FakeForm:
    fields: list[Spec]
    submit: str = "Submit application"
    title: str = "Software Engineer, Agents"
    frame: bool = False  # the form inside a company site's iframe (refs f1eN)
    files: tuple[str, ...] = ("cv.pdf", "letter.pdf")
    pages: dict[str, str] = field(default_factory=dict)  # url -> a page's text instead of the form
    on_call: Callable[[FakeForm, int], None] | None = None  # mutates the form after call n
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    url: str = ""
    cut: bool = False
    newsletter: bool = False  # the company page's own "Email" box, outside the frame
    captcha: str = ""  # "beside" the form, or "instead" of it
    outside: list[str] = field(default_factory=list)  # calls on the newsletter box
    _next: int = 0
    _refs: dict[str, tuple[str, int, str]] = field(default_factory=dict)
    _other: str | None = None
    submit_clicks: int = 0

    # -- rendering --
    def _ref(self, what: str, index: int = -1, part: str = "") -> str:
        self._next += 1
        ref = f"{'f1' if self.frame else ''}e{self._next}"
        self._refs[ref] = (what, index, part)
        return ref

    def render(self) -> str:
        self._refs = {}
        if self._other is not None:
            return f'- heading "{self._other}" [level=1] [ref={self._ref("page")}]'
        pad = "      " if self.frame else "  "
        out = [f'{pad}- heading "{self.title}" [level=1] [ref={self._ref("page")}]']
        robot = f'{pad}- checkbox "I\'m not a robot" [ref={self._ref("captcha")}]'
        if self.captcha:
            out.append(robot)
        if self.captcha != "instead":
            out.append(f"{pad}- generic [ref={self._ref('form')}]:")
            for i, spec in enumerate(self.fields):
                out += [pad + "  " + line for line in self._field(i, spec)]
            out.append(f'{pad}  - button "{self.submit}" [ref={self._ref("submit")}]')
        if self.frame:
            site = [
                f"- generic [active] [ref={self._ref('site')}]:",
                f'  - heading "Careers at Northwind Labs" [level=1] [ref={self._ref("page")}]',
            ]
            if self.newsletter:
                site += [
                    f"  - generic [ref={self._ref('site')}]:",
                    f"    - generic [ref={self._ref('site')}]: Stay in touch",
                    f'    - textbox "Email" [ref={self._ref("newsletter")}]',
                    f'    - button "Subscribe" [ref={self._ref("newsletter")}]',
                ]
            out = [
                *site,
                f"  - iframe [ref={self._ref('iframe')}]:",
                f"    - generic [ref={self._ref('frame')}]:",
                *out,
            ]
        else:
            out = [f"- generic [active] [ref={self._ref('page')}]:", *out]
        return "\n".join(out)

    def _label(self, spec: Spec) -> list[str]:
        if spec.aria_only:
            return []
        if spec.required:
            return [
                f"- generic [ref={self._ref('label')}]:",
                f"  - text: {spec.label}",
                f'  - generic [ref={self._ref("star")}]: "*"',
            ]
        return [f"- generic [ref={self._ref('label')}]: {spec.label}"]

    def _field(self, i: int, s: Spec) -> list[str]:
        name = json.dumps(s.label + (" *" if s.required else ""))
        lines = [f"- generic [ref={self._ref('group', i)}]:"]
        body: list[str] = []
        if s.kind == "checkbox":
            mark = " [checked]" if s.checked else ""
            body.append(f"- checkbox {name}{mark} [ref={self._ref('box', i)}]")
            body += self._label(s)
        else:
            body += self._label(s)
        if s.kind == "text":
            value = f": {json.dumps(s.value)}" if s.value else ""
            body.append(f"- textbox {name} [ref={self._ref('input', i)}]{value}")
        elif s.kind == "select":
            body.append(f"- combobox {name} [ref={self._ref('select', i)}]:")
            for option in ("Select...", *s.options):
                mark = " [selected]" if option == (s.value or "Select...") else ""
                body.append(f"  - option {json.dumps(option)}{mark}")
        elif s.kind == "react":
            body.append(f"- generic [ref={self._ref('control', i)}] [cursor=pointer]:")
            shown = s.value or "Select..."
            body.append(f"  - generic [ref={self._ref('shown', i)}] [cursor=pointer]: {shown}")
            state = " [expanded]" if s.open else ""
            typed = f": {s.typed}" if s.typed else ""
            body.append(f"  - combobox {name}{state} [ref={self._ref('input', i)}]{typed}")
            if s.open:
                shown_options = [o for o in s.options if s.typed.lower() in o.lower()]
                for option in shown_options:
                    ref = self._ref("option", i, option)
                    body.append(f"  - option {json.dumps(option)} [ref={ref}] [cursor=pointer]")
        elif s.kind == "typeahead":
            typed = f": {s.value or s.typed}" if (s.value or s.typed) else ""
            body.append(f"- combobox {name} [ref={self._ref('input', i)}]{typed}")
            if s.typed and len(s.typed) >= 2 and not s.value:
                s.shown_after -= 1
                hits = [o for o in s.options if s.typed.lower() in o.lower()]
                if s.shown_after < 0 and hits:
                    body.append(f"- listbox [ref={self._ref('listbox', i)}]:")
                    for option in hits:
                        ref = self._ref("option", i, option)
                        body.append(f"  - option {json.dumps(option)} [ref={ref}]")
        elif s.kind == "yesno":
            body.append(f"- generic [ref={self._ref('pair', i)}]:")
            for option in ("Yes", "No"):
                mark = " [pressed]" if s.pressed_state and s.value == option else ""
                what = "refused" if s.refused == option else "button"
                body.append(f'  - button "{option}"{mark} [ref={self._ref(what, i, option)}]')
        elif s.kind == "buttons":
            body.append(f"- generic [ref={self._ref('pair', i)}]:")
            for option in s.options:
                ref = self._ref("choice", i, option)
                if s.wrapped:
                    body.append(f"  - generic [ref={self._ref('wrap', i)}]:")
                    body.append(f'    - button "{option}" [ref={ref}]')
                else:
                    body.append(f'  - button "{option}" [ref={ref}]')
        elif s.kind == "listbox":
            body.append(f"- listbox {json.dumps(s.label)} [ref={self._ref('listbox', i)}]:")
            for option in s.options:
                body.append(
                    f"  - option {json.dumps(option)} [ref={self._ref('choice', i, option)}]"
                )
        elif s.kind == "file":
            body.append(f"- button {name} [ref={self._ref('file', i)}]")
            if s.value:
                body.append(f"- generic [ref={self._ref('chosen', i)}]: {s.value}")
        if s.error:
            body.append(f"- alert [ref={self._ref('alert', i)}]: {s.error}")
        return lines + ["  " + b for b in body]

    # -- the tools --
    def _log(self, name: str, **args: Any) -> None:
        self.calls.append((name, args))
        if self.on_call is not None:
            self.on_call(self, len(self.calls))

    def _target(self, ref: str) -> tuple[str, int, str] | None:
        return self._refs.get(ref)

    async def snapshot(self) -> dict[str, Any]:
        self._log("snapshot")
        return {"ok": True, "url": self.url, "snapshot": self.render(), "cut": self.cut}

    async def navigate(self, url: str) -> dict[str, Any]:
        self._log("navigate", url=url)
        self.url = url
        self._other = self.pages.get(url)
        return {"ok": True, "url": url}

    async def click(self, ref: str, element: str = "") -> dict[str, Any]:
        self._log("click", ref=ref, element=element)
        target = self._target(ref)
        if target is None:
            return {"ok": False, "error": "stale_ref"}
        what, i, part = target
        if what == "submit":
            self.submit_clicks += 1
            return {"ok": False, "error": "submit"}  # the tool layer's refusal
        if what == "refused":
            return {"ok": False, "error": "submit"}
        if what == "newsletter":
            self.outside.append("click")
        elif what == "input" and self.fields[i].kind == "react":
            self.fields[i].open = not self.fields[i].open
        elif what == "option" and not self.fields[i].ignore_click:
            spec = self.fields[i]
            spec.value, spec.typed, spec.open = part, "", False
        elif what in ("button", "choice"):
            self.fields[i].value = part
        return {"ok": True}

    async def type(
        self, ref: str, text: str, element: str = "", submit: bool = False
    ) -> dict[str, Any]:
        self._log("type", ref=ref, text=text, submit=submit)
        target = self._target(ref)
        if target is None:
            return {"ok": False, "error": "stale_ref"}
        what, i, _ = target
        if what == "newsletter":
            self.outside.append("type")
            return {"ok": True}
        if what != "input":
            return {"ok": False, "error": "not_editable"}
        spec = self.fields[i]
        if spec.kind == "text":
            spec.value = text
        elif spec.kind == "react":
            spec.typed, spec.open = text, True
        elif spec.kind == "typeahead":
            spec.typed, spec.value, spec.shown_after = text, "", spec.load_after
        return {"ok": True}

    async def select_option(self, ref: str, values: list[str], element: str = "") -> dict[str, Any]:
        self._log("select_option", ref=ref, values=values)
        target = self._target(ref)
        if target is None:
            return {"ok": False, "error": "stale_ref"}
        if target[0] == "newsletter":
            self.outside.append("set")
            return {"ok": True}
        self.fields[target[1]].value = values[0]
        return {"ok": True, "selected": values}

    async def check(self, ref: str, checked: bool = True, element: str = "") -> dict[str, Any]:
        self._log("check", ref=ref, checked=checked)
        target = self._target(ref)
        if target is None:
            return {"ok": False, "error": "stale_ref"}
        if target[0] == "newsletter":
            self.outside.append("set")
            return {"ok": True}
        self.fields[target[1]].checked = checked
        return {"ok": True}

    async def upload(self, ref: str, names: list[str], element: str = "") -> dict[str, Any]:
        self._log("upload", ref=ref, names=names)
        if any(n not in self.files for n in names):
            return {"ok": False, "error": "file_not_given"}
        target = self._target(ref)
        if target is None:
            return {"ok": False, "error": "stale_ref"}
        if target[0] == "newsletter":
            self.outside.append("upload")
            return {"ok": True, "uploaded": names}
        spec = self.fields[target[1]]
        if spec.reject_upload:
            spec.error = "This file type is not supported."
        else:
            spec.value = names[0]
        return {"ok": True, "uploaded": names}

    async def press_key(self, key: str) -> dict[str, Any]:
        self._log("press_key", key=key)
        if key == "Escape":
            for spec in self.fields:
                spec.open = False
        return {"ok": True}

    async def wait_for(
        self, *, time: float | None = None, text: str | None = None, text_gone: str | None = None
    ) -> dict[str, Any]:
        self._log("wait_for", time=time)
        return {"ok": True}

    # -- what tests read --
    def acted(self) -> list[tuple[str, dict[str, Any]]]:
        """The calls that change the page (not snapshot, wait or navigate)."""
        return [c for c in self.calls if c[0] not in ("snapshot", "wait_for", "navigate")]

    def spec(self, label: str) -> Spec:
        return next(s for s in self.fields if s.label == label)


def greenhouse_form(**rest: Any) -> FakeForm:
    """The A1 stub's Greenhouse form, field for field."""
    eeo = ("Male", "Female", "Decline To Self Identify")
    return FakeForm(
        [
            Spec("First Name", "text", True),
            Spec("Last Name", "text", True),
            Spec("Email", "text", True),
            Spec("Phone", "text"),
            Spec("Resume/CV", "file", True),
            Spec("Cover Letter", "file"),
            Spec("Country", "react", True, ("France", "Germany", "United Kingdom")),
            Spec("How did you hear about this job?", "react", False, ("LinkedIn", "Other")),
            Spec("LinkedIn Profile", "text"),
            Spec("Gender", "react", False, eeo),
            Spec(
                "I agree to resolve any employment dispute through binding arbitration.",
                "checkbox",
            ),
            Spec(
                "I would like to receive news and marketing updates from Northwind Labs.",
                "checkbox",
            ),
            Spec("I have read the Applicant Privacy Notice.", "checkbox", True),
        ],
        **rest,
    )


def ashby_form(**rest: Any) -> FakeForm:
    """The A1 stub's Ashby form, field for field."""
    return FakeForm(
        [
            Spec("Full name", "text", True),
            Spec("Email", "text", True),
            Spec("Phone", "text"),
            Spec(
                "Current location",
                "typeahead",
                True,
                ("Paris, France", "Berlin, Germany", "Remote, Europe"),
            ),
            Spec("Resume", "file", True),
            Spec("Cover letter", "file"),
            Spec("Will you now or in the future require visa sponsorship?", "yesno", True),
            Spec("Why Example Robotics?", "text", True),
            Spec("LinkedIn profile", "text"),
            Spec("I agree to the processing of my data for this application.", "checkbox", True),
        ],
        submit="Submit Application",
        title="Applied AI Engineer",
        **rest,
    )
