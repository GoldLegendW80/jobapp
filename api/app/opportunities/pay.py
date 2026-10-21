"""The pay and the work mode a posting states (opportunities O3, SPEC-opportunities "The card").

Pure and linear in the size of the text. Never a guess: a figure is kept only when the posting
states it in a shape of the allow list below, with its currency and its period; anything else is
refused and nothing is stored.

**Pay in a posting's text** (`pays`, `pay_of`). One statement is an amount or a range of two
amounts, with a currency named (a code of the profile's CURRENCIES, "euro" or "euros", or a sign:
€, £, $ read as USD, US$, C$ and CA$, A$ and AU$, S$ and SG$, NZ$, HK$) and a period named,
after the figures or just before them:
- ranges: "60,000 to 70,000 EUR a year", "60k-70k € per year" (a hyphen or a dash between two
  figures is a range), "de 500 à 650 € par jour", "between $50 and $60 an hour", "60-70k€" (a "k"
  on the second figure alone counts for both);
- one figure: "€600/day", "TJM 600 €", "45 K€ brut annuel", "$120,000 a year";
- figures: thousands grouped by comma, dot, apostrophe, space, thin or non breaking space, "k" or
  "K" for thousands, up to two decimals ("1,5k €" is 1,500); a figure that is not whole once
  read is refused;
- periods after: "a year", "per year", "/year", "/yr", "per annum", "p.a.", "annually", "yearly",
  "annual", "par an", "/an", "annuel", "a month", "/mo", "par mois", "monthly", "mensuel", "a
  day", "/day", "/j", "par jour", "daily", "journalier", "an hour", "/h", "/hr", "par heure", "de
  l'heure", "hourly", "horaire", with "brut", "gross", "net", "base", "HT" or "TTC" in between;
  before: "TJM", "taux journalier", "daily rate", "day rate", "annual salary", "salaire annuel",
  "rémunération annuelle", "monthly salary", "salaire mensuel", "hourly rate", "taux horaire".
Refused: no currency ("60,000 a year"), no period ("60k-70k €"), two currencies or two periods in
one statement, a reversed range, a figure under 1 or over 10,000,000, a figure under the floor of
its period (under 1,000 a year, 100 a month or 20 a day: a perk, never a pay), a percentage, a
figure that continues a run of digits (a phone number, a postcode, a date: "06 12 34 56 78 €"), a
plain four digit figure from 1900 to 2100 written right before its currency in a yearly
statement with no other sign of a pay (no currency before it, no "brut", "net", "gross" or "HT"
after it, no "TJM" or "annual salary" before it, no "salaire", "salary", "rémunération" or
"gratification" earlier in its clause unless a date word such as "in", "en" or "since" comes
right before the figure: "2026 € a year" and "Salary review in 2026 € a year" are years, "2000 €
par mois" is a pay),
and one figure after an open bound ("up to",
"from", "starting at", "jusqu'à", "à partir de", "dès", "minimum", "maximum", "at least", "au
moins", "more than", "over", "plus de").
A bonus or a benefit is never a pay: a statement is refused when its clause before it names a
bonus ("bonus", "prime", "equity", "stock", "RSU", "BSPCE", "commission", "variable",
"signing", "sign-on", "relocation", "allowance", "indemnité", "intéressement", "participation",
"abondement", "13e mois", "profit sharing", "options", "OTE") or a perk, in English or French
("tickets restaurant", "titres restaurant", "meal", "lunch", "voucher", "Swile", "mutuelle",
"budget", "stipend", "membership", "gym", "credits", "conference", "formation", "training",
"transport", "Navigo", "home office", "insurance", "per diem", ...): "Tickets restaurant 9 € par
jour", "Learning budget of €1,000 a year". A comma, a parenthesis, a semicolon, a bar, a bullet
or a plus right after such a word does not end the clause ("Bonus, €5,000 per year", "Bonus |
€5,000 per year"), nor does a line break the figure follows at once ("Bonus\\n€5,000 per year").
After the statement, such a word anywhere in its clause refuses it ("45 K€ brut annuel
variable", "$5,000 a year annual bonus", "€1,000 a year to spend on conferences", "€60,000 a
year on target earnings"), with two exceptions: a clause that opens with "with", "avec",
"hors", "excluding", "without" or "plus" names an addition, and a full pay (at least SALARY_MIN
for its period: 10,000 a year, 500 a month, 50 a day, 5 an hour) is kept ("€70,000 per year
with great benefits", "40 000 € brut par an hors variable"; "€5,000 a year with stock options"
is refused); and "for a" or "as a" followed by a capitalised run whose last word is not a bonus
or perk word is a role ("€50,000 per year for a Training Manager"; "as a Performance Bonus" is
refused).
"Competitive" and "selon profil" hold no figure, so nothing is found.

A text may state several pays (a salary and a daily rate, or one pay in two currencies): `pays`
returns every one in order, each already past every refusal above; `pay_of` keeps one, the first
with a period that fits the contract (a freelance mission: a day or an hour first; any other
contract: a year or a month first), else the first `pays` returns, never a refused figure.

**Work mode** (`work_mode_of`), from a location: "Remote", "Télétravail" (remote), "Hybrid",
"Hybride", "Télétravail partiel", "partial remote" (hybrid), "On site", "Onsite", "In office",
"Sur site", "Présentiel" (office), as whole words in any case. Two modes named, or a mode right
after "no", "not", "non", "pas de", "sans" or "without", is refused (None).

Not found (declared open point): a pay written only in an image, a PDF attachment or a table cell
the text extraction lost; a pay with a currency the allow list does not name; an hourly rate with
cents.
"""

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.profile.schemas import CURRENCIES

PAY_MAX = 10_000_000
PERIODS = ("year", "month", "day", "hour")
WORK_MODES = ("remote", "hybrid", "office")
FREELANCE_PERIODS = ("day", "hour")


@dataclass(frozen=True)
class Pay:
    min: int
    max: int
    currency: str
    period: str


# --- the shapes ---------------------------------------------------------------------------------


_SIGNS = {
    "€": "EUR",
    "£": "GBP",
    "$": "USD",
    "us$": "USD",
    "c$": "CAD",
    "ca$": "CAD",
    "a$": "AUD",
    "au$": "AUD",
    "s$": "SGD",
    "sg$": "SGD",
    "nz$": "NZD",
    "hk$": "HKD",
    "euro": "EUR",
    "euros": "EUR",
}
_CODES = "|".join(CURRENCIES)
# Codes in capitals only ("try" or "mad" in a sentence is no currency), right after a figure's
# "k" too ("60kEUR"); "euro" and "euros" in any case.
_CURRENCY = (
    r"(?:(?-i:US|CA|AU|SG|NZ|HK|[CAS])\$|[€£$]|"
    rf"(?:(?<![^\W\d_])|(?<=[kK]))(?-i:{_CODES})(?![^\W\d_])|(?<![^\W\d_])euros?(?![^\W\d_]))"
)
# A figure: thousands in groups of three (at most four groups), or plain digits, then up to two
# decimals. Never inside a longer run of digits (the lookarounds), so a phone number, a postcode
# or a date gives no figure.
_NUMBER = (
    r"(?<!\d)(?<!\d[,.'\u2019\u00a0\u202f\u2009 ])"
    r"(?:\d{1,3}(?:[,.'\u2019\u00a0\u202f\u2009 ]\d{3}){1,4}|\d{1,8})(?:[.,]\d{1,2})?"
    r"(?!\d)(?![,.'\u2019\u00a0\u202f\u2009 ]\d)(?!\s?%)"
)
_THOUSANDS = r"(?:\s?[kK](?=[€£$]|EUR|eur)|\s?[kK](?![^\W_]))"
_RANGE_WORD = r"\s{0,3}(?:-|\u2013|\u2014|~|to|à|and|et)\s{0,3}"
_FILLER = r"(?:\s{0,3}(?<![^\W\d_])(?:brut|bruts|gross|net|base|fixe|HT|TTC)(?![^\W\d_]))"
_WORD_START = r"(?<![^\W\d_])"
_PERIOD_AFTER = (
    r"\s{0,3}(?:/\s{0,2}|"
    rf"{_WORD_START}(?:(?:per|a|an|par|by\s+the|by)\s{{1,3}}|de\s+l['\u2019]\s{{0,2}}))"
    r"(?P<after_noun>year|yr|y|annum|an|année|annee|month|mo|mois|day|d|jour|journée|j|hour|hr|h|heure)"
    r"(?![^\W\d_])"
    rf"|\s{{0,3}}{_WORD_START}(?P<after_adj>p\.\s?a\.|pa|annually|yearly|annual|annuel|annuelle|"
    r"annuels|monthly|mensuel|mensuelle|daily|journalier|hourly|horaire)(?![^\W\d_])"
)
_PERIOD_BEFORE = (
    r"(?P<before>TJM|taux\s+journalier(?:\s+moyen)?|daily\s+rate|day\s+rate|"
    r"annual\s+(?:base\s+)?(?:salary|compensation|pay)|salaire\s+(?:brut\s+)?annuel|"
    r"rémunération\s+(?:brute\s+)?annuelle|monthly\s+(?:salary|pay)|"
    r"salaire\s+(?:brut\s+)?mensuel|hourly\s+(?:rate|wage|pay)|taux\s+horaire)"
    r"(?![^\W\d_])\s{0,3}(?::\s{0,3})?"
    r"(?:(?:of|de|du|between|entre|from|around|environ)\s{1,3})?"
)
_STATEMENT = re.compile(
    rf"(?:{_PERIOD_BEFORE})?"
    rf"(?P<cur1>{_CURRENCY})?\s{{0,2}}(?P<n1>{_NUMBER})(?P<k1>{_THOUSANDS})?\s{{0,2}}"
    rf"(?P<cur2>{_CURRENCY})?"
    rf"(?:{_RANGE_WORD}(?P<cur3>{_CURRENCY})?\s{{0,2}}(?P<n2>{_NUMBER})(?P<k2>{_THOUSANDS})?"
    rf"\s{{0,2}}(?P<cur4>{_CURRENCY})?)?"
    rf"(?:{_FILLER}{{0,2}}(?:{_PERIOD_AFTER}))?",
    re.IGNORECASE,
)

_PERIOD_WORDS = {
    "year": ("year", "yr", "y", "annum", "an", "année", "annee", "p.a.", "pa", "annually",
             "yearly", "annual", "annuel", "annuelle", "annuels"),
    "month": ("month", "mo", "mois", "monthly", "mensuel", "mensuelle"),
    "day": ("day", "d", "jour", "journée", "j", "daily", "journalier"),
    "hour": ("hour", "hr", "h", "heure", "hourly", "horaire"),
}  # fmt: skip
_PERIOD_OF = {word: period for period, words in _PERIOD_WORDS.items() for word in words}
_BEFORE_PERIOD = (
    (re.compile(r"tjm|journalier|daily|day", re.IGNORECASE), "day"),
    (re.compile(r"annual|annuel", re.IGNORECASE), "year"),
    (re.compile(r"monthly|mensuel", re.IGNORECASE), "month"),
    (re.compile(r"hourly|horaire", re.IGNORECASE), "hour"),
)

# What the clause around a statement must not hold (at most WINDOW characters before it, cut at
# the last clause mark, and WINDOW after it, cut at the first). A bonus or a perk word refuses any
# statement; an open bound refuses one figure.
WINDOW = 40
_CLAUSE_MARK = re.compile(r"[.,;\n+|•(]|\bplus\b(?!\s+de)|\band\b|\bet\b", re.IGNORECASE)
_BONUS_WORDS = (
    r"bonus|bonuses|prime|primes|equity|stock|stocks|rsu|rsus|bspce|commission|commissions|"
    r"variable|signing|relocation|allowance|allowances|indemnité|indemnités|indemnite|indemnites|"
    # Profit shares, a thirteenth month, sign-on payments, options and on target earnings
    # (review of PR #498, follow-up 1): never a base pay.
    r"intéressement|interessement|participation|participations|abondement|abondements|"
    r"13\s?(?:e|è|ème|eme|ieme|ième|th)\s+mois|13\s?(?:th|e)\s+month|treizième\s+mois|"
    r"treizieme\s+mois|thirteenth\s+month|profit[\s-]+sharing|gain[\s-]+sharing|"
    r"sign[\s-]?on|options|ote|on[\s-]+target[\s-]+earnings?"
)
_PERK_WORDS = (
    r"tickets?\s+restaurants?|titres?[\s-]+restaurants?|restaurant|meal|meals|lunch|lunches|"
    r"déjeuner|dejeuner|repas|voucher|vouchers|swile|mutuelle|mutuelles|budget|budgets|stipend|"
    r"stipends|membership|memberships|gym|sport|sports|wellness|wellbeing|credit|credits|crédit|"
    r"crédits|conference|conferences|conférence|conférences|formation|formations|training|"
    r"trainings|transport|transports|navigo|mobilité|mobilités|home\s+office|coworking|"
    r"insurance|assurance|pension|retraite|perk|perks|benefit|benefits|avantage|avantages|"
    r"reimbursement|reimbursed|remboursement|remboursé|remboursée|per\s+diem|equipment|"
    r"équipement|childcare|crèche|creche"
)
_REFUSED = re.compile(
    rf"(?<![^\W\d_])(?:{_BONUS_WORDS}|{_PERK_WORDS})(?![^\W\d_])",
    re.IGNORECASE,
)
# A refused word that ends the text, maybe followed by marks: a mark right after it (a comma, a
# parenthesis, a semicolon, a bar, a bullet, a plus) ends no clause; a full stop still does,
# except before a line break the figure follows at once ("Bonus.\n€5,000 per year").
_LABEL_MARKS = ",(;|•+"
_ONLY_MARKS = re.compile(r"[\s.,(;|•+:]*")
_REFUSED_END = re.compile(
    rf"(?<![^\W\d_])(?:{_BONUS_WORDS}|{_PERK_WORDS})[\s.,(;|•+:]*$",
    re.IGNORECASE,
)
# The clause after a statement (review of #513): any refused word in it refuses the figure,
# except after an opening addition word, or in a role ("for a" or "as a", then a capitalised run
# of at most four words whose last word is not refused).
_ADDITION = re.compile(
    r"\s{0,3}(?:with|avec|hors|excluding|without|plus)(?![^\W\d_])", re.IGNORECASE
)
_ROLE_LEAD = re.compile(r"\s{0,3}(?:for|as)\s{1,3}(?:a|an)\s{1,3}", re.IGNORECASE)
_CAPITALISED_RUN = re.compile(r"[A-Z][\w-]{0,40}(?:\s{1,3}[A-Z][\w-]{0,40}){0,3}")
_REFUSED_LAST = re.compile(
    rf"(?<![^\W\d_])(?:{_BONUS_WORDS}|{_PERK_WORDS})$",
    re.IGNORECASE,
)
# The floor of a plausible pay per period: anything under it is a perk or a typo, never a pay.
FLOOR = {"year": 1000, "month": 100, "day": 20, "hour": 1}
# The least a full pay per period, for a figure followed by an addition that names a bonus or a
# perk ("with stock options"): under it, the figure is the addition's, not a pay.
SALARY_MIN = {"year": 10000, "month": 500, "day": 50, "hour": 5}
_YEAR_FIGURE = re.compile(r"(?:19|20)\d\d|2100")
# A word that names a pay, before a figure: then a figure from 1900 to 2100 is a pay, not a year.
_PAY_WORD = re.compile(
    r"(?<![^\W\d_])(?:salaire|salaires|salary|salaries|rémunération|remuneration|"
    r"gratification|gratifications|pay|wage|wages)(?![^\W\d_])",
    re.IGNORECASE,
)
# A word that dates what follows: a figure from 1900 to 2100 right after it is a year, whatever
# pay word comes earlier ("Salary review in 2026 € a year").
_DATE_LEAD = re.compile(
    r"(?<![^\W\d_])(?:in|en|since|depuis|until|till|by|after|before|during|avant|après|apres|"
    r"pendant|jusqu['\u2019]\s?en)\s{0,3}$",
    re.IGNORECASE,
)
_OPEN_BOUND = re.compile(
    r"(?<![^\W\d_])(?:up\s+to|from|starting\s+(?:at|from)|jusqu['\u2019]\s?à|jusqu['\u2019]\s?a|"
    r"à\s+partir\s+de|a\s+partir\s+de|dès|minimum|min|maximum|max|at\s+least|au\s+moins|"
    r"more\s+than|over|plus\s+de)\s*$",
    re.IGNORECASE,
)


def _amount(digits: str, thousands: bool) -> Decimal | None:
    """A figure as written: the last separator followed by one or two digits is the decimal mark,
    every other separator groups thousands."""
    text = "".join(digits.split())
    text = text.replace("\u00a0", "").replace("\u202f", "").replace("\u2009", "")
    decimals = ""
    match = re.search(r"[.,](\d{1,2})$", text)
    if match and not re.fullmatch(r"\d{1,3}(?:[.,']\d{3})+", text):
        decimals = match.group(1)
        text = text[: match.start()]
    whole = re.sub(r"[,.'\u2019]", "", text)
    try:
        value = Decimal(f"{whole}.{decimals or '0'}")
    except InvalidOperation:
        return None
    return value * 1000 if thousands else value


def _currency(token: str | None) -> str | None:
    if token is None:
        return None
    key = token.strip().lower()
    return _SIGNS.get(key) or (key.upper() if key.upper() in CURRENCIES else None)


def _period(match: re.Match[str]) -> str | None:
    """The one period the statement names, None when it names none or two."""
    named: set[str] = set()
    word = match.group("after_noun") or match.group("after_adj")
    if word:
        period = _PERIOD_OF.get(re.sub(r"\s", "", word.lower()))
        if period is None:
            return None
        named.add(period)
    if match.group("before"):
        named.update(p for pattern, p in _BEFORE_PERIOD if pattern.search(match.group("before")))
    return named.pop() if len(named) == 1 else None


def _clause_before(text: str, start: int, *, labelled: bool = False) -> str:
    """The clause the statement starting at `start` belongs to, up to WINDOW characters back.

    A mark that only follows a refused word ("Bonus, ", "Bonus; ", "Bonus | ", "Bonus • ",
    "Bonus + ", "Bonus,  , ") does not end the clause: the figure belongs to the word. A line
    break does not either when the figure follows it at once ("Bonus\\n€5,000 per year", a
    heading), but it does when words follow ("Benefits\\nSalary: €60,000 a year") or when the
    statement opens with its own pay label (`labelled`: "Tickets restaurant\\nTJM 600 €")."""
    window = text[max(0, start - WINDOW) : start]
    for mark in reversed(list(_CLAUSE_MARK.finditer(window))):
        if mark.group() in _LABEL_MARKS and _REFUSED_END.search(window, 0, mark.start()):
            continue  # "Bonus, €5,000 per year": the clause goes on
        if (
            not labelled
            and mark.group() in "\n."
            and _ONLY_MARKS.fullmatch(window, mark.end())
            and (mark.group() == "\n" or "\n" in window[mark.end() :])
            and _REFUSED_END.search(window, 0, mark.start())
        ):
            continue  # "Bonus\n€5,000 per year", "Bonus.\n€5,000": a heading over its figure
        return window[mark.end() :]
    return window


def _clause_after(text: str, end: int) -> str:
    window = text[end : end + WINDOW]
    mark = _CLAUSE_MARK.search(window)
    return window[: mark.start()] if mark else window


def _refused_after(after: str, low: Decimal, period: str) -> bool:
    """Whether the clause right after a statement makes it a bonus or a perk: a refused word
    anywhere in it refuses the figure ("45 K€ brut annuel variable", "$5,000 a year annual
    bonus", "€1,000 a year to spend on conferences", "€60,000 a year on target earnings").
    Two shapes are carved out:
    - the clause opens with "with", "avec", "hors", "excluding", "without" or "plus": the word is
      an addition to the pay ("€70,000 per year with great benefits", "40 000 € brut par an hors
      variable"), kept when the figure is a full salary (SALARY_MIN), refused under it ("€5,000
      a year with stock options");
    - "for a" or "as a" then a capitalised run whose last word is not refused: a role ("for a
      Training Manager", "as a Senior Equity Analyst"); "as a Performance Bonus" is refused."""
    if _ADDITION.match(after):
        return _REFUSED.search(after) is not None and low < SALARY_MIN[period]
    role = _ROLE_LEAD.match(after)
    if role:
        run = _CAPITALISED_RUN.match(after, role.end())
        if run and not _REFUSED_LAST.search(run.group()):
            return False
    return _REFUSED.search(after) is not None


def _a_year(match: re.Match[str], period: str, before: str) -> bool:
    """A plain four digit figure from 1900 to 2100 written right before its currency, with no
    other sign of a pay, in a yearly statement: a year next to a sign ("2026 € a year"), not a
    pay. A monthly, daily or hourly figure is never read as a year ("2000 € par mois"), and a
    pay word earlier in the clause ("Salaire 2000 € par an") is a sign of a pay,
    unless a date word comes right before the figure ("Salary review in 2026 € a year")."""
    if period != "year":
        return False
    if _PAY_WORD.search(before) and not _DATE_LEAD.search(before):
        return False
    if match.group("before") or match.group("cur1") or match.group("cur3"):
        return False
    if re.search(_FILLER, match.group(0)):
        return False
    pairs = (("n1", "k1", "cur2"), ("n2", "k2", "cur4"))
    return any(
        match.group(n) is not None
        and not match.group(k)
        and match.group(cur) is not None
        and _YEAR_FIGURE.fullmatch(match.group(n)) is not None
        for n, k, cur in pairs
    )


def _statement(text: str, match: re.Match[str]) -> Pay | None:
    currencies = {_currency(match.group(name)) for name in ("cur1", "cur2", "cur3", "cur4")} - {
        None
    }
    if len(currencies) != 1 or any(
        match.group(name) and _currency(match.group(name)) is None
        for name in ("cur1", "cur2", "cur3", "cur4")
    ):
        return None
    period = _period(match)
    if period is None:
        return None
    ranged = match.group("n2") is not None
    k1, k2 = bool(match.group("k1")), bool(match.group("k2"))
    low = _amount(match.group("n1"), k1)
    high = _amount(match.group("n2"), k2) if ranged else low
    if low is None or high is None:
        return None
    if ranged and k2 and not k1 and low < 1000:
        low *= 1000  # "60-70k€": the "k" counts for both
    if low != low.to_integral_value() or high != high.to_integral_value():
        return None
    if low < max(1, FLOOR[period]) or high > PAY_MAX or low > high:
        return None
    before = _clause_before(text, match.start(), labelled=bool(match.group("before")))
    if _REFUSED.search(before) or _refused_after(_clause_after(text, match.end()), low, period):
        return None
    if not ranged and _OPEN_BOUND.search(before):
        return None
    if _a_year(match, period, before):
        return None
    return Pay(min=int(low), max=int(high), currency=currencies.pop(), period=period)


def pays(text: str | None) -> list[Pay]:
    """Every pay the text states in an accepted shape, in order."""
    found: list[Pay] = []
    for match in _STATEMENT.finditer(text or ""):
        if not match.group("n1"):
            continue
        pay = _statement(text or "", match)
        if pay is not None:
            found.append(pay)
    return found


def pay_of(text: str | None, *, freelance: bool = False) -> Pay | None:
    """The one pay to store for a posting: the first whose period fits its contract, else the
    first stated, else None."""
    found = pays(text)
    fits = [p for p in found if (p.period in FREELANCE_PERIODS) == freelance]
    return (fits or found or [None])[0]


def pay_text(low: int, high: int, currency: str, period: str) -> str:
    """The pay as the card says it: "45,000 to 55,000 EUR a year", "600 EUR a day"."""
    per = "an hour" if period == "hour" else f"a {period}"
    figures = f"{low:,}" if low == high else f"{low:,} to {high:,}"
    return f"{figures} {currency} {per}"


# --- work mode ----------------------------------------------------------------------------------

_MODES = re.compile(
    r"(?<![^\W\d_])(?P<neg>(?:no|not|non|pas\s+de|pas\s+en|sans|without)[\s-]{1,3})?"
    r"(?:(?P<hybrid>hybrid|hybride|t[ée]l[ée]travail\s+partiel|partial(?:ly)?\s+remote)"
    r"|(?P<remote>remote|t[ée]l[ée]travail)"
    r"|(?P<office>on[\s-]?site|in[\s-]office|sur\s+site|pr[ée]sentiel))(?![^\W\d_])",
    re.IGNORECASE,
)


def work_mode_of(location: str | None) -> str | None:
    """The work mode a location names, None when it names none, two, or one negated."""
    named: set[str] = set()
    for match in _MODES.finditer(location or ""):
        if match.group("neg"):
            return None
        named.add(next(m for m in WORK_MODES if match.group(m)))
    return named.pop() if len(named) == 1 else None


# --- what an ATS states -------------------------------------------------------------------------


def stated_pay(low: object, high: object, currency: object, period: str | None) -> Pay | None:
    """A pay an ATS API states as figures, a currency code and a period, checked as the text's:
    whole numbers from 1 to PAY_MAX, min at most max, a known currency, a period. Never raises:
    any other value (a string, a bool, NaN, an infinity, a float too large) is no pay."""
    if period not in PERIODS or not isinstance(currency, str):
        return None
    code = currency.strip().upper()
    if code not in CURRENCIES:
        return None
    figures: list[int] = []
    for value in (low, high):
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        # NaN, an infinity or 1e400 (json reads them all) never reach int(): bounds first.
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if not 1 <= value <= PAY_MAX or value != int(value):
            return None
        figures.append(int(value))
    if figures[0] < 1 or figures[1] > PAY_MAX or figures[0] > figures[1]:
        return None
    return Pay(min=figures[0], max=figures[1], currency=code, period=period)
