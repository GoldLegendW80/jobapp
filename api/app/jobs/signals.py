"""What a posting's own text says before the person clicks (owner, 2026-11-07, "build all" of the
career-ops analysis; SPEC-jobs, "Before the click"): pure functions of the text, no profile, no
database, linear in the text's length.

- `read(text)`: the eligibility signals, each with the posting's own sentence quoted:
  `no_sponsorship` (it does not sponsor a visa or a work permit), `sponsorship` (it does),
  `work_permit` (it asks for the right to work in a place; `value` the place, a name of
  `app.apply.answers.COUNTRIES`), `degree` (it requires a degree), `contractor` (independent
  contractor, 1099, auto-entrepreneur, portage salarial), `fixed_term`, `unpaid`, `candidate_fee`
  (the candidate pays), and `closing` (the closing date it states, `value` its ISO date).
- `closing(text)`: that closing date and its sentence, or None.
- `fingerprint(text)`: a 64-bit SimHash of the text's distinct word pairs (a signed bigint), or
  None for a text too short to tell; `same_text(a, b)` for two fingerprints at most SAME_BITS
  apart (career-ops's cross-listing check: 92 % similar, at most 5 of 64 bits differ).
- `stored(text)`: the three columns a store or a rescore writes (`signals`, `text_fingerprint`,
  `closes_on`, migration 0038).

Rules are allow lists of English and French sentences, matched one sentence at a time on its
words (folded as `never_apply.fold` folds, then every sign but letters, digits and "+" read as a
space). A sentence a rule does not recognise gives nothing: silence is the absence of a signal,
never a claim. A rule that offers or requires something skips a sentence that negates it, and a
closing date is read only in an unambiguous form with its year.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.apply.answers import COUNTRIES
from app.jobs.never_apply import fold

TEXT_MAX = 200_000  # characters read; a longer posting is read up to here
SENTENCE_MAX = 1_000  # words a single sentence is matched on
QUOTE_MAX = 240
SHINGLE_WORDS = 2
SHINGLES_MIN = 40  # fewer distinct shingles: no fingerprint, never compared
SAME_BITS = 5
_MASK = (1 << 64) - 1


@dataclass(frozen=True)
class Signal:
    code: str
    quote: str
    value: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code, "quote": self.quote, "value": self.value}


# --- sentences ----------------------------------------------------------------------------------

_US = re.compile(r"\bU\.S\.(?:A\.)?|\bU\.S\b|\bUSA\b|\bUS\b")
_LINES = re.compile("[\r\n\u2028\u2029\u2022\u00b7\u25aa\u25cf\u25e6]+")
_STOPS = re.compile(r"(?<=[.!?;])\s+(?=[\"“«(A-ZÀ-Ý])")
_BULLET = re.compile("^[\\s*>#\\-\u2010-\u2015]+")
_SPACES = re.compile(r"\s+")
_NOT_WORD = re.compile(r"[^a-z0-9+]+")


def _sentences(text: str | None) -> list[str]:
    """The text's sentences as written: one per line or bullet, split again after . ! ? or ;
    when a capital follows (so "Ph.D. required" stays one)."""
    found = []
    for line in _LINES.split((text or "")[:TEXT_MAX]):
        for part in _STOPS.split(line):
            sentence = _SPACES.sub(" ", _BULLET.sub("", part)).strip()
            if sentence:
                found.append(sentence)
    return found


def words(sentence: str) -> str:
    """A sentence as the rules read it: "Must hold the U.S. right-to-work" reads
    "must hold the usa right to work"; "n'offrons" reads "n offrons"."""
    folded = fold(_US.sub(" USA ", sentence))
    return " ".join(_NOT_WORD.sub(" ", folded).split()[:SENTENCE_MAX])


def _quote(sentence: str) -> str:
    return sentence if len(sentence) <= QUOTE_MAX else sentence[: QUOTE_MAX - 1].rstrip() + "…"


# --- places -------------------------------------------------------------------------------------

_PLACE_OF = {phrase: name for name, phrases in COUNTRIES.items() for phrase in phrases}
_PLACES = "|".join(re.escape(p) for p in sorted(_PLACE_OF, key=len, reverse=True))


def place_of(text: str | None) -> str | None:
    """The country or region a text names, as a name of COUNTRIES ("UK", "Royaume-Uni" and "the
    United Kingdom" read "United Kingdom"), or None when it names none or several."""
    said = re.sub(r"^(?:the|l|le|la|les) ", "", words(text or ""))
    if said in _PLACE_OF:
        return _PLACE_OF[said]
    named = {name for phrase, name in _PLACE_OF.items() if re.search(rf"\b{phrase}\b", said)}
    return named.pop() if len(named) == 1 else None


# --- the rules ----------------------------------------------------------------------------------

# "do not", "cannot", "won't" ... before a verb of sponsoring.
_NEG = (
    r"(?:cannot|can not|can t|do not|don t|does not|doesn t|will not|won t|would not|are not"
    r"|is not|unable to|not able to|not in a position to|no longer)"
)
_SPONSOR_OBJECT = (
    r"(?:visa|visas|sponsorship|work permit|work permits|work authorization|work authorisation"
    r"|immigration|h1b|h 1b)"
)
_FR_SPONSOR_OBJECT = (
    r"(?:visa|visas|parrainage|sponsoring|sponsorisation|permis de travail|titre de sejour)"
)
_FR_SPONSOR_VERB = (
    r"(?:parrainer|sponsoriser|parrainons|sponsorisons|prendre en charge|prenons en charge"
    r"|accompagner|accompagnons|proposer|proposons|offrir|offrons|fournir|fournissons|assurer"
    r"|assurons)"
)
NO_SPONSORSHIP = (
    rf"\b{_NEG}(?: [a-z]+){{0,4}}? (?:sponsor|sponsoring|support|supporting|provide|providing"
    rf"|offer|offering)(?: [a-z0-9]+){{0,4}}? {_SPONSOR_OBJECT}\b",
    rf"\b{_NEG}(?: [a-z]+){{0,3}}? (?:sponsor|sponsoring)\b",
    r"\b(?:visa |immigration |work permit |employment visa |h1b )?sponsorship (?:is |will |would "
    r"|can )?(?:not|n t|no longer) (?:be )?(?:available|offered|provided|possible|supported"
    r"|considered)\b",
    r"\bsponsorship (?:is )?unavailable\b",
    r"\bno (?:visa |immigration |work permit )?sponsorship\b(?! (?:is |will be )?(?:needed"
    r"|required|necessary))",
    r"\bwithout (?:the need for |the need of |needing |requiring |requiring any |need for |any "
    r"|any need for )?(?:current or future |future |present or future )?(?:visa |immigration "
    r"|employer |company |work permit |employment visa |h1b )?sponsorship\b",
    r"\b(?:will|do|does|must|should) not (?:now or in the future |currently |now |presently )?"
    r"(?:require|need)(?: [a-z]+){0,3}? sponsorship\b",
    rf"\b(?:ne|n)(?: [a-z]+){{0,3}} (?:pas|plus) (?:en mesure de |la possibilite de "
    rf"|possible de )?(?:de )?{_FR_SPONSOR_VERB}(?: [a-z]+){{0,4}}? {_FR_SPONSOR_OBJECT}\b",
    rf"\b(?:ne|n) {_FR_SPONSOR_VERB} (?:pas|aucun|aucune|plus)(?: [a-z]+){{0,4}}? "
    rf"{_FR_SPONSOR_OBJECT}\b",
    r"\b(?:pas de|aucun|aucune|sans) (?:possibilite de )?(?:parrainage|sponsoring|sponsorisation)"
    r"(?: de| d un| pour| pour le| pour un)?(?: visa| visas| permis de travail)?\b"
    r"(?! (?:necessaire|requis|requise|besoin))",
    r"\b(?:parrainage|sponsoring|sponsorisation)(?: de| d un)?(?: visa| visas| permis de travail)?"
    r" (?:n est |ne sera |ne peut etre )?(?:pas|non) (?:possible|propose|proposee|disponible"
    r"|assure|assuree|pris en charge|prise en charge|envisageable)\b",
)
SPONSORSHIP = (
    r"\b(?:visa |immigration |work permit |employment visa )?sponsorship (?:is |will be |can be "
    r"|may be |are )?(?:available|offered|provided|possible|supported)\b",
    r"\b(?:we|we re|we are|company|employer) (?:can |will |do |are able to |are happy to |also "
    r"|gladly |happily )?(?:sponsor|sponsors|support)(?: [a-z]+){0,2}? (?:visa|visas"
    r"|work permit|work permits|visa applications)\b",
    r"\b(?:offer|offers|offering|provide|provides|providing|include|includes|including)"
    r"(?: [a-z]+){0,3}? (?:visa sponsorship|visa support|work permit support|immigration support"
    r"|sponsorship for (?:a |your )?(?:visa|work permit))\b",
    r"^(?:visa|immigration|work permit) (?:sponsorship|support)(?: (?:and|plus|\+) relocation"
    r"(?: support| package| assistance)?)?$",
    r"^relocation (?:and|plus|\+) (?:visa|immigration) (?:sponsorship|support)$",
    r"\b(?:parrainage|sponsoring|sponsorisation|accompagnement|prise en charge)(?: de| du| des"
    r"| pour| pour le| pour la| a la| au| dans les| dans vos)?(?: (?:demande|demandes|demarches"
    r"|procedure|procedures))?(?: de| d)?(?: visa| visas| permis de travail| titre de sejour"
    r"| immigration)(?: [a-z]+){0,2}? (?:possible|propose|proposee|disponible|assure|assuree"
    r"|inclus|incluse|offert|offerte)\b",
    r"\bnous (?:pouvons |proposons de |sommes en mesure de )?(?:parrainer|sponsoriser|accompagner"
    r"|prendre en charge|parrainons|sponsorisons|accompagnons|prenons en charge)(?: [a-z]+){0,4}?"
    r" (?:visa|visas|permis de travail|titre de sejour)\b",
)
_WORK_RIGHT = (
    r"(?:right to work|rights to work|right to live and work|authorized to work|authorised to work"
    r"|authorization to work|authorisation to work|work authorization|work authorisation"
    r"|eligible to work|eligibility to work|entitled to work|entitlement to work|permitted to work"
    r"|permission to work|legally able to work|legally work|work permit|work visa|autorise a"
    r" travailler|autorisee a travailler|autorises a travailler|autorisees a travailler"
    r"|autorisation de travail|autorisation de travailler|droit de travailler|droit au travail"
    r"|permis de travail|titre de sejour)"
)
WORK_PERMIT = (
    rf"\b{_WORK_RIGHT}(?: [a-z0-9]+){{0,4}}? (?:in|within|for|en|au|aux|dans|pour|sur le"
    rf" territoire)(?: (?:the|l|le|la|les|de|du|des))? (?P<place>{_PLACES})\b",
)
DEGREE = (
    r"\b(?:bachelor s degree|bachelors degree|bachelor degree|bachelor of|bachelor s in"
    r"|bachelors in|bachelor s or|bachelors or|master s degree|masters degree|master degree"
    r"|master of|master s in|masters in|master s or|masters or|msc|bsc|mba|phd|ph d|doctorate"
    r"|doctoral degree|university degree|college degree|academic degree|graduate degree"
    r"|undergraduate degree|engineering degree|degree in|degree from|degree level"
    r"|degree or equivalent|degree required|degree is required|(?<!to )a degree(?! of)"
    r"|relevant degree)\b",
    r"\b(?:bac ?\+ ?[2-8]|diplome d ingenieur|diplome d ecole d ingenieur|ecole d ingenieur"
    r"|diplome de niveau|diplome universitaire|diplome superieur|master 1|master 2|master en"
    r"|master de|master ou|doctorat|licence en|licence professionnelle|licence ou|niveau master"
    r"|niveau licence|dut|bts|grande ecole|ecole de commerce|formation superieure)\b",
)
CONTRACTOR = (
    r"\b(?:independent contractor|independent contractors|1099 (?:contractor|contract|position"
    r"|role|basis|employee|only)|on a 1099|w2 or 1099|w 2 or 1099|corp to corp|c2c"
    r"|contractor basis|contract basis|as a contractor|on a freelance basis|freelance basis"
    r"|auto entrepreneur|auto entrepreneurs|micro entrepreneur|statut d independant"
    r"|statut independant|statut de freelance|statut freelance|en tant qu independant"
    r"|en tant que freelance|en freelance|portage salarial|en portage)\b",
)
_MONTHS_SPAN = r"(?:[1-9]|1[0-9]|2[0-4]|three|six|nine|twelve|eighteen)"
FIXED_TERM = (
    rf"\b(?:fixed term|temporary contract|temporary position|temporary role|temporary assignment"
    rf"|temp to perm|maternity cover|maternity leave cover|parental leave cover"
    rf"|{_MONTHS_SPAN} months? (?:contract|fixed term|assignment|mission|engagement)"
    rf"|contract (?:of|for) {_MONTHS_SPAN} months)\b",
    r"\b(?:cdd|contrat a duree determinee|duree determinee|contrat de (?:[1-9]|1[0-9]|2[0-4])"
    r" mois|mission de (?:[1-9]|1[0-9]|2[0-4]) mois|mission d interim|(?<!the )interim"
    r"|remplacement (?:d un |de )?conge (?:maternite|parental)|(?:[1-9]|1[0-9]|2[0-4]) mois"
    r" renouvelable)\b",
)
UNPAID = (
    r"\b(?:unpaid (?:internship|internships|position|role|placement|opportunity|work|trial"
    r"|volunteer)|(?:role|position|internship|placement|opportunity|this) is unpaid"
    r"|volunteer (?:position|role|opportunity|basis)|on a volunteer basis|on a voluntary basis"
    r"|commission only|commission based only)\b",
)
UNPAID_FR = (
    r"\b(?:stage|poste|mission|emploi|travail|contrat)(?: [a-z]+){0,2}? (?:non|pas) remuner"
    r"(?:e|ee|es|ees)\b",
    r"\b(?:poste|mission|role) (?:de )?benevole\b",
)
CANDIDATE_FEE = (
    r"\b(?:you|candidates|candidate|applicants|applicant|trainees|participants) (?:will |must "
    r"|need to |have to |will need to |will have to |are required to |would need to "
    r"|may need to )?pay (?:(?:a |an |the "
    r"|your )?(?:[a-z]+ )?(?:fee|fees|deposit)|for (?:the |your )?(?:own )?(?:training|course"
    r"|certification|registration))\b",
    r"\b(?:training|registration|application|enrolment|enrollment|onboarding|processing"
    r"|certification|starter kit|administration|admin|background check) fees? (?:of|is|are"
    r"|applies|apply|required|payable|must be paid|to be paid)\b",
    r"\b(?:refundable|upfront|up front) (?:training |registration |application |enrolment "
    r"|enrollment |onboarding |starter kit |admin |administration )?(?:fee|fees)\b",
    r"\bfrais (?:de formation|d inscription|de dossier|de candidature|d adhesion"
    r"|de certification)(?: [a-z0-9]+){0,4}? (?:a la charge du (?:candidat|stagiaire"
    r"|participant)|payant|payants|s elevent|sont de|est de|obligatoire|obligatoires)\b",
    r"\b(?:formation|certification) payante\b",
    r"\ba la charge du candidat\b",
    r"\ble candidat (?:devra|doit) (?:payer|regler|financer)\b",
)

# A sentence that negates what a rule offers or requires gives no signal.
_NEGATED = re.compile(
    r"\b(?:not|no|never|cannot|without|unable|ne|n|pas|aucun|aucune|sans|non|jamais|t)\b"
)
_NOT_ASKED = re.compile(
    r"\b(?:not required|not needed|not necessary|not a requirement|no need|don t need"
    r"|do not need|doesn t need|does not need|pas besoin|pas necessaire|pas requis|pas requise"
    r"|pas obligatoire|pas exige|pas exigee|non requis|non requise|non necessaire)\b"
)
# Help to get a permit, or a degree that is only a plus, is not a requirement.
_HELPED = re.compile(
    r"\b(?:help|helps|helping|assist|assists|assisting|assistance|obtain|obtaining|support"
    r"|supports|supporting|guidance|aider|aidons|aidera|obtenir|obtention|accompagner"
    r"|accompagnons|accompagnement|demarches)\b"
)
_PREFERRED = re.compile(
    r"\b(?:a plus|is a plus|preferred|preferable|nice to have|bonus|ideally|souhaite|souhaitee"
    r"|apprecie|appreciee|un plus|idealement|serait un plus)\b"
)
_REASSURED = re.compile(r"\b(?:don t worry|do not worry|no worries|no problem|not a problem)\b")
_FEE_COVERED = re.compile(
    r"\b(?:free|waived|covered|reimbursed|refunded|we pay|we cover|company pays|paid by"
    r"|employer pays|gratuit|gratuite|offert|offerte|pris en charge|prise en charge|rembourse"
    r"|remboursee|rembourses)\b"
)


def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


_NO_SPONSORSHIP = _compile(NO_SPONSORSHIP)
_SPONSORSHIP = _compile(SPONSORSHIP)
_WORK_PERMIT = _compile(WORK_PERMIT)
_DEGREE = _compile(DEGREE)
_CONTRACTOR = _compile(CONTRACTOR)
_FIXED_TERM = _compile(FIXED_TERM)
_UNPAID = _compile(UNPAID)
_UNPAID_FR = _compile(UNPAID_FR)
_CANDIDATE_FEE = _compile(CANDIDATE_FEE)


def _any(patterns: tuple[re.Pattern[str], ...], said: str) -> bool:
    return any(p.search(said) for p in patterns)


def _sentence_signals(said: str) -> list[tuple[str, str | None]]:
    """The codes one sentence's words give, with a value for work_permit."""
    found: list[tuple[str, str | None]] = []
    negated = bool(_NEGATED.search(said))
    refused = _any(_NO_SPONSORSHIP, said) and not _REASSURED.search(said)
    if refused:
        found.append(("no_sponsorship", None))
    elif not negated and _any(_SPONSORSHIP, said):
        found.append(("sponsorship", None))
    if not _NOT_ASKED.search(said) and not _HELPED.search(said):
        for match in _WORK_PERMIT[0].finditer(said):
            found.append(("work_permit", _PLACE_OF[match.group("place")]))
    if not negated and not _PREFERRED.search(said) and _any(_DEGREE, said):
        found.append(("degree", None))
    if not negated and _any(_CONTRACTOR, said):
        found.append(("contractor", None))
    if not negated and _any(_FIXED_TERM, said):
        found.append(("fixed_term", None))
    if (not negated and _any(_UNPAID, said)) or _any(_UNPAID_FR, said):
        found.append(("unpaid", None))
    if (
        not _FEE_COVERED.search(said)
        and not _NEGATED.search(said.replace("non refundable", "refundable"))
        and _any(_CANDIDATE_FEE, said)
    ):
        found.append(("candidate_fee", None))
    return found


def read(text: str | None) -> list[Signal]:
    """The signals of a posting's text, each code with its first sentence (a work permit once
    per place), then its closing date when it states one."""
    signals: list[Signal] = []
    seen: set[tuple[str, str | None]] = set()
    for sentence in _sentences(text):
        if sentence.endswith("?"):
            continue  # a question asks, it states nothing
        for code, value in _sentence_signals(words(sentence)):
            if (code, value) not in seen:
                seen.add((code, value))
                signals.append(Signal(code, _quote(sentence), value))
    closes = closing(text)
    if closes is not None:
        signals.append(Signal("closing", closes[1], closes[0].isoformat()))
    return signals


# --- the closing date ---------------------------------------------------------------------------

_MONTH_NUMBER = {
    **dict.fromkeys(("january", "jan", "janvier", "janv"), 1),
    **dict.fromkeys(("february", "feb", "fevrier", "fevr"), 2),
    **dict.fromkeys(("march", "mar", "mars"), 3),
    **dict.fromkeys(("april", "apr", "avril", "avr"), 4),
    **dict.fromkeys(("may", "mai"), 5),
    **dict.fromkeys(("june", "jun", "juin"), 6),
    **dict.fromkeys(("july", "jul", "juillet", "juil"), 7),
    **dict.fromkeys(("august", "aug", "aout"), 8),
    **dict.fromkeys(("september", "sep", "sept", "septembre"), 9),
    **dict.fromkeys(("october", "oct", "octobre"), 10),
    **dict.fromkeys(("november", "nov", "novembre"), 11),
    **dict.fromkeys(("december", "dec", "decembre"), 12),
}
_MONTH = "(?:" + "|".join(sorted(_MONTH_NUMBER, key=len, reverse=True)) + ")"
_DAYS = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun"
    r"|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
)
_EN_MARKER = (
    r"(?:closing date|application deadline|deadline for applications|deadline to apply|deadline"
    r"|applications close|applications closing|closes|closing|apply by|apply before"
    r"|applications accepted until|accepting applications until|applications open until"
    r"|open until|submit your application by|submit by)"
)
_FR_MARKER = (
    r"(?:date limite de depot des candidatures|date limite de candidature|date limite de depot"
    r"|date limite|date de cloture des candidatures|date de cloture|cloture des candidatures"
    r"|candidatures ouvertes jusqu|candidatures acceptees jusqu|candidatures jusqu|postulez avant"
    r"|candidatez avant|postuler avant|envoyez votre candidature avant)"
)
_LEAD = rf"(?: (?:is|on|by|the|of|for|le|au|a|est|fixee|fixe|until|jusqu|{_DAYS}))*"
_DATE = (
    r" (?:(?P<iy>20\d\d) (?P<im>\d{1,2}) (?P<id>\d{1,2})"
    r"|(?P<na>\d{1,2}) (?P<nb>\d{1,2}) (?P<ny>20\d\d)"
    rf"|(?P<dd>\d{{1,2}})(?: ?(?:st|nd|rd|th|er))?(?: of)? (?P<dm>{_MONTH}) (?P<dy>20\d\d)"
    rf"|(?P<mm>{_MONTH}) (?P<md>\d{{1,2}})(?: ?(?:st|nd|rd|th))? (?P<my>20\d\d))\b"
)
_CLOSING = re.compile(rf"\b(?:(?P<en>{_EN_MARKER})|(?P<fr>{_FR_MARKER})){_LEAD}{_DATE}")


def _day(year: str, month: int, day: str) -> date | None:
    try:
        return date(int(year), month, int(day))
    except ValueError:
        return None


def _date_of(match: re.Match[str]) -> date | None:
    """The match's date; a numeric day and month in French order after a French marker, and
    after an English one only when the order cannot be mistaken (a part above 12, or both
    equal)."""
    if match.group("iy"):
        return _day(match.group("iy"), int(match.group("im")), match.group("id"))
    if match.group("dm"):
        return _day(match.group("dy"), _MONTH_NUMBER[match.group("dm")], match.group("dd"))
    if match.group("mm"):
        return _day(match.group("my"), _MONTH_NUMBER[match.group("mm")], match.group("md"))
    a, b = int(match.group("na")), int(match.group("nb"))
    if match.group("fr") or a == b or (a > 12 and b <= 12):
        day, month = a, b
    elif b > 12 and a <= 12:
        day, month = b, a
    else:
        return None
    return _day(match.group("ny"), month, str(day))


def closing(text: str | None) -> tuple[date, str] | None:
    """The closing date the text states and its sentence. None when it states none, when a
    date after a marker cannot be read without guessing, or when two markers give two dates."""
    found: tuple[date, str] | None = None
    for sentence in _sentences(text):
        for match in _CLOSING.finditer(words(sentence)):
            day = _date_of(match)
            if day is None:
                return None
            if found is not None and found[0] != day:
                return None
            if found is None:
                found = (day, _quote(sentence))
    return found


# --- the fingerprint ----------------------------------------------------------------------------


def fingerprint(text: str | None) -> int | None:
    """The 64-bit SimHash of the text's distinct word pairs, as a signed bigint, or None when
    the text has fewer than SHINGLES_MIN of them."""
    tokens = words(" ".join(_sentences(text))).split()
    shingles = {
        " ".join(tokens[i : i + SHINGLE_WORDS]) for i in range(len(tokens) - SHINGLE_WORDS + 1)
    }
    if len(shingles) < SHINGLES_MIN:
        return None
    hashes = (hashlib.blake2b(s.encode(), digest_size=8).digest().hex() for s in shingles)
    # Each bit's column of the hashes, most significant first: set when most shingles set it.
    columns = zip(*(format(int(h, 16), "064b") for h in hashes), strict=True)
    value = int("".join("1" if 2 * c.count("1") > len(shingles) else "0" for c in columns), 2)
    return value - (1 << 64) if value >= 1 << 63 else value


def same_text(a: int | None, b: int | None) -> bool:
    """Whether two fingerprints are at most SAME_BITS apart (never for a missing one)."""
    return a is not None and b is not None and ((a ^ b) & _MASK).bit_count() <= SAME_BITS


# --- what a store writes ------------------------------------------------------------------------


def stored(text: str | None) -> dict[str, Any]:
    """The columns a posting's text gives (migration 0038): its signals, its fingerprint and
    its closing date."""
    signals = read(text)
    closes = next((s.value for s in signals if s.code == "closing"), None)
    return {
        "signals": [s.as_json() for s in signals],
        "text_fingerprint": fingerprint(text),
        "closes_on": date.fromisoformat(closes) if closes else None,
    }
