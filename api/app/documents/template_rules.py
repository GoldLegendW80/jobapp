"""The rules of a template derived from an uploaded CV (task D25a; SPEC-documents.md, "Design
capture of an upload", steps 2 to 4, and "Templates"). Pure: no clock, no log; the only files
read are the bundled `_base` ones.

**The checks.** `check(files, photo, fonts, accent) -> [Problem]` is what D25b runs on the
files the design model wrote, before anything renders: [] accepts; else the problems go back to
the model once, then `classic` is kept. A Problem names a file, a line and a code of CODES, never
text of the template, so it can be logged. On top of the authoring rules every template follows
(`templates.problems`; the manifest is `templates.from_files`'s, which D25b calls to store it):
- each `.j2` file is read as Jinja's own parser reads it (never rendered at this stage), and
  only an allow list passes. At the top level: `extends` of `_base/page.html.j2`, `import` of
  `_base/macros.html.j2` as `base`, the block `body`, whitespace; anything else there is
  refused, since Jinja drops what an extending template holds outside its blocks. In the body:
  `if`, `for` over a list field (a `for` inside a `for` only over its item's own list), the
  macros of MACROS with their arguments, the fields of FIELDS read as keys (`group["items"]`,
  never the dict method `group.items`), `join` with punctuation over a list field, `and`, `or`,
  `not`. Any other construct, name or constant is refused, so a template cannot hold a sentence
  of the person in an expression;
- no literal text: the HTML between the Jinja constructs holds tags of TAGS with a `class`, and
  between them only punctuation of PUNCTUATION (no letter, no digit, no dash); a tag, an
  attribute, a comment or a character reference cut by a construct is refused, since what the
  check reads there is not what the render prints; the markup of each `if`, `elif`, `else` and
  `for` body is balanced on its own, so no shape of the content leaves a tag open; `if` and
  `for` nest at most JINJA_DEPTH deep;
- every field printed: each field of REQUIRED is printed, by a macro or an output, where every
  test around it is about that field (`{% if roles %}` around a role, not `{% if photo %}`);
- the render: a file that passed the above is rendered in process on canary content, every
  field filled with a token of its own and every field empty. The HTML printed is parsed: its
  text is only canaries, whitespace and punctuation, its tags those of TAGS (and the macros'
  `a` and `img`), its attributes those of an allow list, and every field of REQUIRED shows its
  canary. Its elements follow the content model the browser's tree builder keeps as written
  (the HTML standard's "in body" and table insertion modes): no element the builder would close,
  move or wrap is accepted, rather than a model of the repair. So no block (_CLOSES_P) while a
  `p` is open below the nearest cell or table, a `li` only as a child of `ul` or `ol`, a heading
  never right inside a heading, the parts of a table only in their place (`tr` only in `thead`
  or `tbody`), no text in a table outside a cell, no `/>` on an element that is not void, no
  `rowspan` (cells of two rows could then share a slot);
- the CSS: `template_css.check`, a static filter of allow lists (see that module): it refuses
  what places a box over another (position, offsets, z-index, negative margins, grid rows and
  areas, any transform), any box a `::before` or `::after` would draw, and a marker string.

What the static filter guarantees: no text of the template, every field printed in a tree the
browser keeps as written, none of the CSS constructs above. Whether every field is truly
visible (contrast on the painted background, a box smaller than its text, inside the page) is
judged on the render by D25b (issue #220), with the fit check and the fields found in the PDF
text, since D25b holds the renderer.

**The tokens.** `from_pdf(DesignPdf)` and `from_docx(DesignDocx)` -> Tokens: what the layout
extraction of D25a's sandbox kinds says of the look, as values the template system takes: the
page (always A4, the spec's), the side margin, one or two columns and the narrower one's side,
the body size, the line height, the name's and the headings' scale, the text colour, the accent,
a band or column fill and the text on it, the fonts through `font_map`. A value that cannot be
read, or falls outside BOUNDS, or a colour under 4.5:1, is replaced by its named default
(DEFAULTS) and a Note says which and why (NOTE_CODES).
The text, the accent and the text on the fill pass the checks' contrast rule by construction.
`steps(tokens, kind)` is the density ladder of a user template's manifest: loosest from the
captured density, down to TIGHTEST, every step within `templates.BOUNDS`.

Declared open points, left to D25b's render check (see template_css): a text colour on a
background set by another rule, a box smaller than its text, a size made small by nesting `em`
sizes. Of the tokens: a page's columns are read from the gaps between its text below the
top fifth, so a two column layout crossed by one full width line reads as one column; a sidebar
is a fill touching a side edge, a band a fill across the top.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from html.parser import HTMLParser
from itertools import pairwise
from statistics import median
from typing import Any

from jinja2 import DictLoader, StrictUndefined, nodes
from jinja2.exceptions import TemplateSyntaxError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from app.documents import font_map, template_css, templates
from app.documents.template_css import AA, PUNCTUATION, TAGS, WHITE, Problem, contrast
from app.ingestion.parser_client import DesignDocx, DesignPdf, TextRun

__all__ = ["AA", "TAGS", "WHITE", "Problem", "check", "check_css", "contrast"]

CODES = (
    "authoring", "syntax", "construct", "name", "constant", "literal", "tag", "attribute",
    "unprinted", "density_unread", "density_pinned", "property", "size", "colour", "contrast",
    "content", "font", "page", "structure", "selector", "too_large",
)  # fmt: skip
FILE_MAX = 64_000  # characters of one file; the bundled ones hold under 10,000
RENDER_MAX = 400_000  # characters of a canary render; the bundled ones print under 20,000
ELEMENTS_MAX = 6_000  # elements of a canary render; the bundled ones print under 200
DEPTH_MAX = 48  # elements nested in a canary render; the bundled ones nest under 12
JINJA_DEPTH = 12  # if and for nested in a file; the bundled ones nest under 5


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
_SLOT = ""  # where an output stands in the HTML between the constructs
_ITEM = " item"  # the scope key of the enclosing loop's item path: no Jinja name has a space


@dataclass(frozen=True)
class _Guard:
    """A test around an output, as what makes it true: a field (true when that field, or an
    item of it, is not empty), any or all of other guards; or nothing it vouches for (`not`, an
    `else`, a loop's place). `about(path)`: the test is true whenever `path` is printed."""

    path: str | None = None
    any: tuple[_Guard, ...] = ()
    all: tuple[_Guard, ...] = ()

    def about(self, path: str) -> bool:
        if self.path:
            return path == self.path or path.startswith((self.path + ".", self.path + "[]"))
        if self.any:
            return any(guard.about(path) for guard in self.any)
        return bool(self.all) and all(guard.about(path) for guard in self.all)


_NEVER = _Guard()


class _Walk:
    def __init__(self, file: str, kind: str) -> None:
        self.file = file
        self.fields = FIELDS[kind]
        self.problems: list[Problem] = []
        self.printed: set[str] = set()
        self.html: list[tuple[int, str]] = []
        self.depth = 0

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

    def top(self, tree: nodes.Template) -> None:
        """The top level of a file: extends, import, the body block and whitespace only."""
        extends = body = False
        for node in tree.body:
            if isinstance(node, nodes.Output) and all(
                isinstance(child, nodes.TemplateData) and not child.data.strip()
                for child in node.nodes
            ):
                continue
            if isinstance(node, nodes.Extends) and not extends:
                extends = True
                if not (isinstance(node.template, nodes.Const) and node.template.value == PAGE):
                    self.refuse(node, "construct", f"extends only {PAGE}")
            elif isinstance(node, nodes.Import):
                ok = isinstance(node.template, nodes.Const) and node.template.value == MACROS_FILE
                if not (ok and node.target == "base" and node.with_context):
                    self.refuse(
                        node, "construct", f"import only {MACROS_FILE} as base with context"
                    )
            elif isinstance(node, nodes.Block) and node.name == "body" and not body:
                body = True
                if node.scoped or node.required:
                    self.refuse(node, "construct", "the body block, neither scoped nor required")
                self.body(node.body, {}, ())
            else:
                self.refuse(node, "construct", "at the top level only extends, import and body")
        if not extends:
            self.problems.append(Problem(self.file, 0, "construct", f"a file that extends {PAGE}"))

    def body(self, items: list[nodes.Node], scope: dict[str, str], guards: tuple) -> None:
        for item in items:
            self.statement(item, scope, guards)

    def statement(self, node: nodes.Node, scope: dict[str, str], guards: tuple) -> None:
        if isinstance(node, nodes.Output):
            for child in node.nodes:
                if isinstance(child, nodes.TemplateData):
                    self.html.append((child.lineno, child.data))
                else:
                    self.html.append((child.lineno, _SLOT))
                    self.output(child, scope, guards)
        elif isinstance(node, nodes.If | nodes.For):
            if self.depth >= JINJA_DEPTH:
                self.refuse(node, "construct", f"if and for nested deeper than {JINJA_DEPTH}")
                return
            bodies = (node.body, *(b.body for b in getattr(node, "elif_", ())), node.else_)
            if not all(map(_balanced, bodies)):
                detail = "markup an if or a for opens or closes in one branch only"
                self.refuse(node, "structure", detail)
            self.depth += 1
            if isinstance(node, nodes.If):
                self.branches(node, scope, guards)
            else:
                self.loop(node, scope, guards)
            self.depth -= 1
        elif isinstance(node, nodes.Block):
            self.refuse(node, "construct", "a block inside the body block")
        else:
            self.refuse(node, "construct", f"the construct {type(node).__name__}")

    def branches(self, node: nodes.If, scope: dict[str, str], guards: tuple) -> None:
        self.body(node.body, scope, (*guards, self.test(node.test, scope)))
        for branch in node.elif_:
            self.test(branch.test, scope)
            self.body(branch.body, scope, (*guards, _NEVER))
        self.body(node.else_, scope, (*guards, _NEVER))

    def loop(self, node: nodes.For, scope: dict[str, str], guards: tuple) -> None:
        source = self.expr(node.iter, scope)
        what = self.fields.get(source or "")
        if not isinstance(node.target, nodes.Name) or what not in ("list", "items"):
            self.refuse(node, "construct", "a for over a list field, into one name")
            return
        outer = scope.get(_ITEM)
        if outer and not (source or "").startswith(outer + "."):
            self.refuse(node, "construct", "a for inside a for only over its item's own list")
            return
        if node.test is not None or node.recursive:
            self.refuse(node, "construct", "a for with a test or recursive")
        item = f"{source}[]"
        inner = {**scope, node.target.name: item, "loop": "loop", _ITEM: item}
        self.body(node.body, inner, (*guards, _Guard(source)))
        self.body(node.else_, scope, (*guards, _NEVER))

    def test(self, node: nodes.Expr, scope: dict[str, str]) -> _Guard:
        if isinstance(node, nodes.And | nodes.Or):
            parts = (self.test(node.left, scope), self.test(node.right, scope))
            return _Guard(all=parts) if isinstance(node, nodes.And) else _Guard(any=parts)
        if isinstance(node, nodes.Not):
            self.test(node.node, scope)
            return _NEVER
        path = self.expr(node, scope)
        if path is None or path == "loop" or path.startswith("loop."):
            return _NEVER
        return _Guard(path)

    def expr(self, node: nodes.Expr, scope: dict[str, str]) -> str | None:
        """The field path `node` names, None (and a problem) when it names none."""
        if isinstance(node, nodes.Name):
            if node.name in scope and node.name != _ITEM:
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
            if isinstance(node, nodes.Getattr) and hasattr(dict, key):
                self.refuse(node, "name", 'a field that is a method of a dict: read it as ["key"]')
                return None
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
            elif path:
                self.refuse(node, "construct", "a join over a field that is not a list of text")
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
            if key == "colspan" and (value or "").isdigit():
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


class _Balance(HTMLParser):
    """Whether markup closes, in order, every element it opens."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.broken = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.broken |= tag not in _VOID

    def handle_endtag(self, tag: str) -> None:
        if self.open and self.open[-1] == tag:
            self.open.pop()
        else:
            self.broken = True


def _balanced(body: list[nodes.Node]) -> bool:
    """Whether the markup of a body, its nested bodies included, is balanced on its own."""
    parser = _Balance()
    for node in body:
        for data in node.find_all(nodes.TemplateData):
            parser.feed(data.data)
    parser.close()
    return not (parser.broken or parser.open or parser.rawdata)


def check_html(kind: str, file: str, source: str) -> list[Problem]:
    """The problems of one `.j2` file of `kind`, read as source: the constructs, the HTML
    between them, every field of REQUIRED printed."""
    walk = _Walk(file, kind)
    if _SLOT in source:
        walk.problems.append(Problem(file, 0, "literal", "a character of the private use area"))
    try:
        tree = ImmutableSandboxedEnvironment(autoescape=True).parse(source)
        walk.top(tree)
    except TemplateSyntaxError as error:
        return [Problem(file, error.lineno or 0, "syntax", "Jinja that does not parse")]
    except RecursionError:
        return [Problem(file, 0, "syntax", "Jinja nested deeper than Python reads")]
    except ValueError:  # an integer literal over Python's 4,300 digits
        return [Problem(file, 0, "syntax", "a number longer than Python reads")]
    parser = _Html(walk)
    cut: set[int] = set()
    for line, data in walk.html:
        parser.line = line
        parser.feed(data)
        if parser.rawdata and line not in cut:
            cut.add(line)
            walk.problems.append(Problem(file, line, "literal", "markup cut by a Jinja construct"))
    parser.close()
    walk.problems += [
        Problem(file, 0, "unprinted", f"{field} is not printed, or only under another test")
        for field in REQUIRED[kind]
        if field not in walk.printed
    ]
    return walk.problems


# --- the render on canary content ---------------------------------------------------------------

PHOTO_CANARY = "data:image/png;base64,AAAA"
_VOID = frozenset(("br", "hr", "img"))
# The content model the browser's tree builder keeps as written: a start tag of _CLOSES_P closes
# a `p` open below the nearest of _SCOPE; a table part outside its place in _TABLE is moved.
_CLOSES_P = frozenset(
    ("div", "section", "article", "aside", "header", "footer", "main", "p", "ul", "ol", "li",
     "h1", "h2", "h3", "h4", "h5", "h6", "hr", "table")
)  # fmt: skip
_SCOPE = frozenset(("td", "th", "table", "html"))
_TABLE = {"table": ("thead", "tbody"), "thead": ("tr",), "tbody": ("tr",), "tr": ("td", "th")}
_TABLE_PARTS = frozenset(("thead", "tbody", "tr", "td", "th"))
_HEADINGS = frozenset(("h1", "h2", "h3", "h4", "h5", "h6"))


def _letters(n: int) -> str:
    return "".join(chr(ord("a") + (n // 26**k) % 26) for k in (2, 1, 0))


def canaries(kind: str) -> dict[str, str]:
    """A token per text field path and per label: letters only, an upper case at each end so
    it is never a class name, the same length so none holds another."""
    keys = [p for p, what in FIELDS[kind].items() if what == "text"]
    keys += [f"labels.{key}" for key in LABELS] + ["contact[].href"]
    return {key: f"Q{_letters(n)}Z" for n, key in enumerate(keys)}


def _context(kind: str, full: bool, photo: str) -> dict[str, Any]:
    fields, tokens = FIELDS[kind], canaries(kind)

    def value(path: str) -> Any:
        what = fields[path]
        if what == "text":
            return tokens[path] if full else ""
        if what == "flag":
            return PHOTO_CANARY if full and photo != "none" else None
        if what in ("list", "items"):
            return [value(f"{path}[]") for _ in range(2)] if full else []
        prefix = f"{path}."
        record = {}
        for child in fields:
            rest = child.removeprefix(prefix)
            if child.startswith(prefix) and "." not in rest and not rest.endswith("[]"):
                record[rest] = value(child)
        if path == "contact[]":
            record["href"] = tokens["contact[].href"]
        return record

    top = [p for p in fields if "." not in p and "[" not in p]
    context = {p: value(p) for p in top}
    if full:
        context["contact"][1]["href"] = None
    context |= {
        "lang": "en",
        "kind": kind,
        "style": "",
        "accent": "#000000",
        "labels": {key: tokens[f"labels.{key}"] for key in LABELS},
    }
    return context


@cache
def _base_files() -> Mapping[str, str]:
    return {
        f"_base/{path.name}": path.read_text(encoding="utf-8")
        for path in sorted(templates.BASE.glob("*.j2"))
    }


def _environment(files: Mapping[str, str]) -> ImmutableSandboxedEnvironment:
    """The environment `templates.environment` builds for a template, for these files."""
    env = ImmutableSandboxedEnvironment(
        loader=DictLoader({**_base_files(), **{n: files.get(n, "") for n in templates.FILES}}),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    for name in templates.UNESCAPING:
        env.filters.pop(name, None)
    return env


class _Rendered(HTMLParser):
    """The body of a canary render, read for what in it breaks the allow lists."""

    def __init__(self, file: str, tokens: Mapping[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.file = file
        self.tokens = tokens
        self.name = tokens["name"]
        self.found: set[str] = set()
        self.problems: dict[tuple[str, str], None] = {}
        self.open = ["html", "body"]
        self.count = 2

    def refuse(self, code: str, detail: str) -> None:
        self.problems[(code, detail)] = None

    def _attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            text = value or ""
            if key == "class" and _CLASS.fullmatch(text.strip()):
                continue
            if not self._kept(tag, key, text):
                self.refuse("attribute", "an attribute outside the allowed list, once rendered")

    def _kept(self, tag: str, key: str, text: str) -> bool:
        if key == "colspan":
            return tag in ("td", "th") and text.isdigit()
        if tag == "a":
            return key == "href" and text == self.tokens["contact[].href"]
        return tag == "img" and (key, text) in (("src", PHOTO_CANARY), ("alt", self.name))

    def _in_p(self) -> bool:
        """Whether a `p` is open below the nearest cell or table."""
        for tag in reversed(self.open):
            if tag == "p":
                return True
            if tag in _SCOPE:
                return False
        return False

    def _structure(self, tag: str) -> str | None:
        """Why the tree builder would not keep `tag` where it starts, None when it would."""
        parent = self.open[-1]
        if tag in _CLOSES_P and self._in_p():
            return "a block inside a p, which the browser closes before it"
        if tag == "li" and parent not in ("ul", "ol"):
            return "a li outside a ul or an ol"
        if tag in _HEADINGS and parent in _HEADINGS:
            return "a heading inside a heading, which the browser closes before it"
        allowed = _TABLE.get(parent, ())
        if (allowed or tag in _TABLE_PARTS) and tag not in allowed:
            return "a table part outside its place"
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in TAGS | {"a", "img"}:
            self.refuse("tag", "a tag outside the allowed list, once rendered")
        wrong = self._structure(tag)
        if wrong:
            self.refuse("structure", wrong)
        self._attributes(tag, attrs)
        self.count += 1
        if tag not in _VOID:
            self.open.append(tag)
            if len(self.open) > DEPTH_MAX:
                self.refuse("structure", f"elements nested deeper than {DEPTH_MAX}")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID:
            self.refuse("structure", "a /> on an element the browser keeps open")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            self.refuse("structure", "an end tag of a void element")
            return
        if self.open[-1] != tag or len(self.open) <= 2:
            self.refuse("structure", "an end tag that closes another element")
            return
        self.open.pop()

    def handle_data(self, data: str) -> None:
        rest = data
        for key, token in self.tokens.items():
            if token in rest:
                self.found.add(key)
                rest = rest.replace(token, "")
        if not all(ch.isspace() or ch in PUNCTUATION for ch in rest):
            self.refuse("literal", "text that is not a field, once rendered")
        if data.strip() and self.open[-1] in _TABLE:
            self.refuse("structure", "text in a table outside its cells")

    def handle_comment(self, data: str) -> None:
        self.refuse("literal", "an HTML comment, once rendered")

    def handle_decl(self, decl: str) -> None:
        self.refuse("tag", "a declaration, once rendered")

    handle_pi = handle_decl
    unknown_decl = handle_decl


def render_check(kind: str, files: Mapping[str, str], photo: str) -> list[Problem]:
    """The problems of the canary renders of `kind`: every field filled, then every field
    empty."""
    file = f"{kind}.html.j2"
    env = _environment(files)
    tokens = canaries(kind)
    problems: list[Problem] = []
    for full in (True, False):
        context = _context(kind, full, photo)
        try:
            printed = env.get_template(file).render(context)
            wrapper = env.get_template(PAGE).render(context)
        except Exception:  # anything the render raises: the template is refused, not the caller
            return [Problem(file, 0, "construct", "a template that does not render")]
        if len(printed) > RENDER_MAX:
            return [Problem(file, 0, "too_large", f"a render over {RENDER_MAX} characters")]
        opening = f'<body class="{kind}">'
        head = wrapper[: wrapper.index(opening) + len(opening)]
        tail = wrapper[wrapper.rindex("</body>") :]
        if not (printed.startswith(head) and printed.endswith(tail)):
            problems.append(Problem(file, 0, "construct", "a page other than the base page"))
            continue
        parser = _Rendered(file, tokens)
        parser.feed(printed[len(head) : len(printed) - len(tail)])
        parser.close()
        if parser.rawdata or len(parser.open) != 2:
            parser.refuse("structure", "markup not closed, once rendered")
        if parser.count > ELEMENTS_MAX:
            parser.refuse("too_large", f"a render of over {ELEMENTS_MAX} elements")
        problems += [Problem(file, 0, code, detail) for code, detail in parser.problems]
        if full:
            for field in REQUIRED[kind]:
                key = field if FIELDS[kind][field] == "text" else f"{field}[]"
                if key not in parser.found:
                    detail = f"{field} is not printed, or only under another test"
                    problems.append(Problem(file, 0, "unprinted", detail))
    return list(dict.fromkeys(problems))


def check_css(
    css: str, fonts: tuple[str, ...], accent: str, file: str = "style.css"
) -> list[Problem]:
    """The problems of a template's style sheet: `template_css.check`."""
    return template_css.check(css, fonts, accent, file)


def check(
    files: Mapping[str, str], *, photo: str, fonts: tuple[str, ...], accent: str
) -> list[Problem]:
    """Every problem of a template's three files, in a fixed order; [] accepts it."""
    found = []
    for line in templates.problems(files, photo):
        file, _, detail = line.partition(": ")
        found.append(Problem(file, 0, "authoring", detail))
    large = [name for name in templates.FILES if len(files.get(name, "")) > FILE_MAX]
    found += [Problem(name, 0, "too_large", f"over {FILE_MAX} characters") for name in large]
    for kind in templates.KINDS:
        name = f"{kind}.html.j2"
        if name in large:
            continue
        source = check_html(kind, name, files.get(name, ""))
        found += source
        if not source and not found:
            found += render_check(kind, files, photo)
    accent = accent.lower() if re.fullmatch(r"#[0-9a-f]{6}", accent.lower()) else WHITE
    if "style.css" not in large:
        found += check_css(files.get("style.css", ""), fonts, accent)
    return found


# --- the tokens of a captured design ------------------------------------------------------------

A4 = (5953, 8419)  # tenths of a point, as the sandbox measures
MM = 72 / 25.4 * 10  # tenths of a point in a millimetre
EDGE = 0.05  # of the page width: a fill this close to a side touches it
GUTTER = 120  # tenths of a point: the least gap between two columns
BOUNDS = {
    "margin": templates.BOUNDS["margin"],
    "body_size": templates.BOUNDS["body_size"],
    "line_height": templates.BOUNDS["line_height"],
    "name_scale": (1.2, 4.0),
    "heading_scale": (0.8, 2.0),
}
DEFAULTS = {
    "margin": 15.0,
    "body_size": 10.0,
    "line_height": 1.35,
    "name_scale": 2.2,
    "heading_scale": 1.0,
    "text": "#1d232b",
    "accent": "#1f4e79",
}
NOTE_CODES = ("unknown", "out_of_bounds", "low_contrast", "not_a4", "columns")


@dataclass(frozen=True)
class Note:
    token: str  # a field of Tokens
    code: str  # one of NOTE_CODES


@dataclass(frozen=True)
class Tokens:
    page: str  # "A4": what every template prints on; a Note says when the file had another
    margin: float  # mm, the side margin of the text
    columns: int  # 1 or 2
    side: str | None  # "left" or "right": the narrower of two columns
    side_width: float | None  # mm
    body_size: float  # pt
    line_height: float
    name_scale: float  # the name's size over the body's
    heading_scale: float
    text: str  # #rrggbb
    accent: str
    fill: str | None  # a band or column fill
    on_fill: str | None  # the text on the fill
    fonts: tuple[str, ...]  # families of font_map.FAMILIES, the body's first
    notes: tuple[Note, ...]


class _Reading:
    """The values read so far, each held to its bounds or replaced by its default with a Note."""

    def __init__(self) -> None:
        self.notes: list[Note] = []

    def bounded(self, token: str, value: float | None) -> float:
        low, high = BOUNDS[token]
        if value is None or not low <= value <= high:
            self.notes.append(Note(token, "unknown" if value is None else "out_of_bounds"))
            return DEFAULTS[token]
        return round(value, 2)

    def colour(self, token: str, candidates: Iterable[object], back: str = WHITE) -> str | None:
        """The first candidate at 4.5:1 on `back`, else the default (None for the fill)."""
        found = [c for c in candidates if isinstance(c, str)]
        for colour in found:
            if contrast(colour, back) >= AA:
                return colour
        self.notes.append(Note(token, "low_contrast" if found else "unknown"))
        return DEFAULTS.get(token)

    def page(self, width: int | None, height: int | None) -> str:
        if width is None or height is None:
            self.notes.append(Note("page", "unknown"))
        elif abs(width - A4[0]) > 20 or abs(height - A4[1]) > 20:
            self.notes.append(Note("page", "not_a4"))
        return "A4"


def _most(counts: Counter) -> object | None:
    return max(counts, key=lambda key: (counts[key], str(key))) if counts else None


def _ranked(counts: Counter) -> list:
    return sorted(counts, key=lambda key: (-counts[key], str(key)))


def _fonts(reading: Iterable[tuple[font_map.Mapped, int]]) -> tuple[str, ...]:
    """The family that draws the most characters, symbol fonts left out, else the default."""
    counts: Counter = Counter()
    for mapped, chars in reading:
        if not mapped.symbol:
            counts[mapped.family] += chars
    return tuple(_ranked(counts)[:1]) or (font_map.GENRES[font_map.DEFAULT_GENRE],)


def _fill_kind(box: tuple[int, int, int, int], width: int, height: int) -> str | None:
    """ "left" or "right" for a column fill touching that edge, "band" for one across the top."""
    x0, y0, x1, y1 = box
    if y1 - y0 >= 0.6 * height and 0.15 * width <= x1 - x0 <= 0.45 * width:
        if x0 <= EDGE * width:
            return "left"
        if x1 >= (1 - EDGE) * width:
            return "right"
    if x1 - x0 >= 0.8 * width and y0 <= 0.3 * height and y1 - y0 <= 0.35 * height:
        return "band"
    return None


def _gaps(runs: tuple[TextRun, ...], width: int, height: int) -> list[tuple[int, int]]:
    """The gaps of at least GUTTER across the text below the top fifth of the page whose middle
    is in the middle three fifths of its width: one gap, two columns."""
    spans = sorted((r.box[0], r.box[2]) for r in runs if r.box[1] >= height // 5)
    gaps: list[tuple[int, int]] = []
    reach: int | None = None
    for start, end in spans:
        middle = (start + (reach or 0)) / 2
        if reach is not None and start - reach >= GUTTER and width / 5 < middle < width * 4 / 5:
            gaps.append((reach, start))
        reach = end if reach is None else max(reach, end)
    return gaps


@dataclass(frozen=True)
class _Layout:
    columns: int = 1
    side: str | None = None
    side_width: float | None = None
    fill: str | None = None
    on_fill: str | None = None


def _layout(t: _Reading, page, text: str) -> _Layout:
    """The columns and the fill of a page: a column fill makes two columns, else one gap."""
    fills = [
        (kind, shape)
        for shape in page.shapes
        if shape.type == "fill" and contrast(shape.colour, WHITE) >= 1.1
        if (kind := _fill_kind(shape.box, page.width, page.height))
    ]
    if fills:
        kind, shape = max(
            fills, key=lambda f: (f[1].box[2] - f[1].box[0]) * (f[1].box[3] - f[1].box[1])
        )
        x0, y0, x1, y1 = shape.box
        inside: Counter = Counter()
        for run in page.runs:
            if (
                x0 <= (run.box[0] + run.box[2]) / 2 <= x1
                and y0 <= (run.box[1] + run.box[3]) / 2 <= y1
            ):
                inside[run.colour] += len(run.text)
        on_fill = t.colour("on_fill", [*_ranked(inside), text, WHITE], shape.colour)
        if on_fill and kind == "band":
            return _Layout(fill=shape.colour, on_fill=on_fill)
        if on_fill:
            return _Layout(2, kind, round((x1 - x0) / MM, 1), shape.colour, on_fill)
    gaps = _gaps(page.runs, page.width, page.height)
    if len(gaps) > 1:
        t.notes.append(Note("columns", "columns"))
    if len(gaps) != 1:
        return _Layout()
    _, right, _, left = page.margins or (0, 0, 0, 0)
    ((start, end),) = gaps
    if (start + end) / 2 < page.width / 2:
        return _Layout(2, "left", round((start - left) / MM, 1))
    return _Layout(2, "right", round((page.width - right - end) / MM, 1))


def from_pdf(design: DesignPdf) -> Tokens:
    """The tokens of the first page of a PDF's layout (see the module docstring)."""
    t = _Reading()
    page = design.read[0] if design.read else None
    runs = page.runs if page else ()
    sizes: Counter = Counter()
    for run in runs:
        if run.size > 0:
            sizes[run.size] += len(run.text)
    body = _most(sizes) or 0
    ratios = [
        (b.box[3] - a.box[3]) / body
        for a, b in pairwise(runs)
        if body and a.size == b.size == body and b.box[1] > a.box[1]
        if abs(b.box[0] - a.box[0]) <= 20
    ]
    ratios = [r for r in ratios if 0.9 <= r <= 2.5]
    largest = max((r.size for r in runs), default=0)
    headings, colours, accents = Counter(), Counter(), Counter()
    bold = {i for i, f in enumerate(design.fonts) if f.weight >= 600}
    for run in runs:
        if body * 1.05 < run.size < largest:
            headings[run.size] += len(run.text)
        if run.size == body:
            colours[run.colour] += len(run.text)
    text = t.colour("text", [_most(colours)]) or DEFAULTS["text"]
    for run in runs:
        if run.colour != text and (run.size > body or run.font in bold):
            accents[run.colour] += len(run.text)
    shapes = [s.colour for s in (page.shapes if page else ()) if s.colour != text]
    layout = _layout(t, page, text) if page else _Layout()
    faces = [(font_map.face(**_face(design.fonts[r.font])), r) for r in runs]
    body_font = _fonts((m, len(r.text)) for m, r in faces if r.size == body)
    heading_font = (
        _fonts((m, len(r.text)) for m, r in faces if r.size > body) if largest > body else ()
    )
    margins = page.margins if page else None
    return Tokens(
        page=t.page(page.width if page else None, page.height if page else None),
        margin=t.bounded("margin", min(margins[1], margins[3]) / MM if margins else None),
        columns=layout.columns,
        side=layout.side,
        side_width=layout.side_width,
        body_size=t.bounded("body_size", body / 10 if body else None),
        line_height=t.bounded("line_height", median(ratios) if ratios else None),
        name_scale=t.bounded("name_scale", largest / body if body else None),
        heading_scale=t.bounded("heading_scale", _most(headings) / body if headings else 1.0),
        text=text,
        accent=t.colour("accent", [*_ranked(accents), *shapes]) or DEFAULTS["accent"],
        fill=layout.fill,
        on_fill=layout.on_fill,
        fonts=tuple(dict.fromkeys(body_font + heading_font)),
        notes=tuple(t.notes),
    )


def _face(font) -> dict:
    return {
        "name": font.name,
        "weight": font.weight,
        "italic": font.italic,
        "serif": font.serif,
        "monospace": font.monospace,
    }


SINGLE = 1.2  # the line height of Word's single spacing, as CSS counts it
BASED_ON = 10  # the most styles a property is looked up through


def from_docx(design: DesignDocx) -> Tokens:
    """The tokens of a Word document's styles: its Normal style (else the document's defaults)
    for the body, Heading 1 (outline level 0) for the headings, Title for the name, the first
    section for the page, the theme for what the styles leave unsaid."""
    t = _Reading()
    by_id = {style["id"]: style for style in design.styles}
    paragraphs = [s for s in design.styles if s["type"] == "paragraph"]

    def named(*names: str) -> Mapping | None:
        return next((s for s in paragraphs if (s["name"] or "").casefold() in names), None)

    def prop(style: Mapping | None, key: str, inherit: bool = True) -> object:
        for _ in range(BASED_ON):
            if style is None or style.get(key) is not None:
                break
            style = by_id.get(style["based_on"] or "")
        if style is not None and style.get(key) is not None:
            return style[key]
        return design.defaults.get(key) if inherit else None

    normal = next((s for s in paragraphs if s["default"]), None) or named("normal")
    heading = next((s for s in paragraphs if s["outline"] == 0), None) or named("heading 1")
    title = named("title") or heading
    theme = design.theme or {}
    colours = theme.get("colours") or {}
    body = prop(normal, "size")
    line, rule = prop(normal, "line"), prop(normal, "line_rule")
    height = None
    if isinstance(line, int) and rule == "auto":
        height = line / 100 * SINGLE
    elif isinstance(line, int) and isinstance(body, int) and body:
        height = line / body
    section = design.sections[0] if design.sections else None
    margins = section["margins"] if section else None
    columns = section["columns"] if section else 1
    if columns == 1 and design.tables and design.table_columns == 2:
        columns = 2
    if columns > 2:
        t.notes.append(Note("columns", "columns"))
        columns = 1

    def size(style: Mapping | None) -> float | None:
        found = prop(style, "size", inherit=False) if style else None
        return found / body if isinstance(found, int) and isinstance(body, int) and body else None

    def font(style: Mapping | None, fallback: object) -> tuple[str, ...]:
        name = (prop(style, "font") if style else None) or fallback
        bold = 700 if style and prop(style, "bold") else 0
        mapped = font_map.face(
            name if isinstance(name, str) else "", bold, bool(style and prop(style, "italic"))
        )
        return _fonts([(mapped, 1)])

    text = t.colour("text", [prop(normal, "colour"), colours.get("dk1")]) or DEFAULTS["text"]
    accents = [prop(s, "colour", inherit=False) if s else None for s in (heading, title)]
    return Tokens(
        page=t.page(section["width"] if section else None, section["height"] if section else None),
        margin=t.bounded("margin", min(margins[1], margins[3]) / MM if margins else None),
        columns=columns,
        side=None,
        side_width=None,
        body_size=t.bounded("body_size", body / 10 if isinstance(body, int) and body else None),
        line_height=t.bounded("line_height", height),
        name_scale=t.bounded("name_scale", size(title)),
        heading_scale=t.bounded("heading_scale", size(heading) or 1.0),
        text=text,
        accent=t.colour("accent", [*accents, colours.get("accent1")]) or DEFAULTS["accent"],
        fill=None,
        on_fill=None,
        fonts=tuple(
            dict.fromkeys(font(normal, theme.get("minor")) + font(heading, theme.get("major")))
        ),
        notes=tuple(t.notes),
    )


TIGHTEST = {
    "cv": templates.Step(7.6, 1.1, 3.0, 1.5, 6.0),
    "letter": templates.Step(8.6, 1.15, 6.0, 3.0, 10.0),
}
SPAN = templates.Step(0.8, 0.1, 2.0, 1.0, 2.0)  # the least each property falls along a ladder
COUNT = {"cv": 8, "letter": 6}


def steps(tokens: Tokens, kind: str) -> list[dict[str, float]]:
    """The density steps of a user template's manifest for `kind`, loosest first: from the
    captured body size (one point more for a letter), line height and margin, the gaps in
    proportion to the body, each held at least SPAN above TIGHTEST and within the bounds of
    `templates.BOUNDS`, down to TIGHTEST in COUNT even steps."""
    tight, count = TIGHTEST[kind], COUNT[kind]
    body = tokens.body_size + (1.0 if kind == "letter" else 0.0)
    wanted = {
        "body_size": body,
        "line_height": tokens.line_height,
        "section_gap": body,
        "item_gap": body / 2,
        "margin": tokens.margin,
    }
    loosest = {
        key: min(max(value, getattr(tight, key) + getattr(SPAN, key)), templates.BOUNDS[key][1])
        for key, value in wanted.items()
    }
    return [
        {
            key: round(value - (value - getattr(tight, key)) * index / (count - 1), 2)
            for key, value in loosest.items()
        }
        for index in range(count)
    ]
