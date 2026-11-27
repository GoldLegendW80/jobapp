"""A25: the codes the person gave in the app on one lease (routes.give_code), kept in browserd's
memory until the lease ends (State.end) and masked in every answer of that lease (actions.run,
SPEC-browser: the credential rule's one exception). A code is matched in its obvious forms: any
case, full width, percent encoded, and with up to three spaces, hyphens, dashes, dots or `+`
between its characters (a page's own format, a GET form's address).

Parts of a code are masked only in the box browserd typed it into (actions.code_shown), never in
the rest of a page: masking them there would tell a steered model, from a page that lists every
group of three digits, which groups make the code. For the same reason a code found on a page of
another host, or in the agent's own call, closes the lease's tools (actions.run) instead of being
masked and answered."""

import re
import unicodedata
from typing import Any

MASK = "[the code the person gave]"
CODES_MAX = 8  # codes kept on one lease: routes.give_code refuses one more
PART_MIN = 3  # a shorter value of the code box is no part the mask hides
GAP = (
    r"(?:[\s\u00a0\u2007\u202f\-\u2010-\u2015\u2212\ufe58\ufe63\uff0d.\uff0e+]"
    r"|%(?:20|2d|2e|2b|c2%a0|e2%80%9[0-5])){0,3}"
)
# Playwright's refs in a snapshot (`[ref=e12]`, `[ref=f1e5]`) stay whole: the agent acts by them.
REF = re.compile(r"\[ref=[a-z0-9]{1,24}\]")
SEPARATORS = re.compile(r"[\s\-\u2010-\u2015\u2212.+_]+")


def pattern(code: str) -> re.Pattern[str]:
    """`code` (ASCII letters and digits) in its obvious forms."""
    units = (f"(?:{re.escape(c)}|{chr(ord(c) + 0xFEE0)}|%{ord(c):02x})" for c in code)
    return re.compile(GAP.join(units), re.IGNORECASE)


class Codes:
    """The codes given on one lease, each with the host of the ask it was given for. Its repr
    names how many, never one."""

    def __init__(self) -> None:
        self._kept: list[tuple[re.Pattern[str], str, str]] = []

    def __bool__(self) -> bool:
        return bool(self._kept)

    def __repr__(self) -> str:
        return f"Codes({len(self._kept)})"

    def add(self, code: str, host: str) -> bool:
        if len(self._kept) >= CODES_MAX:
            return False
        if all(code.casefold() != kept for _, kept, _ in self._kept):
            self._kept.append((pattern(code), code.casefold(), host))
        return True

    def found(self, value: Any) -> set[str]:
        """The hosts of the codes any string of `value` holds (dict values, list items too)."""
        return {host for text in _strings(value) for p, _, host in self._kept if p.search(text)}

    def part(self, value: Any) -> bool:
        """`value`, its separators out and folded, is the code or a part of it of PART_MIN or
        more characters (the code box's own value, cut or reformatted by its page)."""
        if not isinstance(value, str):
            return False
        folded = SEPARATORS.sub("", unicodedata.normalize("NFKC", value)).casefold()
        return len(folded) >= PART_MIN and any(folded in code for _, code, _ in self._kept)

    def hide(self, value: Any, box: str = "") -> Any:
        """`value` with every code masked in each of its strings, and `box` (the code box's own
        value when it is a part, as the page writes it) masked as written."""
        if isinstance(value, str):
            return self._hide_text(value, box)
        if isinstance(value, dict):
            return {k: self.hide(v, box) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.hide(v, box) for v in value]
        return value

    def _hide_text(self, text: str, box: str) -> str:
        refs, out = REF.findall(text), []
        for i, piece in enumerate(REF.split(text)):
            for found, _, _ in self._kept:
                piece = found.sub(MASK, piece)
            out.append(piece.replace(box, MASK) if box else piece)
            if i < len(refs):
                out.append(refs[i])
        return "".join(out)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []
