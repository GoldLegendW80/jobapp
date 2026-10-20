"""The style sheet of a template written from an upload (task D25a; SPEC-documents.md, "Design
capture of an upload", step 4, and "Templates"). Pure: no file, no clock, no log.

`check(css, fonts, accent, file, pages) -> [Problem]`. The sheet is read by a tokenizer of its
own (no CSS parser is installed) that stops at the first input it does not know: `url(`, an
escape outside a string, a comment or a string not closed, a character outside the ASCII set of
the rules below. A sheet it stops on is refused whole, so nothing after a construct the browser
would read another way is ever judged. Then everything is held to allow lists:
- the structure: rules and an exact `@page`, no other at-rule, no block inside a block;
- the selectors (SELECTORS): type selectors of ELEMENTS, classes, `*`, `:root`, `:first-child`,
  `:last-child`, the combinators space, `>`, `+`, `~`, a final `::before`, `::after` or
  `::marker`; at most MAX_COMPOUNDS parts of MAX_CLASSES classes;
- the properties of VALUES, each with its own allow list of values; sizes of text in `em` or
  `%` or the density property, every other length from 0 up to CAPS; colours `#rrggbb` or the
  accent; strings only punctuation; functions only `var()` and, in a grid template, `minmax()`;
- custom properties: `var()` reads a density property, `--accent`, or a custom property the
  sheet sets once to one length, number or `#rrggbb` (minimal's `--rail`), read in its place;
  a density property or `--accent` set by the sheet is refused (the step sets them);
- the density: each property of DENSITY read, `--body-size` by a `font-size`, `--line-height`
  by a `line-height`, `--margin` too when `@page` sets a margin;
- the fonts: a `font-family` starts with a family of the manifest;
- the contrast (WCAG 2 AA, 4.5:1), in three ways: a rule that sets both a text colour and a
  background holds them against each other; on the rendered pages (`pages`, the elements of a
  render with every field filled, built by `template_rules`) each element holding text has its
  colour and its backdrop computed as the cascade does; a selector that matched no element, or
  whose match depends on the shape of the content (`:first-child`, `:last-child`, `+`, `~`), is
  held against what its own parts say: the backgrounds of the parts it names (else the page's
  white), or for a background the colours of the rules inside it.

Declared open points: the cascade is computed on one shape of the content (every field filled,
two items per list); `position: absolute` can lay a box over another background than its
parent's; a size made small by nesting `em` sizes is not measured; the colour of a `::marker`,
`::before` or `::after` (punctuation only) is not held to the contrast rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from app.documents import font_map


@dataclass(frozen=True)
class Problem:
    file: str
    line: int  # 0: the whole file
    code: str  # one of template_rules.CODES
    detail: str  # a fixed phrase, a field or a CSS name of the allow lists

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.detail}"


TAGS = frozenset(
    (
        "div", "section", "article", "aside", "header", "footer", "main", "span", "p", "ul", "ol",
        "li", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "b", "i", "small", "br", "hr",
        "table", "thead", "tbody", "tr", "td", "th",
    )
)  # fmt: skip
ELEMENTS = TAGS | {"html", "body", "a", "img"}  # the macros write `a` and `img`, the page the rest
PUNCTUATION = frozenset(",:;·•|/().&")
CONTENT_CHARS = PUNCTUATION | frozenset("\u00a0 ◦▪")
DENSITY = ("--body-size", "--line-height", "--section-gap", "--item-gap", "--margin")
DENSITY_LENGTHS = frozenset(("--body-size", "--section-gap", "--item-gap", "--margin"))
GENERIC = frozenset(("serif", "sans-serif", "monospace"))
AA = 4.5  # WCAG 2, contrast of body text
WHITE = "#ffffff"
BLACK = "#000000"
MAX_COMPOUNDS = 4
MAX_CLASSES = 4
CAPS = {"pt": 850.0, "px": 1134.0, "mm": 300.0, "em": 40.0, "%": 100.0}  # about one A4 height
PSEUDO_CLASSES = frozenset(("root", "first-child", "last-child"))
PSEUDO_ELEMENTS = frozenset(("before", "after", "marker"))
_HEX = re.compile(r"[0-9a-f]{3}|[0-9a-f]{6}")
_NAME_START = re.compile(r"--[A-Za-z0-9_-]+|-?[A-Za-z_][A-Za-z0-9_-]*")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)")
_UNIT = re.compile(r"%|[A-Za-z]+")
_HASH = re.compile(r"#[A-Za-z0-9_-]+")
_DELIMS = frozenset("{}();:,.>+~*/!")


# --- the tokens ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    kind: str  # ident, at, hash, string, number, function, delim, space
    text: str  # an ident or a function's name as written, a string's value, a delim's character
    line: int
    value: float = 0.0  # of a number
    unit: str = ""  # of a number: "", "%" or the unit in lower case


class _Stop(Exception):  # noqa: N818, the tokenizer's stop, never raised out of this module
    def __init__(self, line: int, code: str, detail: str) -> None:
        self.line, self.code, self.detail = line, code, detail


def _string(css: str, at: int, line: int) -> tuple[str, int]:
    """The value of the string that starts at `at`, its escapes read, and where it ends."""
    quote, out, i = css[at], [], at + 1
    while i < len(css):
        ch = css[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch in "\n\r\f":
            break
        if ch == "\\":
            digits = re.match(r"[0-9a-fA-F]{1,6}[ \t\n]?", css[i + 1 : i + 8])
            if digits:
                point = int(digits.group().strip(), 16)
                surrogate = 0xD800 <= point <= 0xDFFF
                out.append(chr(point) if 0 < point <= 0x10FFFF and not surrogate else "�")
                i += 1 + len(digits.group())
                continue
            if i + 1 >= len(css) or css[i + 1] in "\n\r\f":
                break
            out.append(css[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    raise _Stop(line, "structure", "a string that is not closed")


def tokens(css: str) -> list[Token]:
    """The tokens of a sheet, comments dropped; raises _Stop at the first unknown input."""
    found: list[Token] = []
    i, line, n = 0, 1, len(css)
    while i < n:
        ch = css[i]
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            if end < 0:
                raise _Stop(line, "structure", "a comment that is not closed")
            line += css.count("\n", i, end)
            i = end + 2
            continue
        if ch in " \t\n\r\f":
            start = i
            while i < n and css[i] in " \t\n\r\f":
                i += 1
            line += css.count("\n", start, i)
            if not found or found[-1].kind != "space":
                found.append(Token("space", " ", line))
            continue
        if ch in "\"'":
            text, end = _string(css, i, line)
            found.append(Token("string", text, line))
            i = end
            continue
        if ch == "\\":
            raise _Stop(line, "structure", "an escape outside a string")
        digit_next = i + 1 < n and (css[i + 1].isdigit() or css[i + 1] == ".")
        if (
            ch.isdigit()
            or (ch == "." and i + 1 < n and css[i + 1].isdigit())
            or (ch in "+-" and digit_next and _NUMBER.match(css, i))
        ):
            number = _NUMBER.match(css, i)
            if number is None:
                raise _Stop(line, "structure", "a number CSS does not read")
            i = number.end()
            unit = _UNIT.match(css, i)
            suffix = unit.group() if unit else ""
            i += len(suffix)
            found.append(
                Token("number", number.group(), line, float(number.group()), suffix.lower())
            )
            continue
        name = _NAME_START.match(css, i) if ch.isascii() and (ch.isalpha() or ch in "-_") else None
        if name:
            i = name.end()
            if i < n and css[i] == "(":
                if name.group().lower() == "url":
                    raise _Stop(line, "property", "url(), a template from an upload embeds nothing")
                found.append(Token("function", name.group(), line))
                i += 1
            else:
                found.append(Token("ident", name.group(), line))
            continue
        if ch == "@":
            name = _NAME_START.match(css, i + 1)
            if not name:
                raise _Stop(line, "structure", "an at-rule without a name")
            found.append(Token("at", name.group(), line))
            i = name.end()
            continue
        if ch == "#":
            hashed = _HASH.match(css, i)
            if not hashed:
                raise _Stop(line, "structure", "a # without a name")
            found.append(Token("hash", hashed.group()[1:], line))
            i = hashed.end()
            continue
        if ch in _DELIMS:
            found.append(Token("delim", ch, line))
            i += 1
            continue
        raise _Stop(line, "structure", "a character the rules of a sheet do not use")
    return found


# --- the rules ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Compound:
    tag: str | None
    classes: frozenset[str]
    pseudo: frozenset[str]  # of PSEUDO_CLASSES

    def covers(self, other: Compound) -> bool:
        """Whether every element `other` matches is one this part matches too."""
        return (
            (self.tag is None or self.tag == other.tag)
            and self.classes <= other.classes
            and ("root" not in self.pseudo or "root" in other.pseudo or other.tag == "html")
        )


@dataclass(frozen=True)
class Selector:
    compounds: tuple[Compound, ...]  # the subject last
    combinators: tuple[str, ...]  # between the parts: " ", ">", "+", "~"
    element: str | None  # a pseudo-element of PSEUDO_ELEMENTS

    @property
    def specificity(self) -> tuple[int, int]:
        classes = sum(len(c.classes) + len(c.pseudo) for c in self.compounds)
        return classes, sum(c.tag is not None for c in self.compounds) + (self.element is not None)

    @property
    def shaped(self) -> bool:
        """Whether what it matches depends on the shape of the content."""
        positional = any(c.pseudo - {"root"} for c in self.compounds)
        return positional or bool(set(self.combinators) & {"+", "~"})


@dataclass(frozen=True)
class Declaration:
    prop: str  # lower case, a custom property as written
    value: tuple[Token, ...]
    important: bool
    line: int


@dataclass(frozen=True)
class Rule:
    selectors: tuple[Selector, ...] | None  # None: @page
    declarations: tuple[Declaration, ...]
    line: int


def _selector(parts: Sequence[Token]) -> Selector | str:
    """One selector of a rule's prelude, or what is wrong with it."""
    compounds: list[Compound] = []
    combinators: list[str] = []
    element: str | None = None
    i, n = 0, len(parts)
    while i < n:
        if compounds:
            combinator = " "
            while i < n and parts[i].kind == "space":
                i += 1
            if i < n and parts[i].kind == "delim" and parts[i].text in ">+~":
                combinator = parts[i].text
                i += 1
                while i < n and parts[i].kind == "space":
                    i += 1
            if i == n:
                return "a combinator without a part after it"
            combinators.append(combinator)
        if element is not None:
            return "a part after a pseudo-element"
        tag, classes, pseudo, start = None, set(), set(), i
        if parts[i].kind == "ident":
            tag = parts[i].text.lower()
            if tag not in ELEMENTS:
                return "a type selector outside the allowed tags"
            i += 1
        elif parts[i].kind == "delim" and parts[i].text == "*":
            i += 1
        while i < n and parts[i].kind == "delim" and parts[i].text in ".:":
            nxt = parts[i + 1] if i + 1 < n else None
            if parts[i].text == "." and nxt is not None and nxt.kind == "ident":
                classes.add(nxt.text)
                i += 2
            elif parts[i].text == ":" and nxt is not None and nxt.kind == "ident":
                if nxt.text.lower() not in PSEUDO_CLASSES:
                    return "a pseudo-class outside :root, :first-child and :last-child"
                pseudo.add(nxt.text.lower())
                i += 2
            elif (
                parts[i].text == ":"
                and nxt is not None
                and nxt.kind == "delim"
                and nxt.text == ":"
                and i + 2 < n
                and parts[i + 2].kind == "ident"
                and parts[i + 2].text.lower() in PSEUDO_ELEMENTS
            ):
                element = parts[i + 2].text.lower()
                i += 3
            else:
                return "a selector outside the allowed forms"
        if i == start:
            return "a selector outside the allowed forms"
        compounds.append(Compound(tag, frozenset(classes), frozenset(pseudo)))
        if len(classes) > MAX_CLASSES:
            return f"a part of more than {MAX_CLASSES} classes"
        if len(compounds) > MAX_COMPOUNDS:
            return f"a selector of more than {MAX_COMPOUNDS} parts"
    if not compounds:
        return "an empty selector"
    return Selector(tuple(compounds), tuple(combinators), element)


def _strip(parts: Sequence[Token]) -> list[Token]:
    start, end = 0, len(parts)
    while start < end and parts[start].kind == "space":
        start += 1
    while end > start and parts[end - 1].kind == "space":
        end -= 1
    return list(parts[start:end])


def _declaration(parts: list[Token]) -> Declaration | str | None:
    parts = _strip(parts)
    if not parts:
        return None
    if parts[0].kind != "ident" or len(parts) < 2:
        return "a declaration without a property name"
    rest = _strip(parts[1:])
    if not rest or rest[0].kind != "delim" or rest[0].text != ":":
        return "a declaration without a colon"
    value = _strip(rest[1:])
    important = False
    if len(value) >= 2 and value[-1].kind == "ident" and value[-1].text.lower() == "important":
        before = _strip(value[:-1])
        if before and before[-1].kind == "delim" and before[-1].text == "!":
            important, value = True, _strip(before[:-1])
    name = parts[0].text if parts[0].text.startswith("--") else parts[0].text.lower()
    return Declaration(name, tuple(value), important, parts[0].line)


def parse(css: str, file: str) -> tuple[list[Rule], list[Problem]]:
    """The rules of a sheet and its problems. A sheet the tokenizer stops on, or whose
    structure is not rules and `@page`, gives no rule: it is refused whole."""
    try:
        found = tokens(css)
    except _Stop as stop:
        return [], [Problem(file, stop.line, stop.code, stop.detail)]
    rules: list[Rule] = []
    problems: list[Problem] = []
    interned: dict[Selector, Selector] = {}  # one object per selector: the cascade keys by id
    i, n = 0, len(found)
    while i < n:
        if found[i].kind == "space":
            i += 1
            continue
        start = i
        while i < n and not (found[i].kind == "delim" and found[i].text in "{};"):
            i += 1
        prelude = _strip(found[start:i])
        line = prelude[0].line if prelude else found[min(i, n - 1)].line
        if i == n or found[i].text != "{":
            return [], [Problem(file, line, "structure", "a rule without a block")]
        if prelude and prelude[0].kind == "at":
            if len(prelude) != 1 or prelude[0].text.lower() != "page":
                return [], [Problem(file, line, "structure", "an at-rule other than @page")]
            selectors: tuple[Selector, ...] | None = None
        else:
            groups: list[list[Token]] = [[]]
            for token in prelude:
                if token.kind == "delim" and token.text == ",":
                    groups.append([])
                else:
                    groups[-1].append(token)
            read = [_selector(_strip(group)) for group in groups]
            wrong = [r for r in read if isinstance(r, str)]
            if wrong:
                problems.append(Problem(file, line, "selector", wrong[0]))
            selectors = tuple(interned.setdefault(r, r) for r in read if isinstance(r, Selector))
        i += 1
        declarations: list[Declaration] = []
        part: list[Token] = []
        while True:
            if i == n:
                return [], [Problem(file, line, "structure", "a block that is not closed")]
            token = found[i]
            i += 1
            if token.kind == "delim" and token.text == "{":
                return [], [Problem(file, token.line, "structure", "a block inside a block")]
            if token.kind == "delim" and token.text in ";}":
                read_one = _declaration(part)
                if isinstance(read_one, str):
                    problems.append(Problem(file, token.line, "structure", read_one))
                elif read_one is not None:
                    declarations.append(read_one)
                part = []
                if token.text == "}":
                    break
                continue
            part.append(token)
        rules.append(Rule(selectors, tuple(declarations), line))
    return rules, problems


# --- the values ---------------------------------------------------------------------------------

# A value read into parts: ("kw", ident), ("num", n), ("len", n, unit), ("colour", "#rrggbb"),
# ("str", text), ("sep", "," or "/"), ("var", a density property), ("minmax", parts), ("fn", name),
# ("delim", ch).
Part = tuple


class _Bad(Exception):  # noqa: N818, a value outside the allow lists, never raised out of here
    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail


def _six(hex_digits: str) -> str:
    digits = hex_digits.lower()
    return "#" + (digits if len(digits) == 6 else "".join(ch * 2 for ch in digits))


def parts(value: Sequence[Token], customs: Mapping[str, Part], accent: str) -> list[Part]:
    """The parts of a value, `var()` read; raises _Bad on a function or a name it does not know."""
    out: list[Part] = []
    i = 0
    while i < len(value):
        token = value[i]
        i += 1
        if token.kind == "space":
            continue
        if token.kind == "ident":
            out.append(("kw", token.text.lower()))
        elif token.kind == "number":
            out.append(("num", token.value) if not token.unit else ("len", token.value, token.unit))
        elif token.kind == "hash":
            if not _HEX.fullmatch(token.text.lower()):
                raise _Bad("colour", "a colour other than #rrggbb")
            out.append(("colour", _six(token.text)))
        elif token.kind == "string":
            out.append(("str", token.text))
        elif token.kind == "delim" and token.text in ",/":
            out.append(("sep", token.text))
        elif token.kind == "function":
            depth, start = 1, i
            while i < len(value) and depth:
                if value[i].kind == "function" or (value[i].kind, value[i].text) == ("delim", "("):
                    depth += 1
                elif (value[i].kind, value[i].text) == ("delim", ")"):
                    depth -= 1
                i += 1
            if depth:
                raise _Bad("property", "a function that is not closed")
            inner = _strip(value[start : i - 1])
            name = token.text.lower()
            if name == "var":
                out.append(_var(inner, customs, accent))
            elif name == "minmax":
                out.append(("minmax", tuple(parts(inner, customs, accent))))
            else:
                out.append(("fn", name))
        else:
            out.append(("delim", token.text))
    return out


def _var(inner: list[Token], customs: Mapping[str, Part], accent: str) -> Part:
    if len(inner) != 1 or inner[0].kind != "ident" or not inner[0].text.startswith("--"):
        raise _Bad("property", "var() of one custom property, without a fallback")
    name = inner[0].text
    if name == "--accent":
        return ("colour", accent)
    if name in DENSITY:
        return ("var", name)
    if name in customs:
        return customs[name]
    raise _Bad("property", "var() of a property the sheet does not set")


def _length(part: Part, units: Iterable[str] = CAPS) -> bool:
    if part == ("num", 0.0):
        return True
    if part[0] == "var":
        return part[1] in DENSITY_LENGTHS
    return part[0] == "len" and part[2] in units and 0 <= part[1] <= CAPS.get(part[2], 20.0)


def _colour(part: Part) -> bool:
    return part[0] == "colour"


def _within(part: Part, unit: str | None, low: float, high: float) -> bool:
    if unit is None:
        return part[0] == "num" and low <= part[1] <= high
    return part[0] == "len" and part[2] == unit and low <= part[1] <= high


def _kw(*names: str):
    allowed = frozenset(names)
    return lambda found: len(found) == 1 and found[0][0] == "kw" and found[0][1] in allowed


def _lengths(most: int, *keywords: str):
    def ok(found: list[Part]) -> bool:
        words = frozenset(keywords)
        return 1 <= len(found) <= most and all(
            _length(p) or (p[0] == "kw" and p[1] in words) for p in found
        )

    return ok


BORDER_STYLES = frozenset(("solid", "dashed", "dotted", "double", "none"))
LIST_TYPES = frozenset(("none", "disc", "circle", "square", "decimal"))
ALIGN = ("start", "end", "center", "stretch", "baseline", "flex-start", "flex-end", "normal")
JUSTIFY = (*ALIGN, "space-between", "space-around", "space-evenly", "left", "right")
DISPLAY = frozenset(
    ("block", "inline", "inline-block", "flex", "grid", "list-item", "table", "table-row",
     "table-cell", "table-row-group", "table-header-group")
)  # fmt: skip
POSITION = frozenset(("static", "relative", "absolute"))


def _border(found: list[Part]) -> bool:
    if found in ([("num", 0.0)], [("kw", "none")]):
        return True
    widths = [p for p in found if _length(p)]
    styles = [p for p in found if p[0] == "kw" and p[1] in BORDER_STYLES]
    colours = [p for p in found if _colour(p)]
    sizes = (len(widths), len(styles), len(colours))
    return 1 <= len(found) <= 3 and sum(sizes) == len(found) and max(sizes) <= 1


def _flex(found: list[Part]) -> bool:
    simple = (("num",), ("kw", "auto"), ("kw", "none"))
    return 1 <= len(found) <= 3 and all(
        (p[0] == "num" and 0 <= p[1] <= 20) or _length(p) or p in simple[1:] for p in found
    )


def _track(part: Part) -> bool:
    if part[0] == "minmax":
        inner = [p for p in part[1] if p[0] != "sep"]
        return len(inner) == 2 and all(_track(p) for p in inner)
    words = ("auto", "min-content", "max-content")
    return _length(part) or _within(part, "fr", 0, 20) or (part[0] == "kw" and part[1] in words)


def _tracks(found: list[Part]) -> bool:
    return found == [("kw", "none")] or (1 <= len(found) <= 12 and all(map(_track, found)))


def _grid_line(found: list[Part]) -> bool:
    def ok(p: Part) -> bool:
        whole = p[0] == "num" and p[1] == int(p[1]) and 0 < abs(p[1]) <= 20
        return whole or p in (("kw", "span"), ("kw", "auto"), ("sep", "/"))

    return 1 <= len(found) <= 7 and all(map(ok, found))


def _list_style(found: list[Part]) -> bool:
    places = ("inside", "outside")
    return 1 <= len(found) <= 2 and all(
        p[0] == "str" or (p[0] == "kw" and (p[1] in LIST_TYPES or p[1] in places)) for p in found
    )


def _weight(found: list[Part]) -> bool:
    if _kw("normal", "bold")(found):
        return True
    return len(found) == 1 and found[0][0] == "num" and found[0][1] in range(100, 1000, 100)


def _columns(found: list[Part]) -> bool:
    return len(found) == 1 and found[0][0] == "num" and found[0][1] in (1, 2, 3)


VALUES = {
    **dict.fromkeys(
        ("margin", "padding", "border-radius", "border-width"), _lengths(4, "auto")
    ),
    **dict.fromkeys(
        ("margin-top", "margin-right", "margin-bottom", "margin-left", "width", "height",
         "min-width", "min-height", "left", "top", "right", "bottom", "flex-basis",
         "column-width"), _lengths(1, "auto")
    ),
    **dict.fromkeys(
        ("padding-top", "padding-right", "padding-bottom", "padding-left", "row-gap",
         "column-gap"), _lengths(1)
    ),
    "max-width": _lengths(1, "none"),
    "gap": _lengths(2),
    "border-spacing": _lengths(2),
    **dict.fromkeys(
        ("border", "border-top", "border-right", "border-bottom", "border-left"), _border
    ),
    "border-color": lambda found: 1 <= len(found) <= 4 and all(map(_colour, found)),
    "border-style": lambda found: 1 <= len(found) <= 4 and all(
        p[0] == "kw" and p[1] in BORDER_STYLES for p in found
    ),
    "border-collapse": _kw("collapse", "separate"),
    "box-sizing": _kw("border-box", "content-box"),
    "font-weight": _weight,
    "font-style": _kw("normal", "italic"),
    "font-variant": _kw("normal", "small-caps", "all-small-caps"),
    "font-variant-caps": _kw("normal", "small-caps", "all-small-caps"),
    "text-transform": _kw("none", "uppercase", "lowercase", "capitalize"),
    "text-align": _kw("left", "right", "center", "justify", "start", "end"),
    "text-decoration": _kw("none", "underline"),
    "vertical-align": _kw("baseline", "top", "middle", "bottom", "super", "sub"),
    "white-space": _kw("normal", "pre-line", "pre-wrap", "nowrap"),
    "overflow-wrap": _kw("normal", "anywhere", "break-word"),
    "word-break": _kw("normal", "break-word", "keep-all"),
    "align-items": _kw(*ALIGN),
    "align-self": _kw(*ALIGN, "auto"),
    "align-content": _kw(*JUSTIFY),
    "justify-content": _kw(*JUSTIFY),
    "justify-items": _kw(*ALIGN, "left", "right"),
    "justify-self": _kw(*ALIGN, "left", "right", "auto"),
    "flex": _flex,
    "flex-wrap": _kw("wrap", "nowrap"),
    "flex-direction": _kw("row", "column"),
    "flex-grow": lambda found: len(found) == 1 and _within(found[0], None, 0, 20),
    "flex-shrink": lambda found: len(found) == 1 and _within(found[0], None, 0, 20),
    "order": lambda found: len(found) == 1 and _within(found[0], None, -20, 20),
    "grid-template-columns": _tracks,
    "grid-template-rows": _tracks,
    "grid-auto-rows": _tracks,
    "grid-auto-flow": _kw("row", "column", "dense"),
    "grid-column": _grid_line,
    "grid-row": _grid_line,
    "grid-area": _grid_line,
    "columns": _columns,
    "column-count": _columns,
    **dict.fromkeys(("break-inside", "page-break-inside"), _kw("auto", "avoid")),
    **dict.fromkeys(
        ("break-before", "break-after", "page-break-before", "page-break-after"),
        _kw("auto", "avoid"),
    ),
    "object-fit": _kw("cover", "contain", "fill", "none", "scale-down"),
    "object-position": lambda found: 1 <= len(found) <= 2 and all(
        _length(p) or p in (("kw", "center"), ("kw", "top"), ("kw", "bottom"),
                            ("kw", "left"), ("kw", "right"))
        for p in found
    ),
    "list-style": _list_style,
    "list-style-type": lambda found: len(found) == 1 and (
        found[0][0] == "str" or (found[0][0] == "kw" and found[0][1] in LIST_TYPES)
    ),
    "list-style-position": _kw("inside", "outside"),
}  # fmt: skip
SPECIAL = frozenset(
    ("color", "background", "background-color", "display", "position", "content", "font-family",
     "font-size", "line-height", "letter-spacing")
)  # fmt: skip
PROPERTIES = frozenset(VALUES) | SPECIAL


def _special(prop: str, found: list[Part], names: frozenset[str]) -> tuple[str, str] | None:
    one = found[0] if len(found) == 1 else None
    if prop == "font-size":
        relative = one and (_within(one, "em", 0.5, 4) or _within(one, "%", 50, 400))
        if not (relative or one == ("var", "--body-size")):
            return "size", "font-size: em from 0.5 to 4, % or var(--body-size)"
    elif prop == "line-height":
        plain = one and (_within(one, None, 0.9, 2.5) or one in (("kw", "normal"),))
        if not (plain or one == ("var", "--line-height")):
            return "size", "line-height: a number from 0.9 to 2.5 or var(--line-height)"
    elif prop == "letter-spacing":
        if not (
            one and (one in (("kw", "normal"), ("num", 0.0)) or _within(one, "em", -0.05, 0.5))
        ):
            return "size", "letter-spacing: em from -0.05 to 0.5"
    elif prop == "display":
        if not (one and one[0] == "kw" and one[1] in DISPLAY):
            return "property", "display: a value that shows the box"
    elif prop == "position":
        if not (one and one[0] == "kw" and one[1] in POSITION):
            return "property", "position: static, relative or absolute"
    elif prop == "content":
        if not (one and (one[0] == "str" or one in (("kw", "none"), ("kw", "normal")))):
            return "content", "content: punctuation only"
    elif prop == "font-family":
        items: list[list[Part]] = [[]]
        for p in found:
            items[-1].append(p) if p != ("sep", ",") else items.append([])
        read = []
        for item in items:
            if len(item) == 1 and item[0][0] == "str":
                read.append(" ".join(item[0][1].split()).casefold())
            elif item and all(p[0] == "kw" for p in item):
                read.append(" ".join(p[1] for p in item))
            else:
                read.append("")
        if not read[0] or read[0] in GENERIC or read[0] not in names:
            return "font", "font-family: a family the manifest names, first"
        if not all(name in names or name in GENERIC for name in read):
            return "font", "font-family: a family the manifest does not name"
    elif prop == "color":
        if not (one and (_colour(one) or one in (("kw", "currentcolor"),))):
            return "colour", "color: #rrggbb or var(--accent)"
    elif prop in ("background", "background-color"):
        if not (one and (_colour(one) or one in (("kw", "none"), ("kw", "transparent")))):
            return "colour", f"{prop}: a flat colour written #rrggbb or var(--accent)"
    return None


_CODE = {
    "font-size": "size", "line-height": "size", "letter-spacing": "size", "content": "content",
    "color": "colour", "background": "colour", "background-color": "colour",
}  # fmt: skip


def _custom(found: list[Part]) -> bool:
    if len(found) != 1:
        return False
    one = found[0]
    return _colour(one) or (one[0] == "num" and one[1] >= 0) or (_length(one) and one[0] == "len")


def _shown(prop: str) -> str:
    return "a custom property" if prop.startswith("--") else prop


def value_problem(
    d: Declaration, customs: Mapping[str, Part], accent: str, names: frozenset[str]
) -> tuple[str, str] | None:
    """The code and the detail of what is wrong with one declaration of a rule, None when
    nothing."""
    if d.prop in DENSITY or d.prop == "--accent":
        return "density_pinned", f"{d.prop} is set by the density step, not by the sheet"
    if not d.prop.startswith("--") and d.prop not in PROPERTIES:
        return "property", "a property outside the allowed list"
    try:
        found = parts(d.value, customs, accent)
    except _Bad as bad:
        return _CODE.get(d.prop, bad.code), f"{_shown(d.prop)}: {bad.detail}"
    if d.prop != "font-family" and any(
        p[0] == "str" and not set(p[1]) <= CONTENT_CHARS for p in found
    ):
        return (
            "content",
            f"{_shown(d.prop)}: a string other than punctuation",
        )
    if d.prop.startswith("--"):
        if _custom(found):
            return None
        return "property", "a custom property: one length, number or #rrggbb"
    if found == [("kw", "inherit")]:
        return None
    if any(p[0] == "fn" for p in found):
        return _CODE.get(d.prop, "property"), f"{d.prop}: a function outside var() and minmax()"
    if any(p[0] == "minmax" for p in found) and d.prop not in (
        "grid-template-columns",
        "grid-template-rows",
    ):
        return "property", f"{d.prop}: minmax() outside a grid template"
    if d.prop in SPECIAL:
        return _special(d.prop, found, names)
    if d.prop in ("left", "top", "right", "bottom") and any(
        p[0] in ("len", "num") and p[1] < 0 for p in found
    ):
        return "property", f"{d.prop}: a negative offset"
    if not VALUES[d.prop](found):
        return "property", f"{d.prop}: a value outside the allowed list"
    return None


def _page_problem(d: Declaration, customs: Mapping[str, Part], accent: str) -> tuple | None:
    try:
        found = parts(d.value, customs, accent)
    except _Bad:
        return "page", "@page: only size A4 and a margin"
    if d.prop == "size":
        a4 = found in ([("kw", "a4")], [("kw", "a4"), ("kw", "portrait")])
        return None if a4 else ("page", "@page: a size other than A4")
    if d.prop in ("margin", "margin-top", "margin-right", "margin-bottom", "margin-left"):
        return None if _lengths(4)(found) else ("page", "@page: a margin of lengths")
    return "page", "@page: only size and margin"


# --- the contrast -------------------------------------------------------------------------------


def luminance(colour: str) -> float:
    """The relative luminance of `#rrggbb` (WCAG 2)."""
    channels = (int(colour[k : k + 2], 16) / 255 for k in (1, 3, 5))
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one: str, other: str) -> float:
    """The contrast ratio of two `#rrggbb` colours, from 1 to 21 (WCAG 2)."""
    light, dark = sorted((luminance(one), luminance(other)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@dataclass(eq=False)
class Element:
    """An element of a rendered page: what the selectors read, and whether it holds text."""

    tag: str
    classes: frozenset[str]
    parent: Element | None = None
    children: list[Element] = field(default_factory=list)
    text: bool = False

    def add(self, tag: str, classes: frozenset[str]) -> Element:
        child = Element(tag, classes, self)
        self.children.append(child)
        return child

    def walk(self) -> Iterable[Element]:
        stack = [self]
        while stack:
            element = stack.pop()
            yield element
            stack.extend(reversed(element.children))


@dataclass(frozen=True)
class _Paint:
    """A colour or a background a declaration of a rule sets, as the cascade weighs it."""

    selector: Selector
    colour: str | None  # #rrggbb; None: inherit (a colour) or transparent (a background)
    important: bool
    order: int
    line: int

    @property
    def weight(self) -> tuple:
        return (self.important, self.selector.specificity, self.order)


class _Matcher:
    def __init__(self, relaxed: bool, above: dict[int, frozenset[str]] | None = None) -> None:
        self.relaxed = relaxed
        self.memo: dict[tuple[int, int, int], bool] = {}
        self.above = {} if above is None else above  # per element, the keys of its ancestors
        self.needs: dict[int, frozenset[str]] = {}

    def ancestors(self, e: Element) -> frozenset[str]:
        """The keys of the ancestors of `e`."""
        chain, node = [], e
        while node is not None and id(node) not in self.above:
            chain.append(node)
            node = node.parent
        for node in reversed(chain):
            parent = node.parent
            keys = () if parent is None else (*self.above[id(parent)], *_element_keys(parent))
            self.above[id(node)] = frozenset(keys)
        return self.above[id(e)]

    def candidate(self, s: Selector, e: Element) -> bool:
        """Whether the ancestors of `e` have every key the selector asks of them."""
        if id(s) not in self.needs:
            self.needs[id(s)] = _ancestry(s)
        return self.needs[id(s)] <= self.ancestors(e)

    def compound(self, c: Compound, e: Element) -> bool:
        if (c.tag and c.tag != e.tag) or not c.classes <= e.classes:
            return False
        if "root" in c.pseudo and e.tag != "html":
            return False
        if self.relaxed or e.parent is None:
            return not (e.parent is None and c.pseudo - {"root"})
        siblings = e.parent.children
        return ("first-child" not in c.pseudo or siblings[0] is e) and (
            "last-child" not in c.pseudo or siblings[-1] is e
        )

    def matches(self, s: Selector, e: Element, k: int | None = None) -> bool:
        if k is None:
            if not self.candidate(s, e):
                return False
            k = len(s.compounds) - 1
        key = (id(s), k, id(e))
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = found = self._matches(s, e, k)
        return found

    def _matches(self, s: Selector, e: Element, k: int) -> bool:
        if not self.compound(s.compounds[k], e):
            return False
        if k == 0:
            return True
        combinator, parent = s.combinators[k - 1], e.parent
        if combinator == ">":
            return parent is not None and self.matches(s, parent, k - 1)
        if combinator == " ":
            while parent is not None:
                if self.matches(s, parent, k - 1):
                    return True
                parent = parent.parent
            return False
        siblings = parent.children if parent else []
        if self.relaxed:
            others = [x for x in siblings if x is not e]
        else:
            before = siblings[: next(i for i, x in enumerate(siblings) if x is e)]
            others = before[-1:] if combinator == "+" else before
        return any(self.matches(s, x, k - 1) for x in others)


def _paints(rules: Sequence[Rule], customs, accent) -> tuple[list[_Paint], list[_Paint]]:
    """The colours and the backgrounds the rules set, in the order of the sheet."""
    colours: list[_Paint] = []
    backs: list[_Paint] = []
    order = 0
    for rule in rules:
        if rule.selectors is None:
            continue
        for d in rule.declarations:
            if d.prop not in ("color", "background", "background-color"):
                continue
            try:
                found = parts(d.value, customs, accent)
            except _Bad:
                continue
            if len(found) != 1:
                continue
            value = found[0][1] if _colour(found[0]) else None
            order += 1
            for selector in rule.selectors:
                if selector.element is None:
                    paint = _Paint(selector, value, d.important, order, d.line)
                    (colours if d.prop == "color" else backs).append(paint)
    return colours, backs


class _Index:
    """Selectors by two keys an element they match must have: one of its subject's, and another
    of its subject's or of an ancestor's (None when there is none), so an element meets only
    the selectors whose two keys it and its ancestors have."""

    def __init__(self, selectors: Iterable[Selector]) -> None:
        self.buckets: dict[str, dict[str | None, list[Selector]]] = {}
        for selector in selectors:
            subject = selector.compounds[-1]
            first = _key(subject)
            rest = ({*_needs(subject)} | _ancestry(selector)) - {first}
            bucket = self.buckets.setdefault(first, {})
            bucket.setdefault(min(rest, default=None), []).append(selector)

    def candidates(self, e: Element, above: frozenset[str]) -> Iterable[Selector]:
        keys = _element_keys(e)
        seconds = {None, *keys, *above}
        for key in dict.fromkeys(keys):
            bucket = self.buckets.get(key)
            if bucket:
                for second in seconds if len(seconds) < len(bucket) else list(bucket):
                    if second in seconds:
                        yield from bucket.get(second, ())


class _Cascade:
    def __init__(self, colours: list[_Paint], backs: list[_Paint]) -> None:
        self.strict = _Matcher(relaxed=False)
        self.colour_ids = {id(p) for p in colours}
        # per distinct selector, the strongest colour and background it sets
        self.selectors: dict[int, Selector] = {}
        self.best: dict[int, list[_Paint | None]] = {}
        for paint in (*colours, *backs):
            slot = 0 if id(paint) in self.colour_ids else 1
            self.selectors[id(paint.selector)] = paint.selector
            pair = self.best.setdefault(id(paint.selector), [None, None])
            if pair[slot] is None or paint.weight > pair[slot].weight:
                pair[slot] = paint
        self.index = _Index(self.selectors.values())
        self.matched: set[int] = set()
        self.colour_of: dict[int, tuple[str, int]] = {}
        self.back_of: dict[int, tuple[str | None, int]] = {}

    def winners(self, e: Element) -> tuple[_Paint | None, _Paint | None]:
        best: list[_Paint | None] = [None, None]
        for selector in self.index.candidates(e, self.strict.ancestors(e)):
            if not self.strict.matches(selector, e):
                continue
            self.matched.add(id(selector))
            for slot, paint in enumerate(self.best[id(selector)]):
                if paint is not None and (best[slot] is None or paint.weight > best[slot].weight):
                    best[slot] = paint
        return best[0], best[1]

    def run(self, root: Element) -> None:
        """The colour and the own background of every element, parents first."""
        for e in root.walk():
            colour, back = self.winners(e)
            parent = self.colour_of.get(id(e.parent)) if e.parent else None
            if colour is not None and colour.colour:
                self.colour_of[id(e)] = (colour.colour, colour.line)
            else:
                self.colour_of[id(e)] = parent or (BLACK, 0)
            own = back.colour if back is not None else None
            self.back_of[id(e)] = (own, back.line if back is not None and own else 0)

    def backdrop(self, e: Element) -> tuple[str, int]:
        node: Element | None = e
        while node is not None:
            own, line = self.back_of.get(id(node), (None, 0))
            if own:
                return own, line
            node = node.parent
        return WHITE, 0


def _element_keys(e: Element) -> list[str]:
    return [f".{c}" for c in e.classes] + [e.tag, "*"]


def _key(compound: Compound) -> str:
    """The one key a part is indexed by: a class of it, else its tag, else `*`."""
    first = min(compound.classes, default=None)
    return f".{first}" if first else (compound.tag or "*")


def _keys(compound: Compound) -> list[str]:
    """Every key a part a rule covers can be indexed by."""
    found = [f".{c}" for c in compound.classes] + [compound.tag or "*", "*"]
    return [*found, "html"] if "root" in compound.pseudo else found


def _subsets(classes: frozenset[str]) -> Iterable[frozenset[str]]:
    items = sorted(classes)
    for mask in range(1 << len(items)):
        yield frozenset(c for i, c in enumerate(items) if mask >> i & 1)


class _Fallback:
    """What a selector's own parts say when no rendered element tells: the backgrounds of the
    parts it names (a background rule whose part covers one of them), and the colours of the
    rules a background's part holds."""

    def __init__(self, colours: list[_Paint], backs: list[_Paint]) -> None:
        self.backs: dict[tuple, set[str]] = {}
        for paint in backs:
            if paint.colour:
                subject = paint.selector.compounds[-1]
                shape = (subject.tag, subject.classes, "root" in subject.pseudo)
                self.backs.setdefault(shape, set()).add(paint.colour)
        self.parts: dict[str, dict[Compound, set[str]]] = {}
        for paint in colours:
            for part in paint.selector.compounds if paint.colour else ():
                for key in {*_needs(part), "*"}:
                    self.parts.setdefault(key, {}).setdefault(part, set()).add(paint.colour)
        root = Compound("html", frozenset(), frozenset(("root",)))
        self.root_backs = self.covering(root)
        self.root_colours = {
            p.colour
            for p in colours
            if p.colour and len(p.selector.compounds) == 1 and p.selector.compounds[0].covers(root)
        } or {BLACK}
        self.memo: dict[Compound, set[str]] = {}

    def covering(self, part: Compound) -> set[str]:
        """The background colours of the rules whose part covers `part`."""
        found: set[str] = set()
        roots = (False, True) if "root" in part.pseudo or part.tag == "html" else (False,)
        for tag in {None, part.tag}:
            for classes in _subsets(part.classes):
                for root in roots:
                    found |= self.backs.get((tag, classes, root), set())
        return found

    def behind(self, selector: Selector) -> set[str]:
        """The backgrounds behind a colour: of its nearest part that has one, else the root's."""
        for part in reversed(selector.compounds):
            if part not in self.memo:
                self.memo[part] = self.covering(part)
            if self.memo[part]:
                return self.memo[part]
        return self.root_backs or {WHITE}

    def held(self, subject: Compound) -> set[str]:
        """The colours a background holds: of the rules with a part it covers, else the root's."""
        needs = _needs(subject) or ["*"]
        bucket = min((self.parts.get(key, {}) for key in needs), key=len)
        found = {c for part, cs in bucket.items() if subject.covers(part) for c in cs}
        return found or self.root_colours


def _needs(part: Compound) -> list[str]:
    """The keys every element a part matches has: its classes and its tag."""
    return [f".{c}" for c in part.classes] + ([part.tag] if part.tag else [])


def _ancestry(selector: Selector) -> frozenset[str]:
    """The keys that some ancestor of any element the selector matches must have."""
    found: set[str] = set()
    for k in range(len(selector.compounds) - 1, 0, -1):
        if selector.combinators[k - 1] in (" ", ">"):
            found |= set(_needs(selector.compounds[k - 1]))
    return frozenset(found)


def _contrast(
    rules: Sequence[Rule], customs, accent: str, file: str, pages: Sequence[Element]
) -> list[Problem]:
    colours, backs = _paints(rules, customs, accent)
    problems: list[Problem] = []

    def low(one: str, other: str, line: int, detail: str) -> None:
        if contrast(one, other) < AA:
            problems.append(Problem(file, line, "contrast", detail))

    for rule in rules:
        if rule.selectors is None:
            continue
        own: dict[str, tuple[str, int]] = {}
        for d in rule.declarations:
            try:
                found = parts(d.value, customs, accent)
            except _Bad:
                continue
            painted = d.prop in ("color", "background", "background-color")
            if painted and len(found) == 1 and _colour(found[0]):
                own["color" if d.prop == "color" else "back"] = (found[0][1], d.line)
        if "color" in own and "back" in own:
            (colour, line), (back, _) = own["color"], own["back"]
            low(colour, back, line, "color: under 4.5 to 1 on its own background")

    cascade = _Cascade(colours, backs)
    elements: list[Element] = []
    for root in pages:
        cascade.run(root)
        elements += list(root.walk())
    for e in elements:
        if e.text:
            colour, line = cascade.colour_of[id(e)]
            back, back_line = cascade.backdrop(e)
            detail = "color: under 4.5 to 1 on the background it lands on"
            low(colour, back, line or back_line, detail)

    under: dict[int, set[str]] = {}
    for e in reversed(elements):
        found = {cascade.colour_of[id(e)][0]} if e.text else set()
        for child in e.children:
            if not cascade.back_of[id(child)][0]:
                found |= under[id(child)]
        under[id(e)] = found
    relaxed = _Matcher(relaxed=True, above=cascade.strict.above)
    shaped = _Index(s for s in cascade.selectors.values() if s.shaped)
    reach: dict[int, list[Element]] = {}
    for e in elements:
        for selector in shaped.candidates(e, relaxed.ancestors(e)):
            if relaxed.matches(selector, e):
                reach.setdefault(id(selector), []).append(e)

    fallback = _Fallback(colours, backs)
    against: dict[tuple[int, bool], set[str]] = {}
    for paint in (*colours, *backs):
        if paint.colour is None:
            continue
        selector, is_colour = paint.selector, id(paint) in cascade.colour_ids
        if id(selector) in cascade.matched and not selector.shaped:
            continue
        key = (id(selector), is_colour)
        if key not in against:
            if id(selector) in reach:
                against[key] = set()
                for e in reach[id(selector)]:
                    against[key] |= {cascade.backdrop(e)[0]} if is_colour else under[id(e)]
            elif is_colour:
                against[key] = fallback.behind(selector)
            else:
                against[key] = fallback.held(selector.compounds[-1])
        for other in against[key]:
            detail = "color: under 4.5 to 1 on a background its rules can put it on"
            low(paint.colour, other, paint.line, detail)
    return list(dict.fromkeys(problems))


# --- the whole sheet ----------------------------------------------------------------------------


def check(
    css: str,
    fonts: tuple[str, ...],
    accent: str,
    file: str = "style.css",
    pages: Sequence[Element] = (),
) -> list[Problem]:
    """The problems of a template's style sheet (see the module docstring); `pages` are the
    rendered pages the cascade is computed on, none for a sheet alone."""
    rules, problems = parse(css, file)
    if not rules:
        return problems or _density(rules, file)
    names = frozenset(font_map.FAMILIES[f].name.casefold() for f in fonts if f in font_map.FAMILIES)
    customs: dict[str, Part] = {}
    for rule in rules:
        for d in rule.declarations:
            if rule.selectors is None or not d.prop.startswith("--"):
                continue
            if d.prop in DENSITY or d.prop == "--accent":
                continue
            try:
                found = parts(d.value, {}, accent)
            except _Bad:
                continue
            if d.prop in customs:
                problems.append(Problem(file, d.line, "property", "a custom property set twice"))
            elif _custom(found):
                customs[d.prop] = found[0]
    for rule in rules:
        for d in rule.declarations:
            if rule.selectors is None:
                wrong = _page_problem(d, customs, accent)
            else:
                wrong = value_problem(d, customs, accent, names)
            if wrong:
                problems.append(Problem(file, d.line, *wrong))
    problems += _density(rules, file)
    problems += _contrast(rules, customs, accent, file, pages)
    return problems


def _density(rules: Sequence[Rule], file: str) -> list[Problem]:
    reads = {
        "--body-size": ("font-size",),
        "--line-height": ("line-height",),
        "--section-gap": None,
        "--item-gap": None,
    }
    page_margin = any(
        rule.selectors is None and d.prop.startswith("margin")
        for rule in rules
        for d in rule.declarations
    )
    if page_margin:
        reads["--margin"] = None
    found: set[tuple[str, str]] = set()
    for rule in rules:
        if rule.selectors is None:
            continue
        for d in rule.declarations:
            for i, token in enumerate(d.value):
                if token.kind == "function" and token.text.lower() == "var":
                    inner = _strip(d.value[i + 1 : i + 3])
                    if inner and inner[0].kind == "ident":
                        found.add((inner[0].text, d.prop))
    problems = []
    for name, props in reads.items():
        if not any(n == name and (props is None or p in props) for n, p in found):
            where = f" by {props[0]}" if props else ""
            problems.append(Problem(file, 0, "density_unread", f"{name} is not read{where}"))
    return problems
