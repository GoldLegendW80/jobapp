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
the child is what holds if it is fooled); `HTML_MAX_BYTES` and `TEXT_MAX_CHARS` of output; and
the whole answer under `ANSWER_MAX` bytes. Each cut sets `truncated`.

A failure is `{"ok": false, "failure": "file", "code": ...}`: `unreadable` (the sanitiser gave
up), `too_slow` (the budget was spent). The worker then stores the mail with its plain text
part and no HTML: a mail is never lost because its HTML is hostile.

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
ADDRESS_MAX = 2000
ALT_MAX = 500

_PASS_1 = {"a": {"href"}, "img": {"alt", "width", "height", "src"}}
_PASS_3 = {"a": {"href"}, "img": {"alt", "width", "height", "data-src"}}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_OPENING = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)")
_LINK = re.compile(r"(?:https?://|mailto:)[^\s<>\"']+", re.IGNORECASE)
_REMOTE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_NUMBER = re.compile(r"[0-9]{1,4}")
_SPACES = re.compile(r"[ \t\r\n\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")
# Tags the HTML parser never nests in themselves (it closes the one before), and the void ones.
_NOT_NESTING = frozenset(
    [
        "p",
        "li",
        "td",
        "th",
        "tr",
        "dt",
        "dd",
        "option",
        "thead",
        "tbody",
        "tfoot",
        "br",
        "hr",
        "img",
        "meta",
        "link",
        "input",
        "col",
        "area",
        "base",
        "wbr",
        "source",
        "param",
        "embed",
        "track",
    ]
)
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


def cut_at_depth(text: str) -> tuple[str, bool]:
    """`text` up to the tag that would open more than SCAN_DEPTH_MAX elements at once, and
    whether it was cut. A linear estimate: start tags are counted, an end tag closes the
    nearest open element of its name and all those above it, as the tree builder does."""
    open_names: list[str] = []
    counts: dict[str, int] = {}
    for found in _OPENING.finditer(text):
        name = found[2].lower()
        if name in _NOT_NESTING:
            continue
        if not found[1]:
            open_names.append(name)
            counts[name] = counts.get(name, 0) + 1
            if len(open_names) > SCAN_DEPTH_MAX:
                return text[: found.start()], True
        elif counts.get(name):
            while open_names:
                last = open_names.pop()
                counts[last] -= 1
                if last == name:
                    break
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


class _Walker(HTMLParser):
    """Reads the output of pass 1 and writes two things: the same markup with `src` moved to
    `data-src` and every value checked, and the text version."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.markup: list[str] = []
        self.text: list[str] = []
        self.open: list[tuple[str, bool]] = []  # (tag, whether its start tag was written)
        self.links: list[
            tuple[str, int] | None
        ] = []  # per open `a`: (address, where its label starts)
        self.preformatted = 0
        self.remote_images = 0
        self.truncated = False

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
        if tag in VOID or not any(name == tag for name, _ in self.open):
            return
        while self.open:
            name, fits = self.open.pop()
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
    if budget.spent():
        return slow
    walker = _Walker()
    walker.feed(first)
    walker.finish()
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
