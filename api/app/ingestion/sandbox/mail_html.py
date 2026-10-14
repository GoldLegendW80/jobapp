"""The HTML of a mail, made safe to store and to show (mail M3, tasks/mail/todo.md, "A mail into
stored text"). Runs only inside the locked down child of the parser service: main.py imports
this file after the lockdown and calls `handle`. The bytes were written by a stranger.

    handle(data, options, budget) -> the whole answer, as parser_client.check_mail_html reads it

`data` is the HTML part of one message as UTF-8 (the worker decoded its charset, mail/parse.py).
What comes back:

    {"ok": true, "kind": "mail_html", "html": ..., "text": ..., "remote_images": n,
     "truncated": bool}

**The HTML is an allow list, never a block list.** Only the tags of `TAGS` survive:

    p br div span a b strong i em u ul ol li blockquote pre code
    table thead tbody tr td th h1 h2 h3 h4 hr img

`a` keeps `href` when it is an absolute `http`, `https` or `mailto` address. `img` keeps `alt`,
`width` and `height`, and an absolute `http` or `https` source is moved to `data-src`: the
stored HTML holds no `src` at all, so nothing loads until the reader asks ("Show images"), and
the answer counts those images (`remote_images`). Everything else is gone: `style`, `class`,
`id`, every `on...` attribute, `form`, `svg`, `math`, `base`, `meta`, `link`, comments, a
relative or `//host` address, `cid:`, `data:`, `javascript:`. The text of a dropped tag stays,
except inside the tags of `DROPPED_WITH_CONTENT`, whose content was never meant to be read.

Three passes, and **nothing is fetched by any of them**:

1. `nh3` (ammonia, an HTML5 parser in Rust) cleans the document against the allow list. It is
   the one that reads the stranger's markup.
2. A walker of the standard library reads what pass 1 wrote, which is well formed and holds
   allowed tags only. It writes the text version, moves `src` to `data-src`, checks every
   address and number itself, and stops nesting at `DEPTH_MAX` (deeper tags are dropped, their
   text kept, `truncated` set).
3. `nh3` again, on what the walker wrote, without `src` in its list: the stored HTML is always
   the direct output of the sanitiser, whatever the walker did.

**The text version** is what every agent reads and what the page falls back on: a line per
block, a tab between cells, and a link written "label (address)" whenever the two differ, so a
link that shows one address and leads to another shows both. It is plain text: instructions
written in a mail come out as the words they are.

**Caps, enforced before and while parsing:** `INPUT_MAX_CHARS` of input (the rest is dropped);
an estimate of the nesting made before pass 1, which cuts the document where it passes
`SCAN_DEPTH_MAX` open tags (the HTML5 tree builder is quadratic in the depth: 100,000 nested
`div` take 10 s on an M4, 3,000 take 10 ms; the estimate is not a proof, and the CPU limit of
the child is what holds if it is fooled); in the same scan, an estimate of the formatting
elements the tree builder opens again (it reopens every open `b`, `i`, `font`... under each
block that follows, so n of them before m blocks are written n times m times), which cuts the
document where it passes `SCAN_REOPENED_MAX`; in the same scan, the tags that stand directly in
a table (the tree builder moves each in front of the table, and the library pays for each move
as many steps as were moved before), cut where they pass `SCAN_FOSTERED_MAX`; `GROWTH_MAX`, what
pass 1 may write in times what it read, beyond which the page is refused before the walker reads
it; `HTML_MAX_BYTES` and `TEXT_MAX_CHARS` of output; and the whole answer under `ANSWER_MAX`
bytes. Each cut sets `truncated`. The walker answers an end tag without reading its stack of
open tags, and looks at the budget every `WALK_BUDGET_EVERY` steps.

A failure is `{"ok": false, "failure": "file", "code": ...}`: `unreadable` (the sanitiser gave
up), `too_slow` (the budget was spent, or pass 1 wrote more than `GROWTH_MAX` times its input).
The worker then stores the mail with its plain text part and no HTML: a mail is never lost
because its HTML is hostile.

Only main.py may import this file in production. The tests import it too.
"""

import html
import json
import re
from html.parser import HTMLParser

import nh3

TAGS = frozenset(
    [
        "p",
        "br",
        "div",
        "span",
        "a",
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "hr",
        "img",
    ]
)
VOID = frozenset({"br", "hr", "img"})
# Dropped with what they hold: code, style sheets, and markup whose content is not the mail's
# text (a title, the fallback of a frame, the options of a list, foreign content).
DROPPED_WITH_CONTENT = frozenset(
    [
        "script",
        "style",
        "title",
        "textarea",
        "noscript",
        "iframe",
        "object",
        "embed",
        "template",
        "svg",
        "math",
        "select",
        "noembed",
        "noframes",
        "xmp",
        "plaintext",
        "canvas",
        "audio",
        "video",
        "applet",
        "frameset",
        "head",
    ]
)
SCHEMES = frozenset({"http", "https", "mailto"})
LINK_REL = "noopener noreferrer nofollow"

INPUT_MAX_CHARS = 500_000
HTML_MAX_BYTES = 500_000
TEXT_MAX_CHARS = 500_000
ANSWER_MAX = 1_499_000  # the worker reads 1.5 MB; main.py adds `usage` to the answer
DEPTH_MAX = 100  # nesting of the stored HTML
SCAN_DEPTH_MAX = 3000  # nesting of the input, estimated, beyond which it is cut
SCAN_REOPENED_MAX = 300_000  # formatting elements opened again, estimated, beyond which it is cut
SCAN_FOSTERED_MAX = 20_000  # tags directly inside a table, beyond which it is cut
ALIKE_MAX = 3  # elements alike the tree builder holds open for reopening (its "Noah's Ark")
PLAIN_TAG_MAX = 200  # characters of a start tag beyond which it is alike to no other
GROWTH_MAX = 8  # what pass 1 may write, in times what it read: escaping alone writes six
GROWTH_FLOOR = 10_000  # characters a short page is counted as, for that bound
WALK_BUDGET_EVERY = 4096  # tags and pieces of text the walker reads between two looks at the budget
ADDRESS_MAX = 2000
ALT_MAX = 500

_PASS_1 = {"a": {"href"}, "img": {"alt", "width", "height", "src"}}
_PASS_3 = {"a": {"href"}, "img": {"alt", "width", "height", "data-src"}}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A tag and its name as the tokenizer reads them: the name runs to a space, a `/` or a `>`, so
# `<p-x>` is an element of its own and not a paragraph. Only A to Z are lowered, as there: the
# Kelvin sign is lowered to `k` by Python and by no tokenizer.
_NAME = re.compile(r"[a-zA-Z][^\t\n\f\r />]*+")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
# The rest of a tag after its name, to the `>` that ends it, as the tokenizer reads it (review of
# PR #179, N2): attributes, each a name (which may begin with `=` and hold quotes and `<`) and
# perhaps a value, quoted or not. A `>` ends the tag anywhere but inside a quoted value, so
# `<div </div>` is one start tag and `<div title="</div>">` another. Each piece is possessive and
# begins with a character no other piece begins with: one pass, no backtracking.
_TAG_REST = re.compile(
    r"(?:[\t\n\f\r /]++"
    r"|(?:=[^\t\n\f\r />=]*+|[^\t\n\f\r />=]++)"
    r"(?:[\t\n\f\r ]*+=[\t\n\f\r ]*+"
    r"""(?:"[^"]*+"|'[^']*+'|[^\t\n\f\r >"'][^\t\n\f\r >]*+|(?=>)))?+"""
    r")*+>"
)
_COMMENT_END = re.compile(r"--!?>")
_MARKUP = re.compile(r"<[a-zA-Z/!?]")
# The elements whose content the tokenizer reads as text up to their own end tag, when the tree
# builder opens them as HTML elements. In `<svg>` or `<math>` they are elements like any other
# and their content is markup.
_RAW_TEXT = frozenset(
    [
        "iframe",
        "noembed",
        "noframes",
        "noscript",
        "plaintext",
        "script",
        "style",
        "textarea",
        "title",
        "xmp",
    ]
)
_RAW_END = {
    name: re.compile(rf"</{name}(?=[\t\n\f\r />])", re.IGNORECASE | re.ASCII) for name in _RAW_TEXT
}
# What moves the tokenizer between the states of a script's content: `<!--` escapes it, a
# `<script` inside the escape escapes it twice, and `</script` ends it only outside the second.
_SCRIPT_STATES = re.compile(
    r"<!--(-*>)?|--+>|<(/?)script(?=[\t\n\f\r />])", re.IGNORECASE | re.ASCII
)
# What follows the name of a start tag written plainly, up to its `>`: attributes without a
# value, or with one that holds no quote and no angle bracket. Two such tags written alike are
# the same element to the tree builder; of any other tag nothing is assumed.
_PLAIN_REST = re.compile(r"""(?:\s+[^\s"'<>=/`]+(?:=(?:[^\s"'<>=`]+|"[^"<>]*"|'[^'<>]*'))?)*\s*>""")
_LINK = re.compile(r"(?:https?://|mailto:)[^\s<>\"']+", re.IGNORECASE)
_REMOTE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_NUMBER = re.compile(r"[0-9]{1,4}")
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")
# What the estimate leaves uncounted, and why (review of PR #179, N1). Only the void elements:
# in HTML content the tree builder inserts one and closes it at once (`col` outside a table is
# not inserted at all), so none of them ever holds the next tag. Inside `<svg>` or `<math>` any
# name may hold the next tag, voids included: from the first start tag of those two on, the
# mark is kept to the end of the page (a `</svg>` in a comment, a CDATA section or an attribute
# closes nothing for the tree builder) and every start tag is counted.
_VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
_FOREIGN = frozenset({"svg", "math"})
# The names that were also left uncounted in the first two rounds (`p`, `option`, the cells, the
# rows and the sections) are counted now, each with the narrow rule of the list items: a start
# tag closes the element right above it, the top of the estimate, when that element is of one
# of the kinds `_CLOSES` lists for it, in that order, and never looks further down. Each is no
# more than what the tree builder does: `<option>` closes the option that is the current node,
# `<li>` an item of its kind, a cell, a row or a section the cell, row and section it ends.
# Whatever else stands between two of them is counted, and so is the next: `<li><dd><li>` and
# `<td><b><td>` grow the estimate. The paragraph is the one kind looked for further down, as
# the tree builder looks for it: each of these closes the nearest open `p` and all above it,
# unless one of `_BUTTON_SCOPE` stands above that `p` (`<p><b>x<p>` is two paragraphs side by
# side, `<p><object><p>` nests and is counted). In foreign content only the rule of the items
# holds, as it did before.
_KIND = {
    "p": "p",
    "li": "li",
    "dd": "dd",
    "dt": "dd",
    "option": "option",
    "td": "cell",
    "th": "cell",
    "tr": "row",
    "tbody": "section",
    "thead": "section",
    "tfoot": "section",
}
# The scope fences of the HTML standard: an end tag of a block closes nothing below one of them.
# `select` is one here too, though not in the standard: what stands below a select is closed by
# no end tag in the scan.
_SCOPE = frozenset(
    [
        "applet",
        "caption",
        "html",
        "marquee",
        "object",
        "table",
        "td",
        "th",
        "template",
        "select",
        "mi",
        "mo",
        "mn",
        "ms",
        "mtext",
        "annotation-xml",
        "foreignobject",
        "desc",
        "title",
    ]
)
_BUTTON_SCOPE = _SCOPE | {"button"}
# The blocks whose start tag closes the nearest open `p` in button scope, as `<p>` does.
_KIND.update(
    dict.fromkeys(
        [
            "address",
            "article",
            "aside",
            "blockquote",
            "center",
            "details",
            "dialog",
            "dir",
            "div",
            "dl",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "form",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hgroup",
            "listing",
            "main",
            "menu",
            "nav",
            "ol",
            "pre",
            "search",
            "section",
            "summary",
            "ul",
        ],
        "block",
    )
)
_CLOSES = {
    "p": ("p",),
    "li": ("p", "li"),
    "dd": ("p", "dd"),
    "option": ("option",),
    "cell": ("p", "cell"),
    "row": ("p", "cell", "row"),
    "section": ("p", "cell", "row", "section"),
    "block": ("p",),
}
# A cell, a row, a section, a caption or a column group outside a table (or a template) is
# ignored by the tree builder in body: such a tag is not counted while the estimate holds no
# `table` and no `template` open. Nor is a `head` or a `frame`, which it ignores in HTML. An
# element the estimate holds and the tree builder does not would let an end tag of its name
# close, in the estimate, the elements opened above it (`<span></caption><caption>`).
_TABLE_ONLY = frozenset({"td", "th", "tr", "tbody", "thead", "tfoot", "caption", "colgroup"})
_IGNORED = frozenset({"head", "frame"})
# What a table holds itself. Any other tag that stands directly in a table (a `table` open, no
# cell, caption or template open in it) is moved in front of the table by the tree builder, and
# the library inserts each such node in front of its sibling at a cost that grows with the
# siblings already moved: quadratic, so those tags are counted against SCAN_FOSTERED_MAX.
_TABLE_PARTS = frozenset(
    ["table", "caption", "colgroup", "col", "tbody", "thead", "tfoot", "tr", "td", "th"]
)
_TABLE_CONTEXT = frozenset({"table", "td", "th", "caption", "template"})
_BLOCKS = frozenset(
    [
        "p",
        "div",
        "blockquote",
        "pre",
        "ul",
        "ol",
        "li",
        "table",
        "thead",
        "tbody",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
    ]
)
_CELLS = frozenset({"td", "th"})
# The items of the first rounds' rule, which also holds in foreign content.
_ITEMS = {"li": "li", "dd": "dd", "dt": "dd"}
# The formatting elements of the HTML standard: closed by a block's end, the tree builder opens
# them again under whatever follows.
_FORMATTING = frozenset(
    [
        "a",
        "b",
        "big",
        "code",
        "em",
        "font",
        "i",
        "nobr",
        "s",
        "small",
        "strike",
        "strong",
        "tt",
        "u",
    ]
)
# What an end tag closes in the estimate (review of PR #179, N2): never more than the tree
# builder closes, so a page whose end tags close nothing for the tree builder grows the estimate
# as it grows the tree. Each end tag closes the nearest open element of its name and all those
# above it, and only when none of its fences stands above that element; else it closes nothing.
# - the blocks, `dd`, `dt`, the headings (any level ends any), `applet`, `marquee`, `object`
#   and `caption` are fenced by `_SCOPE`; `li` by `_SCOPE` and the lists; `p` by `_BUTTON_SCOPE`;
# - the parts of a table by a table, a template, `html` and a select;
# - every other name, the formatting elements among them, by the special elements of the HTML
#   standard: the tree builder ignores `</x>` and `</span>` when a `div` stands above the `x` or
#   the `span`, and it moves blocks rather than close them for a `</b>` with a block above it
#   (the adoption agency), which the estimate does not follow;
# - `colgroup` and `form` close only when they are the top of the estimate, `body`, `html`
#   and `br` never (`</br>` is a `<br>`).
_SPECIAL = _SCOPE | frozenset(
    [
        "address",
        "area",
        "article",
        "aside",
        "base",
        "basefont",
        "bgsound",
        "blockquote",
        "body",
        "br",
        "button",
        "center",
        "col",
        "colgroup",
        "dd",
        "details",
        "dir",
        "div",
        "dl",
        "dt",
        "embed",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "frame",
        "frameset",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hgroup",
        "hr",
        "iframe",
        "img",
        "input",
        "keygen",
        "li",
        "link",
        "listing",
        "main",
        "menu",
        "meta",
        "nav",
        "noembed",
        "noframes",
        "noscript",
        "ol",
        "p",
        "param",
        "plaintext",
        "pre",
        "script",
        "search",
        "section",
        "source",
        "style",
        "summary",
        "tbody",
        "textarea",
        "tfoot",
        "thead",
        "tr",
        "track",
        "ul",
        "wbr",
        "xmp",
    ]
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_HEADING = "h1 to h6"  # the key of the headings in the estimate: no tag name holds a space
_FENCES = {
    "special": _SPECIAL,
    "scope": _SCOPE,
    "list": _SCOPE | {"ol", "ul"},
    "table": frozenset({"html", "table", "template", "select"}),
}
_END_IN_SCOPE = frozenset(
    {name for name, kind in _KIND.items() if kind in ("block", "dd") and name != "form"}
    | {"applet", "marquee", "object", "caption"}
)
_END_IN_TABLE = frozenset({"table", "tbody", "thead", "tfoot", "tr", "td", "th"})
_END_ON_TOP = frozenset({"colgroup", "form"})
_END_NEVER = frozenset({"body", "html", "br"})
# The start tags before which the tree builder does not open again the formatting elements it
# holds: the blocks, the items, the parts of a table, and what belongs in a head.
_NO_REOPEN = (
    (frozenset(_KIND) - {"option"})
    | _TABLE_PARTS
    | _IGNORED
    | frozenset(
        [
            "base",
            "basefont",
            "bgsound",
            "body",
            "frameset",
            "hr",
            "html",
            "link",
            "meta",
            "param",
            "source",
            "template",
            "track",
        ]
    )
)


def _end_rule(name: str) -> str:
    """The fence of an end tag of `name`, as `_FENCES` names them."""
    if name in _END_IN_SCOPE:
        return "scope"
    if name == "li":
        return "list"
    if name in _END_IN_TABLE:
        return "table"
    return "special"


def _comment_end(text: str, start: int) -> int:
    """Where the comment that opens at `start` ends: `<!-->` and `<!--->` end at once."""
    after = start + 4
    if text.startswith(">", after):
        return after + 1
    if text.startswith("->", after):
        return after + 2
    found = _COMMENT_END.search(text, after)
    return found.end() if found else len(text)


def _script_end(text: str, start: int) -> int:
    """Where the end tag of a script whose content begins at `start` stands."""
    state = 0  # 0 its content, 1 escaped by `<!--`, 2 escaped twice by a `<script` in that
    for found in _SCRIPT_STATES.finditer(text, start):
        if found[0].startswith("<!--") and found[1] is None:
            state = state or 1
        elif found[0].startswith(("<!--", "-")):
            state = 0
        elif found[2]:
            if state < 2:
                return found.start()
            state = 1
        elif state == 1:
            state = 2
    return len(text)


def _raw_end(text: str, name: str, start: int) -> int:
    """Where the end tag of the raw text element `name` whose content begins at `start` stands,
    or the end of `text`."""
    if name == "script":
        return _script_end(text, start)
    found = None if name == "plaintext" else _RAW_END[name].search(text, start)
    return found.start() if found else len(text)


def _only_text(text: str, start: int, end: int) -> bool:
    """Whether `text[start:end]` read as markup holds no tag: only text, and comments or CDATA
    sections that end inside it. Then it is the same read as text or as markup."""
    while found := _MARKUP.search(text, start, end):
        at = found.start()
        if text.startswith("<!--", at):
            start = _comment_end(text, at)
        elif text.startswith("<![CDATA[", at):
            closing = text.find("]]>", at + 9, end)
            start = closing + 3 if closing >= 0 else end + 1
        else:
            return False
        if start > end:
            return False
    return True


def cut_at_depth(text: str) -> tuple[str, bool]:
    """`text` up to the tag that would open more than SCAN_DEPTH_MAX elements at once, open
    again more than SCAN_REOPENED_MAX formatting elements, or be the tag past SCAN_FOSTERED_MAX
    that stands directly in a table, and whether it was cut. A linear estimate, read as the
    tokenizer reads the page (`_TAG_REST`, comments, raw text): start tags are counted but for
    the voids and the narrow rules of `_CLOSES` and `_TABLE_ONLY`, none of which holds once
    `<svg>` or `<math>` was seen; an end tag closes what `_FENCES` lets it close, never more
    than the tree builder. A formatting element is held from its start tag to an end tag of its
    name that ends it, and every tag in between may open again all those that are held. Of
    start tags written alike three are held, as the tree builder holds three elements that are
    alike: a page that never closes its `<b>` is not one that grows. After `<svg>` or `<math>`
    the scan cannot tell whether a `<style>` or a `<![CDATA[` is read as text or as markup: the
    page is cut there unless both readings hold the same tags."""
    open_names: list[str] = []
    where: dict[str, list[int]] = {}  # per name: where in open_names its elements stand
    fenced: dict[str, list[int]] = {rule: [] for rule in _FENCES}  # where the fences stand
    contexts: list[int] = []  # where in open_names a table, a cell, a caption, a template stand
    scopes: list[int] = []  # where in open_names a `p` or one of _BUTTON_SCOPE stands
    # Per name: the start tags held, each as [written if plain, whether its element is open].
    held: dict[str, list[list]] = {}
    open_held: list[list | None] = []  # beside open_names: the held start tag of each, if any
    alike: dict[str, int] = {}
    reopening: set[str] = set()  # the names of which some are held
    holding = reopened = fostered = 0
    foreign = False

    def open_element(name: str, entry: list | None = None) -> None:
        position = len(open_names)
        if name in _TABLE_CONTEXT:
            contexts.append(position)
        if name == "p" or name in _BUTTON_SCOPE:
            scopes.append(position)
        for fence, names in _FENCES.items():
            if name in names:
                fenced[fence].append(position)
        open_names.append(name)
        open_held.append(entry)
        where.setdefault(_HEADING if name in _HEADINGS else name, []).append(position)

    def close_top() -> None:
        name = open_names.pop()
        entry = open_held.pop()
        if entry is not None:
            entry[1] = False
        where[_HEADING if name in _HEADINGS else name].pop()
        for fence in fenced.values():
            if fence and fence[-1] == len(open_names):
                fence.pop()
        if contexts and contexts[-1] == len(open_names):
            contexts.pop()
        if scopes and scopes[-1] == len(open_names):
            scopes.pop()

    def fenced_off(key: str, rule: str) -> bool:
        """Whether an element of `key` is open with a fence of `rule` above the nearest one."""
        fence = fenced[rule]
        return bool(where.get(key) and fence and fence[-1] > where[key][-1])

    def close_to(key: str) -> None:
        """Closes the nearest open element of `key` and all above it."""
        bottom = where[key][-1]
        while len(open_names) > bottom:
            close_top()

    at = 0
    while (start := text.find("<", at)) >= 0:
        after = start + 1
        closing = text.startswith("/", after)
        named = _NAME.match(text, after + closing)
        if named is None:
            if text.startswith("!--", after):
                at = _comment_end(text, start)
            elif text.startswith("![CDATA[", after) and foreign:
                # Text to `]]>` in foreign content, a comment to the first `>` elsewhere.
                ends = text.find("]]>", after), text.find(">", after)
                if ends[1] >= 0 and ends[1] != ends[0] + 2:
                    return text[:start], True
                at = ends[1] + 1 if ends[1] >= 0 else len(text)
            elif closing and text.startswith(">", after + 1):
                at = after + 2
            elif text.startswith(("!", "?", "/"), after):
                ends = text.find(">", after)
                at = ends + 1 if ends >= 0 else len(text)
            else:
                at = after
            continue
        rest = _TAG_REST.match(text, named.end())
        if rest is None:  # a tag open to the end of the page: the tokenizer drops it
            break
        at = rest.end()
        name = named[0].translate(_ASCII_LOWER)
        key = _HEADING if name in _HEADINGS else name
        rule = _end_rule(name)
        if closing and held.get(name) and not fenced_off(name, "special"):
            ended = held[name].pop()[0]
            if ended is not None:
                alike[ended] -= 1
            holding -= 1
            if not held[name]:
                reopening.discard(name)
        reopened += holding
        if reopened > SCAN_REOPENED_MAX:
            return text[:start], True
        if contexts and open_names[contexts[-1]] == "table" and name not in _TABLE_PARTS:
            fostered += 1
            if fostered > SCAN_FOSTERED_MAX:
                return text[:start], True
        if closing:
            if name in _END_NEVER or not where.get(key):
                continue
            if name in _END_ON_TOP:
                if open_names[-1] == name:
                    close_top()
            elif name == "p":
                if open_names[scopes[-1]] == "p":
                    close_to("p")
            elif not fenced_off(key, rule):
                close_to(key)
            continue
        if name in _RAW_TEXT:
            ends = _raw_end(text, name, at)
            # In foreign content the same characters are markup: both readings must agree.
            if foreign and not _only_text(text, at, ends):
                return text[:start], True
            if (
                ends == len(text)
                or (end_tag := _TAG_REST.match(text, ends + 2 + len(name))) is None
            ):
                break
            at = end_tag.end()
            continue
        foreign = foreign or name in _FOREIGN
        if not foreign:
            if name in _IGNORED:
                continue
            if name in _TABLE_ONLY and not (where.get("table") or where.get("template")):
                continue
            for kind in _CLOSES.get(_KIND.get(name, ""), ()):
                if kind == "p" and scopes and open_names[scopes[-1]] == "p":
                    bottom = scopes[-1]
                    while len(open_names) > bottom:
                        close_top()
                elif open_names and _KIND.get(open_names[-1]) == kind:
                    close_top()
        elif name in _ITEMS and open_names and _ITEMS.get(open_names[-1]) == _ITEMS[name]:
            close_top()
        if name not in _NO_REOPEN:
            # The held formatting elements that another end tag closed are opened again here,
            # where the tree builder opens them again, and stay open as they do there. At most
            # `holding` steps, which `reopened` counted: linear in its cap.
            for formatting in reopening:
                for entry in held[formatting]:
                    if not entry[1]:
                        entry[1] = True
                        open_element(formatting, entry)
            if len(open_names) > SCAN_DEPTH_MAX:
                return text[:start], True
        if not foreign and name in _VOID:
            continue
        entry = None
        # An `a` ends the `a` before it: one is held at a time.
        if name in _FORMATTING and not (name == "a" and held.get("a")):
            plain = _PLAIN_REST.match(text, named.end(), named.end() + PLAIN_TAG_MAX)
            written = text[start : plain.end()] if plain else None
            if written is not None and alike.get(written) == ALIKE_MAX:
                # The first of the three leaves. At most `holding` steps, and this tag has
                # just added as much to `reopened`: the scan stays linear in its cap.
                entries = held[name]
                del entries[next(i for i, entry in enumerate(entries) if entry[0] == written)]
            else:
                holding += 1
                if written is not None:
                    alike[written] = alike.get(written, 0) + 1
            entry = [written, True]
            held.setdefault(name, []).append(entry)
            reopening.add(name)
        open_element(name, entry)
        if len(open_names) > SCAN_DEPTH_MAX:
            return text[:start], True
    return text, False


def _clean(markup: str, attributes: dict, link_rel: str | None) -> str | None:
    """`markup` through the sanitiser, or None when it gave up. A panic of the Rust side is a
    BaseException: it is the document's failure too, never a traceback."""
    try:
        return nh3.clean(
            markup,
            tags=set(TAGS),
            clean_content_tags=set(DROPPED_WITH_CONTENT),
            attributes=attributes,
            url_schemes=set(SCHEMES),
            link_rel=link_rel,
            strip_comments=True,
        )
    except MemoryError:
        raise
    except BaseException:
        return None


class _TooSlowError(Exception):
    """The budget was spent while the walker read."""


class _Walker(HTMLParser):
    """Reads the output of pass 1 and writes two things: the same markup with `src` moved to
    `data-src` and every value checked, and the text version. Raises _TooSlowError when `budget` is
    spent: it is asked every WALK_BUDGET_EVERY tags and pieces of text."""

    def __init__(self, budget) -> None:
        super().__init__(convert_charrefs=True)
        self.budget = budget
        self.steps = 0
        self.markup: list[str] = []
        self.text: list[str] = []
        self.open: list[tuple[str, bool]] = []  # (tag, whether its start tag was written)
        # How many of each name are open: an end tag is answered without reading `open`, which
        # is as deep as the scan allows.
        self.open_names: dict[str, int] = {}
        self.links: list[
            tuple[str, int] | None
        ] = []  # per open `a`: (address, where its label starts)
        self.preformatted = 0
        self.remote_images = 0
        self.truncated = False

    def _step(self) -> None:
        self.steps += 1
        if self.steps % WALK_BUDGET_EVERY == 0 and self.budget.spent():
            raise _TooSlowError

    # --- markup ---

    def _attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> tuple[str, str | None]:
        """(the attributes to write, the address of a link)."""
        given = {name: _CONTROL.sub("", value or "") for name, value in reversed(attrs)}
        kept: list[tuple[str, str]] = []
        address = None
        if tag == "a":
            href = given.get("href", "").strip()
            if len(href) <= ADDRESS_MAX and _LINK.fullmatch(href):
                kept.append(("href", href))
                address = href
        elif tag == "img":
            source = given.get("src", "").strip()
            if len(source) <= ADDRESS_MAX and _REMOTE.fullmatch(source):
                kept.append(("data-src", source))
                self.remote_images += 1
            for name in ("width", "height"):
                if _NUMBER.fullmatch(given.get(name, "")):
                    kept.append((name, given[name]))
            if "alt" in given:
                kept.append(("alt", given["alt"][:ALT_MAX]))
        return "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in kept
        ), address

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._step()
        if tag not in TAGS:
            return
        written, address = self._attributes(tag, attrs)
        if tag in VOID:
            self.markup.append(f"<{tag}{written}>")
            if tag == "img":
                self._say(dict(attrs).get("alt") or "")
            else:
                self.text.append("\n")
            return
        fits = len(self.open) < DEPTH_MAX
        self.truncated = self.truncated or not fits
        self.open.append((tag, fits))
        self.open_names[tag] = self.open_names.get(tag, 0) + 1
        if fits:
            self.markup.append(f"<{tag}{written}>")
        if tag == "a":
            self.links.append((address, len(self.text)) if address else None)
        elif tag in _BLOCKS:
            self.text.append("\n")
            self.preformatted += tag == "pre"

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self._step()
        if tag in VOID or not self.open_names.get(tag):
            return
        while self.open:
            name, fits = self.open.pop()
            self.open_names[name] -= 1
            if fits:
                self.markup.append(f"</{name}>")
            self._closed(name)
            if name == tag:
                break

    def _closed(self, tag: str) -> None:
        if tag == "a":
            link = self.links.pop() if self.links else None
            if link is not None:
                self._say_address(*link)
        elif tag in _CELLS:
            self.text.append("\t")
        elif tag in _BLOCKS:
            self.text.append("\n")
            self.preformatted -= tag == "pre"

    # --- text ---

    def _say(self, words: str) -> None:
        if words:
            self.text.append(words if self.preformatted else _SPACES.sub(" ", words))

    def _say_address(self, address: str, start: int) -> None:
        """After the label of a link, its address, unless the label already is that address."""
        label = "".join(self.text[start:]).strip()
        shown = address[7:] if address[:7].lower() == "mailto:" else address
        if not label:
            self.text.append(shown)
        elif label.rstrip("/").lower() not in (shown.rstrip("/").lower(), address.lower()):
            self.text.append(f" ({shown})")

    def handle_data(self, data: str) -> None:
        self._step()
        # A character reference can still name a control character: none is kept.
        data = _CONTROL.sub("", data)
        self.markup.append(html.escape(data, quote=False))
        self._say(data)

    def finish(self) -> None:
        self.close()
        while self.open:
            self.handle_endtag(self.open[-1][0])


def _text(pieces: list[str]) -> str:
    lines = [line.strip(" \t") for line in _CONTROL.sub("", "".join(pieces)).split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _encoded(answer: dict) -> int:
    """The size of the answer as main.py writes it."""
    return len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def handle(data: bytes, options: dict, budget) -> dict:
    slow = {"ok": False, "failure": "file", "code": "too_slow"}
    unreadable = {"ok": False, "failure": "file", "code": "unreadable"}
    markup = _CONTROL.sub("", data[: INPUT_MAX_CHARS * 4].decode("utf-8", "replace"))
    truncated = len(data) > INPUT_MAX_CHARS * 4 or len(markup) > INPUT_MAX_CHARS
    markup, cut = cut_at_depth(markup[:INPUT_MAX_CHARS])
    truncated = truncated or cut

    first = _clean(markup, _PASS_1, None)
    if first is None:
        return unreadable
    # What the scan did not foresee: a page the tree builder wrote many times over is not read.
    if len(first) > GROWTH_MAX * max(len(markup), GROWTH_FLOOR) or budget.spent():
        return slow
    walker = _Walker(budget)
    try:
        walker.feed(first)
        walker.finish()
    except _TooSlowError:
        return slow
    truncated = truncated or walker.truncated
    if budget.spent():
        return slow

    # Pass 3, and the cap of the stored HTML: what does not fit is cut from the end and
    # cleaned again, so that what is stored is always the sanitiser's own output.
    source = "".join(walker.markup)
    stored = ""
    for _ in range(8):
        cleaned = _clean(source, _PASS_3, LINK_REL)
        if cleaned is None:
            return unreadable
        size = len(cleaned.encode("utf-8"))
        if size <= HTML_MAX_BYTES:
            stored = cleaned
            break
        truncated = True
        source = source[: int(len(source) * HTML_MAX_BYTES / size * 0.9)]
        if budget.spent():
            return slow
    else:
        truncated = True

    text = _text(walker.text)
    if len(text) > TEXT_MAX_CHARS:
        text, truncated = text[:TEXT_MAX_CHARS], True
    answer = {
        "ok": True,
        "kind": "mail_html",
        "html": stored,
        "text": text,
        "remote_images": walker.remote_images,
        "truncated": truncated,
    }
    while _encoded(answer) > ANSWER_MAX and answer["text"]:
        answer["text"] = answer["text"][: len(answer["text"]) // 2]
        answer["truncated"] = True
    if _encoded(answer) > ANSWER_MAX:  # cannot happen under the caps above; closed all the same
        answer.update(html="", truncated=True)
    return answer
