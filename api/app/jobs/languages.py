"""The languages a posting asks for, compared with the person's (jobs J35, issue #858; SPEC-jobs,
"Kept with a badge"). The rule follows the Language Gate of ai-job-search (MIT, NOTICE): what the
role itself requires counts, not the language the ad is written in; a required language the
person does not declare is outside her criteria; a declared one asked at a level that may be above
hers is flagged for her to judge, never passed in silence; anything else passes.

The posting reader stores what it read (`jobs.languages`, reader.py): the language the ad is
written in, and each language asked with its name, its level as the posting writes it, whether the
reader read it as required, the other languages accepted instead, and its quote. The person's
languages are the profile's (`identity.languages`: a name and a level as she wrote them).

The rule (`compare`), pure and linear in the number of languages:
- Names: a closed list (KNOWN: about forty languages, each with its English, French, German and
  own spellings, and the adjectives postings use, "anglophone", "Deutschkenntnisse"). A name holds
  the known languages whose spelling is one of its words (a German stem also starts a word:
  "Deutschkenntnisse" holds German); a name that holds none is kept as written and compared as
  written, case, accents and punctuation aside.
- Required or welcome: required when the reader says so and its quote holds no word of a welcome
  (WELCOME: "a plus", "un atout", "von Vorteil"); welcome when the reader says so and the quote
  holds one; anything else is unsure.
- Levels: each side read on one scale (LEVELS: CEFR letters A1 to C2, then native), from the
  words of the level as written in English, French or German. The person's level is the lowest
  reading of her words (with no level written, those of the name she wrote: "Anglais courant"),
  the posting's the highest; a negation next to a word ("non native", "pas courant") or words the
  scale does not know make the level unknown.
- A required language the person does not declare: the badge (`missing`). An unsure one she does
  not declare: a notice. A declared language (or one of its alternatives) asked with a level:
  passes when hers is native, or both are read and hers is at least the posting's; else a notice
  with both levels as written. With no language required or unsure, an ad written in a language
  she does not declare: a notice. A welcome language gives nothing; a profile that declares no
  language gives nothing.
"""

import contextlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.browser.words import words as _words
from app.profile.models import Profile
from app.profile.schemas import IdentitySection

# The stored shape's bounds, what the reader accepts (reader.py `_Asked`).
ASKED_MAX = 8
ALTERNATIVES_MAX = 3
NAME_MAX = 60
LEVEL_MAX = 80

# Each known language by its English name: its spellings in English, French, German and its own,
# and the adjectives postings use. A trailing "*" is a German stem, which also starts a word.
_KNOWN: dict[str, tuple[str, ...]] = {
    "English": ("english", "anglais", "anglaise", "anglophone", "englisch*", "anglo saxon"),
    "French": ("french", "francais", "francaise", "francophone", "franzosisch*"),
    "German": (
        "german", "allemand", "allemande", "germanophone", "deutsch*", "schweizerdeutsch*",
    ),
    "Spanish": (
        "spanish", "espagnol", "espagnole", "hispanophone", "spanisch*", "espanol", "castellano",
        "castilian",
    ),
    "Italian": ("italian", "italien", "italienne", "italophone", "italienisch*", "italiano"),
    "Portuguese": (
        "portuguese", "portugais", "portugaise", "lusophone", "portugiesisch*", "portugues",
    ),
    "Dutch": (
        "dutch", "neerlandais", "neerlandaise", "neerlandophone", "niederlandisch*", "nederlands",
        "flemish", "flamand", "flamande", "flamisch*", "vlaams",
    ),
    "Luxembourgish": (
        "luxembourgish", "luxembourgeois", "luxemburgisch*", "letzebuergesch",
    ),
    "Polish": ("polish", "polonais", "polonaise", "polnisch*", "polski"),
    "Russian": ("russian", "russe", "russophone", "russisch*", "русскии", "русскии язык"),
    "Ukrainian": ("ukrainian", "ukrainien", "ukrainienne", "ukrainisch*", "украінська"),
    "Czech": ("czech", "tcheque", "tschechisch*", "cestina"),
    "Slovak": ("slovak", "slovaque", "slowakisch*", "slovencina"),
    "Slovenian": ("slovenian", "slovene", "slowenisch*", "slovenscina"),
    "Croatian": ("croatian", "croate", "kroatisch*", "hrvatski"),
    "Serbian": ("serbian", "serbe", "serbisch*", "srpski"),
    "Bulgarian": ("bulgarian", "bulgare", "bulgarisch*"),
    "Romanian": ("romanian", "roumain", "roumaine", "rumanisch*", "romana"),
    "Hungarian": ("hungarian", "hongrois", "hongroise", "ungarisch*", "magyar"),
    "Greek": ("greek", "grec", "grecque", "griechisch*", "ελληνικα"),
    "Turkish": ("turkish", "turc", "turque", "turkisch*", "turkce"),
    "Swedish": ("swedish", "suedois", "suedoise", "schwedisch*", "svenska"),
    "Danish": ("danish", "danois", "danoise", "danisch*", "dansk"),
    "Norwegian": ("norwegian", "norvegien", "norvegienne", "norwegisch*", "norsk"),
    "Finnish": ("finnish", "finnois", "finnoise", "finnisch*", "suomi"),
    "Estonian": ("estonian", "estonien", "estnisch*", "eesti"),
    "Latvian": ("latvian", "letton", "lettisch*", "latviesu"),
    "Lithuanian": ("lithuanian", "lituanien", "litauisch*", "lietuviu"),
    "Catalan": ("catalan", "catalane", "katalanisch*", "catala"),
    "Basque": ("basque", "baskisch*", "euskara"),
    "Breton": ("breton", "bretonisch*", "brezhoneg"),
    "Corsican": ("corsican", "corse", "korsisch*"),
    "Arabic": ("arabic", "arabe", "arabophone", "arabisch*", "العربية"),
    "Hebrew": ("hebrew", "hebreu", "hebraisch*", "עברית"),
    "Persian": ("persian", "farsi", "persan", "persisch*"),
    "Hindi": ("hindi", "हिन्दी"),
    "Chinese": (
        "chinese", "chinois", "chinoise", "sinophone", "chinesisch*", "mandarin", "putonghua",
        "中文", "普通话",
    ),
    "Cantonese": ("cantonese", "cantonais", "kantonesisch*"),
    "Japanese": ("japanese", "japonais", "japonaise", "japanisch*", "日本語"),
    "Korean": ("korean", "coreen", "coreenne", "koreanisch*", "한국어"),
    "Vietnamese": ("vietnamese", "vietnamien", "vietnamesisch*"),
    "Thai": ("thai", "thailandais", "thailandisch*"),
    "Indonesian": ("indonesian", "indonesien", "indonesisch*", "bahasa indonesia"),
    "Malay": ("malay", "malais", "malaiisch*", "bahasa melayu"),
    "Tahitian": ("tahitian", "tahitien", "tahitienne", "tahitianisch*", "reo tahiti", "reo maohi"),
}  # fmt: skip

# Words that make a quote's language welcome, not required.
_WELCOME = (
    "a plus", "an advantage", "advantageous", "an asset", "nice to have",
    "a bonus", "bonus", "preferred", "preferably", "desirable", "ideally", "would be great",
    "un atout", "atout", "un plus", "est un plus", "serait un plus", "apprecie", "appreciee",
    "souhaite", "souhaitee", "souhaitable", "idealement", "de preference", "von vorteil",
    "vorteilhaft", "wunschenswert", "idealerweise", "gerne gesehen", "ein plus",
)  # fmt: skip

NATIVE = 7
# The scale: 1 to 6 are the CEFR's A1 to C2, 7 native. A trailing "*" is a stem (German, French
# endings: "fliessende", "verhandlungssicheres", "courante").
_LEVELS: dict[int, tuple[str, ...]] = {
    1: ("a1", "beginner", "debutant*", "anfanger*", "notions", "notion"),
    2: (
        "a2", "elementary", "elementaire", "basic", "basique", "notions de base", "de base",
        "grundkenntnisse", "elementary proficiency", "scolaire", "school level",
        "schulkenntnisse",
    ),
    3: (
        "b1", "intermediate", "intermediaire", "conversational", "conversationnel*",
        "limited working proficiency", "working knowledge", "gute kenntnisse", "mittelstufe",
        "correct", "correcte",
    ),
    4: (
        "b2", "upper intermediate", "good", "bon", "bonne*", "bon niveau", "bonnes connaissances",
        "professional working proficiency", "operational", "operationnel*", "autonome",
        "sehr gute kenntnisse", "gut", "gute*", "solid", "solide",
    ),
    5: (
        "c1", "fluent", "fluently", "fluency", "courant*", "couramment", "fliessend*", "fluide",
        "advanced", "avance*", "business", "business level", "business fluent", "professional",
        "professionnel*", "niveau professionnel", "full professional proficiency", "excellent*",
        "excellen*", "tres bon*", "tres bonne*", "tres bon niveau",
        "tres bonnes connaissances", "sehr gut*", "strong", "maitrise", "proficient",
        "ausgezeichnet*",
    ),
    6: (
        "c2", "bilingual", "bilingue", "zweisprachig*", "near native", "quasi natif",
        "quasi native", "quasi bilingue", "presque bilingue", "mastery", "perfect", "parfait*",
        "parfaite maitrise", "perfekt*", "verhandlungssicher*", "native or bilingual proficiency",
        "native or bilingual",
    ),
    NATIVE: (
        "native", "native speaker", "mother tongue", "first language", "langue maternelle",
        "maternelle", "maternel", "natif", "langue natale", "muttersprach*",
        "erstsprache",
    ),
}  # fmt: skip
_NEGATIONS = frozenset({"not", "no", "non", "pas", "nicht", "kein", "keine", "sans", "without"})


@dataclass(frozen=True)
class _Phrase:
    words: tuple[str, ...]
    stem: bool  # its last word also starts a longer word

    def at(self, text: Sequence[str], start: int) -> bool:
        end = start + len(self.words)
        if end > len(text) or tuple(text[start : end - 1]) != self.words[:-1]:
            return False
        last, said = self.words[-1], text[end - 1]
        return said == last or (self.stem and said.startswith(last))


def _phrase(spelling: str) -> _Phrase:
    stem = spelling.endswith("*")
    return _Phrase(tuple(_words(spelling.rstrip("*")).split()), stem)


def _longest_first[T](table: Iterable[tuple[T, str]]) -> tuple[tuple[T, _Phrase], ...]:
    return tuple(
        sorted(((tag, _phrase(s)) for tag, s in table), key=lambda one: -len(one[1].words))
    )


KNOWN = tuple(_KNOWN)
_NAME_PHRASES = _longest_first((name, s) for name, spellings in _KNOWN.items() for s in spellings)
_LEVEL_PHRASES = _longest_first(
    (point, s) for point, spellings in _LEVELS.items() for s in spellings
)
_WELCOME_PHRASES = _longest_first((True, s) for s in _WELCOME)


def _found[T](table: tuple[tuple[T, _Phrase], ...], text: str) -> list[tuple[T, int]]:
    """Every tag of `table` whose phrase is in `text`'s words, longest first and never
    overlapping, with the index of its first word."""
    said = _words(text).split()
    taken = [False] * len(said)
    found: list[tuple[T, int]] = []
    for tag, phrase in table:
        for start in range(len(said)):
            span = range(start, start + len(phrase.words))
            if phrase.at(said, start) and not any(taken[i] for i in span):
                for i in span:
                    taken[i] = True
                found.append((tag, start))
    return found


def keys_of(name: str) -> frozenset[str]:
    """The languages a name holds: the known ones among its words, else the name itself as its
    words (case, accents and punctuation aside); empty for a name of no word."""
    known = frozenset(tag for tag, _ in _found(_NAME_PHRASES, name))
    if known:
        return known
    plain = _words(name)
    return frozenset({plain}) if plain else frozenset()


def named_in(name: str, quote: str) -> bool:
    """Whether the quote names the language of `name`: a spelling of a known language it holds,
    else the name's own words."""
    known = {tag for tag, _ in _found(_NAME_PHRASES, name)}
    if known:
        return bool(known & {tag for tag, _ in _found(_NAME_PHRASES, quote)})
    plain = _words(name)
    return bool(plain) and f" {plain} " in f" {_words(quote)} "


def display(name: str) -> str:
    """A language's name as the app says it: the English name of a known one, else as written."""
    known = sorted({tag for tag, _ in _found(_NAME_PHRASES, name)}, key=KNOWN.index)
    return " or ".join(known) if known else " ".join(name.split())


def welcome_said(quote: str) -> bool:
    return bool(_found(_WELCOME_PHRASES, quote))


Reading = Literal["low", "high"]


def level_of(level: str | None, reading: Reading) -> int | Literal["unknown"] | None:
    """A level on the scale: None when none is written, "unknown" when its words are not the
    scale's or a negation stands next to one; else the lowest reading of its words (a person's
    level) or the highest (a posting's)."""
    if level is None or not _words(level):
        return None
    said = _words(level).split()
    found = _found(_LEVEL_PHRASES, level)
    if not found:
        return "unknown"
    for _, start in found:
        if any(said[at] in _NEGATIONS for at in range(max(0, start - 2), start)):
            return "unknown"
    points = [point for point, _ in found]
    return min(points) if reading == "low" else max(points)


# --- the two sides ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Spoken:
    """One language of the person's profile: its name and its level as she wrote them."""

    name: str
    level: str | None = None


@dataclass(frozen=True)
class Asked:
    """One language a posting asks for, as stored by the reader."""

    name: str
    level: str | None
    required: bool
    alternatives: tuple[str, ...]
    quote: str

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.alternatives)

    @property
    def status(self) -> Literal["required", "welcome", "unsure"]:
        welcome = welcome_said(self.quote)
        if self.required and not welcome:
            return "required"
        if not self.required and welcome:
            return "welcome"
        return "unsure"


@dataclass(frozen=True)
class Read:
    """What the reader stored of a posting's languages."""

    written_in: str | None
    asked: tuple[Asked, ...]


def _text(value: object, most: int) -> str | None:
    return value if isinstance(value, str) and value.strip() and len(value) <= most else None


def read_stored(raw: object) -> Read | None:
    """`jobs.languages` read defensively: None when never read or not the stored shape; an item
    that is not one is left out."""
    if not isinstance(raw, Mapping):
        return None
    asked: list[Asked] = []
    items = raw.get("asked")
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, Mapping):
            continue
        name, quote = _text(item.get("name"), NAME_MAX), _text(item.get("quote"), 2000)
        others = item.get("alternatives")
        alternatives = tuple(
            one for one in (others if isinstance(others, list) else ()) if _text(one, NAME_MAX)
        )
        if name is None or quote is None or not isinstance(item.get("required"), bool):
            continue
        level = _text(item.get("level"), LEVEL_MAX)
        asked.append(Asked(name, level, bool(item["required"]), alternatives, quote))
    return Read(_text(raw.get("written_in"), NAME_MAX), tuple(asked[:ASKED_MAX]))


def spoken_from(sections: Mapping[str, Any] | None) -> tuple[Spoken, ...]:
    """The profile's languages; none when the identity section was never saved or no longer
    validates (a profile with no language declared gives nothing)."""
    raw = (sections or {}).get("identity")
    if not isinstance(raw, Mapping):
        return ()
    with contextlib.suppress(ValueError):
        identity = IdentitySection.model_validate(dict(raw))
        return tuple(Spoken(one.name, one.level) for one in identity.languages)
    return ()


def spoken_of(db: Session, user_id: uuid.UUID) -> tuple[Spoken, ...]:
    sections = db.execute(
        sa.select(Profile.sections).where(Profile.user_id == user_id)
    ).scalar_one_or_none()
    return spoken_from(sections)


# --- the comparison ---------------------------------------------------------------------------


GapKind = Literal["missing", "unsure", "level", "written_in"]


@dataclass(frozen=True)
class Gap:
    """One thing the comparison found: `missing` (a required language she does not declare: the
    badge), `unsure` (one that may be required), `level` (asked at a level that may be above
    hers), `written_in` (the ad's language, with no language asked)."""

    kind: GapKind
    language: str  # as the app says it: "German", "German or Dutch"
    quote: str | None = None
    asked_level: str | None = None
    spoken: Spoken | None = None


def _declared(names: Iterable[str], spoken: Sequence[Spoken]) -> list[Spoken]:
    wanted = frozenset().union(*(keys_of(name) for name in names))
    return [one for one in spoken if keys_of(one.name) & wanted]


def _level_gap(item: Asked, mine: list[Spoken], language: str) -> Gap | None:
    """The person's best declared reading against the asked level; a gap when unsure."""
    asked = level_of(item.level, "high")
    if asked is None:
        return None
    readings = [(level_of(one.level or one.name, "low"), one) for one in mine]
    if any(point == NATIVE for point, _ in readings):
        return None
    known = [(point, one) for point, one in readings if isinstance(point, int)]
    if asked != "unknown" and known and max(point for point, _ in known) >= asked:
        return None
    best = max(known, key=lambda pair: pair[0])[1] if known else mine[0]
    return Gap("level", language, item.quote, item.level, best)


def compare(stored: object, spoken: Sequence[Spoken]) -> list[Gap]:
    """The posting's languages against the person's (module docstring), in the posting's order,
    the ad's language last; [] when she declares none or the posting was never read."""
    read = read_stored(stored)
    if not spoken or read is None:
        return []
    gaps: list[Gap] = []
    asks = False
    for item in read.asked:
        status = item.status
        if status == "welcome":
            continue
        asks = True
        language = " or ".join(dict.fromkeys(display(name) for name in item.names))
        mine = _declared(item.names, spoken)
        if not mine:
            gaps.append(Gap("missing" if status == "required" else "unsure", language, item.quote))
            continue
        gap = _level_gap(item, mine, language)
        if gap is not None:
            gaps.append(gap)
    if not asks and read.written_in and not _declared([read.written_in], spoken):
        gaps.append(Gap("written_in", display(read.written_in)))
    return gaps


def missing(stored: object, spoken: Sequence[Spoken]) -> list[str]:
    """The required languages she does not declare, as the app says them: the badge's."""
    return list(
        dict.fromkeys(gap.language for gap in compare(stored, spoken) if gap.kind == "missing")
    )


def as_stored(written_in: str | None, asked: Sequence[Asked]) -> dict[str, Any]:
    return {
        "written_in": written_in,
        "asked": [
            {
                "name": item.name,
                "level": item.level,
                "required": item.required,
                "alternatives": list(item.alternatives),
                "quote": item.quote,
            }
            for item in asked
        ],
    }
