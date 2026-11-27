"""A25: the codes the person gave in the app on one lease (routes.give_code), kept in browserd's
memory until the lease ends (State.end), and the finder of them in what the lease would answer
(actions.run, SPEC-browser: the credential rule's one exception). Nothing is masked: an answer
that holds a code is never given, and the lease's tools close instead (actions.close_codes), since
a mask tells a steered model which value of a page is the code.

A code is found in its obvious forms: any case, full width, percent encoded, with up to eight
spaces, hyphens, dashes, dots, commas, slashes, underscores, `+` or invisible marks (U+00AD,
U+200B to U+200D, U+2060, U+FEFF) between its characters (a page's own format, a GET form's
address); and written one character per element in order (`singles`: a snapshot's lines whose
text is one letter or digit, with only the snapshot's own structure between them, as the slots
of a box of one character each show it)."""

import re
import unicodedata
from typing import Any

CODES_MAX = 8  # codes kept on one lease: routes.give_code refuses one more
SEP = (
    r"[\s\-.+,/_"
    "\u00a0\u2007\u202f\u2010-\u2015\u2212\ufe58\ufe63\uff0d\uff0e\uff0c\uff0f"
    "\u00ad\u200b-\u200d\u2060\ufeff]"
)
PERCENT = r"%(?:20|2b|2c|2d|2e|2f|5f|c2%a0|c2%ad|e2%80%8[b-d]|e2%80%9[0-5]|e2%81%a0|ef%bb%bf)"
GAP = rf"(?:{SEP}|{PERCENT}){{0,8}}"
# A line of Playwright's AI snapshot: `- role "name" [attr] [ref=e5]: text`; the text after the
# colon is the element's own (a slot's character, a box's value), a name never counts.
LINE = re.compile(
    r'\s*- (?:[a-z/]+)(?: "(?:[^"\\]|\\.)*")?(?: \[[^\]\n]*\])*(?::(?: (?P<text>.*))?)?'
)
ONE = re.compile(rf"(?:{SEP}|[\"'])*(?P<char>[^\W_])(?:{SEP}|[\"'])*")
SEPARATOR_ONLY = re.compile(rf"(?:{SEP}|[\"'])*")
BREAK = "\n\x00\n"  # between runs of singles: no gap of the pattern crosses it


def pattern(code: str) -> re.Pattern[str]:
    """`code` (ASCII letters and digits) in its obvious forms."""
    units = (f"(?:{re.escape(c)}|{chr(ord(c) + 0xFEE0)}|%{ord(c):02x})" for c in code)
    return re.compile(GAP.join(units), re.IGNORECASE)


def singles(text: str) -> str:
    """The runs of `text`'s snapshot lines whose own text is one letter or digit, each run's
    characters joined in order; a line with no text of its own, or only a separator, is the
    snapshot's structure and leaves the run whole; any other line ends it."""
    runs: list[str] = []
    run: list[str] = []
    for line in text.splitlines():
        found = LINE.fullmatch(line)
        own = found["text"] if found else None
        if found and (own is None or SEPARATOR_ONLY.fullmatch(own)):
            continue
        one = ONE.fullmatch(unicodedata.normalize("NFKC", own)) if own is not None else None
        if one:
            run.append(one["char"])
            continue
        if len(run) > 1:
            runs.append("".join(run))
        run = []
    if len(run) > 1:
        runs.append("".join(run))
    return BREAK.join(runs)


class Codes:
    """The codes given on one lease. Its repr names how many, never one."""

    def __init__(self) -> None:
        self._kept: list[tuple[re.Pattern[str], str]] = []

    def __bool__(self) -> bool:
        return bool(self._kept)

    def __repr__(self) -> str:
        return f"Codes({len(self._kept)})"

    def add(self, code: str) -> bool:
        if len(self._kept) >= CODES_MAX:
            return False
        if all(code.casefold() != kept for _, kept in self._kept):
            self._kept.append((pattern(code), code.casefold()))
        return True

    def found(self, value: Any) -> bool:
        """Any string of `value` (dict keys and values, list items too) holds a code given on
        the lease, in its obvious forms or one character per snapshot line."""
        for text in _strings(value):
            one_each = singles(text) if "\n" in text else ""
            for code, _ in self._kept:
                if code.search(text) or (one_each and code.search(one_each)):
                    return True
        return False


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []
