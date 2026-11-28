"""A posting's text as a reader sees it (jobs J39, issue #877; SPEC-jobs.md, "Text as a reader
sees it"): `visible(text)` drops what a person reading the page cannot see and a model reading
the text still reads. The idea is ai-job-search's code gate in front of every model that reads a
posting (`tools/posting_guard.py`, MIT, NOTICE); its scan of visible sentences is left out (the
measure of 2026-11-28 found nothing for it in 334 postings, and one false alarm).

What goes, by Unicode category (`unicodedata.category`, the Unicode version of the Python that
runs the app, `unicodedata.unidata_version`), never by a list of characters:
- Cc, the control characters (NUL included), but the tab, the line feed and the carriage return;
- Cf, the format characters: zero-width spaces and joiners, direction marks and overrides, the
  tag block, the byte order mark, the soft hyphen;
- Cs, a lone surrogate; Co, a private use character (an icon font's bullet); Cn, an unassigned
  code point (the noncharacters too, and a character newer than that Unicode version);
- the variation selectors (Unicode's Variation_Selector property, PropList.txt): the
  supplementary ones (U+E0100 to U+E01EF) always; one of the others (U+FE00 to U+FE0F, the
  Mongolian U+180B to U+180D and U+180F) stays only alone directly after a letter, a number, a
  punctuation mark or a symbol (an emoji's presentation), so a run of them, which can spell
  bytes, goes whole.

Everything else stays as it is: letters, marks (an accent written as a combining mark), numbers,
punctuation, symbols, emoji and spaces; no normalisation, no trimming. The function is pure,
idempotent and linear in the text's length: two substitutions of one character class each, the
class built once from the category table.
"""

import functools
import re
import sys
import unicodedata

DROPPED = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
KEPT_CONTROLS = frozenset("\t\n\r")
SUPPLEMENTARY_SELECTORS = (0xE0100, 0xE01EF)
SELECTORS = re.compile(r"[\u180b-\u180d\u180f\ufe00-\ufe0f]+")
# The first letter of the categories a variation selector may follow alone.
BASES = frozenset("LNPS")
ASTRAL = 0x10000


def _ranges(points: list[int]) -> str:
    """The points, ascending, as the ranges of a character class."""
    parts: list[str] = []
    start = previous = points[0]
    for point in points[1:]:
        if point != previous + 1:
            parts.append(f"\\U{start:08x}-\\U{previous:08x}")
            start = point
        previous = point
    parts.append(f"\\U{start:08x}-\\U{previous:08x}")
    return "".join(parts)


def _drops(char: str) -> bool:
    low, high = SUPPLEMENTARY_SELECTORS
    if low <= ord(char) <= high:
        return True
    return unicodedata.category(char) in DROPPED and char not in KEPT_CONTROLS


# A class of a few hundred astral ranges is checked range by range for every character: the
# basic plane is a class (a bitmap), each astral character is asked its category alone.
@functools.cache
def _dropped() -> re.Pattern[str]:
    points = [point for point in range(ASTRAL) if _drops(chr(point))]
    return re.compile(f"[{_ranges(points)}]+|[\\U{ASTRAL:08x}-\\U{sys.maxunicode:08x}]")


def _kept(match: re.Match[str]) -> str:
    found = match.group()
    return found if ord(found[0]) >= ASTRAL and not _drops(found) else ""


def _selector(match: re.Match[str]) -> str:
    start, string = match.start(), match.string
    alone = match.end() - start == 1
    if alone and start and unicodedata.category(string[start - 1])[0] in BASES:
        return match.group()
    return ""


def visible(text: str) -> str:
    """`text` without what a reader cannot see (module docstring)."""
    if not text:
        return text
    return SELECTORS.sub(_selector, _dropped().sub(_kept, text))
