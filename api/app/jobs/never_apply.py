"""The person's exclusion rules, shared by sweeps and apply.

Matching uses apply's reading of visible text: accents, compatibility forms, hidden characters,
lookalike letters and terms at the start or end of a glued word. Rule patterns compile once.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.profile.matching import PLURAL, escape

_JOINERS = re.compile(r"[\s\-\u2010-\u2015_/.]+")
# What a reader does not see: formatting, controls, surrogates, private use, unassigned points,
# accents, variation selectors and enclosing marks. Whitespace still separates words.
_UNSEEN = frozenset({"Cf", "Cc", "Cs", "Co", "Cn", "Mn", "Me"})
_HANGUL_FILLERS = frozenset("\u115f\u1160")
_LOOKALIKES = str.maketrans(
    "авекмнорстхуѕіјһԁԛԝүӏαβεζηικμνορτυχϳᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘʀꜱᴛᴜᴠᴡʏᴢıȷɑɡɩ",
    "abekmhopctxysijhdqwyiabezhikmnoptyxjabcdefghijklmnoprstuvwyzijagi",
)


def _seen(c: str) -> bool:
    if c.isspace():
        return True
    if unicodedata.combining(c) or c in _HANGUL_FILLERS:
        return False
    return unicodedata.category(c) not in _UNSEEN


def fold(text: str) -> str:
    """Read case and Unicode variants alike, dropping hidden characters and joining separators.

    NFKC and NFKD leave ordinary letters; the case fold comes before the lookalike mapping so a
    capital and its small letter read alike. Tabs and line breaks remain word separators.
    """
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text))
    plain = "".join(c for c in decomposed if _seen(c)).casefold().translate(_LOOKALIKES)
    return _JOINERS.sub(" ", plain)


def _term_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    """Whole words and terms at the start or end of a glued word, using apply's existing rule."""
    folded = [escape(fold(term)) for term in terms if fold(term).strip()]
    if not folded:
        return None
    alternation = "|".join(folded)
    starts = rf"(?<!\w)(?:{alternation})"
    ends = rf"(?:{alternation}){PLURAL}(?!\w)"
    return re.compile(f"{starts}|{ends}")


@dataclass(frozen=True)
class NeverApplyRule:
    text: str
    terms: tuple[str, ...]
    pattern: re.Pattern[str] | None

    @classmethod
    def parse(cls, text: str) -> "NeverApplyRule":
        _, colon, body = text.partition(":")
        body = re.sub(r"\([^)]*\)", " ", body) if colon else ""
        terms = tuple(
            term
            for term in (part.strip(" .!\t") for part in re.split(r"[,;]", body))
            if term and len(term) <= 200
        )
        return cls(text=text, terms=terms, pattern=_term_pattern(terms))

    def matches(self, company: str, title: str) -> bool:
        return matching_rule([self], company, title) is not None


def matching_rule(rules: Iterable[NeverApplyRule], *texts: str) -> NeverApplyRule | None:
    """The first rule whose terms match any of the folded texts, or None."""
    folded = " \n ".join(fold(text) for text in texts if text)
    for rule in rules:
        if rule.pattern is not None and rule.pattern.search(folded) is not None:
            return rule
    return None
