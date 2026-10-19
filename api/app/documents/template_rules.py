"""The rules of a template derived from an uploaded CV (task D25a; SPEC-documents.md, "Design
capture of an upload", steps 2 to 4, and "Templates"). Pure: no file, no clock, no log.

**The checks.** `check(kind, files, photo, fonts, accent) -> [Problem]` is what D25b runs on the
files the design model wrote, before anything renders: [] accepts; else the problems go back to
the model once, then `classic` is kept. A Problem names a file, a line and a code of CODES, never
text of the template, so it can be logged. On top of the authoring rules every template follows
(`templates.problems`; the manifest is `templates.from_files`'s, which D25b calls to store it):
- each `.j2` file is read as Jinja's tree (parsed, never rendered), and only an allow list
  passes: `extends` of `_base/page.html.j2`, `import` of `_base/macros.html.j2` as `base`,
  the block `body`, `if`, `for`, the macros of MACROS with their arguments, the fields of
  FIELDS, `join` with punctuation, `and`, `or`, `not`. Any other construct, name or constant is
  refused, so a template cannot hold a sentence of the person in an expression;
- no literal text: the HTML between the Jinja constructs holds tags of TAGS with a `class`,
  and between them only punctuation of PUNCTUATION (no letter, no digit, no dash);
- every field printed: each field of REQUIRED is printed, by a macro or an output, where every
  test around it is about that field (`{% if roles %}` around a role, not `{% if photo %}`);
- the CSS: only the properties of PROPERTIES; sizes in `em` or `%` or the density property, so
  the fit loop tightens them; each density property read and none set; colours `#rrggbb` (or
  the accent) that a background of the sheet, or the page's white, holds at 4.5:1 (WCAG 2 AA);
  nothing that hides a field (`display: none`, negative offsets); `content` only punctuation;
  font families only those of the manifest; `@page` only `size: A4` and a margin, and a margin
  set there means `--margin` is read elsewhere.

The fit check on the render (the tightest step gives a shorter page than the loosest) and the
fields found in the PDF text are D25b's, which holds the renderer.

Declared open points: a field printed in a box whose size or overflow hides it, or in a colour
close to a background that another rule sets around it, passes these checks; the PDF text check
of D25b is what sees it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser

from jinja2 import Environment, nodes
from jinja2.exceptions import TemplateSyntaxError

from app.documents import font_map, templates

CODES = (
    "authoring", "syntax", "construct", "name", "constant", "literal", "tag", "attribute",
    "unprinted", "density_unread", "density_pinned", "property", "size", "colour", "contrast",
    "content", "font", "page", "structure",
)  # fmt: skip


@dataclass(frozen=True)
class Problem:
    file: str
    line: int  # 0: the whole file
    code: str  # one of CODES
    detail: str  # a fixed phrase, a field or a CSS name of the allow lists

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.detail}"


# --- the Jinja tree -----------------------------------------------------------------------------

PAGE = "_base/page.html.j2"
MACROS_FILE = "_base/macros.html.j2"
LABELS = (
    "summary", "highlights", "competencies", "experience", "education", "skills", "languages",
    "contact", "subject",
)  # fmt: skip

_ROLE = ("organisation", "title", "location", "period")
_SCHOOL = ("school", "degree", "location", "period")


def _fields(kind: str) -> dict[str, str]:
    """Each field a template of `kind` may name, as a path ("roles[].title": the title of an
    item of `roles`), and what it is: text, list (of text), items (a list of records), record."""
    shared = {"name": "text", "contact": "items", "contact[]": "record", "contact[].text": "text"}
    shared |= {"photo": "flag", "content": "record", "content.headline": "text"}
    if kind == "letter":
        letter = dict.fromkeys(("recipient", "subject", "salutation", "closing"), "text")
        return (
            shared
            | {f"content.{k}": v for k, v in letter.items()}
            | {"date": "text", "content.paragraphs": "list", "content.paragraphs[]": "text"}
        )
    found = shared | {"content.summary": "text"}
    for name in ("highlights", "competencies"):
        found |= {f"content.{name}": "list", f"content.{name}[]": "text"}
    found |= {"content.skills": "items", "content.skills[]": "record"}
    found |= {"content.skills[].label": "text", "content.skills[].items": "list"}
    found |= {"content.skills[].items[]": "text"}
    found |= {"roles": "items", "roles[]": "record", "roles[].bullets": "list"}
    found |= {"roles[].bullets[]": "text"} | {f"roles[].{k}": "text" for k in _ROLE}
    found |= {"schools": "items", "schools[]": "record"}
    found |= {f"schools[].{k}": "text" for k in _SCHOOL}
    found |= {"languages": "items", "languages[]": "record"}
    found |= {"languages[].name": "text", "languages[].level": "text"}
    return found


FIELDS = {kind: _fields(kind) for kind in templates.KINDS}
REQUIRED = {
    "cv": (
        "name", "contact[].text", "content.headline", "content.summary", "content.highlights",
        "content.competencies", "content.skills[].label", "content.skills[].items",
        *(f"roles[].{k}" for k in (*_ROLE, "bullets")), *(f"schools[].{k}" for k in _SCHOOL),
        "languages[].name", "languages[].level",
    ),
    "letter": (
        "name", "contact[].text", "content.headline", "date", "content.recipient",
        "content.subject", "content.salutation", "content.paragraphs", "content.closing",
    ),
}  # fmt: skip

# The macros of _base/macros.html.j2: each parameter, and what it takes (a label key, a class
# name, a field of that kind or that path); then the fields a call prints besides its arguments.
MACROS: dict[str, tuple[tuple[str, str], ...]] = {
    "label": (("key", "key"),),
    "contact_lines": (("class", "class"),),
    "portrait": (("class", "class"),),
    "header": (("headline", "text"),),
    "bullets": (("items", "list"), ("class", "class")),
    "employer": (("role", "roles[]"),),
    "school": (("item", "schools[]"),),
    "language": (("item", "languages[]"),),
    "skill_group": (("group", "content.skills[]"),),
    "paragraphs": (("items", "list"),),
    "letter_date": (),
}
MACRO_PRINTS = {
    "header": ("name", "contact[].text", "photo"),
    "contact_lines": ("contact[].text",),
    "portrait": ("photo",),
    "letter_date": ("date",),
}
_CLASS = re.compile(r"[a-z][a-z0-9-]{0,39}( [a-z][a-z0-9-]{0,39}){0,3}")
PUNCTUATION = frozenset(",:;·•|/().&")
_SLOT = "\ue000"  # where an output stands in the HTML between the constructs


@dataclass(frozen=True)
class _Guard:
    """A test around an output, reduced to what it is about: `paths` (any of them, for `or`;
    all of them, for `and`), or nothing it could vouch for (`not`, an `else`, a loop test)."""

    paths: tuple[str, ...] = ()
    every: bool = False

    def about(self, path: str) -> bool:
        hits = [path == p or path.startswith((p + ".", p + "[]")) for p in self.paths]
        return bool(hits) and (all(hits) if self.every else any(hits))


_NEVER = _Guard()


class _Walk:
    def __init__(self, file: str, kind: str) -> None:
        self.file = file
        self.fields = FIELDS[kind]
        self.problems: list[Problem] = []
        self.printed: set[str] = set()
        self.html: list[tuple[int, str]] = []

    def refuse(self, node: nodes.Node, code: str, detail: str) -> None:
        self.problems.append(Problem(self.file, getattr(node, "lineno", 0) or 0, code, detail))

    def mark(self, path: str, guards: tuple[_Guard, ...]) -> None:
        if all(guard.about(path) for guard in guards):
            self.printed.add(path)
            if path.endswith("[]") and self.fields.get(path[:-2]) == "list":
                self.printed.add(path[:-2])

    def mark_record(self, path: str, guards: tuple[_Guard, ...]) -> None:
        for field, what in self.fields.items():
            if field.startswith(path + ".") and what in ("text", "list"):
                self.mark(field, guards)

    def body(self, items: list[nodes.Node], scope: dict[str, str], guards: tuple) -> None:
        for item in items:
            self.statement(item, scope, guards)

    def statement(self, node: nodes.Node, scope: dict[str, str], guards: tuple) -> None:
        if isinstance(node, nodes.Extends):
            if not (isinstance(node.template, nodes.Const) and node.template.value == PAGE):
                self.refuse(node, "construct", f"extends only {PAGE}")
        elif isinstance(node, nodes.Import):
            ok = isinstance(node.template, nodes.Const) and node.template.value == MACROS_FILE
            if not (ok and node.target == "base" and node.with_context):
                self.refuse(node, "construct", f"import only {MACROS_FILE} as base with context")
        elif isinstance(node, nodes.Block):
            if node.name != "body":
                self.refuse(node, "construct", "a block other than body")
            self.body(node.body, scope, guards)
        elif isinstance(node, nodes.Output):
            for child in node.nodes:
                if isinstance(child, nodes.TemplateData):
                    self.html.append((child.lineno, child.data))
                else:
                    self.html.append((child.lineno, _SLOT))
                    self.output(child, scope, guards)
        elif isinstance(node, nodes.If):
            self.body(node.body, scope, (*guards, self.test(node.test, scope)))
            for branch in node.elif_:
                self.test(branch.test, scope)
                self.body(branch.body, scope, (*guards, _NEVER))
            self.body(node.else_, scope, (*guards, _NEVER))
        elif isinstance(node, nodes.For):
            source = self.expr(node.iter, scope)
            what = self.fields.get(source or "")
            if not isinstance(node.target, nodes.Name) or what not in ("list", "items"):
                self.refuse(node, "construct", "a for over a list field, into one name")
                return
            if node.test is not None or node.recursive:
                self.refuse(node, "construct", "a for with a test or recursive")
            inner = {**scope, node.target.name: f"{source}[]", "loop": "loop"}
            self.body(node.body, inner, (*guards, _Guard((source,))))
            self.body(node.else_, scope, (*guards, _NEVER))
        else:
            self.refuse(node, "construct", f"the construct {type(node).__name__}")

    def test(self, node: nodes.Expr, scope: dict[str, str]) -> _Guard:
        if isinstance(node, nodes.And | nodes.Or):
            left, right = self.test(node.left, scope), self.test(node.right, scope)
            every = isinstance(node, nodes.And)
            mixed = {left.every, right.every} != {every} and left.paths and right.paths
            if not (left.paths and right.paths) or mixed:
                return _NEVER if every else _Guard(left.paths + right.paths)
            return _Guard(left.paths + right.paths, every)
        if isinstance(node, nodes.Not):
            self.test(node.node, scope)
            return _NEVER
        path = self.expr(node, scope)
        if path == "loop" or (path or "").startswith("loop."):
            return _NEVER
        return _Guard((path,)) if path else _NEVER

    def expr(self, node: nodes.Expr, scope: dict[str, str]) -> str | None:
        """The field path `node` names, None (and a problem) when it names none."""
        if isinstance(node, nodes.Name):
            if node.name in scope:
                return scope[node.name]
            if node.name in self.fields:
                return node.name
            self.refuse(node, "name", "a name that is not a field")
            return None
        if isinstance(node, nodes.Getattr | nodes.Getitem):
            key = node.attr if isinstance(node, nodes.Getattr) else node.arg
            if isinstance(key, nodes.Const):
                key = key.value
            parent = self.expr(node.node, scope)
            if parent is None:
                return None
            if parent == "loop" and key in ("first", "last"):
                return f"loop.{key}"
            path = f"{parent}.{key}" if isinstance(key, str) else None
            if path not in self.fields:
                self.refuse(node, "name", f"a field that {parent} has not")
                return None
            return path
        if isinstance(node, nodes.Const):
            self.refuse(node, "constant", "a constant outside a label or a class")
        else:
            self.refuse(node, "construct", f"the expression {type(node).__name__}")
        return None

    def output(self, node: nodes.Expr, scope: dict[str, str], guards: tuple) -> None:
        if isinstance(node, nodes.Call):
            self.call(node, scope, guards)
            return
        if isinstance(node, nodes.Filter):
            separator = node.args[0] if len(node.args) == 1 else None
            punctuation = isinstance(separator, nodes.Const) and _punctuation(separator.value)
            path = self.expr(node.node, scope) if node.node is not None else None
            if node.name != "join" or node.kwargs or not punctuation:
                self.refuse(node, "construct", "a filter other than join with punctuation")
            elif path and self.fields.get(path) == "list":
                self.mark(path, guards)
            return
        path = self.expr(node, scope)
        if path and self.fields.get(path) == "text":
            self.mark(path, guards)
        elif path:
            self.refuse(node, "construct", "a list, a record, a flag or a loop printed whole")

    def call(self, node: nodes.Call, scope: dict[str, str], guards: tuple) -> None:
        target = node.node
        is_base = isinstance(target, nodes.Getattr) and isinstance(target.node, nodes.Name)
        if not (is_base and target.node.name == "base" and target.attr in MACROS):
            self.refuse(node, "construct", "a call of anything but a macro of base")
            return
        if node.dyn_args is not None or node.dyn_kwargs is not None:
            self.refuse(node, "construct", "a call with * or **")
            return
        params = dict(MACROS[target.attr])
        given = list(zip(params, node.args, strict=False))
        given += [(kw.key, kw.value) for kw in node.kwargs]
        if len(node.args) > len(params) or any(name not in params for name, _ in given):
            self.refuse(node, "construct", f"an argument {target.attr} does not take")
            return
        for field in MACRO_PRINTS.get(target.attr, ()):
            self.mark(field, guards)
        for name, value in given:
            self.argument(target.attr, params[name], value, scope, guards)

    def argument(self, macro: str, takes: str, value: nodes.Expr, scope, guards) -> None:
        if takes in ("key", "class"):
            text = value.value if isinstance(value, nodes.Const) else None
            ok = (
                text in LABELS
                if takes == "key"
                else isinstance(text, str) and _CLASS.fullmatch(text)
            )
            if not ok:
                self.refuse(
                    value,
                    "constant",
                    f"{macro}: a {'label key' if takes == 'key' else 'class name'}",
                )
            return
        path = self.expr(value, scope)
        if path is None:
            return
        what = self.fields.get(path)
        if takes in ("text", "list") and what == takes:
            self.mark(path, guards)
        elif takes == path:
            self.mark_record(path, guards)
        else:
            self.refuse(value, "construct", f"{macro}: an argument of another kind")


def _punctuation(value: object) -> bool:
    return isinstance(value, str) and all(ch.isspace() or ch in PUNCTUATION for ch in value)


TAGS = frozenset(
    (
        "div", "section", "article", "aside", "header", "footer", "main", "span", "p", "ul", "ol",
        "li", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i", "small", "br", "hr",
        "table", "thead", "tbody", "tr", "td", "th",
    )
)  # fmt: skip


class _Html(HTMLParser):
    """The HTML between the constructs, fed in order, the outputs as _SLOT."""

    def __init__(self, walk: _Walk) -> None:
        super().__init__(convert_charrefs=True)
        self.walk = walk
        self.line = 0

    def _refuse(self, code: str, detail: str) -> None:
        self.walk.problems.append(Problem(self.walk.file, self.line, code, detail))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in TAGS:
            self._refuse("tag", "a tag outside the allowed list")
        for key, value in attrs:
            if key == "class" and value is not None and _CLASS.fullmatch(value.strip()):
                continue
            if key in ("colspan", "rowspan") and (value or "").isdigit():
                continue
            self._refuse("attribute", "an attribute other than a class name")

    handle_startendtag = handle_starttag

    def handle_data(self, data: str) -> None:
        if not all(ch.isspace() or ch in PUNCTUATION or ch == _SLOT for ch in data):
            self._refuse("literal", "literal text outside the label macro")

    def handle_comment(self, data: str) -> None:
        self._refuse("literal", "an HTML comment (a Jinja comment is not printed)")

    def handle_decl(self, decl: str) -> None:
        self._refuse("tag", "a declaration")

    handle_pi = handle_decl
    unknown_decl = handle_decl


def check_html(kind: str, file: str, source: str) -> list[Problem]:
    """The problems of one `.j2` file of `kind`, every field of REQUIRED printed included."""
    walk = _Walk(file, kind)
    try:
        tree = Environment(autoescape=True).parse(source)
    except TemplateSyntaxError as error:
        return [Problem(file, error.lineno or 0, "syntax", "Jinja that does not parse")]
    walk.body(tree.body, {}, ())
    parser = _Html(walk)
    for line, data in walk.html:
        parser.line = line
        parser.feed(data)
    parser.close()
    walk.problems += [
        Problem(file, 0, "unprinted", f"{field} is not printed, or only under another test")
        for field in REQUIRED[kind]
        if field not in walk.printed
    ]
    return walk.problems


# --- the CSS ------------------------------------------------------------------------------------

DENSITY = ("--body-size", "--line-height", "--section-gap", "--item-gap", "--margin")
PROPERTIES = frozenset(
    (
        "margin", "margin-top", "margin-right", "margin-bottom", "margin-left", "padding",
        "padding-top", "padding-right", "padding-bottom", "padding-left", "box-sizing",
        "font-family", "font-size", "font-weight", "font-style", "font-variant",
        "font-variant-caps", "line-height", "letter-spacing", "text-transform", "text-align",
        "text-decoration", "vertical-align", "white-space", "overflow-wrap", "word-break",
        "color", "background", "background-color", "display", "gap", "row-gap", "column-gap",
        "align-items", "align-self", "align-content", "justify-content", "justify-items",
        "justify-self", "flex", "flex-wrap", "flex-direction", "flex-grow", "flex-shrink",
        "flex-basis", "order", "grid-template-columns", "grid-template-rows", "grid-column",
        "grid-row", "grid-area", "grid-auto-flow", "grid-auto-rows", "columns", "column-count",
        "column-width", "break-inside", "break-before", "break-after", "page-break-inside",
        "page-break-before", "page-break-after", "width", "height", "min-width", "min-height",
        "max-width", "object-fit", "object-position", "border", "border-top", "border-right",
        "border-bottom", "border-left", "border-color", "border-style", "border-width",
        "border-radius", "border-collapse", "border-spacing", "list-style", "list-style-type",
        "list-style-position", "position", "left", "top", "right", "bottom", "content",
    )
)  # fmt: skip
DISPLAY = frozenset(
    ("block", "inline", "inline-block", "flex", "grid", "list-item", "table", "table-row",
     "table-cell", "table-row-group", "table-header-group")
)  # fmt: skip
POSITION = frozenset(("static", "relative", "absolute"))
GENERIC = frozenset(("serif", "sans-serif", "monospace"))
CONTENT_CHARS = PUNCTUATION | frozenset("\u00a0 ◦▪")
AA = 4.5  # WCAG 2, contrast of body text
WHITE = "#ffffff"
_STRING = "\ue001"  # where a string stands in a value
_HEX = re.compile(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})")
_EM = re.compile(r"(-?\d*\.?\d+)(em|%)?")


@dataclass(frozen=True)
class _Declaration:
    page: bool  # inside @page
    prop: str
    value: str  # lower case, comments gone, `!important` gone, each string as _STRING
    strings: tuple[str, ...]  # the strings of the value, their escapes read
    line: int


def _string(css: str, at: int) -> tuple[str, int, bool]:
    """The string that starts at `at`, its escapes read; where it ends; whether it is closed."""
    quote, out, i = css[at], [], at + 1
    while i < len(css):
        ch = css[i]
        if ch == quote:
            return "".join(out), i + 1, True
        if ch == "\n":
            break
        if ch == "\\":
            digits = re.match(r"[0-9a-fA-F]{1,6}\s?", css[i + 1 : i + 8])
            if digits:
                point = int(digits.group().strip(), 16)
                out.append(chr(point) if 0 < point <= 0x10FFFF else "�")
                i += 1 + len(digits.group())
                continue
            out.append(css[i + 1 : i + 2].strip("\n"))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), i, False


def _sheet(css: str, file: str) -> tuple[list[_Declaration], list[Problem]]:
    """The declarations of a style sheet in one pass, and its structure problems: a nested
    block, an at-rule other than @page, an escape outside a string, an unclosed string."""
    found: list[_Declaration] = []
    problems: list[Problem] = []
    buffer: list[str] = []
    strings: list[str] = []
    line, depth, page, i = 1, 0, False, 0

    def refuse(detail: str) -> None:
        problems.append(Problem(file, line, "structure", detail))

    def flush() -> None:
        text = "".join(buffer).strip()
        if text:
            prop, colon, value = text.partition(":")
            value = value.lower().replace("!important", "").strip()
            if not colon:
                refuse("a declaration without a colon")
            else:
                found.append(_Declaration(page, prop.strip().lower(), value, tuple(strings), line))
        buffer.clear()
        strings.clear()

    while i < len(css):
        ch = css[i]
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = len(css) if end < 0 else end + 2
            line += css.count("\n", i, end)
            buffer.append(" ")
            i = end
            continue
        if ch in "\"'":
            text, i, closed = _string(css, i)
            if not closed:
                refuse("a string that is not closed")
            strings.append(text)
            buffer.append(_STRING)
            continue
        if ch == "\\":
            refuse("an escape outside a string")
        elif ch == "{":
            depth += 1
            if depth == 1:
                prelude = "".join(buffer).strip().lower()
                page = prelude.startswith("@page")
                if prelude.startswith("@") and not page:
                    refuse("an at-rule other than @page")
                buffer.clear()
                strings.clear()
            else:
                refuse("a block inside a block")
        elif ch == "}":
            if depth == 1:
                flush()
            depth = max(depth - 1, 0)
        elif ch == ";" and depth == 1:
            flush()
        elif ch == ";" and depth == 0:
            refuse("an at-rule other than @page")
            buffer.clear()
            strings.clear()
        else:
            line += ch == "\n"
            buffer.append(ch)
        i += 1
    if depth:
        refuse("a block that is not closed")
    return found, problems


def _six(colour: str) -> str:
    return colour if len(colour) == 7 else "#" + "".join(ch * 2 for ch in colour[1:])


def luminance(colour: str) -> float:
    """The relative luminance of `#rrggbb` (WCAG 2)."""
    channels = (int(colour[k : k + 2], 16) / 255 for k in (1, 3, 5))
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one: str, other: str) -> float:
    """The contrast ratio of two `#rrggbb` colours, from 1 to 21 (WCAG 2)."""
    light, dark = sorted((luminance(one), luminance(other)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _within(value: str, units: tuple[str | None, ...], low: float, high: float) -> bool:
    found = _EM.fullmatch(value)
    return bool(found) and found.group(2) in units and low <= float(found.group(1)) <= high


def _colour(value: str, accent: str) -> str | None:
    if value == "var(--accent)":
        return accent
    return _six(value) if _HEX.fullmatch(value) else None


def _value(d: _Declaration, names: frozenset[str], backs: set[str], accent: str) -> tuple | None:
    """The code and the detail of what is wrong with one declaration, None when nothing."""
    value = d.value
    if d.page:
        if d.prop == "size":
            return (
                None if value in ("a4", "a4 portrait") else ("page", "@page: a size other than A4")
            )
        return None if d.prop.startswith("margin") else ("page", "@page: only size and margin")
    if d.prop in DENSITY or d.prop == "--accent":
        return "density_pinned", f"{d.prop} is set by the density step, not by the sheet"
    if d.prop.startswith("--"):
        return None
    if d.prop not in PROPERTIES:
        return "property", "a property outside the allowed list"
    relative = _within(value, ("em",), 0.5, 4) or _within(value, ("%",), 50, 400)
    if d.prop == "font-size" and value not in ("var(--body-size)", "inherit") and not relative:
        return "size", "font-size: em from 0.5 to 4, % or var(--body-size)"
    plain = value in ("var(--line-height)", "normal", "inherit") or _within(
        value, (None,), 0.9, 2.5
    )
    if d.prop == "line-height" and not plain:
        return "size", "line-height: a number from 0.9 to 2.5 or var(--line-height)"
    spacing = value in ("normal", "0") or _within(value, ("em",), -0.05, 0.5)
    if d.prop == "letter-spacing" and not spacing:
        return "size", "letter-spacing: em from -0.05 to 0.5"
    if d.prop == "display" and value not in DISPLAY:
        return "property", "display: a value that shows the box"
    if d.prop == "position" and value not in POSITION:
        return "property", "position: static, relative or absolute"
    if d.prop in ("left", "top", "right", "bottom") and value.startswith("-"):
        return "property", f"{d.prop}: a negative offset"
    if d.prop == "content" and value not in ("none", "normal"):
        text = d.strings[0] if value == _STRING and len(d.strings) == 1 else None
        if text is None or not all(ch in CONTENT_CHARS for ch in text):
            return "content", "content: punctuation only"
    if d.prop == "font-family":
        items = iter(d.strings)
        for item in value.split(","):
            item = item.strip()
            name = next(items, "") if item == _STRING else " ".join(item.split())
            if name.casefold() not in names:
                return "font", "font-family: a family the manifest does not name"
    flat = all(t in ("none", "transparent", "inherit") or _colour(t, accent) for t in value.split())
    if d.prop in ("background", "background-color") and not flat:
        return "colour", f"{d.prop}: a flat colour written #rrggbb or var(--accent)"
    if d.prop == "color" and value not in ("inherit", "currentcolor"):
        colour = _colour(value, accent)
        if colour is None:
            return "colour", "color: #rrggbb or var(--accent)"
        if max(contrast(colour, back) for back in backs) < AA:
            return "contrast", "color: under 4.5 to 1 on every background of the sheet"
    return None


def check_css(
    css: str, fonts: tuple[str, ...], accent: str, file: str = "style.css"
) -> list[Problem]:
    """The problems of a template's style sheet (see the module docstring)."""
    declarations, problems = _sheet(css, file)
    names = GENERIC | {
        font_map.FAMILIES[f].name.casefold() for f in fonts if f in font_map.FAMILIES
    }
    backs = {WHITE}
    for d in declarations:
        if d.prop in ("background", "background-color"):
            backs |= {c for t in d.value.split() if (c := _colour(t, accent))}
    for d in declarations:
        wrong = _value(d, frozenset(names), backs, accent)
        if wrong:
            problems.append(Problem(file, d.line, *wrong))
    reads = {
        "--body-size": ("font-size",),
        "--line-height": ("line-height",),
        "--section-gap": None,
        "--item-gap": None,
    }
    if any(d.page and d.prop.startswith("margin") for d in declarations):
        reads["--margin"] = None
    for name, props in reads.items():
        if not any(
            f"var({name})" in d.value and (props is None or d.prop in props)
            for d in declarations
            if not d.page
        ):
            where = f" by {props[0]}" if props else ""
            problems.append(Problem(file, 0, "density_unread", f"{name} is not read{where}"))
    return problems


def check(
    files: Mapping[str, str], *, photo: str, fonts: tuple[str, ...], accent: str
) -> list[Problem]:
    """Every problem of a template's three files, in a fixed order; [] accepts it."""
    found = []
    for line in templates.problems(files, photo):
        file, _, detail = line.partition(": ")
        found.append(Problem(file, 0, "authoring", detail))
    for kind in templates.KINDS:
        name = f"{kind}.html.j2"
        found += check_html(kind, name, files.get(name, ""))
    accent = _six(accent.lower()) if _HEX.fullmatch(accent.lower()) else WHITE
    found += check_css(files.get("style.css", ""), fonts, accent)
    return found
