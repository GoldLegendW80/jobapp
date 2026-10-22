"""The style sheet of a template written from an upload (task D25a; SPEC-documents.md, "Design
capture of an upload", step 4, and "Templates"). Pure: no file, no clock, no log.

`check(css, fonts, accent, file) -> [Problem]`. A static filter of allow lists: it never models
the browser's cascade or layout. Whether every field is truly visible (its contrast on the
painted background, not covered, inside the page) is judged on a render by D25b (issue #220).

The sheet is read by a tokenizer of its own (no CSS parser is installed) that stops at the first
input it does not know: `url(`, an escape outside a string, a comment or a string not closed, a
character outside the ASCII set of the rules below, functions nested deeper than
FUNCTION_DEPTH, a sheet over SHEET_MAX characters. A sheet it stops on is refused whole. Then
everything is held to allow lists:
- the structure: rules and an exact `@page`, no other at-rule, no block inside a block; `@page`
  holds only `size: A4` and `margin: var(--margin)`, so the page box is the one the render
  check lays out (design_check); `@page :first` (page 1's) is read, for the repository's
  templates, and refused as `page`;
- the selectors: type selectors of ELEMENTS, classes, `*`, `:root`, `:first-child`,
  `:last-child`, the combinators space, `>`, `+`, `~`, a final `::before`, `::after` or
  `::marker`; at most MAX_COMPOUNDS parts of MAX_CLASSES classes;
- the properties of VALUES, each with its own allow list of values; sizes of text in `em` or
  `%` or the density property, every other length from 0 up to CAPS; colours `#rrggbb` or the
  accent; strings only punctuation; functions only `var()` and `minmax()` in a grid template;
- what places a box over another is refused (OVERLAY): `position` other than static and
  relative, an offset (`left`, `top`, `right`, `bottom`, `inset`), `z-index`, `float`, a
  negative margin, any `transform` (it paints above the text and carries glyphs off their own
  box), a grid placement other than `grid-column` (the
  grid placement algorithm, CSS Grid 1 section 8.5, puts an item placed on one axis only where
  it overlaps no other item; `grid-row` and `grid-area` can put two items in one cell);
- a `::before` or `::after` is a decoration: punctuation of CONTENT_CHARS with a colour, a font
  and space around it; no fill, border, size or position (DECORATION); `content` is set on
  nothing else;
- a list marker is a glyph of LIST_TYPES: no marker string (`list-style`, `list-style-type` or
  `::marker` `content`, which Chromium lays out of the list's box, over the column beside it),
  and a `::marker` takes only a colour and a font without a size (MARKER);
- custom properties: set only on `:root` or `html`, once each, to one length, number or
  `#rrggbb` (minimal's `--rail`), and read in their place by `var()`; `var()` reads too a
  density property or `--accent`, which the sheet never sets (the step sets them);
- what a box shorter or narrower than its text would let run over its neighbours: a `height`
  or `min-height` other than `auto` only on the photo (`on_photo`; the class `photo` is the
  portrait's alone, template_rules), so a flex basis never makes a column item shorter than its
  text; no `max-height`; a grid row track only
  sized by its content (`auto`, `min-content`, `max-content`, a positive `fr`); a column track
  either that or a length of at least TRACK_MIN, `minmax()` of a floor and such a track;
- the density: each property of DENSITY but `--margin` read (the page's margin is always the
  step's), `--body-size` by a `font-size`, `--line-height` by a `line-height`;
- the fonts: a `font-family` starts with a family of the manifest;
- the contrast: a rule that sets both a text colour and a background holds them at 4.5:1 (WCAG 2
  AA). A colour on a background another rule sets is the render check's;
- no `!important` on a property of ENFORCED: the app appends ENFORCED_CSS after every sheet
  (templates.build_html), so no word is wider than its box, whatever box the sheet draws.

Declared open points, left to D25b's render check: a text colour on a background set by another
rule (the page's white, a band, a column); a line that may not wrap (`white-space: nowrap`)
in a box narrower than it, whose text, a `::before` or `::after` too, then runs over its
neighbours, under the background of a later flex or grid item or of a `position: relative` box;
the vertical padding or border of an inline box, which paints over the line above; a size made
small or large by nesting `em` sizes; a box pushed off the page by its margins; the one glyph of
an outside marker, at the size of its item (any box with `display: list-item`), beside a box
without padding.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

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
CONTENT_CHARS = PUNCTUATION | frozenset("\u00a0 ◦▪#>+")
SHEET_MAX = 64_000  # characters, template_rules.FILE_MAX
FUNCTION_DEPTH = 2  # minmax(var(--rail), 1fr)
DENSITY = ("--body-size", "--line-height", "--section-gap", "--item-gap", "--margin")
DENSITY_LENGTHS = frozenset(("--body-size", "--section-gap", "--item-gap", "--margin"))
GENERIC = frozenset(("serif", "sans-serif", "monospace"))
AA = 4.5  # WCAG 2, contrast of body text
WHITE = "#ffffff"
MAX_COMPOUNDS = 4
MAX_CLASSES = 4
CAPS = {"pt": 850.0, "px": 1134.0, "mm": 300.0, "em": 40.0, "%": 100.0}  # about one A4 height
TRACK_MIN = {"pt": 56.7, "px": 75.6, "mm": 20.0, "em": 5.0, "%": 10.0}  # a column of a word or two
ENFORCED = (("overflow-wrap", "anywhere"), ("word-break", "normal"))
ENFORCED_CSS = "*,*::before,*::after{" + "".join(f"{p}:{v}!important;" for p, v in ENFORCED) + "}"
PSEUDO_CLASSES = frozenset(("root", "first-child", "last-child"))
PSEUDO_ELEMENTS = frozenset(("before", "after", "marker"))
_HEX = re.compile(r"[0-9a-f]{3}|[0-9a-f]{6}")
_NAME_START = re.compile(r"--[A-Za-z0-9_-]+|-?[A-Za-z_][A-Za-z0-9_-]*")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)")
# an ident after a number is its unit, as CSS reads it: `1px-0px` is one number of unit `px-0px`
_UNIT = re.compile(r"%|-?[A-Za-z_][A-Za-z0-9_-]*|--[A-Za-z0-9_-]*")
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
    if len(css) > SHEET_MAX:
        raise _Stop(0, "too_large", f"a sheet over {SHEET_MAX} characters")
    found: list[Token] = []
    i, line, n, depth = 0, 1, len(css), 0
    while i < n:
        if depth > FUNCTION_DEPTH:
            raise _Stop(line, "structure", f"functions nested deeper than {FUNCTION_DEPTH}")
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
                depth += 1
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
            if ch == "(":
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
            i += 1
            continue
        raise _Stop(line, "structure", "a character the rules of a sheet do not use")
    if depth > FUNCTION_DEPTH:
        raise _Stop(line, "structure", f"functions nested deeper than {FUNCTION_DEPTH}")
    return found


# --- the rules ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Compound:
    tag: str | None
    classes: frozenset[str]
    pseudo: frozenset[str]  # of PSEUDO_CLASSES


@dataclass(frozen=True)
class Selector:
    compounds: tuple[Compound, ...]  # the subject last
    combinators: tuple[str, ...]  # between the parts: " ", ">", "+", "~"
    element: str | None  # a pseudo-element of PSEUDO_ELEMENTS


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
    first: bool = False  # @page :first


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
            pseudo = [(t.kind, t.text.lower()) for t in _strip(prelude[1:])]
            first = pseudo in ([], [("delim", ":"), ("ident", "first")])
            if prelude[0].text.lower() != "page" or not first:
                return [], [Problem(file, line, "structure", "an at-rule other than @page")]
            selectors: tuple[Selector, ...] | None = None
            page_first = bool(pseudo)
        else:
            page_first = False
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
            selectors = tuple(r for r in read if isinstance(r, Selector))
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
        rules.append(Rule(selectors, tuple(declarations), line, page_first))
    return rules, problems


# --- the values ---------------------------------------------------------------------------------

# A value read into parts: ("kw", ident), ("num", n), ("len", n, unit), ("colour", "#rrggbb"),
# ("str", text), ("sep", "," or "/"), ("var", a density property), ("minmax", parts),
# ("fn", name), ("delim", ch).
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
                out.append((name, tuple(parts(inner, customs, accent))))
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
POSITION = frozenset(("static", "relative"))


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


def _sized_by_content(part: Part) -> bool:
    words = ("auto", "min-content", "max-content")
    return _within(part, "fr", 0.1, 20) or (part[0] == "kw" and part[1] in words)


def _wide(part: Part) -> bool:
    return (
        part[0] == "len"
        and part[2] in TRACK_MIN
        and _within(part, part[2], TRACK_MIN[part[2]], CAPS[part[2]])
    )


def _column(part: Part) -> bool:
    """A column track that grows with its text, or one at least TRACK_MIN wide."""
    if part[0] == "minmax":
        inner = [p for p in part[1] if p[0] != "sep"]
        if len(inner) != 2 or any(p[0] == "minmax" for p in inner):
            return False
        low, high = inner
        floor = _length(low) or (
            low[0] == "kw" and low[1] in ("auto", "min-content", "max-content")
        )
        return floor and _column(high)
    return _sized_by_content(part) or _wide(part)


def _tracks(row: bool):
    track = _sized_by_content if row else _column

    def ok(found: list[Part]) -> bool:
        return found == [("kw", "none")] or (1 <= len(found) <= 12 and all(map(track, found)))

    return ok


def _grid_line(found: list[Part]) -> bool:
    def ok(p: Part) -> bool:
        whole = p[0] == "num" and 0 < abs(p[1]) <= 20 and p[1] == int(p[1])
        return whole or p in (("kw", "span"), ("kw", "auto"), ("sep", "/"))

    return 1 <= len(found) <= 7 and all(map(ok, found))


def _list_style(found: list[Part]) -> bool:
    places = ("inside", "outside")
    return 1 <= len(found) <= 2 and all(
        p[0] == "kw" and (p[1] in LIST_TYPES or p[1] in places) for p in found
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
         "min-width", "min-height", "flex-basis",
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
    "box-decoration-break": _kw("slice", "clone"),
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
    "grid-template-columns": _tracks(row=False),
    "grid-template-rows": _tracks(row=True),
    "grid-auto-rows": _tracks(row=True),
    "grid-auto-flow": _kw("row", "column", "dense"),
    "grid-column": _grid_line,
    "contain": _kw("none", "inline-size"),
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
    "list-style-type": _kw(*LIST_TYPES),
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
            return "property", "position: static or relative"
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


DECORATIONS = frozenset(("before", "after"))
DECORATION = frozenset(
    ("content", "color", "font-family", "font-size", "font-weight", "font-style", "font-variant",
     "font-variant-caps", "letter-spacing", "line-height", "vertical-align", "white-space",
     "display", "text-transform", "text-decoration", "margin", "margin-top",
     "margin-right", "margin-bottom", "margin-left", "padding", "padding-top", "padding-right",
     "padding-bottom", "padding-left")
)  # fmt: skip
MARKER = frozenset(
    ("color", "font-family", "font-weight", "font-style", "font-variant", "font-variant-caps")
)  # fmt: skip
# what moves or places a box over another; position is held in _special
OVERLAY = frozenset(
    ("left", "top", "right", "bottom", "inset", "inset-block", "inset-inline", "z-index",
     "grid-row", "grid-row-start", "grid-row-end", "grid-area", "grid-column-start",
     "grid-column-end", "float", "transform", "translate", "rotate", "scale", "offset",
     "margin-trim")
)  # fmt: skip


def _target_problem(prop: str, targets: frozenset[str | None]) -> tuple[str, str] | None:
    """What a property may not set on what the rule's selectors match: `targets` holds their
    pseudo-elements, None for an element."""
    if targets & DECORATIONS and prop not in DECORATION:
        return "property", f"{prop}: a ::before or ::after holds punctuation only, no box"
    if "marker" in targets and prop not in MARKER:
        return "property", f"{prop}: a ::marker takes only a colour and a font, no size"
    if None in targets and prop == "content":
        return "property", f"{prop}: only on a ::before or ::after"
    return None


def on_photo(selector: Selector) -> bool:
    """Whether a selector's subject is the photo: an `img`, or the `.photo` class, which only
    the macro `portrait`'s `img` may hold (template_rules)."""
    subject = selector.compounds[-1]
    photo = subject.tag == "img" or (subject.tag is None and "photo" in subject.classes)
    return selector.element is None and photo


def value_problem(
    d: Declaration,
    customs: Mapping[str, Part],
    accent: str,
    names: frozenset[str],
    targets: frozenset[str | None] = frozenset((None,)),
    photo: bool = False,
) -> tuple[str, str] | None:
    """The code and the detail of what is wrong with one declaration of a rule whose selectors
    match `targets` (see _target_problem), None when nothing. `photo`: every selector of the
    rule has the photo for subject (`on_photo`), the one box a fixed height may hold."""
    if d.prop in DENSITY or d.prop == "--accent":
        return "density_pinned", f"{d.prop} is set by the density step, not by the sheet"
    if d.prop in OVERLAY:
        return "property", f"{d.prop}: it can lay a box over another"
    if not d.prop.startswith("--") and d.prop not in PROPERTIES:
        return "property", "a property outside the allowed list"
    if not d.prop.startswith("--") and (wrong := _target_problem(d.prop, targets)):
        return wrong
    if d.important and d.prop in dict(ENFORCED):
        return "property", f"{d.prop}: no !important, the app's own rule comes after the sheet"
    try:
        found = parts(d.value, customs, accent)
    except _Bad as bad:
        return _CODE.get(d.prop, bad.code), f"{_shown(d.prop)}: {bad.detail}"
    if d.prop != "font-family" and any(
        p[0] == "str" and not set(p[1]) <= CONTENT_CHARS for p in found
    ):
        return "content", f"{_shown(d.prop)}: a string other than punctuation"
    if d.prop.startswith("--"):
        if _custom(found):
            return None
        return "property", "a custom property: one length, number or #rrggbb"
    if found == [("kw", "inherit")]:
        return None
    if any(p[0] == "fn" for p in found):
        return _CODE.get(d.prop, "property"), f"{d.prop}: a function outside the allowed list"
    if d.prop in SPECIAL:
        return _special(d.prop, found, names)
    if d.prop in ("height", "min-height") and found != [("kw", "auto")] and not photo:
        return "property", f"{d.prop}: auto, a length only on the photo (img or .photo)"
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
    if d.prop == "margin":
        return None if found == [("var", "--margin")] else ("page", "@page: margin: var(--margin)")
    return "page", "@page: only size: A4 and margin: var(--margin)"


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


def _contrast(rules: Sequence[Rule], customs, accent: str, file: str) -> list[Problem]:
    """A rule that sets both a text colour and a background: the two at AA or more. Of two
    declarations of one property, the important one wins, else the later."""
    problems: list[Problem] = []
    for rule in rules:
        if rule.selectors is None:
            continue
        own: dict[str, tuple[bool, str, int]] = {}
        for d in rule.declarations:
            if d.prop not in ("color", "background", "background-color"):
                continue
            try:
                found = parts(d.value, customs, accent)
            except _Bad:
                continue
            slot = "color" if d.prop == "color" else "back"
            if slot in own and own[slot][0] and not d.important:
                continue
            colour = found[0][1] if len(found) == 1 and _colour(found[0]) else ""
            own[slot] = (d.important, colour, d.line)
        colour, back = own.get("color"), own.get("back")
        if colour and back and colour[1] and back[1] and contrast(colour[1], back[1]) < AA:
            detail = "color: under 4.5 to 1 on the background of its own rule"
            problems.append(Problem(file, colour[2], "contrast", detail))
    return problems


# --- the whole sheet ----------------------------------------------------------------------------


def _on_root(selector: Selector) -> bool:
    """Whether a selector is exactly `:root` or `html`."""
    if len(selector.compounds) != 1 or selector.element is not None:
        return False
    part = selector.compounds[0]
    root = part.tag == "html" or (part.tag is None and "root" in part.pseudo)
    return root and not part.classes and part.pseudo <= {"root"}


def check(css: str, fonts: tuple[str, ...], accent: str, file: str = "style.css") -> list[Problem]:
    """The problems of a template's style sheet (see the module docstring)."""
    rules, problems = parse(css, file)
    if not rules:
        return problems or _density(rules, file)
    names = frozenset(font_map.FAMILIES[f].name.casefold() for f in fonts if f in font_map.FAMILIES)
    customs: dict[str, Part] = {}
    seen: set[str] = set()
    for rule in rules:
        for d in rule.declarations:
            if rule.selectors is None or not d.prop.startswith("--"):
                continue
            if d.prop in DENSITY or d.prop == "--accent":
                continue
            if not rule.selectors or not all(map(_on_root, rule.selectors)):
                detail = "a custom property set on another selector than :root"
                problems.append(Problem(file, d.line, "property", detail))
                continue
            if d.prop in seen:
                problems.append(Problem(file, d.line, "property", "a custom property set twice"))
                continue
            seen.add(d.prop)
            try:
                found = parts(d.value, {}, accent)
            except _Bad:
                continue
            if _custom(found):
                customs[d.prop] = found[0]
    for rule in rules:
        if rule.first:  # refused whole, its declarations are not judged one by one
            detail = "@page :first: the repository's templates only"
            problems.append(Problem(file, rule.line, "page", detail))
            continue
        for d in rule.declarations:
            if rule.selectors is None:
                wrong = _page_problem(d, customs, accent)
            else:
                targets = frozenset(s.element for s in rule.selectors)
                photo = bool(rule.selectors) and all(map(on_photo, rule.selectors))
                wrong = value_problem(d, customs, accent, names, targets, photo)
            if wrong:
                problems.append(Problem(file, d.line, *wrong))
    problems += _density(rules, file)
    problems += _contrast(rules, customs, accent, file)
    return problems


def _density(rules: Sequence[Rule], file: str) -> list[Problem]:
    reads = {
        "--body-size": ("font-size",),
        "--line-height": ("line-height",),
        "--section-gap": None,
        "--item-gap": None,
    }
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
