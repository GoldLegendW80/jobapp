"""Is a posting still open: what one answer of its own address says (SPEC-jobs, "Liveness").

Pure functions, no network and no database: `judge(status, text, requested, final)` reads the
answer's status and its page as text and returns a `Finding`; app/jobs/liveness_check.py makes
the request and stores the finding on the posting.

- `closed`, the one verdict that moves a posting to closed by itself: the posting's own address
  answered 404 or 410, or one clause of its own page says, whole, that this posting is closed,
  naming the posting, the page asked for or the employer ("This job is no longer available.",
  "We are no longer accepting applications.", "Cette offre n'est plus disponible.", "Diese
  Stelle ist bereits besetzt.", "Error 404"). The address's own page: no redirect, or redirects
  on the same host that keep the posting's id (or its path, when it has none).
- `may_be_closed`, a proposal the person confirms with one click: such a clause on a page the
  address redirected to; a whole closure clause that names nothing ("Applications closed", "No
  longer accepting applications"), which a "Similar jobs" list may carry for another posting;
  closure words inside a longer clause that no time or condition word opens ("Apply before the
  position has been filled" is not one); a short badge ("Job expired", "Poste pourvu"); a
  listing page ("12 jobs found"); an address that now leads to another page.
- `unconfirmed`: any other status (401, 403, 429, 451, 5xx), a bot check, a sign in page, or too
  little text to tell (a page its script draws). Never a closure.
- `no_closure`: a page with text that says none of the above.

Matching runs on folded text (case, accents, typographic quotes and apostrophes, hidden and
lookalike characters), clause by clause: a line, cut at `.` and `!` (never `?`: "Not found?"),
then at commas, semicolons, colons, brackets and spaced dashes. The tiers, the closure sentences
in English, French, German, Dutch, Spanish and Polish, the guard of time and condition words and
the "closed-loop" guard follow career-ops's liveness-core.mjs (MIT, NOTICE).
"""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

CLOSED, MAY_BE_CLOSED, UNCONFIRMED, NO_CLOSURE = (
    "closed",
    "may_be_closed",
    "unconfirmed",
    "no_closure",
)
QUOTE_MAX = 300
# A page with fewer letters than this says too little to tell (a shell its script fills).
LETTERS_MIN = 200
# A bot check is a short page: on a long one the same words are the posting's own copy.
BOT_PAGE_MAX = 3000
# The words before closure words that a time or condition word may open (bounded in characters
# too, so a page of repeated matches stays linear).
CONDITION_WORDS, CONDITION_CHARS_BACK = 10, 200


@dataclass(frozen=True)
class Finding:
    verdict: str
    reason: str
    # The page's own sentence that says it, as the page writes it, at most QUOTE_MAX characters.
    sentence: str | None = None


_FOLD = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u02bc": "'",
        "\u2032": "'",
        "\u00b4": "'",
        "`": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2033": '"',
        "\u0141": "l",
        "\u0142": "l",
        "\u00f8": "o",
        "\u00d8": "o",
        "\u0111": "d",
        "\u0110": "d",
    }
)


def fold(text: str) -> str:
    """Case, accents, typographic quotes, hidden characters and spacing folded."""
    plain = unicodedata.normalize("NFKD", text.translate(_FOLD)).casefold()
    plain = "".join(
        c for c in plain if not unicodedata.combining(c) and unicodedata.category(c) != "Cf"
    )
    return re.sub(r"\s+", " ", plain).strip()


# --- the clauses of a page ---------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!\u2026\u3002])\s+")
_CLAUSE_CUT = re.compile(r"[,;:()\[\]{}|\u2022\u00b7\u00bb\u00ab<>]+|\s[-\u2013\u2014]+\s")


def _clause_text(raw: str) -> str:
    """A clause as the patterns read it: folded, every mark but letters, digits, apostrophes
    and inner hyphens turned to a space."""
    plain = re.sub(r"[^\w' -]+", " ", fold(raw))
    plain = re.sub(r"(?<![\w])[-']+|[-']+(?![\w])", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def sentences(text: str) -> list[tuple[str, list[str]]]:
    """Each sentence of the page as it is written, with its clauses as the patterns read them."""
    out = []
    for line in text.splitlines():
        for sentence in _SENTENCE_END.split(line):
            shown = " ".join(sentence.split()).strip()
            clauses = [c for c in (_clause_text(part) for part in _CLAUSE_CUT.split(shown)) if c]
            if clauses:
                out.append((shown, clauses))
    return out


def quote(sentence: str) -> str:
    shown = sentence.strip(" \"'\u201c\u201d\u00ab\u00bb")
    return shown if len(shown) <= QUOTE_MAX else shown[: QUOTE_MAX - 1].rstrip() + "\u2026"


# --- what a clause says, whole --------------------------------------------------------------------

# Words that open a clause without changing what it states: "Sorry, ...", "Leider ...".
_LEAD = (
    r"(?:(?:we're |we are |we're very |we are very |so |very )?sorry |oops |unfortunately |"
    r"but |however |please note |note |desole |nous sommes desoles |malheureusement |mais |"
    r"leider |helaas |lo sentimos |lamentablemente |niestety |oups |ups )*"
)
# What may follow a closure without changing it: "at this time", a date or a time (a digit).
_TAIL = (
    r"(?: (?:at this time|anymore|any more|for now|on (?:this|our) (?:site|website|page)|"
    r"sur (?:notre|ce) site|sur le site|"
    r"(?:on|at|since|as of|le|depuis|am|seit|el|desde|op|sinds|dnia) "
    r"[\w' ]{0,40}\d[\w' ]{0,40}))?"
)
_EN_NOUN = (
    r"(?:job|job posting|job listing|job offer|job opening|job ad|job advert|position|role|"
    r"posting|listing|vacancy|opening|opportunity|requisition|offer|advert)"
)
_EN_LOOKING = (
    r"(?: (?:you|you've|you have) (?:are |were |re )?(?:looking for|searching for|"
    r"trying to (?:view|reach|access|find|apply (?:for|to))|requested|applied (?:for|to)))?"
)
_EN_SUBJECT = rf"(?:this|the|that) {_EN_NOUN}{_EN_LOOKING}"
_EN_STATE = (
    r"(?:(?:is|was) (?:now )?(?:no longer (?:available|open|active|online|live|listed|posted|"
    r"accepting (?:applications|applicants|candidates))|not (?:available|open|active)"
    r"(?: (?:any ?more|at this time))?|unavailable|closed|filled|expired|inactive|archived|"
    r"unpublished|removed|deleted|not found)"
    r"|(?:has|have) (?:now )?(?:already )?been (?:filled|closed|removed|deleted|archived|"
    r"taken down|withdrawn|unpublished|cancelled|canceled)"
    r"|was (?:already )?(?:taken down|withdrawn|cancelled|canceled)"
    r"|has (?:now )?(?:expired|closed)"
    r"|(?:does not|doesn't|no longer) exists?"
    r"|(?:could not|couldn't|cannot|can't) be found)"
)
_FR_NOUN = (
    r"(?:offre|annonce|offre d'emploi|annonce d'emploi|offre de stage|offre d'alternance|"
    r"offre de poste|mission)"
)
_FR_SUBJECT = (
    rf"(?:(?:cette |l'|la |l )?{_FR_NOUN}(?: que vous (?:recherchez|cherchez|consultez|avez "
    r"demandee))?)"
)
_FR_STATE = (
    r"(?:n'est plus (?:disponible|en ligne|active|valable|d'actualite|accessible|ouverte|"
    r"visible|a pourvoir|consultable)|n'est pas disponible|n'existe plus|est introuvable|"
    r"(?:a (?:deja )?ete|est) (?:deja )?(?:pourvue|cloturee|close|fermee|desactivee|supprimee|"
    r"retiree|archivee|expiree|terminee|depubliee)|a expire)"
)
_DE_NOUN = (
    r"(?:stelle|stellenanzeige|stellenausschreibung|ausschreibung|anzeige|position|vakanz|"
    r"jobanzeige|stellenangebot|job)"
)
_DE_STATE = (
    r"(?:nicht mehr (?:verfugbar|aktiv|online|vakant|ausgeschrieben|offen|gultig)|"
    r"besetzt|vergeben|abgelaufen|geschlossen|deaktiviert|entfernt|archiviert)"
)
_NL_NOUN = r"(?:vacature|functie|positie|baan)"
_ES_NOUN = (
    r"(?:oferta|vacante|posicion|puesto|oferta de empleo|oferta de trabajo|empleo|oferta laboral)"
)
_ES_STATE = (
    r"(?:ya no esta (?:disponible|activa|activo|abierta|abierto|vigente|publicada|publicado)|"
    r"no esta disponible|ya no existe|"
    r"(?:ha sido|fue) (?:cerrada|cerrado|cubierta|cubierto|eliminada|eliminado|retirada|"
    r"retirado|desactivada|desactivado)|"
    r"ha (?:caducado|expirado|finalizado|vencido)|"
    r"esta (?:cerrada|cerrado|cubierta|cubierto|caducada|expirada|vencida|finalizada|inactiva))"
)
_ES_NOT_TAKING = (
    r"(?:ya )?no (?:se )?(?:aceptan|admiten|reciben) (?:mas )?(?:candidaturas|solicitudes|"
    r"postulaciones|aplicaciones)"
)
_EN_NOT_TAKING = (
    r"(?:no longer|not) (?:accepting|taking|receiving|considering) (?:any )?(?:new )?"
    r"(?:applications|applicants|candidates)"
)
_EN_FOR = rf" for (?:this|the) {_EN_NOUN}"

_CLOSURES = (
    # English
    rf"{_EN_SUBJECT} {_EN_STATE}",
    rf"(?:{_EN_NOUN}|page) not found",
    r"(?:the )?page you (?:are |were |re )?(?:looking for|requested|trying to (?:reach|access|"
    r"view)) (?:does not exist|doesn't exist|no longer exists|could not be found|cannot be "
    r"found|can't be found|was not found|is not available|is no longer available|is "
    r"unavailable)",
    # The number with its word: a bare "404" is a phone number's area code as often.
    r"error 404(?: error)?(?: page)?(?: not found)?|404 (?:error(?: page)?|page)(?: not found)?"
    r"|404 not found",
    rf"(?:we are |we're |we have |we've |this employer is |the employer is ){_EN_NOT_TAKING}"
    rf"(?:{_EN_FOR})?",
    rf"{_EN_NOT_TAKING}{_EN_FOR}",
    rf"(?:the )?applications? {_EN_FOR.strip()} (?:are|is|have|has) (?:now )?(?:been )?closed",
    rf"applications? (?:now )?closed{_EN_FOR}",
    r"(?:the )?(?:application|applications|recruitment|hiring) (?:period|window|process) "
    r"for (?:this|the) (?:job|position|role) (?:is (?:now )?(?:closed|over)|has "
    r"(?:now )?(?:closed|ended|expired))",
    rf"you can't view this {_EN_NOUN} because it's (?:no longer|not) available",
    # French
    rf"{_FR_SUBJECT} {_FR_STATE}",
    r"(?:ce |le )?poste(?: que vous recherchez)? (?:n'est plus (?:disponible|a pourvoir|"
    r"ouvert|vacant|d'actualite)|(?:a (?:deja )?ete|est) (?:deja )?(?:pourvu|clos|ferme|supprime))",
    r"(?:les )?candidatures (?:sont )?(?:closes|cloturees|fermees|terminees)"
    r" pour (?:cette offre|ce poste)",
    r"(?:le )?recrutement (?:est )?(?:termine|cloture|clos|ferme) pour (?:cette offre|ce poste)",
    r"(?:nous )?n'acceptons plus (?:de )?candidatures(?: pour (?:cette offre|ce poste))?",
    r"(?:cette offre|ce poste|l'offre) n'accepte plus (?:de )?candidatures",
    r"(?:la )?page (?:que vous (?:recherchez|cherchez) |demandee )?(?:n'existe pas|n'existe "
    r"plus|est introuvable)",
    r"(?:page|offre|annonce) introuvable",
    # German
    rf"(?:diese|die) {_DE_NOUN} (?:ist|wurde) (?:leider )?(?:bereits |schon )?{_DE_STATE}",
    rf"ist (?:diese|die) {_DE_NOUN} (?:leider )?(?:bereits |schon )?{_DE_STATE}",
    rf"(?:diese|die) {_DE_NOUN} existiert nicht mehr",
    r"wir nehmen (?:leider )?keine (?:weiteren )?bewerbungen mehr (?:an|entgegen)"
    r"(?: fur diese stelle)?",
    r"es werden (?:leider )?keine (?:weiteren )?bewerbungen mehr angenommen fur diese stelle",
    rf"(?:{_DE_NOUN}|seite) nicht gefunden",
    # Dutch
    rf"(?:deze|de) {_NL_NOUN} is (?:niet meer (?:gepubliceerd|beschikbaar|actief|online|open|"
    r"geldig)|(?:al )?(?:gesloten|vervuld|verlopen|ingevuld|vervallen|verwijderd))",
    rf"(?:deze|de) {_NL_NOUN} bestaat niet meer",
    r"(?:we|wij) (?:accepteren|nemen) geen (?:nieuwe )?sollicitaties meer(?: aan)?",
    # Spanish
    rf"(?:esta|la|este|el) {_ES_NOUN} {_ES_STATE}",
    rf"{_ES_NOT_TAKING} para (?:esta oferta|este puesto|esta vacante)",
    r"(?:la )?pagina (?:que buscas |solicitada )?no existe",
    r"(?:pagina|oferta) no encontrada",
    # Polish
    r"ogloszenie (?:juz )?(?:wygaslo|jest nieaktualne|zostalo zakonczone|nie jest juz "
    r"aktualne|jest archiwalne)",
    r"(?:to |ta )?(?:ogloszenie|oferta) (?:jest )?(?:juz )?(?:nieaktualn[ae]|archiwaln[ae]|"
    r"wygasl[ao])",
    r"pracodawca zakonczyl zbieranie zgloszen(?: na to stanowisko)?",
    r"rekrutacja na to stanowisko (?:zostala )?(?:zakonczona|zamknieta)",
    r"rekrutacja (?:zostala )?(?:zakonczona|zamknieta) na to stanowisko",
    r"(?:oferta|strona|ogloszenie) nie (?:zostala|zostalo) znalezion[ao]",
)
_CLOSURE = re.compile(rf"{_LEAD}(?:{'|'.join(_CLOSURES)}){_TAIL}")

# The same clauses naming nothing: on the posting's own page they are its badge as often as the
# badge of another posting in a "Similar jobs" list, so whole, they only propose.
_UNSCOPED = (
    # English
    _EN_NOT_TAKING,
    r"(?:the )?applications? (?:are|is|have|has) (?:now )?(?:been )?closed",
    r"applications? (?:now )?closed",
    r"(?:the )?(?:application|applications|recruitment|hiring) (?:period|window|process) "
    r"(?:is (?:now )?(?:closed|over)|has (?:now )?(?:closed|ended|expired))",
    r"(?:the )?(?:application|closing) (?:deadline|date) (?:has )?(?:passed|expired)",
    # French
    r"(?:les )?candidatures (?:sont )?(?:closes|cloturees|fermees|terminees)",
    r"(?:le )?recrutement (?:est )?(?:termine|cloture|clos|ferme)",
    # German
    r"es werden (?:leider )?keine (?:weiteren )?bewerbungen mehr angenommen",
    r"(?:die )?bewerbungsfrist ist (?:abgelaufen|beendet|vorbei)",
    # Dutch
    r"(?:de )?sollicitatietermijn is (?:verstreken|verlopen|gesloten)",
    r"(?:sollicitaties|reacties) (?:zijn )?gesloten",
    # Spanish
    _ES_NOT_TAKING,
    r"oferta cerrada el \d{1,2} \d{1,2} \d{2,4}",
    # Polish
    r"kandydaci nie sa juz akceptowani",
    r"rekrutacja (?:zostala )?(?:zakonczona|zamknieta)",
)
_UNSCOPED_CLOSURE = re.compile(rf"{_LEAD}(?:{'|'.join(_UNSCOPED)}){_TAIL}")


# --- closure words inside a longer clause, and badges: proposals only ----------------------------

_WORDS = (
    # English
    r"\bno longer (?:available|open|active|online|listed|accepting)\b",
    r"\b(?:job|position|role|posting|listing|vacancy|opening|offer|requisition) "
    r"(?:has been |was |is |has )?(?:filled|closed|expired|removed)\b(?!-)"
    r"(?! (?:on a rolling|as soon|immediately|quickly|asap)\b)",
    r"\bapplications? (?:are |is |have been |has been |have |were )?(?:now )?closed\b(?!-)",
    r"\b(?:job|posting|listing|position) not found\b",
    r"\b(?:job expired|expired job)\b",
    # French
    r"\bn'est plus (?:disponible|en ligne|active|a pourvoir|d'actualite)\b",
    r"\b(?:offre|annonce|poste) (?:(?:a (?:deja )?ete|est) )?(?:deja )?(?:pourvue?|expiree?|"
    r"cloturee?|close|fermee?|desactivee?|terminee?)\b",
    r"\bcandidatures (?:sont )?(?:closes|cloturees|fermees)\b",
    r"\brecrutement (?:est )?(?:termine|cloture|clos)\b",
    # German
    r"\bnicht mehr (?:verfugbar|aktiv|online|ausgeschrieben)\b",
    r"\b(?:bereits|schon) besetzt\b",
    r"\b(?:stelle|anzeige|stellenanzeige) (?:ist )?(?:abgelaufen|besetzt|vergeben)\b",
    r"\bkeine bewerbungen mehr\b",
    # Dutch
    r"\bniet meer (?:gepubliceerd|beschikbaar|actief)\b",
    r"\bvacature (?:is )?(?:gesloten|vervuld|verlopen)\b",
    r"\bgeen sollicitaties meer\b",
    # Spanish
    r"\bya no esta (?:disponible|activa|abierta)\b",
    r"\boferta (?:cerrada|caducada|expirada|finalizada)\b",
    r"\bno (?:se )?aceptan (?:mas )?(?:candidaturas|solicitudes|postulaciones)\b",
    # Polish
    r"\bogloszenie wygaslo\b",
    r"\boferta (?:jest )?(?:nieaktualna|archiwalna)\b",
    r"\bzakonczyl zbieranie zgloszen\b",
    r"\brekrutacja (?:zostala )?zakonczona\b",
    # A listing page in place of the posting.
    r"\b\d+ (?:jobs?|positions|openings|offres|postes|emplois|stellen|stellenangebote|"
    r"vacatures|ofertas|empleos) (?:found|trouvees?|gefunden|gevonden|encontrad[ao]s)\b",
    r"\bsearch for jobs page is loaded\b",
)
_CLOSURE_WORDS = re.compile("|".join(_WORDS))
# A time or condition word in the words before a match: the clause says how the posting will
# end, not that it has ("until the position has been filled", "une fois l'offre pourvue").
_CONDITION = re.compile(
    r"\b(?:until|till|once|when|whenever|after|before|unless|if|whether|as soon as|in case|"
    r"by the time|jusqu'a|jusqu'au|jusqu'a ce que|des que|lorsque|lorsqu|quand|si|s'il|avant|"
    r"apres|une fois|tant que|sauf si|au cas ou|bis|sobald|wenn|falls|nachdem|bevor|solange|"
    r"tot|totdat|zodra|wanneer|als|nadat|voordat|indien|hasta|cuando|antes|despues|una vez|"
    r"mientras|dopoki|gdy|kiedy|jesli|jezeli|zanim)\b"
)


def _stated(clause: str) -> bool:
    """Closure words in the clause that no time or condition word opens."""
    for match in _CLOSURE_WORDS.finditer(clause):
        start = match.start()
        before = clause[max(0, start - CONDITION_CHARS_BACK) : start].split()[-CONDITION_WORDS:]
        if not _CONDITION.search(" ".join(before)):
            return True
    return False


# --- what is not the posting --------------------------------------------------------------------

_BOT_CHECK = re.compile(
    r"\b(?:just a moment|checking your browser|performing security verification|"
    r"verify (?:that )?you are (?:a |not a )?(?:human|robot)|are you a robot|are you human|"
    r"enable javascript and cookies to continue|attention required|ray id|cf-ray|"
    r"please complete the security check|access denied|verifiez que vous etes (?:un )?humain|"
    r"etes-vous un robot|acces refuse)\b"
)
_SIGN_IN_SEGMENTS = frozenset(
    {
        "login",
        "log-in",
        "logon",
        "signin",
        "sign-in",
        "sign_in",
        "authwall",
        "auth",
        "sso",
        "uas",
        "connexion",
        "anmelden",
        "inloggen",
        "iniciar-sesion",
        "logowanie",
    }
)
_SIGN_IN_HOSTS = ("login.", "auth.", "sso.", "accounts.", "signin.")
_EXPIRED_ADDRESS = re.compile(r"[?&]error=true(?:&|#|$)", re.IGNORECASE)
# A posting's id in its address: a UUID (Lever, Ashby) or five digits or more (Greenhouse,
# Workday, SmartRecruiters).
_ID_TOKEN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{5,}")


def _host(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.casefold().rstrip(".").removeprefix("www.")


def own_page(requested: str, final: str) -> bool:
    """`final` is still the posting's own page: the same host, and the posting's id kept (its
    path, when the address holds no id)."""
    if not final or final == requested:
        return True
    if _host(requested) != _host(final):
        return False
    try:
        asked, landed = urlsplit(requested), urlsplit(final)
    except ValueError:
        return False
    ids = _ID_TOKEN.findall(f"{asked.path}?{asked.query}".casefold())
    if ids:
        return ids[-1] in f"{landed.path}?{landed.query}".casefold()
    return asked.path.rstrip("/").casefold() == landed.path.rstrip("/").casefold()


def _sign_in(final: str) -> bool:
    try:
        parts = urlsplit(final)
    except ValueError:
        return False
    host = (parts.hostname or "").casefold()
    segments = {s.casefold() for s in parts.path.split("/") if s}
    return bool(segments & _SIGN_IN_SEGMENTS) or host.startswith(_SIGN_IN_HOSTS)


def judge(status: int, text: str, requested: str, final: str) -> Finding:
    """What one answer of the posting's address says (module docstring)."""
    own = own_page(requested, final)
    if status in (404, 410):
        return Finding(CLOSED if own else MAY_BE_CLOSED, f"http_{status}" if own else "redirected")
    if status == 429:
        return Finding(UNCONFIRMED, "rate_limited")
    if status in (401, 403, 407, 451):
        return Finding(UNCONFIRMED, "refused")
    if status >= 500:
        return Finding(UNCONFIRMED, "server_error")
    if not 200 <= status < 300:
        return Finding(UNCONFIRMED, "unexpected_answer")
    if final != requested and _sign_in(final):
        return Finding(UNCONFIRMED, "sign_in")
    found = sentences(text)
    letters = sum(ch.isalpha() for ch in text)
    if letters <= BOT_PAGE_MAX and _BOT_CHECK.search(fold(text)):
        return Finding(UNCONFIRMED, "bot_check")
    for shown, clauses in found:
        if any(_CLOSURE.fullmatch(clause) for clause in clauses):
            return Finding(CLOSED if own else MAY_BE_CLOSED, "page_says", quote(shown))
    if not own and _EXPIRED_ADDRESS.search(final):
        return Finding(MAY_BE_CLOSED, "redirected")
    for shown, clauses in found:
        if any(_UNSCOPED_CLOSURE.fullmatch(clause) or _stated(clause) for clause in clauses):
            return Finding(MAY_BE_CLOSED, "page_hints", quote(shown))
    if not own:
        return Finding(MAY_BE_CLOSED, "redirected")
    if letters < LETTERS_MIN:
        return Finding(UNCONFIRMED, "unreadable")
    return Finding(NO_CLOSURE, "none")
