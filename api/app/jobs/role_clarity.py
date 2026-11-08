"""What a posting says about the role, read by code, no model (SPEC-jobs.md, "Assess this
posting"): six facts a candidate needs before spending an interview on it. Ported from
career-ops's role-clarity.mjs (MIT, credited in NOTICE), with French wording added.

Each point is `present`, `partial` or `missing`, with at most two quotes, each an exact piece
of the posting (at most QUOTE_MAX characters). `present` only means the posting holds wording
of that kind. When MISSING_THRESHOLD points or more are missing, `questions` holds one question
per point that is not present, to ask before committing to an interview.

The patterns know English and French only. A text that reads as neither (`read` False) gets no
point and no question: six missing points there would be the matcher's blindness, not the
posting's. The posting is data: it is only matched, never acted on.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["present", "partial", "missing"]

MISSING_THRESHOLD = 3
QUOTE_MAX = 240
TEXT_MAX = 60_000
# A text is read when this share of its words, and at least LANGUAGE_MIN_HITS of them, are
# common English words, or common French ones.
LANGUAGE_SHARE = 0.06
LANGUAGE_MIN_HITS = 4


@dataclass(frozen=True)
class Point:
    id: str
    label: str
    question: str


POINTS: tuple[Point, ...] = (
    Point(
        "supervisor",
        "Who the role reports to, and in which team",
        "Who would this role report to, and in which department or team?",
    ),
    Point(
        "scope",
        "What the role owns",
        "What does this role own, and what is out of scope or owned by someone else?",
    ),
    Point(
        "deliverables",
        "What it delivers, and in what form",
        "What are the first concrete deliverables, in what form, and by when?",
    ),
    Point(
        "success",
        "How success is measured in the first months",
        "How will success be measured in the first three to six months, and who judges it?",
    ),
    Point(
        "coordination",
        "Who it works with",
        "Which teams or people would this role work with day to day?",
    ),
    Point(
        "cadence",
        "How often it delivers",
        "How often does the role deliver, and which deadlines are already fixed?",
    ),
)


@dataclass(frozen=True)
class Found:
    status: Status
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleClarity:
    read: bool
    points: dict[str, Found]
    questions: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "read": self.read,
            "points": [
                {
                    "id": point.id,
                    "label": point.label,
                    "status": self.points[point.id].status,
                    "evidence": list(self.points[point.id].evidence),
                }
                for point in POINTS
                if point.id in self.points
            ],
            "questions": list(self.questions),
        }


_I = re.IGNORECASE

# --- language ------------------------------------------------------------------------------------

_EN_WORDS = frozenset(
    ["the", "and", "you", "your", "with", "for", "our", "will", "are", "this", "to", "of", "we"]
)
# Words French has and the neighbouring languages lack ("du" is German, "que" Spanish).
_FR_WORDS = frozenset(
    [
        "et",
        "vous",
        "votre",
        "vos",
        "nous",
        "les",
        "des",
        "une",
        "avec",
        "pour",
        "est",
        "dans",
        "sur",
        "au",
        "aux",
    ]
)
_WORD = re.compile(r"[a-z]+", _I)


def _readable(folded: str) -> bool:
    words = [word.lower() for word in _WORD.findall(folded)]
    if not words:
        return False
    for known in (_EN_WORDS, _FR_WORDS):
        hits = sum(1 for word in words if word in known)
        if hits >= LANGUAGE_MIN_HITS and hits >= LANGUAGE_SHARE * len(words):
            return True
    return False


# --- text ----------------------------------------------------------------------------------------

_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u00a0": " "})


def _fold(text: str) -> str:
    """The text with accents dropped and typographic apostrophes straightened, one character for
    one, so a match in it is a match at the same place in the original."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in text.translate(_APOSTROPHES))


@dataclass(frozen=True)
class _Segment:
    text: str
    folded: str


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _segments(text: str) -> list[_Segment]:
    out: list[_Segment] = []
    for line in text.splitlines():
        for part in _SENTENCE_END.split(line):
            piece = part.strip()
            if piece:
                out.append(_Segment(piece, _fold(piece)))
    return out


def _quote(segment: _Segment, focus: re.Pattern[str] | None) -> str:
    text = segment.text
    if len(text) <= QUOTE_MAX:
        return text
    found = focus.search(segment.folded) if focus is not None else None
    start = 0
    if found is not None:
        room = max(0, QUOTE_MAX - min(len(found.group(0)), QUOTE_MAX))
        start = min(max(0, found.start() - room // 2), len(text) - QUOTE_MAX)
    return text[start : start + QUOTE_MAX]


def _first(
    segments: list[_Segment],
    test: Callable[[str], bool],
    limit: int,
    focus: re.Pattern[str] | None = None,
) -> list[str]:
    out: list[str] = []
    for segment in segments:
        if test(segment.folded):
            out.append(_quote(segment, focus))
            if len(out) >= limit:
                break
    return out


def _has(pattern: re.Pattern[str]) -> Callable[[str], bool]:
    return lambda text: pattern.search(text) is not None


# --- supervisor ----------------------------------------------------------------------------------

_REPORTS_TO = re.compile(
    r"(?<![a-z0-9])(?:reports?|reporting)\s+(?:directly\s+)?to[*_]*"
    r"(?:\s*[:\u2013\u2014][*_]*\s*|\s+-\s*|\s+)[*_]*(?:(?:the|our|a|an)\s+)?"
    r"(?!(?:the|our|a|an|n/a|tb[dca]|none|unknown)(?![a-z0-9]))[a-z][\w&/'-]*"
    r"|\brattache(?:e|\(e\)|[.\u00b7]e)?s?\s+(?:directement\s+|hierarchiquement\s+)?(?:a|au|aux)\s+[a-z']"
    r"|\bsous\s+la\s+(?:responsabilite|direction|supervision)\s+(?:directe\s+)?d(?:e|u|es|')\s*[a-z]"
    r"|\breporting\s+(?:direct\s+)?(?:a|au|aupres\s+d[eu']?)\s+[a-z']"
    r"|\bvous\s+reporte(?:z|rez)\s+(?:directement\s+)?(?:a|au|aupres)\b",
    _I,
)
_NAMED_TEAM = re.compile(
    r"\b(?:in|within|of|for|from)\s+(?:the\s+)?[\w&\s-]{2,40}?\b"
    r"(?:department|division|team|group|office|unit)\b"
    r"|\b(?:department|division)\s+of\s+[a-z]\w+"
    r"|\bau\s+sein\s+d(?:e|u|es|')\s*(?:la\s+|l')?"
    r"(?:equipe|direction|departement|pole|service|division|squad)\b",
    _I,
)
_NAMED_TEAM_CASED = re.compile(
    r"\b[A-Z][\w&-]+\s+(?:Department|Division|Team|Group|Office|Unit)\b"
    r"|\b(?:[Ee]quipe|[Dd]epartement|[Pp]ole|[Ss]ervice|[Dd]irection)\s+[A-Z][\w&-]+"
)
_BOSS_WORD = re.compile(
    r"\b(?:supervisor|line\s+manager|manager|director|head\s+of|team\s+lead|lead|"
    r"responsable|directeur|directrice|chef\s+d'equipe|cto|vp)\b",
    _I,
)
_BOSS_CONTEXT = re.compile(
    r"\b(?:under|with|alongside|led\s+by|guidance\s+of|direction\s+of|join(?:ing)?|"
    r"avec|aupres|aux\s+cotes|encadre(?:e|s)?|accompagne(?:e|s)?)\b",
    _I,
)
_JOIN_TEAM = re.compile(
    r"\b(?:join|within|part\s+of)\s+(?:the|our)\s+[\w&\s-]{2,40}\b"
    r"(?:department|division|team|group|office|unit)\b"
    r"|\b(?:rejoindre|rejoignez|rejoindrez|integrer|integrez|integrerez)\s+"
    r"(?:l'|la\s+|le\s+|notre\s+|nos\s+)?(?:equipe|direction|departement|pole|service)\b",
    _I,
)


def _named_team(text: str) -> bool:
    return _NAMED_TEAM.search(text) is not None or _NAMED_TEAM_CASED.search(text) is not None


def _supervisor(segments: list[_Segment]) -> Found:
    strong = _first(segments, _has(_REPORTS_TO), 1, _REPORTS_TO)
    if strong:
        team = _first(segments, _named_team, 1, _NAMED_TEAM)
        if team:
            return Found("present", tuple(dict.fromkeys(strong + team)))
        return Found("partial", tuple(strong))
    weak = _first(
        segments,
        lambda s: (
            (_BOSS_WORD.search(s) is not None and _BOSS_CONTEXT.search(s) is not None)
            or _JOIN_TEAM.search(s) is not None
        ),
        2,
        _BOSS_WORD,
    )
    return Found("partial", tuple(weak)) if weak else Found("missing")


# --- scope ---------------------------------------------------------------------------------------

_OWNED = re.compile(
    r"\b(?:responsible\s+for|accountable\s+for|you\s+will\s+(?:own|lead|manage|run|build|design|"
    r"develop|deliver|drive|oversee|create|maintain|write|plan)|will\s+(?:own|lead|manage|oversee)|"
    r"own(?:ing)?\s+the|lead(?:ing)?\s+the|owns?\s+)\b"
    r"|\bvous\s+(?:serez|etes)\s+(?:responsable|en\s+charge|garant|garante)\b"
    r"|\b(?:vous\s+aurez|aura)\s+(?:la\s+)?(?:charge|responsabilite)\b"
    r"|\bvous\s+(?:pilote(?:z|rez)|gere(?:z|rez)|managez|managerez|encadre(?:z|rez)|"
    r"conce(?:vez|vrez)|developpe(?:z|rez)|dirige(?:z|rez)|construi(?:sez|rez)|porte(?:z|rez)|"
    r"prenez\s+en\s+charge|prendrez\s+en\s+charge)\b",
    _I,
)
_DUTIES_HEADING = re.compile(
    r"^(?:#+\s*)?(?:key\s+|main\s+|core\s+)?(?:responsibilities|duties|what\s+you'?ll\s+do|"
    r"what\s+you\s+will\s+do|the\s+role|scope(?:\s+of\s+work)?|about\s+the\s+role|"
    r"(?:vos\s+|les\s+)?missions?(?:\s+principales)?|(?:vos\s+)?responsabilites|le\s+poste|"
    r"votre\s+role|ce\s+que\s+vous\s+ferez|descriptif\s+du\s+poste)\s*:?\s*$",
    _I,
)


def _scope(segments: list[_Segment]) -> Found:
    owned = _first(segments, _has(_OWNED), 3, _OWNED)
    if len(owned) >= 2:
        return Found("present", tuple(owned[:2]))
    if owned:
        return Found("partial", tuple(owned))
    for at, segment in enumerate(segments):
        if _DUTIES_HEADING.search(segment.folded):
            # A list of duties under a heading, none naming what the person owns.
            body = [_quote(s, None) for s in segments[at + 1 : at + 2]]
            return Found("partial", tuple([_quote(segment, None), *body]))
    return Found("missing")


# --- deliverables --------------------------------------------------------------------------------

_DELIVERABLE = re.compile(
    r"\b(?:deliverables?|outputs?|you\s+will\s+(?:produce|deliver|publish|write|prepare|ship|"
    r"launch|create|build|draft)|will\s+(?:produce|deliver|publish|ship|launch))\b"
    r"|\b(?:livrables?|vous\s+(?:produi(?:sez|rez)|livre(?:z|rez)|redige(?:z|rez)|"
    r"realise(?:z|rez)|prepare(?:z|rez)|publie(?:z|rez)|concevrez|construirez|"
    r"mettrez\s+en\s+place))\b",
    _I,
)
_FORM = re.compile(
    r"\b(?:reports?|dashboards?|curricul(?:um|a)|courses?|documents?|documentation|plans?|"
    r"roadmaps?|modules?|manuals?|guides?|presentations?|policies|policy|prototypes?|releases?|"
    r"pipelines?|datasets?|playbooks?|runbooks?|proposals?|specs?|specifications?|"
    r"training\s+materials?|workshops?|handbooks?|"
    r"rapports?|tableaux?\s+de\s+bord|feuilles?\s+de\s+route|cahiers?\s+des\s+charges|"
    r"procedures?|formations?|politiques?)\b",
    _I,
)


def _deliverables(segments: list[_Segment]) -> Found:
    both = _first(
        segments, lambda s: _DELIVERABLE.search(s) is not None and _FORM.search(s) is not None, 2
    )
    if both:
        return Found("present", tuple(both))
    word = _first(segments, _has(_DELIVERABLE), 2, _DELIVERABLE)
    return Found("partial", tuple(word)) if word else Found("missing")


# --- success -------------------------------------------------------------------------------------

_MEASURE = re.compile(
    r"\b(?:success\s+(?:is|will\s+be|looks\s+like|means|measure)|measured\s+(?:by|on|against)|"
    r"kpis?|okrs?|metrics?|targets?|quota|success\s+criteria|performance\s+indicators?)\b"
    r"|\b(?:indicateurs?|objectifs?\s+(?:chiffres|mesurables|quantitatifs)|"
    r"(?:le\s+)?succes\s+(?:sera|se\s+mesure(?:ra)?|du\s+poste)|"
    r"mesure(?:e|s|es)?\s+(?:par|sur|a\s+travers)|resultats\s+attendus|"
    r"criteres\s+de\s+(?:succes|reussite))\b",
    _I,
)
_HORIZON = re.compile(
    r"\b(?:first|initial)\s+(?:30|60|90|three|six|3|6)\b"
    r"|\bwithin\s+(?:the\s+first\s+)?(?:\d+|three|six|a\s+few)\s+(?:days|weeks|months)\b"
    r"|\b(?:30|60|90)[-\s]day\b"
    r"|\bby\s+the\s+end\s+of\s+(?:the\s+)?(?:first\s+)?(?:quarter|term|semester|\d+\s+months)\b"
    r"|\bfirst\s+(?:quarter|year|term|semester)\b"
    r"|\b(?:\d+|trois|six)\s+premiers\s+mois\b|\bpremiers\s+mois\b"
    r"|\bdans\s+les\s+(?:\d+|trois|six)\s+(?:premiers\s+)?mois\b"
    r"|\b(?:a|d'ici)\s+(?:\d+|trois|six)\s+mois\b"
    r"|\bpremier\s+trimestre\b|\bpremiere\s+annee\b",
    _I,
)


def _success(segments: list[_Segment]) -> Found:
    # Present only when one sentence ties a measure to the first months.
    linked = _first(
        segments, lambda s: _MEASURE.search(s) is not None and _HORIZON.search(s) is not None, 1
    )
    if linked:
        return Found("present", tuple(linked))
    either = _first(segments, _has(_MEASURE), 1, _MEASURE) + _first(
        segments, _has(_HORIZON), 1, _HORIZON
    )
    return Found("partial", tuple(dict.fromkeys(either))) if either else Found("missing")


# --- coordination --------------------------------------------------------------------------------

_WORKS_WITH = re.compile(
    r"\b(?:work(?:s|ing)?\s+(?:closely\s+|cross-functionally\s+)?with|"
    r"partner(?:s|ing)?\s+with|collaborat(?:e|es|ing)\s+with|liais(?:e|es|ing)\s+with|"
    r"coordinat(?:e|es|ing)\s+with|cross-functional)\b"
    r"|\ben\s+(?:etroite\s+)?(?:collaboration|lien|interaction|relation|coordination)\s+"
    r"(?:etroite\s+)?avec\b"
    r"|\b(?:travaille(?:z|rez|r)|collabore(?:z|rez|r)|interagi(?:ssez|rez|r))\s+"
    r"(?:en\s+etroite\s+collaboration\s+|etroitement\s+|au\s+quotidien\s+)?avec\b"
    r"|\btransverse\b",
    _I,
)
_GROUP = re.compile(
    r"\b(?:engineering|engineers|product|design(?:ers)?|finance|legal|hr|human\s+resources|"
    r"marketing|sales|operations|support|data|security|vendors?|suppliers?|instructors?|"
    r"faculty|executives?|leadership|customers?|clients?|procurement|regional\s+managers?|"
    r"account\s+managers?|recruiters?|trainers?|developers?|analysts?|nurses?|teachers?|"
    r"equipes?|produit|ingenierie|ingenieurs|developpeurs|juridique|rh|ressources\s+humaines|"
    r"commerciaux|commerciales?|ventes|securite|direction|metiers?|fournisseurs|partenaires|"
    r"achats|logistique)\b",
    _I,
)


def _names_a_group(text: str) -> bool:
    found = _WORKS_WITH.search(text)
    return found is not None and _GROUP.search(text, found.start(), found.end() + 80) is not None


def _coordination(segments: list[_Segment]) -> Found:
    named = _first(segments, _names_a_group, 2, _WORKS_WITH)
    if named:
        return Found("present", tuple(named))
    verb = _first(segments, _has(_WORKS_WITH), 2, _WORKS_WITH)
    return Found("partial", tuple(verb)) if verb else Found("missing")


# --- cadence -------------------------------------------------------------------------------------

_CADENCE = re.compile(
    r"\b(?:daily|weekly|bi-?weekly|fortnightly|monthly|quarterly|annual(?:ly)?|sprints?|"
    r"release\s+cycles?|each\s+(?:week|month|quarter|term|semester)|"
    r"every\s+(?:\d+|two|three|other)?\s*(?:days?|weeks?|months?|quarters?)|"
    r"per\s+(?:week|month|quarter)|on\s+a\s+(?:daily|weekly|monthly|quarterly)\s+basis)\b"
    r"|\b(?:quotidien(?:ne)?s?|hebdomadaires?|bimensuel(?:le)?s?|mensuel(?:le)?s?|"
    r"trimestriel(?:le)?s?|annuel(?:le)?s?|chaque\s+(?:jour|semaine|mois|trimestre)|"
    r"tous\s+les\s+(?:\d+\s+|deux\s+|trois\s+)?(?:jours|semaines|mois|trimestres)|"
    r"par\s+(?:semaine|mois|trimestre))\b",
    _I,
)
_DELIVERY = re.compile(
    r"\b(?:deliver\w*|release\w*|ship\w*|updates?|report\w*|review\w*|check-?ins?|milestones?|"
    r"launch\w*|publish\w*|cycles?|sprints?|deadlines?|demos?|stand-?ups?|"
    r"livr\w*|mises?\s+en\s+production|points?|revues?|comites?|rituels?|rapports?|deploi\w*|"
    r"echeances?|jalons?)\b",
    _I,
)
_VAGUE_CADENCE = re.compile(
    r"\b(?:regular(?:ly)?|ongoing|periodic(?:ally)?|timely|tight\s+deadlines?|deadlines?|"
    r"fast-paced|regulier(?:e|s|es)?|regulierement|delais\s+serres|rythme\s+soutenu)\b",
    _I,
)


def _cadence(segments: list[_Segment]) -> Found:
    strong = _first(
        segments, lambda s: _CADENCE.search(s) is not None and _DELIVERY.search(s) is not None, 2
    )
    if strong:
        return Found("present", tuple(strong))
    weak = _first(
        segments,
        lambda s: _CADENCE.search(s) is not None or _VAGUE_CADENCE.search(s) is not None,
        2,
        _CADENCE,
    )
    return Found("partial", tuple(weak)) if weak else Found("missing")


_DETECTORS: dict[str, Callable[[list[_Segment]], Found]] = {
    "supervisor": _supervisor,
    "scope": _scope,
    "deliverables": _deliverables,
    "success": _success,
    "coordination": _coordination,
    "cadence": _cadence,
}


def check(text: str | None) -> RoleClarity:
    """The six points of one posting's text (`text` past TEXT_MAX is cut there)."""
    value = (text or "")[:TEXT_MAX]
    if not _readable(_fold(value)):
        return RoleClarity(read=False, points={}, questions=())
    segments = _segments(value)
    points = {point.id: _DETECTORS[point.id](segments) for point in POINTS}
    missing = sum(1 for found in points.values() if found.status == "missing")
    questions = (
        tuple(point.question for point in POINTS if points[point.id].status != "present")
        if missing >= MISSING_THRESHOLD
        else ()
    )
    return RoleClarity(read=True, points=points, questions=questions)
