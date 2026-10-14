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
_OPENING = re.compile(r"<(/?)([a-zA-Z][^\t\n\f\r />]*)")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
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
_BUTTON_SCOPE = frozenset(
    ["applet", "button", "caption", "html", "marquee", "object", "table", "td", "th", "template"]
)
_CLOSES = {
    "p": ("p",),
    "li": ("p", "li"),
    "dd": ("p", "dd"),
    "option": ("option",),
    "cell": ("p", "cell"),
    "row": ("p", "cell", "row"),
    "section": ("p", "cell", "row", "section"),
}
# A cell, a row or a section outside a table (or a template) is ignored by the tree builder in
# body: such a tag is not counted while the estimate holds no `table` and no `template` open.
_TABLE_ONLY = frozenset({"td", "th", "tr", "tbody", "thead", "tfoot"})
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


def cut_at_depth(text: str) -> tuple[str, bool]:
    """`text` up to the tag that would open more than SCAN_DEPTH_MAX elements at once, open
    again more than SCAN_REOPENED_MAX formatting elements, or be the tag past SCAN_FOSTERED_MAX
    that stands directly in a table, and whether it was cut. A linear estimate: start tags are
    counted but for the voids and the narrow rules of `_CLOSES` and `_TABLE_ONLY`, none of
    which holds once `<svg>` or `<math>` was seen; an end tag closes the nearest open element
    of its name and all those above it. A formatting element is held from its start tag to an
    end tag of its name, and every tag in between may open again all those that are held. Of
    start tags written alike three are held, as the tree builder holds three elements that are
    alike: a page that never closes its `<b>` is not one that grows."""
    open_names: list[str] = []
    counts: dict[str, int] = {}
    contexts: list[int] = []  # where in open_names a table, a cell, a caption, a template stand
    scopes: list[int] = []  # where in open_names a `p` or one of _BUTTON_SCOPE stands
    held: dict[str, list[str | None]] = {}  # per name: the start tags held, as written if plain
    alike: dict[str, int] = {}
    holding = reopened = fostered = 0
    foreign = False

    def close_top() -> None:
        counts[open_names.pop()] -= 1
        if contexts and contexts[-1] == len(open_names):
            contexts.pop()
        if scopes and scopes[-1] == len(open_names):
            scopes.pop()

    for found in _OPENING.finditer(text):
        name = found[2].translate(_ASCII_LOWER)
        if found[1] and held.get(name):
            ended = held[name].pop()
            if ended is not None:
                alike[ended] -= 1
            holding -= 1
        reopened += holding
        if reopened > SCAN_REOPENED_MAX:
            return text[: found.start()], True
        if contexts and open_names[contexts[-1]] == "table" and name not in _TABLE_PARTS:
            fostered += 1
            if fostered > SCAN_FOSTERED_MAX:
                return text[: found.start()], True
        if found[1]:
            if counts.get(name):
                while open_names:
                    last = open_names[-1]
                    close_top()
                    if last == name:
                        break
            continue
        foreign = foreign or name in _FOREIGN
        if not foreign:
            if name in _VOID:
                continue
            if name in _TABLE_ONLY and not (counts.get("table") or counts.get("template")):
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
        # An `a` ends the `a` before it: one is held at a time.
        if name in _FORMATTING and not (name == "a" and held.get("a")):
            rest = _PLAIN_REST.match(text, found.end(), found.end() + PLAIN_TAG_MAX)
            written = text[found.start() : rest.end()] if rest else None
            if written is not None and alike.get(written) == ALIKE_MAX:
                # The first of the three leaves. At most `holding` steps, and this tag has
                # just added as much to `reopened`: the scan stays linear in its cap.
                held[name].remove(written)
            else:
                holding += 1
                if written is not None:
                    alike[written] = alike.get(written, 0) + 1
            held.setdefault(name, []).append(written)
        if name in _TABLE_CONTEXT:
            contexts.append(len(open_names))
        if name == "p" or name in _BUTTON_SCOPE:
            scopes.append(len(open_names))
        open_names.append(name)
        counts[name] = counts.get(name, 0) + 1
        if len(open_names) > SCAN_DEPTH_MAX:
            return text[: found.start()], True
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
