"""Fact check rules for a draft (mail M9): pure functions, no store, no decision.

`check(draft_body, sources) -> list[Issue]` finds what a draft claims that no source states: a
number, a year, a duration, an amount, a percentage, a date or a time that is in no source; a
capitalised organisation after "at", "chez", "with", "for" that is no employer, school or
participant; a job title, a postal address, a phone number, an e-mail address or a link that the
profile does not hold. `factcheck.py` (M12) runs it, keeps the result and enforces it; this module
never stores, decides or blocks anything.

Pure and deterministic: no I/O, no clock, no randomness, no model, no global state (every cache
is local to one call). The same body and the same sources give the same issues in the same
order, whatever the order of the entries of a source.

This is a safety control: a draft sent to an employer must not carry a claim that is in no source.
So when in doubt it FLAGS: a false issue costs the user one look, a missed one sends a false claim.
Each deliberate choice of that kind carries a comment that starts with "Deliberate:".

What a source may legitimise (the trust model; the thread is a stranger's mail):
- the profile sections, the answers section and the user's instructions are the user's own words
  ("own" below): they legitimise everything;
- the thread legitimises numbers, years, amounts, percentages, dates and times (a draft that
  quotes "salary 90k" from the mail is not wrong), but NOT a duration in years or months (it is a
  claim about the user's experience: the mail's "we want 4 years" must not make "I have 4 years"
  pass), a phone number, an e-mail address, a link, an address, a title or an organisation. An
  organisation is legitimised by the `participants` the caller computed from the mail's headers,
  never by mining the body of the mail.

Offsets: `Issue.start` and `Issue.end` are Python string indices (Unicode code points) into the
ORIGINAL body, `end` excluded, so `body[start:end]` is the flagged text. Everything is compared on
a folded copy (accents, case, non breaking spaces, zero width characters, digit look-alikes such as
full width digits) and mapped back; a combining mark stays inside the span of its base letter.

Linear time: the text is folded once, cut into tokens once (a numeric run is scanned by a loop
that never goes back, the words by a class under a single quantifier), and every rule walks the
token list with a bounded look-around (at most six tokens). No regular expression has a nested or
overlapping quantifier: they are single classes under one `+`, or bounded (`{1,3}`, `{1,9}`), and
the e-mail address, which would be quadratic as a pattern, is found by hand from its "@". A very
long run of digits is never turned into an `int` (Python refuses beyond 4300 digits): values are
compared as canonical strings.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, NamedTuple

IssueKind = Literal["number", "date", "amount", "employer", "school", "title", "address", "link"]

ISSUE_KINDS: tuple[IssueKind, ...] = (
    "number",
    "date",
    "amount",
    "employer",
    "school",
    "title",
    "address",
    "link",
)


@dataclass(frozen=True, slots=True)
class Issue:
    """One thing the draft says that no source says. `code` names the rule that fired."""

    kind: IssueKind
    start: int
    end: int
    code: str


@dataclass(frozen=True, slots=True)
class Sources:
    """What the draft may be checked against, as plain data (the caller flattens its models).

    `profile`, `answers`, `instructions`: the user's own text, one entry per section or line.
    `thread`: the text of the mail thread, a stranger's words, data and never instructions.
    `employers`, `schools`: names from the profile. `titles`: the profile's job titles and the
    title of the posting. `participants`: names and organisations of the thread's participants
    (from its headers, and the company of the job). `recipients`: the draft's recipient
    addresses. `known_terms`: capitalised names that are no organisation but that the profile
    uses (technologies, products), so that "worked with Python" is not an issue.
    """

    profile: tuple[str, ...] = ()
    answers: tuple[str, ...] = ()
    thread: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    employers: tuple[str, ...] = ()
    schools: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    participants: tuple[str, ...] = ()
    recipients: tuple[str, ...] = ()
    known_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A bare string would be read one character at a time and legitimise any digit.
        for field in dataclasses.fields(self):
            if not isinstance(getattr(self, field.name), tuple):
                raise TypeError(f"Sources.{field.name} must be a tuple of strings")


# claim type -> (issue kind, issue code)
_RULES: dict[str, tuple[IssueKind, str]] = {
    "num": ("number", "number_not_in_sources"),
    "pct": ("number", "percent_not_in_sources"),
    "dur": ("number", "duration_not_in_profile"),
    "time": ("number", "time_not_in_sources"),
    "amt": ("amount", "amount_not_in_sources"),
    "year": ("date", "year_not_in_sources"),
    "date": ("date", "date_not_in_sources"),
    "phone": ("address", "phone_not_in_profile"),
    "email": ("address", "email_not_in_profile"),
    "street": ("address", "street_not_in_profile"),
    "link": ("link", "link_not_in_profile"),
    "title": ("title", "title_not_in_profile"),
    "employer": ("employer", "employer_not_in_profile"),
    "school": ("school", "school_not_in_profile"),
}
CODES = frozenset(code for _, code in _RULES.values())


# ---------------------------------------------------------------------------------------------
# Folding: the same text for the draft and the sources
# ---------------------------------------------------------------------------------------------

_DASHES = frozenset("\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d")
_APOSTROPHES = frozenset("\u2018\u2019\u201b\u02bc\u2032\u00b4`")
_NEWLINES = frozenset("\n\r\u2028\u2029\x0b\x0c\x85")
_DIGITS = "0123456789"


def _set(words: str) -> frozenset[str]:
    return frozenset(words.split())


def _table(spec: str) -> dict[str, int]:
    return {word: int(value) for word, value in (item.split("=") for item in spec.split())}


def _simple(ch: str) -> str:
    """Map one already decomposed character to its folded form (maybe empty)."""
    category = unicodedata.category(ch)
    if ch in _NEWLINES:
        return "\n"
    if category[0] == "Z" or ch == "\t":
        return " "
    if category == "Nd":
        return str(unicodedata.decimal(ch))
    if category[0] == "C" or category == "Mn":
        return ""  # controls, zero width and format characters, combining marks
    if ch in _DASHES:
        return "-"
    if ch in _APOSTROPHES:
        return "'"
    if ch == "\u2044":
        return "/"  # the fraction slash of "½" once decomposed
    return ch


def _fold_char(char: str) -> str:
    folded = _simple(char)
    if folded == char and char.isascii() and char.islower():
        return char
    decomposed = unicodedata.normalize("NFKD", char)
    # Casefold after the decomposition, then once more: "İ" and "ß" change shape twice.
    lowered = unicodedata.normalize("NFKD", decomposed.casefold())
    return "".join(_simple(ch) for ch in lowered)


class _Norm:
    """A folded text and, for each folded character, the span of the original it came from."""

    __slots__ = ("ends", "orig", "starts", "text")

    def __init__(self, orig: str) -> None:
        cache: dict[str, str] = {}
        parts: list[str] = []
        starts: list[int] = []
        ends: list[int] = []
        for index, char in enumerate(orig):
            folded = cache.get(char)
            if folded is None:
                folded = cache[char] = _fold_char(char)
            if folded:
                for out in folded:
                    parts.append(out)
                    starts.append(index)
                    ends.append(index + 1)
            elif ends and unicodedata.category(char) == "Mn":
                ends[-1] = index + 1  # a combining mark belongs to its base letter's span
        self.orig = orig
        self.text = "".join(parts)
        self.starts = starts
        self.ends = ends

    def span(self, start: int, end: int) -> tuple[int, int]:
        return self.starts[start], self.ends[end - 1]


def _words(text: str) -> list[str]:
    return _WORDS_RE.findall(text)


_WORDS_RE = re.compile(r"[^\W_]+")
_NEXT_RE = re.compile(r"[0-9]|[^\W\d_]+")
_TRUNK_RE = re.compile(r" ?\(\d{1,3}\) ?(?=\d)")


# ---------------------------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------------------------


class _Tok(NamedTuple):
    kind: str  # "n": a run of digits and their joiners, "w": a word
    text: str
    start: int
    end: int


def _scan_run(n: str, i: int) -> int:
    """The end of the numeric run that starts at `i`: digits joined by one of `.,:/-`, by one
    space when the next group is short, or by a "(0)" trunk. One forward loop, never backs up
    (a group is peeked once and read once)."""
    size = len(n)
    j = i
    last = 0
    while True:
        k = j
        while k < size and n[k] in _DIGITS:
            k += 1
        last = k - j
        j = k
        if j >= size:
            return j
        ch = n[j]
        if ch in ".,:/-" and j + 1 < size and n[j + 1] in _DIGITS:
            j += 1
            continue
        if ch == " " and j + 1 < size and n[j + 1] in _DIGITS:
            m = j + 1
            while m < size and n[m] in _DIGITS:
                m += 1
            following = m - (j + 1)
            # Deliberate: a space joins "06 12 34 56 78", "+33 7 89", "3 500", "415 555 2671" but
            # not "2022 2023" (two years), so the group after it is at most 3 digits, or 4 after
            # a short group.
            if following <= 3 or (
                following == 4 and not (last == 4 and n[j + 1 : j + 3] in ("19", "20"))
            ):
                j += 1
                continue
            return j
        if ch in " (":
            trunk = _TRUNK_RE.match(n, j)
            if trunk:
                j = trunk.end()
                continue
        return j


def _tokenize(n: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    pos = 0
    while True:
        found = _NEXT_RE.search(n, pos)
        if found is None:
            return tokens
        if found.group()[0] in _DIGITS:
            end = _scan_run(n, found.start())
            tokens.append(_Tok("n", n[found.start() : end], found.start(), end))
        else:
            end = found.end()
            tokens.append(_Tok("w", found.group(), found.start(), end))
        pos = end


# ---------------------------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------------------------


class _Claim(NamedTuple):
    type: str
    start: int  # offsets into the folded text
    end: int
    readings: tuple[tuple[str | None, ...], ...]  # the claim holds if ONE reading is sourced
    unit: str = ""  # "y" or "m" for a duration
    bound: str = ""  # "ge": at least, "gt": more than


_SPLIT_RE = re.compile(r"[^0-9]+")


def _canon(whole: str, fraction: str = "") -> str:
    integer = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{integer}.{fraction}" if fraction else integer


_MAX_DIGITS = 20  # beyond it a run is one opaque value, never converted


def _phone_key(digits: str) -> str:
    """The same phone number written with or without its country code gives one key (France)."""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("330") and len(digits) == 12:
        return "0" + digits[3:]
    if digits.startswith("33") and len(digits) == 11:
        return "0" + digits[2:]
    return digits


def _shape_number(groups: list[str], seps: list[str]) -> tuple[tuple[str | None, ...], ...] | None:
    """The readings of a small run seen as one number: "3.5", "3,5", "3 500", "1.234,56". A
    separator followed by exactly three digits is both a thousands mark and a decimal mark.
    Deliberate: an ambiguous run keeps both readings and the draft passes if either is sourced,
    because "3,500" is 3500 in English and 3.5 in French."""
    if len(groups) == 1:
        return ((_canon(groups[0]),),)
    thousands = (
        1 <= len(groups[0]) <= 3
        and all(len(g) == 3 for g in groups[1:])
        and len(set(seps)) == 1
        and seps[0] in " ,."
    )
    readings: list[tuple[str | None, ...]] = []
    if thousands:
        readings.append((_canon("".join(groups)),))
    if len(groups) == 2 and seps[0] in ".,":
        readings.append((_canon(groups[0], groups[1]),))
    if (
        not readings
        and len(groups) >= 3
        and seps[-1] in ".,"
        and len(set(seps[:-1])) == 1
        and seps[0] in " ,."
        and seps[0] != seps[-1]
        and 1 <= len(groups[0]) <= 3
        and all(len(g) == 3 for g in groups[1:-1])
    ):
        readings.append((_canon("".join(groups[:-1]), groups[-1]),))
    return tuple(readings) or None


def _shape_date(groups: list[str], seps: list[str]) -> tuple[str, str, str | None] | None:
    """A numeric date, day first ("12/03/2026" is 12 March), month first only when the day-first
    reading is impossible. Deliberate: one reading, the same for the draft and the sources, so
    a swapped day and month is a different date and is flagged."""
    if len(set(seps)) != 1 or seps[0] not in "/-.":
        return None
    if len(groups) == 3 and len(groups[0]) == 4 and len(groups[1]) <= 2 and len(groups[2]) <= 2:
        year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
    elif (
        len(groups) == 3
        and len(groups[0]) <= 2
        and len(groups[1]) <= 2
        and len(groups[2]) in (2, 4)
    ):
        day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
        if month > 12 >= day:
            day, month = month, day
        if len(groups[2]) == 2:
            year += 2000
    elif len(groups) == 2 and seps[0] == "/" and len(groups[0]) <= 2 and len(groups[1]) <= 2:
        day, month = int(groups[0]), int(groups[1])
        if month > 12 >= day:
            day, month = month, day
        return (str(day), str(month), None) if 1 <= day <= 31 and 1 <= month <= 12 else None
    else:
        return None
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    return str(day), str(month), str(year)


def _classify_run(text: str, plus: bool) -> tuple[str, tuple[tuple[str | None, ...], ...]]:
    """(claim type, readings) of one numeric run."""
    groups = _SPLIT_RE.split(text)
    total = sum(map(len, groups))
    if total > _MAX_DIGITS:
        # Deliberate: a run too long to be anything but a mistake or an attack is one opaque
        # value, flagged unless a source holds exactly the same digits.
        return "num", (("".join(groups).lstrip("0") or "0",),)
    seps = _SPLIT_RE.findall(text)
    if len(groups) == 1:
        if (plus and total >= 8) or total >= 10:
            return "phone", ((_phone_key(groups[0]),),)
        return "num", ((_canon(groups[0]),),)
    if (
        seps == [":"] * len(seps)
        and len(groups) in (2, 3)
        and len(groups[0]) <= 2
        and int(groups[0]) <= 24
        and all(len(g) == 2 and int(g) < 60 for g in groups[1:])
    ):
        return "time", ((f"{int(groups[0]):02d}:{groups[1]}",),)
    date = _shape_date(groups, seps)
    if date is not None:
        return "date", (date,)
    if plus and total >= 8:
        return "phone", ((_phone_key("".join(groups)),),)
    number = _shape_number(groups, seps)
    if number is not None:
        return "num", number
    year_range = len(groups) == 2 and all(len(g) == 4 for g in groups)  # "2021-2024"
    if 8 <= total <= 15 and not year_range and all(not sep.strip(" .-()") for sep in seps):
        return "phone", ((_phone_key("".join(groups)),),)
    # Deliberate: anything else ("2022-2023", "3-6", "1.2.3") is a list of numbers, each one of
    # which must be sourced.
    return "num", (tuple(_canon(g) for g in groups),)


# Words ---------------------------------------------------------------------------------------

_UNITS = _table(
    "un=1 une=1 one=1 deux=2 two=2 trois=3 three=3 quatre=4 four=4 cinq=5 five=5 six=6 sept=7"
    " seven=7 huit=8 eight=8 neuf=9 nine=9"
)
_TEENS = _table(
    "dix=10 ten=10 onze=11 eleven=11 douze=12 twelve=12 treize=13 thirteen=13 quatorze=14"
    " fourteen=14 quinze=15 fifteen=15 seize=16 sixteen=16 seventeen=17 eighteen=18 nineteen=19"
)
_TENS = _table(
    "vingt=20 vingts=20 twenty=20 trente=30 thirty=30 quarante=40 forty=40 cinquante=50"
    " fifty=50 soixante=60 sixty=60 seventy=70 eighty=80 ninety=90"
)
_BIG = _table("mille=1000 thousand=1000 million=1000000 millions=1000000")


def _word_number(word: str) -> tuple[str, int] | None:
    if word in _UNITS:
        return "U", _UNITS[word]
    if word in _TEENS:
        return "T", _TEENS[word]
    if word in _TENS:
        return "D", _TENS[word]
    if word in ("cent", "hundred", "cents"):
        return "H", 100
    if word in _BIG:
        return "K", _BIG[word]
    return None


def _joined(n: str, left: _Tok, right: _Tok, allowed: str = " -") -> bool:
    """Is `right` the next token after `left`, one allowed character between them?"""
    return right.start - left.end == 1 and n[left.end] in allowed


def _parse_words(n: str, ts: list[_Tok], i: int) -> tuple[int, int] | None:
    """(value, index of the last token) of the number written in words that starts at token i,
    in English or French ("twenty one", "quatre vingt dix sept", "deux cents", "mille"). The
    sequence ends at the first word that cannot continue it, so "trois trois" is two numbers."""
    total = group = 0
    prev = ""
    prev_word = ""
    last_big = 10**9
    quatre_vingt = False
    last = -1
    k = i
    while k < len(ts):
        tok = ts[k]
        if tok.kind != "w" or (k > i and not _joined(n, ts[k - 1], tok)):
            break
        word = tok.text
        if word in ("et", "and"):
            after = _word_number(ts[k + 1].text) if k + 1 < len(ts) else None
            if (
                not prev
                or after is None
                or not _joined(n, tok, ts[k + 1])
                or not (
                    (word == "et" and prev == "D" and ts[k + 1].text in ("un", "une", "onze"))
                    or (word == "and" and prev in ("H", "K"))
                )
            ):
                break
            prev_word = word
            k += 1
            continue
        entry = _word_number(word)
        if entry is None or (word == "cent" and k > 0 and ts[k - 1].text in ("pour", "per")):
            break
        cls, value = entry
        if cls == "H":
            if prev not in ("", "U") or (word == "cents" and prev != "U"):
                break
            group = (group if prev == "U" else 1) * 100
        elif cls == "K":
            if prev == "K" or value >= last_big:
                break
            total += (group or 1) * value
            group = 0
            last_big = value
        elif cls == "D":
            if prev == "U" and prev_word == "quatre" and word in ("vingt", "vingts"):
                group += 76  # 4 + 76 = 80
                quatre_vingt = True
            elif prev in ("", "H", "K"):
                group += value
            else:
                break
        elif cls == "T":
            if prev in ("", "H", "K") or (
                prev == "D" and (prev_word == "soixante" or quatre_vingt)
            ):
                group += value
            else:
                break
        else:  # units
            if prev in ("", "H", "K", "D") or (prev == "T" and prev_word == "dix"):
                group += value
            else:
                break
        prev, prev_word, last = cls, word, k
        k += 1
    if last < 0:
        return None
    return total + group, last


_YEAR_UNITS = _set("year years yr yrs an ans annee annees")
_MONTH_UNITS = _set("month months mois")
_PCT_WORDS = _set("percent percents pourcent pourcents pourcentage")
_CURRENCY_WORDS = _set("eur euro euros usd dollar dollars gbp chf")
_CURRENCY_PREFIX = _set("eur usd gbp chf")
_CURRENCY_SYMBOLS = "€$£"

_MONTHS = _table(
    "january=1 janvier=1 jan=1 janv=1 february=2 fevrier=2 feb=2 fev=2 fevr=2 march=3 mars=3"
    " mar=3 april=4 avril=4 apr=4 avr=4 may=5 mai=5 june=6 juin=6 jun=6 july=7 juillet=7 jul=7"
    " juil=7 august=8 aout=8 aug=8 september=9 septembre=9 sep=9 sept=9 october=10 octobre=10"
    " oct=10 november=11 novembre=11 nov=11 december=12 decembre=12 dec=12"
)
# Deliberate: a month alone is a claim ("available in March") but only the unambiguous full
# names count alone; "may", "mar", "sept" (also French for seven) need a day or a year beside them.
_MONTHS_ALONE = _set(
    "january janvier february fevrier march mars april avril mai june juin july juillet august"
    " aout september septembre october octobre november novembre december decembre"
)
_ORDINALS = _set("st nd rd th er ere e eme of de du d")


def _gap(n: str, left: _Tok, right: _Tok, limit: int = 2, allowed: str = " ,.'-") -> bool:
    """Only blanks and light punctuation (at most `limit` characters) between two tokens."""
    between = n[left.end : right.start]
    return len(between) <= limit and all(ch in allowed for ch in between)


def _is_year(tok: _Tok) -> bool:
    return tok.kind == "n" and len(tok.text) == 4 and tok.text[:2] in ("19", "20")


def _small_int(tok: _Tok, low: int, high: int) -> int | None:
    if (
        tok.kind == "n"
        and tok.text.isdigit()
        and len(tok.text) <= 2
        and low <= int(tok.text) <= high
    ):
        return int(tok.text)
    return None


def _scale(value: str, factor: int) -> str:
    if factor == 1 or len(value) > 24:
        return value
    try:
        scaled = Decimal(value) * factor
    except InvalidOperation:  # pragma: no cover (values are canonical digits)
        return value
    return _decimal_text(scaled)


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text[:-2] if text.endswith(".0") else text


def _half_words(n: str, ts: list[_Tok], k: int, pos: int) -> int:
    """How many tokens from k spell "and a half" / "et demi(e)", one blank after `pos`."""
    if k + 1 >= len(ts) or ts[k].start != pos + 1 or n[pos] != " ":
        return 0
    first = ts[k].text
    if first == "et" and ts[k + 1].text in ("demi", "demie") and _joined(n, ts[k], ts[k + 1], " "):
        return 2
    if (
        first == "and"
        and k + 2 < len(ts)
        and ts[k + 1].text == "a"
        and ts[k + 2].text == "half"
        and _joined(n, ts[k], ts[k + 1], " ")
        and _joined(n, ts[k + 1], ts[k + 2], " ")
    ):
        return 3
    return 0


def _bound_before(n: str, ts: list[_Tok], first: int) -> str:
    """ "at least 3 years", "more than 3 years": a lower bound the profile can still honour."""
    if first >= 1 and _joined(n, ts[first - 1], ts[first], " "):
        word = ts[first - 1].text
        before = (
            ts[first - 2].text
            if first >= 2 and _joined(n, ts[first - 2], ts[first - 1], " ")
            else ""
        )
        if (word, before) in (("least", "at"), ("moins", "au")):
            return "ge"
        if word == "over" or (word, before) in (("than", "more"), ("de", "plus")):
            return "gt"
    return ""


def _after_blank(n: str, pos: int) -> int:
    return pos + 1 if pos < len(n) and n[pos] == " " else pos


def _quantity(
    n: str,
    ts: list[_Tok],
    first: int,
    last: int,
    readings: tuple[tuple[str | None, ...], ...],
    word: bool,
) -> tuple[_Claim | None, int]:
    """Turn the number at tokens first..last into a claim by what surrounds it: a percentage, an
    amount, a duration in years or months, or a bare number. Returns (claim, next token)."""
    size = len(n)
    start = ts[first].start
    end = ts[last].end
    nxt = last + 1
    factor = 1
    # 90k, 1.2M euros
    if (
        nxt < len(ts)
        and ts[nxt].kind == "w"
        and ts[nxt].start == end
        and ts[nxt].text in ("k", "m")
    ):
        suffix = ts[nxt]
        after = _after_blank(n, suffix.end)
        symbol = after < size and n[after] in _CURRENCY_SYMBOLS
        word_after = (
            nxt + 1 < len(ts) and ts[nxt + 1].start == after and ts[nxt + 1].text in _CURRENCY_WORDS
        )
        if suffix.text == "k" or symbol or word_after:
            factor = 1000 if suffix.text == "k" else 10**6
            end = suffix.end
            nxt += 1
    if factor != 1:
        readings = tuple(tuple(_scale(v, factor) if v else v for v in r) for r in readings)
    bound = _bound_before(n, ts, first)
    pos = end
    # A percentage: "20%", "20 %", "20 percent", "20 pour cent"
    pct_pos = _after_blank(n, pos)
    if pct_pos < size and n[pct_pos] == "%":
        return _Claim("pct", start, pct_pos + 1, readings), nxt
    if nxt < len(ts) and ts[nxt].kind == "w" and ts[nxt].start == pct_pos:
        text = ts[nxt].text
        if text in _PCT_WORDS:
            return _Claim("pct", start, ts[nxt].end, readings), nxt + 1
        if text in ("pour", "per") and nxt + 1 < len(ts) and ts[nxt + 1].text == "cent":
            return _Claim("pct", start, ts[nxt + 1].end, readings), nxt + 2
    # An amount: a currency sign or word after ("90 000 €", "50 euros") or before ("$50")
    if pct_pos < size and n[pct_pos] in _CURRENCY_SYMBOLS:
        return _Claim("amt", start, pct_pos + 1, readings), nxt
    if nxt < len(ts) and ts[nxt].start == pct_pos and ts[nxt].text in _CURRENCY_WORDS:
        return _Claim("amt", start, ts[nxt].end, readings), nxt + 1
    before = start - 1
    if before >= 0 and n[before] == " ":
        before -= 1
    if before >= 0 and n[before] in _CURRENCY_SYMBOLS:
        return _Claim("amt", before, end, readings), nxt
    if (
        first > 0
        and ts[first - 1].text in _CURRENCY_PREFIX
        and ts[first - 1].end in (start, start - 1)
    ):
        return _Claim("amt", ts[first - 1].start, end, readings), nxt
    # A duration: "3 years", "3+ years", "3 and a half years", "3 ans et demi", "3-year"
    if pos < size and n[pos] == "+":
        bound = "ge"
        pos += 1
    probe = nxt
    half = False
    skipped = _half_words(n, ts, probe, pos)
    if skipped:
        half = True
        probe += skipped
        pos = ts[probe - 1].end
    if probe < len(ts) and ts[probe].kind == "w" and ts[probe].start - pos in (0, 1):
        unit_tok = ts[probe]
        unit = "y" if unit_tok.text in _YEAR_UNITS else "m" if unit_tok.text in _MONTH_UNITS else ""
        gap_ok = unit_tok.start == pos or n[pos] in " -"
        if unit and gap_ok:
            claim_end = unit_tok.end
            probe += 1
            if not half:
                tail = _half_words(n, ts, probe, claim_end)
                if tail:
                    half = True
                    probe += tail
                    claim_end = ts[probe - 1].end
            if half:
                readings = tuple(
                    tuple(
                        _decimal_text(Decimal(v) + Decimal("0.5")) if v and len(v) < 24 else v
                        for v in r
                    )
                    for r in readings
                )
            return _Claim("dur", start, claim_end, readings, unit, bound), probe
    if word and readings[0][0] == "1":
        return None, nxt  # "un", "one": an article more than a count
    if (
        len(readings) == 1
        and len(readings[0]) == 1
        and factor == 1
        and not word
        and (readings[0][0] or "").isdigit()
        and len(readings[0][0] or "") == 4
        and 1900 <= int(readings[0][0] or 0) <= 2100
    ):
        return _Claim("year", start, end, readings), nxt
    return _Claim("num", start, end, readings), nxt


def _list_marker(n: str, tok: _Tok) -> bool:
    """ "1." or "2)" opening a line: a bullet, not a claim."""
    if len(tok.text) > 2 or not tok.text.isdigit():
        return False
    if tok.end >= len(n) or n[tok.end] not in ".)":
        return False
    if tok.end + 1 < len(n) and n[tok.end + 1] != " ":
        return False
    p = tok.start - 1
    while p >= 0 and n[p] == " ":
        p -= 1
    return p < 0 or n[p] == "\n"


def _month_first(n: str, ts: list[_Tok], i: int) -> tuple[_Claim | None, int]:
    """ "October 14, 2026", "Nov 3", "March 2026", and a bare "in October"."""
    tok = ts[i]
    month = _MONTHS[tok.text]
    if i + 1 < len(ts) and ts[i + 1].kind == "n" and _gap(n, tok, ts[i + 1]):
        second = ts[i + 1]
        if _is_year(second):
            return _Claim("date", tok.start, second.end, ((None, str(month), second.text),)), i + 2
        day = _small_int(second, 1, 31)
        if day is not None:
            if i + 2 < len(ts) and _is_year(ts[i + 2]) and _gap(n, second, ts[i + 2]):
                year = ts[i + 2]
                return _Claim(
                    "date", tok.start, year.end, ((str(day), str(month), year.text),)
                ), i + 3
            return _Claim("date", tok.start, second.end, ((str(day), str(month), None),)), i + 2
    if tok.text in _MONTHS_ALONE:
        return _Claim("date", tok.start, tok.end, ((None, str(month), None),)), i + 1
    return None, i + 1


def _day_first(n: str, ts: list[_Tok], i: int, day: int) -> tuple[_Claim | None, int]:
    """ "14 October 2026", "1st of November", "1er novembre"."""
    j = i + 1
    while j < len(ts) and j <= i + 3 and ts[j].kind == "w" and ts[j].text in _ORDINALS:
        if not _gap(n, ts[j - 1], ts[j]):
            return None, i + 1
        j += 1
    if j < len(ts) and ts[j].kind == "w" and ts[j].text in _MONTHS and _gap(n, ts[j - 1], ts[j]):
        month = _MONTHS[ts[j].text]
        if j + 1 < len(ts) and _is_year(ts[j + 1]) and _gap(n, ts[j], ts[j + 1]):
            return (
                _Claim(
                    "date", ts[i].start, ts[j + 1].end, ((str(day), str(month), ts[j + 1].text),)
                ),
                j + 2,
            )
        return _Claim("date", ts[i].start, ts[j].end, ((str(day), str(month), None),)), j + 1
    return None, i + 1


def _hour(n: str, ts: list[_Tok], i: int) -> tuple[_Claim | None, int]:
    """ "10h", "10h30"."""
    hours = _small_int(ts[i], 0, 24)
    if hours is None or i + 1 >= len(ts) or ts[i + 1].text != "h" or ts[i + 1].start != ts[i].end:
        return None, i + 1
    end = ts[i + 1].end
    j = i + 2
    minutes = "00"
    if (
        j < len(ts)
        and ts[j].kind == "n"
        and ts[j].start == end
        and len(ts[j].text) == 2
        and int(ts[j].text) < 60
    ):
        minutes = ts[j].text
        end = ts[j].end
        j += 1
    return _Claim("time", ts[i].start, end, ((f"{hours:02d}:{minutes}",),)), j


def _quantities(n: str, ts: list[_Tok], mask: bytearray) -> list[_Claim]:
    claims: list[_Claim] = []
    i = 0
    count = len(ts)
    while i < count:
        tok = ts[i]
        if mask[tok.start]:
            i += 1
            continue
        if tok.kind == "n":
            if _list_marker(n, tok):
                i += 1
                continue
            plus = tok.start > 0 and n[tok.start - 1] == "+"
            kind, readings = _classify_run(tok.text, plus)
            if kind in ("time", "date", "phone"):
                begin = tok.start - 1 if plus and kind == "phone" else tok.start
                claims.append(_Claim(kind, begin, tok.end, readings))
                i += 1
                continue
            day = _small_int(tok, 1, 31)
            if day is not None:
                found, nxt = _day_first(n, ts, i, day)
                if found is None:
                    found, nxt = _hour(n, ts, i)
                if found is not None:
                    claims.append(found)
                    i = nxt
                    continue
            else:
                found, nxt = _hour(n, ts, i)
                if found is not None:
                    claims.append(found)
                    i = nxt
                    continue
            found, nxt = _quantity(n, ts, i, i, readings, word=False)
            if found is not None:
                claims.append(found)
            i = nxt
        else:
            if tok.text in _MONTHS:
                found, nxt = _month_first(n, ts, i)
                if found is not None:
                    claims.append(found)
                    i = nxt
                    continue
            parsed = _parse_words(n, ts, i)
            if parsed is None:
                i += 1
                continue
            value, last = parsed
            found, nxt = _quantity(n, ts, i, last, ((str(value),),), word=True)
            if found is not None:
                claims.append(found)
            i = max(nxt, last + 1)
    return claims


# Links, e-mail addresses, streets ------------------------------------------------------------

_TOKEN_RE = re.compile(r"[^\s<>\"'(),;]+")
_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.-]{1,9}://")
_LABEL_RE = re.compile(r"[a-z0-9-]+")
_TLDS = _set(
    "com org net io fr eu co ai app dev me pro uk de es it nl be ch us ca info biz tech xyz"
    " online site cloud fm tv gg so sh ly"
)
_TRAILING = ".:!?]}*_"
_LOCAL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._%+-")


def _links(n: str) -> list[_Claim]:
    claims: list[_Claim] = []
    for found in _TOKEN_RE.finditer(n):
        token = found.group()
        start = found.start()
        scheme = _SCHEME_RE.search(token)
        if scheme is not None:
            link = token[scheme.start() :].rstrip(_TRAILING)
            body = link[scheme.end() - scheme.start() :]
            if body:
                claims.append(
                    _Claim(
                        "link",
                        start + scheme.start(),
                        start + scheme.start() + len(link),
                        ((_link_key(link),),),
                    )
                )
            continue
        if "@" in token:
            local, _, domain = token.rpartition("@")
            domain = domain.rstrip(_TRAILING)
            labels = domain.split(".")
            cut = len(local)
            while cut > 0 and local[cut - 1] in _LOCAL_CHARS:
                cut -= 1
            local = local[cut:]
            valid = (
                local
                and len(labels) >= 2
                and labels[-1].isalpha()
                and len(labels[-1]) >= 2
                and all(_LABEL_RE.fullmatch(label) for label in labels)
            )
            if valid:
                claims.append(
                    _Claim(
                        "email",
                        start + cut,
                        start + cut + len(local) + 1 + len(domain),
                        ((f"{local}@{domain}",),),
                    )
                )
            continue
        trimmed = token.rstrip(_TRAILING)
        if trimmed.startswith("www.") and len(trimmed) > 4:
            claims.append(_Claim("link", start, start + len(trimmed), ((_link_key(trimmed),),)))
            continue
        host = trimmed.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        labels = host.split(".")
        # Deliberate: a bare domain counts only with a well known suffix, so "Node.js" and
        # "e.g." are not links but "janeroe.dev/about" is.
        if len(labels) >= 2 and labels[-1] in _TLDS and all(_LABEL_RE.fullmatch(x) for x in labels):
            claims.append(_Claim("link", start, start + len(trimmed), ((_link_key(trimmed),),)))
    return claims


def _link_key(link: str) -> str:
    """The same page written with or without scheme, "www." or a trailing slash is one key. The
    query and the fragment stay: a tracking tag from a mail makes it another link."""
    key = _SCHEME_RE.sub("", link, count=1)
    key = key.removeprefix("www.")
    return key.rstrip("/")


_STREET_FR = _set("rue avenue boulevard bd allee impasse chemin quai")
_STREET_EN = _set("street road rd lane ave blvd drive avenue")
_STREET_ALL = _STREET_FR | _STREET_EN
_STREET_STOP = _set("a in at dans et and or ou pour for from to near pres")


def _streets(n: str, ts: list[_Tok]) -> list[_Claim]:
    """ "12 rue de la Paix", "10 Downing Street": a house number and a street type, or a French
    street type with its name. Deliberate: the whole phrase must be written in the profile."""
    claims: list[_Claim] = []
    k = 0
    while k < len(ts):
        tok = ts[k]
        if tok.kind != "w" or tok.text not in _STREET_ALL:
            k += 1
            continue
        low = k
        steps = 0
        while low > 0 and steps < 3 and _joined(n, ts[low - 1], ts[low], " "):
            low -= 1
            steps += 1
            if ts[low].kind == "n":
                break
        number = ts[low].kind == "n" and len(ts[low].text) <= 4 and ts[low].text.isdigit()
        if not number:
            low = k
        french = tok.text in _STREET_FR
        if not number and not french:
            k += 1
            continue
        high = k
        if french:
            while (
                high + 1 < len(ts)
                and high - k < 5
                and ts[high + 1].kind == "w"
                and ts[high + 1].text not in _STREET_STOP
                and _gap(n, ts[high], ts[high + 1], limit=1, allowed=" '-")
            ):
                high += 1
        phrase = " ".join(_words(n[ts[low].start : ts[high].end]))
        claims.append(_Claim("street", ts[low].start, ts[high].end, ((phrase,),)))
        k = high + 1
    return claims


# Titles --------------------------------------------------------------------------------------

_HEADS = _set(
    "engineer developer programmer manager director architect consultant analyst scientist"
    " researcher specialist intern apprentice founder cofounder designer cto ceo cio coo cfo vp"
    " president ingenieur ingenieure developpeur developpeuse responsable directeur directrice"
    " architecte consultante analyste chercheur chercheuse stagiaire alternant alternante"
    " fondateur fondatrice chef lead owner officer chief head"
)
# A word that alone is too common to be a title ("I lead the team"): it needs a qualifier.
_HEADS_QUALIFIED = _set("lead owner officer chief head responsable chef")
_HEADS_OF = _set("head director directeur directrice vp responsable chef")
_TITLE_STOP = _set(
    "a an the as au en le la les un une de d du des of for at in my mon ma our notre your"
    " votre this ce cet cette that and et or ou to with avec was is am are be been being comme"
    " i je que qui etais etait suis es est sont etre ete serai sera devenu devenue become became"
    " becoming joined hired he she we they nous vous il elle tu"
)
_OF = _set("of de du des d")
_ARTICLES = _set("la le les l un une the a an du des d")


def _titles(n: str, ts: list[_Tok]) -> list[_Claim]:
    claims: list[_Claim] = []
    for k, tok in enumerate(ts):
        if tok.kind != "w" or tok.text not in _HEADS:
            continue
        low = k
        while (
            low > 0
            and k - low < 3
            and ts[low - 1].kind == "w"
            and ts[low - 1].text not in _TITLE_STOP
            and _gap(n, ts[low - 1], ts[low], limit=1, allowed=" -")
        ):
            low -= 1
        high = k
        if (
            tok.text in _HEADS_OF
            and k + 1 < len(ts)
            and ts[k + 1].text in _OF
            and _gap(n, tok, ts[k + 1], limit=1, allowed=" '")
        ):
            high = k + 1
            taken = 0
            while high < len(ts) - 1 and taken < 3:
                nxt = ts[high + 1]
                article = nxt.text in _ARTICLES and taken == 0
                if nxt.kind != "w" or not _gap(n, ts[high], nxt, limit=1, allowed=" '-"):
                    break
                if nxt.text in _TITLE_STOP and not article:
                    break
                high += 1
                taken += 0 if article else 1
        if tok.text in _HEADS_QUALIFIED and low == k and high == k:
            continue
        phrase = " ".join(_words(n[ts[low].start : ts[high].end]))
        claims.append(_Claim("title", ts[low].start, ts[high].end, ((phrase,),)))
    return claims


# Organisations -------------------------------------------------------------------------------

_ORG_PREPOSITIONS = _set("at chez with for pour avec")
_CONNECTORS = _set("of de du des la le d")
_DETERMINERS = _set("the le la les l un une a an")
_NOT_ORGANISATIONS = _set(
    "i you your yours my our we they it he she this that these those here there thank thanks"
    " best kind kindest warm regards hello hi dear please bonjour bonsoir merci cordialement"
    " vous votre nous mon ma mes notre nos ce cette ces tout tous toute monday tuesday"
    " wednesday thursday friday saturday sunday lundi mardi mercredi jeudi vendredi samedi"
    " dimanche yes no all each every any some"
) | frozenset(_MONTHS)
_SCHOOL_WORDS = _set(
    "university universite school ecole institut institute college lycee iut faculty faculte"
    " polytech academy academie"
)


def _organisations(norm: _Norm, ts: list[_Tok]) -> list[_Claim]:
    n = norm.text
    claims: list[_Claim] = []

    def capitalised(tok: _Tok) -> bool:
        return tok.kind == "w" and norm.orig[norm.starts[tok.start]].isupper()

    def blank(left: _Tok, right: _Tok) -> bool:
        gap = right.start - left.end
        return 0 < gap <= 3 and n[left.end : right.start].isspace()

    for k, tok in enumerate(ts):
        if tok.kind != "w" or tok.text not in _ORG_PREPOSITIONS or k + 1 >= len(ts):
            continue
        head = ts[k + 1]
        if not capitalised(head) or not blank(tok, head):
            continue
        run = [k + 1]
        while len(run) < 6:
            nxt = run[-1] + 1
            if nxt >= len(ts):
                break
            cur, after = ts[run[-1]], ts[nxt]
            if (
                capitalised(after)
                and after.text not in _NOT_ORGANISATIONS
                and (blank(cur, after) or n[cur.end : after.start] in ("-", " & "))
            ):
                run.append(nxt)
            elif (
                after.text in _CONNECTORS
                and blank(cur, after)
                and nxt + 1 < len(ts)
                and capitalised(ts[nxt + 1])
                and blank(after, ts[nxt + 1])
            ):
                run.extend((nxt, nxt + 1))
            else:
                break
        words = [ts[x].text for x in run]
        if words[0] in _NOT_ORGANISATIONS:
            continue
        if words[0] in _DETERMINERS:
            words = words[1:]
            run = run[1:]
            if not words:
                continue
        if len(" ".join(words)) < 2:
            continue
        school = any(w in _SCHOOL_WORDS for w in words)
        kind = "school" if school else "employer"
        claims.append(_Claim(kind, ts[run[0]].start, ts[run[-1]].end, ((" ".join(words),),)))
    return claims


# ---------------------------------------------------------------------------------------------
# Extraction, facts and the check
# ---------------------------------------------------------------------------------------------


def _extract(norm: _Norm, *, organisations: bool) -> list[_Claim]:
    n = norm.text
    mask = bytearray(len(n))
    claims: list[_Claim] = []

    def take(found: list[_Claim]) -> None:
        for claim in found:
            mask[claim.start : claim.end] = b"\x01" * (claim.end - claim.start)
            claims.append(claim)

    take(_links(n))
    ts = _tokenize(n)
    take(_streets(n, ts))
    claims.extend(_quantities(n, ts, mask))
    claims.extend(_titles(n, ts))
    if organisations:
        claims.extend(_organisations(norm, ts))
    return claims


class _Facts:
    """What a set of texts states, by type of claim."""

    __slots__ = (
        "dates",
        "durations",
        "emails",
        "links",
        "longest",
        "numbers",
        "phones",
        "times",
        "years",
    )

    def __init__(self) -> None:
        self.numbers: set[str | None] = set()
        self.years: set[str | None] = set()
        self.durations: set[tuple[str | None, str]] = set()
        self.longest: dict[str, Decimal] = {}
        self.dates: set[tuple[str | None, ...]] = set()
        self.times: set[str | None] = set()
        self.phones: set[str | None] = set()
        self.emails: set[str | None] = set()
        self.links: set[str | None] = set()

    def add(self, claim: _Claim) -> None:
        kind = claim.type
        if kind in ("num", "pct", "amt", "year"):
            for reading in claim.readings:
                self.numbers.update(reading)
        elif kind == "dur":
            for reading in claim.readings:
                for value in reading:
                    self.durations.add((value, claim.unit))
                    self.numbers.add(value)
                    if value is not None and len(value) < 24:
                        number = Decimal(value)
                        if number > self.longest.get(claim.unit, Decimal(-1)):
                            self.longest[claim.unit] = number
        elif kind == "date":
            for day, month, year in claim.readings:
                self.dates.add((day, month, year))
                self.years.add(year)
        elif kind == "time":
            self.times.update(claim.readings[0])
        elif kind == "phone":
            self.phones.update(claim.readings[0])
        elif kind == "email":
            self.emails.update(claim.readings[0])
        elif kind == "link":
            self.links.update(claim.readings[0])

    def union(self, other: _Facts) -> _Facts:
        both = _Facts()
        for name in (
            "numbers",
            "years",
            "durations",
            "dates",
            "times",
            "phones",
            "emails",
            "links",
        ):
            getattr(both, name).update(getattr(self, name), getattr(other, name))
        for facts in (self, other):
            for unit, value in facts.longest.items():
                if value > both.longest.get(unit, Decimal(-1)):
                    both.longest[unit] = value
        return both


def _facts_of(norms: list[_Norm]) -> _Facts:
    facts = _Facts()
    for norm in norms:
        for claim in _extract(norm, organisations=False):
            facts.add(claim)
    return facts


def _corpus(texts: list[str]) -> str:
    """Padded word lists, one per text, so that " a b c " in corpus is a phrase search that
    never matches across two texts or inside a word."""
    return "\x00".join(" " + " ".join(_words(_Norm(text).text)) + " " for text in texts)


def _year_ok(value: str | None, facts: _Facts) -> bool:
    return value in facts.numbers or value in facts.years


def _date_ok(claim: _Claim, facts: _Facts, index: dict[str, dict]) -> bool:
    for day, month, year in claim.readings:
        years = index["by_month"].get(month) if day is None else index["by_day"].get((month, day))
        if not years:
            continue
        if year is None or year in years or (None in years and _year_ok(year, facts)):
            return True
    return False


def _date_index(facts: _Facts) -> dict[str, dict]:
    by_month: dict[str | None, set[str | None]] = {}
    by_day: dict[tuple[str | None, str | None], set[str | None]] = {}
    for day, month, year in facts.dates:
        by_month.setdefault(month, set()).add(year)
        if day is not None:
            by_day.setdefault((month, day), set()).add(year)
    return {"by_month": by_month, "by_day": by_day}


def _duration_ok(claim: _Claim, own: _Facts) -> bool:
    top = own.longest.get(claim.unit)
    for reading in claim.readings:
        for value in reading:
            if value is None:
                return False
            if claim.bound and (top is None or len(value) >= 24):
                return False
            if claim.bound == "ge" and (value, claim.unit) not in own.durations:
                if top is None or top < Decimal(value):
                    return False
            elif claim.bound == "gt":
                if top is None or top <= Decimal(value):
                    return False
            elif not claim.bound and (value, claim.unit) not in own.durations:
                return False
    return True


def _clean_address(address: str) -> str:
    """ "Name <a@b.c>" and "a@b.c" give the same key."""
    low = address.rfind("<")
    if low >= 0:
        high = address.find(">", low)
        address = address[low + 1 : high if high >= 0 else None]
    return _Norm(address).text.strip(" <>\"'")


def check(draft_body: str, sources: Sources) -> list[Issue]:
    """The issues of `draft_body` against `sources`, in order of position then kind then code."""
    if not draft_body.strip():
        return []
    draft = _Norm(draft_body)
    own_texts = [*sources.profile, *sources.answers, *sources.instructions]
    own_norms = [_Norm(text) for text in own_texts]
    own = _facts_of(own_norms)
    everything = own.union(_facts_of([_Norm(text) for text in sources.thread]))
    dates = _date_index(everything)
    phrases = _corpus([*own_texts, *sources.titles])
    organisations = _corpus(
        [*sources.employers, *sources.schools, *sources.participants, *sources.known_terms]
    )
    recipients = {_clean_address(address) for address in sources.recipients}
    memo: dict[tuple[bool, str], bool] = {}

    issues: set[Issue] = set()
    for claim in _extract(draft, organisations=True):
        kind = claim.type
        if kind in ("num", "pct", "amt"):
            sourced = any(all(v in everything.numbers for v in r) for r in claim.readings)
        elif kind == "year":
            sourced = any(all(_year_ok(v, everything) for v in r) for r in claim.readings)
        elif kind == "dur":
            sourced = _duration_ok(claim, own)
        elif kind == "date":
            sourced = _date_ok(claim, everything, dates)
        elif kind == "time":
            sourced = claim.readings[0][0] in everything.times
        elif kind == "phone":
            sourced = claim.readings[0][0] in own.phones
        elif kind == "email":
            sourced = claim.readings[0][0] in own.emails or claim.readings[0][0] in recipients
        elif kind == "link":
            sourced = claim.readings[0][0] in own.links
        else:
            phrase = claim.readings[0][0] or ""
            org = kind in ("employer", "school")
            key = (org, phrase)
            if key not in memo:
                memo[key] = f" {phrase} " in (organisations if org else phrases)
            sourced = memo[key]
        if not sourced:
            issue_kind, code = _RULES[kind]
            start, end = draft.span(claim.start, claim.end)
            issues.add(Issue(issue_kind, start, end, code))
    return sorted(issues, key=lambda i: (i.start, i.end, i.kind, i.code))
