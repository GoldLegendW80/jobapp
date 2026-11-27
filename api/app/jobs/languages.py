"""The languages a posting asks for, compared with the person's (jobs J35, issue #858; SPEC-jobs,
"Kept with a badge"). The rule follows the Language Gate of ai-job-search (MIT, NOTICE): what the
role itself requires counts, not the language the ad is written in; a required language the
person does not declare is outside her criteria; a declared one asked at a level that may be above
hers is flagged for her to judge, never passed in silence; anything else passes.

The posting reader stores what it read (`jobs.languages`, reader.py): the language the ad is
written in, and each language asked with its name, its level as the posting writes it, whether the
reader read it as required, the other languages accepted instead, and its quote. The person's
languages are the profile's (`identity.languages`: a name and a level as she wrote them).

A false badge is the costly error (the posting leaves Discover and Full auto), a missed one leaves
things as they were: every doubt below gives a notice, never the badge. The rule (`compare`), pure
and linear in the length of what it reads:
- Names: a closed list (KNOWN: about eighty languages, each with its English, French, German and
  own spellings, and the adjectives postings use, "anglophone"). A German stem ("deutsch*") names
  the language with an inflection or a skill glued to it ("Deutsche", "Deutschkenntnisse",
  "deutschsprachig"), never with another word ("Deutschland", "deutschlandweit"). The person's
  side also reads the names in Spanish, Italian, Portuguese and Dutch ("Inglés", "Tedesco") and the
  ISO 639 codes ("EN", "deu") as a whole entry, a level aside; an entry that names no language
  ("N/A", "none", a dash) is no entry. A name the list does not hold is compared as written, case,
  accents and punctuation aside.
- Required (the badge's side), for one language asked: the reader says so, and its quote
  1. holds no welcome word (WELCOME: "a plus", "un atout", "von Vorteil") and no negation (NOT:
     "no", "pas", "kein");
  2. offers no other language the person declares as an alternative (one the reader split in
     two): when the quote names one, it holds no slash and no alternative word (OR: "or", "oder",
     "ou", "bzw", "one of") and only "and", "und", "et", "sowie", "ainsi que", a short link
     (JOINS) or another language's name stands between the two names, never nothing ("Fluent
     German and English" asks for both; "German or English", "German/English", "Bilingue
     français allemand", "Fluent English, German" are unsure);
  3. names the language as a language: a spelling of it whose word before and word after are each
     a clause's edge, a linking word (LINKS: "of", "l'", "in"), a level, a requirement word,
     a language noun ("speaker", "niveau") or another language's name. So the people, the place,
     the market and the company are not the language: "the German market", "Deutsche Bank",
     "German speaking clients", "Pays basque", "une entreprise catalane". "speaking" after a name
     is a language noun only before an edge, a level, a requirement word, "is" or a person
     ("German-speaking candidate"); a dash alone between a name and a level is a label, as a colon
     is ("German: fluent", "Deutsch - verhandlungssicher"); "mind.", "at least" before a level are
     links. After "be", "are", "sind" or "êtes", a name needs a level, a requirement word or a noun
     after it, else it is a nationality ("Must be German"). A spelling that is also a place in a
     posting's language ("Italien": Italy in German, "Corse": Corsica) never counts, nor "polish"
     without its capital letter: the code cannot tell them apart; at a clause's start, where a
     verb has a capital too ("Polish the required UI flows"), "Polish" needs an edge, a level or a
     noun after it and a level close to it;
  4. holds a level word or a requirement word (REQUIRED: "required", "exigé", "zwingend",
     "Kenntnisse", "communication skills"; a verb of mastery is a level, "maîtrisez",
     "beherrschst", "sicher") within GAP words of that spelling, only linking words, names and
     nouns between them and no clause's edge; a skill glued to a German stem is one
     ("Deutschkenntnisse").
  Welcome: the reader says it is not required and the quote holds a welcome word. Anything else
  is unsure: a notice, never the badge.
- Levels: each side read on one scale (LEVELS: CEFR letters A1 to C2, then native), from the
  words of the level as written in English, French or German. The person's level is the lowest
  reading of her words (with no level written, those of the name she wrote: "Anglais courant"),
  the posting's the highest; a negation next to a word ("non native", "pas courant") or words the
  scale does not know make the level unknown.
- A required language the person does not declare: the badge (`missing`); when an entry of her
  profile cannot be read as a language of the list ("Tedesco" before it was added, "Wolof" being
  one), the asked language could be that one: a notice (`unread`) instead. An unsure one she does
  not declare: a notice. A declared language (or one of its alternatives) asked with a level:
  passes when hers is native, or both are read and hers is at least the posting's; else a notice
  with both levels as written. With no language required or unsure, an ad written in a language
  she does not declare: a notice. A welcome language gives nothing; a profile that declares no
  language gives nothing.
"""

import contextlib
import functools
import re
import unicodedata
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
# and the adjectives postings use. A trailing "*" is a German stem, which also starts a word with
# one of _ENDINGS.
_KNOWN: dict[str, tuple[str, ...]] = {
    "English": ("english", "anglais", "anglaise", "anglophone", "englisch*", "anglo saxon"),
    "French": ("french", "francais", "francaise", "francophone", "franzosisch*"),
    "German": (
        "german", "allemand", "allemande", "germanophone", "deutsch*", "schweizerdeutsch*",
        "hochdeutsch*",
    ),
    "Spanish": (
        "spanish", "espagnol", "espagnole", "hispanophone", "spanisch*", "espanol", "castellano",
        "castilian", "castillan", "latin american spanish",
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
    "Icelandic": ("icelandic", "islandais", "islandisch*", "islenska"),
    "Irish": ("irish", "gaelic", "irlandais", "gaelique", "irisch*", "gaeilge"),
    "Welsh": ("welsh", "gallois", "walisisch*", "cymraeg"),
    "Maltese": ("maltese", "maltais", "maltesisch*", "malti"),
    "Albanian": ("albanian", "albanais", "albanisch*", "shqip"),
    "Macedonian": ("macedonian", "macedonien", "mazedonisch*"),
    "Bosnian": ("bosnian", "bosnien", "bosnisch*", "bosanski"),
    "Armenian": ("armenian", "armenien", "armenisch*"),
    "Georgian": ("georgian", "georgien", "georgisch*"),
    "Catalan": ("catalan", "catalane", "katalanisch*", "catala"),
    "Galician": ("galician", "galicien", "galicisch*", "galego"),
    "Basque": ("basque", "baskisch*", "euskara"),
    "Breton": ("breton", "bretonisch*", "brezhoneg"),
    "Corsican": ("corsican", "corse", "korsisch*"),
    "Occitan": ("occitan", "okzitanisch*"),
    "Alsatian": ("alsatian", "alsacien", "elsassisch*"),
    "Creole": ("creole", "kreolisch*", "kreyol"),
    "Yiddish": ("yiddish", "jiddisch*"),
    "Esperanto": ("esperanto",),
    "Latin": ("latin", "latein*"),
    "Arabic": ("arabic", "arabe", "arabophone", "arabisch*", "العربية", "darija", "derja"),
    "Hebrew": ("hebrew", "hebreu", "hebraisch*", "עברית"),
    "Persian": ("persian", "farsi", "persan", "persisch*"),
    "Kurdish": ("kurdish", "kurde", "kurdisch*"),
    "Hindi": ("hindi", "हिन्दी"),
    "Urdu": ("urdu", "ourdou"),
    "Bengali": ("bengali", "bangla", "bengalisch*"),
    "Punjabi": ("punjabi", "pendjabi", "pandschabi"),
    "Tamil": ("tamil", "tamoul", "tamilisch*"),
    "Chinese": (
        "chinese", "chinois", "chinoise", "sinophone", "chinesisch*", "mandarin", "putonghua",
        "中文", "普通话",
    ),
    "Cantonese": ("cantonese", "cantonais", "kantonesisch*"),
    "Japanese": ("japanese", "japonais", "japonaise", "japanisch*", "日本語"),
    "Korean": ("korean", "coreen", "coreenne", "koreanisch*", "한국어"),
    "Vietnamese": ("vietnamese", "vietnamien", "vietnamesisch*"),
    "Thai": ("thai", "thailandais", "thailandisch*"),
    "Khmer": ("khmer", "cambodgien"),
    "Indonesian": ("indonesian", "indonesien", "indonesisch*", "bahasa indonesia"),
    "Malay": ("malay", "malais", "malaiisch*", "bahasa melayu"),
    "Tagalog": ("tagalog", "filipino"),
    "Swahili": ("swahili", "kiswahili"),
    "Amharic": ("amharic", "amharique", "amharisch*"),
    "Wolof": ("wolof",),
    "Bambara": ("bambara", "bamanankan"),
    "Kabyle": ("kabyle", "kabylisch*", "taqbaylit"),
    "Lingala": ("lingala",),
    "Malagasy": ("malagasy", "malgache", "madagassisch*"),
    "Afrikaans": ("afrikaans",),
    "Tahitian": ("tahitian", "tahitien", "tahitienne", "tahitianisch*", "reo tahiti", "reo maohi"),
    "French Sign Language": ("french sign language", "langue des signes francaise"),
    "German Sign Language": ("german sign language", "deutsche gebardensprache"),
    "British Sign Language": ("british sign language",),
    "American Sign Language": ("american sign language",),
    "Sign language": ("sign language", "langue des signes", "gebardensprache*"),
}  # fmt: skip

# Read on the person's side only (a posting's quote is in English, French or German): the names of
# the main languages in Spanish, Italian, Portuguese, Dutch and Polish.
_HERS: dict[str, tuple[str, ...]] = {
    "English": ("ingles", "inglese", "engels", "angielski"),
    "French": ("frances", "francese", "frans", "francuski"),
    "German": ("aleman", "tedesco", "alemao", "duits", "niemiecki"),
    "Spanish": ("spagnolo", "espanhol", "spaans", "hiszpanski"),
    "Italian": ("italiaans", "włoski"),
    "Portuguese": ("portoghese", "portugees", "portugalski"),
    "Dutch": ("neerlandes", "holandes", "olandese", "niderlandzki"),
    "Russian": ("ruso", "russo", "rosyjski"),
    "Chinese": ("chino", "cinese", "chines", "chinees"),
    "Arabic": ("arabo",),
    "Japanese": ("japones", "giapponese", "japans"),
    "Polish": ("polaco", "polacco", "polones", "pools"),
}  # fmt: skip

# ISO 639 codes, read on the person's side as a whole entry, a level aside ("EN", "deu C1"). Left
# out, as she may mean a country: "uk" (Ukrainian or the United Kingdom), "br", "ca", "se".
_CODES: dict[str, tuple[str, ...]] = {
    "English": ("en", "eng"), "French": ("fr", "fra", "fre"), "German": ("de", "deu", "ger"),
    "Spanish": ("es", "spa"), "Italian": ("it", "ita"), "Portuguese": ("pt", "por"),
    "Dutch": ("nl", "nld", "dut"), "Luxembourgish": ("lb", "ltz"), "Polish": ("pl", "pol"),
    "Russian": ("ru", "rus"), "Ukrainian": ("ukr",), "Czech": ("cs", "ces", "cze"),
    "Slovak": ("sk", "slk", "slo"), "Slovenian": ("sl", "slv"), "Croatian": ("hr", "hrv"),
    "Serbian": ("sr", "srp"), "Bulgarian": ("bg", "bul"), "Romanian": ("ro", "ron", "rum"),
    "Hungarian": ("hu", "hun"), "Greek": ("el", "ell", "gre"), "Turkish": ("tr", "tur"),
    "Swedish": ("sv", "swe"), "Danish": ("da", "dan"), "Norwegian": ("no", "nor", "nb", "nn"),
    "Finnish": ("fi", "fin"), "Estonian": ("et", "est"), "Latvian": ("lv", "lav"),
    "Lithuanian": ("lt", "lit"), "Catalan": ("cat",), "Basque": ("eu", "eus", "baq"),
    "Arabic": ("ar", "ara"), "Hebrew": ("he", "heb"), "Persian": ("fa", "fas", "per"),
    "Hindi": ("hi", "hin"), "Chinese": ("zh", "zho", "chi", "cmn"), "Cantonese": ("yue",),
    "Japanese": ("ja", "jpn"), "Korean": ("ko", "kor"), "Vietnamese": ("vi", "vie"),
    "Thai": ("th", "tha"), "Indonesian": ("id", "ind"), "Malay": ("ms", "msa", "may"),
    "French Sign Language": ("lsf", "fsl"), "German Sign Language": ("dgs", "gsg"),
    "British Sign Language": ("bsl",), "American Sign Language": ("asl",),
}  # fmt: skip

# What a German stem may end with and still name the language: an inflection, or a skill glued to
# it (_SKILLS also count as a requirement word, rule 4).
_INFLECTIONS = ("", "e", "em", "en", "er", "es")
_SKILLS = ("kenntnis", "kenntnisse", "kenntnissen", "sprachkenntnisse", "niveau")
_SPEAKING = ("sprachig", "sprachige", "sprachigen", "sprachiger", "sprachiges", "sprachler")
_ENDINGS = frozenset((*_INFLECTIONS, *_SKILLS, *_SPEAKING))
# Spellings the code cannot tell from a place ("Mailand, Italien"; "Poste basé en Corse").
_ALSO_PLACE = frozenset({"italien", "corse"})
# Spellings that name the language only with their capital letter ("you will polish the UI").
_CAPITAL_ONLY = frozenset({"polish"})

# Words that make a quote's language welcome, not required; "plus" is one after "a", "un", "ein",
# "is" or "est" (`_PLUS`), never alone ("Plus de 5 ans d'expérience").
_WELCOME = (
    "advantag*", "asset*", "bonus*", "benefic*", "prefer*", "ideal*", "desirabl*", "desired",
    "nice to have", "good to have", "great to have", "would be great", "would be nice",
    "optional*", "welcome*", "appreci*", "helpful", "useful",
    "atout*", "souhait*", "bienvenu*", "optionnel*", "facultati*", "de preference",
    "utile*",
    "vorteil*", "wunschenswert", "erwunscht", "willkommen", "bevorzugt*", "gerne gesehen",
    "gern gesehen", "pluspunkt*", "hilfreich*", "nutzlich*", "von nutzen", "ware schon",
    "waren schon",
)  # fmt: skip
_PLUS_AFTER = frozenset({"a", "an", "un", "ein", "is", "est", "als"})
# Words that make a quote a negation: never the badge.
_NOT = (
    "no", "not", "never", "without", "nor", "neither", "isn", "aren", "doesn", "don", "needn",
    "non", "pas", "sans", "aucun*", "ni", "jamais", "nullement",
    "kein*", "nicht", "ohne", "weder", "nie", "niemals",
)  # fmt: skip
# Words that make a language required (rule 4), with the levels.
_REQUIRED = (
    "required", "requires", "requirement*", "must", "mandatory", "essential", "needed",
    "necessary", "prerequisite*", "proficiency", "command", "knowledge", "skill", "skills",
    "language", "languages", "communication skill", "communication skills",
    "communications skills",
    "exige*", "requis", "requise*", "indispensable*", "obligatoire*", "imperati*", "necessaire*",
    "connaissance*", "langue*",
    "erforderlich*", "zwingend*", "voraussetzung*", "vorausgesetzt", "unabdingbar*",
    "notwendig*", "unerlasslich*", "kenntnis*", "sprachkenntnis*", "sprache*",
    "wort und schrift", "muss", "mussen", "pflicht*", "benotigt*", "verlangt",
)  # fmt: skip
# Words that may stand between a language's name and its requirement word, or next to the name.
# Speaking and writing are among them, never a requirement by themselves ("Our clients speak
# German", "Documents are written in German").
_LINKS = frozenset({
    "the", "of", "in", "a", "an", "and", "or", "is", "are", "be", "with", "at", "to", "very",
    "also", "both", "either", "your", "speak", "write", "written", "spoken", "verbal", "absolutely",
    "strictly", "really", "definitely",
    "l", "le", "la", "les", "de", "d", "du", "des", "en", "et", "ou", "est", "sont", "un", "une",
    "au", "aux", "avec", "tres", "aussi", "votre", "parler", "parlez", "parle", "parlee", "ecrit",
    "ecrite", "oral", "orale", "absolument", "vraiment",
    "im", "und", "oder", "ist", "sind", "auf", "mit", "sehr", "auch", "als", "sowie", "sowohl",
    "sprechen", "sprechend", "schreiben", "absolut", "unbedingt", "wirklich",
})  # fmt: skip
# Links of more than one word ("allemand ainsi que néerlandais").
_LINK_PHRASES = ("ainsi que", "as well as", "als auch")
# Words that bring a level ("Deutschkenntnisse (mind. B2)"): a link only right before a level.
_AT_LEAST = ("mind", "mindestens", "min", "minimum", "at least", "au moins", "au minimum")
# Nouns of a language that may follow its name ("German speaker", "allemand niveau C1").
_NOUNS = frozenset({
    "speaker", "speakers", "level", "locuteur", "locutrice", "locuteurs", "niveau",
    "sprecher", "sprecherin", "sprachniveau",
})  # fmt: skip
# Level words that are also nouns a name qualifies ("German business culture"): never after it.
_LEVEL_NOUNS = frozenset({"business", "professional"})
# "German speaking" is the person's language before an edge, a level, a requirement word, "is",
# or one of these, a requirement on the person ("German-speaking candidate"); never before
# another word ("German speaking clients", "German-speaking market").
_SPEAKING_WORD = "speaking"
_SPEAKING_COPULAS = frozenset({"is", "ist", "est"})
_PERSONS = frozenset({
    "candidate", "candidates", "applicant", "applicants", "person", "individual", "profile",
    "role", "position",
})  # fmt: skip
# A name after these, with no level, requirement word or noun after it, is a nationality ("Must
# be German", "Vous êtes allemand").
_COPULAS = frozenset({"be", "being", "are", "etre", "etes", "sein", "sind", "bist", "seid"})
# At most this many words between a language's name and its requirement word (rule 4).
GAP = 3

# Rule 2: words that make another language she lists an alternative to the one asked, anywhere in
# the quote ("German or English"); a slash does too ("German/English", "and/or").
_OR = (
    "or", "either", "one of", "any of", "alternativ*", "otherwise", "else", "at least one",
    "any one", "one is enough",
    "ou", "soit", "voire", "sinon", "au choix", "l une des", "une des", "un des", "l un des",
    "au moins une", "au moins un",
    "oder", "entweder", "bzw", "beziehungsweise", "respektive", "wahlweise", "ansonsten",
    "andernfalls", "eine der", "einer der", "eine dieser", "einer dieser", "mindestens eine",
    "mindestens einer", "mindestens eines", "eine davon", "einer davon", "eines davon",
    "eins davon",
)  # fmt: skip
# Rule 2: what may stand between the language asked and another she lists, so that the quote asks
# for both ("Fluent German and English"): these, _LINK_PHRASES and another language's name.
_JOINS = frozenset({"and", "of", "in", "the", "und", "sowie", "et", "de", "l", "d", "en"})

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
        "sehr gute kenntnisse", "gut", "gute*", "solid", "solide", "sicher", "sichere",
        "sicheren", "sicherer", "sicheres",
    ),
    5: (
        "c1", "fluent", "fluently", "fluency", "courant*", "couramment", "fliessend*", "fluide",
        "advanced", "avance*", "business", "business level", "business fluent", "professional",
        "professionnel*", "niveau professionnel", "full professional proficiency", "excellent*",
        "excellen*", "tres bon*", "tres bonne*", "tres bon niveau",
        "tres bonnes connaissances", "sehr gut*", "strong", "maitrise", "proficient",
        "ausgezeichnet*", "maitrisez", "maitrises", "maitrisant", "beherrschst", "beherrschen",
        "beherrscht",
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


@dataclass(frozen=True)
class _Phrase:
    words: tuple[str, ...]
    stem: bool  # its last word also starts a longer word
    endings: frozenset[str] | None  # what a stem may end with; None: anything

    def ending(self, said: Sequence[str], start: int) -> str | None:
        """What the phrase's last word ends with past the stem, where the phrase starts at
        `start` ("" when exact); None when the phrase is not there."""
        end = start + len(self.words)
        if end > len(said) or tuple(said[start : end - 1]) != self.words[:-1]:
            return None
        last, word = self.words[-1], said[end - 1]
        if word == last:
            return ""
        if not self.stem or not word.startswith(last):
            return None
        rest = word[len(last) :]
        return rest if self.endings is None or rest in self.endings else None


@dataclass(frozen=True)
class _Hit[T]:
    tag: T
    start: int
    size: int
    ending: str
    spelling: str


@dataclass(frozen=True)
class _Table[T]:
    """Phrases by their first word, and the one word stems by their stem: a word is looked up,
    never every phrase tried."""

    first: Mapping[str, tuple[tuple[T, _Phrase], ...]]
    stems: Mapping[str, tuple[tuple[T, _Phrase], ...]]
    longest_stem: int


def _table[T](rows: Iterable[tuple[T, str]], endings: frozenset[str] | None = None) -> _Table[T]:
    first: dict[str, list[tuple[T, _Phrase]]] = {}
    stems: dict[str, list[tuple[T, _Phrase]]] = {}
    for tag, spelling in rows:
        stem = spelling.endswith("*")
        phrase = _Phrase(tuple(_words(spelling.rstrip("*")).split()), stem, endings)
        if stem and len(phrase.words) == 1:
            stems.setdefault(phrase.words[0], []).append((tag, phrase))
        else:
            first.setdefault(phrase.words[0], []).append((tag, phrase))
    return _Table(
        {word: tuple(rows) for word, rows in first.items()},
        {word: tuple(rows) for word, rows in stems.items()},
        max((len(word) for word in stems), default=0),
    )


def _hits[T](table: _Table[T], said: Sequence[str]) -> list[_Hit[T]]:
    """Every phrase of `table` in `said`, leftmost and longest first, never overlapping."""
    found: list[_Hit[T]] = []
    at = 0
    while at < len(said):
        word = said[at]
        candidates = list(table.first.get(word, ()))
        for size in range(1, min(len(word), table.longest_stem) + 1):
            candidates.extend(table.stems.get(word[:size], ()))
        best: _Hit[T] | None = None
        for tag, phrase in candidates:
            ending = phrase.ending(said, at)
            if ending is not None and (best is None or len(phrase.words) > best.size):
                best = _Hit(tag, at, len(phrase.words), ending, " ".join(phrase.words))
        if best is None:
            at += 1
        else:
            found.append(best)
            at += best.size
    return found


KNOWN = tuple(_KNOWN)
_NAMES = _table(((name, s) for name, spellings in _KNOWN.items() for s in spellings), _ENDINGS)
_ALL_NAMES = _table(
    (
        (name, s)
        for table in (_KNOWN, _HERS)
        for name, spellings in table.items()
        for s in spellings
    ),
    _ENDINGS,
)
_CODE_OF = {code: name for name, codes in _CODES.items() for code in codes}
_LEVEL_TABLE = _table((point, s) for point, spellings in _LEVELS.items() for s in spellings)
_WELCOME_TABLE = _table((True, s) for s in _WELCOME)
_NOT_TABLE = _table((True, s) for s in _NOT)
_REQUIRED_TABLE = _table((True, s) for s in _REQUIRED)
_OR_TABLE = _table((True, s) for s in _OR)
_LINK_PHRASE_TABLE = _table((True, s) for s in _LINK_PHRASES)
_AT_LEAST_TABLE = _table((True, s) for s in _AT_LEAST)


def _split(text: str) -> list[str]:
    return _words(text).split()


def _known(name: str) -> frozenset[str]:
    return frozenset(hit.tag for hit in _hits(_ALL_NAMES, _split(name)))


def keys_of(name: str) -> frozenset[str]:
    """The languages a name holds: the known ones among its words, else the name itself as its
    words (case, accents and punctuation aside); empty for a name of no word."""
    known = _known(name)
    if known:
        return known
    plain = _words(name)
    return frozenset({plain}) if plain else frozenset()


def named_in(name: str, quote: str) -> bool:
    """Whether the quote names the language of `name`: a spelling of a known language it holds,
    else the name's own words."""
    plain = _words(name)
    if not plain:
        return False
    known = _known(name)
    if known & {hit.tag for hit in _hits(_NAMES, _split(quote))}:
        return True
    return f" {plain} " in f" {_words(quote)} "


def display(name: str) -> str:
    """A language's name as the app says it: the English name of a known one, else as written."""
    known = sorted(_known(name), key=KNOWN.index)
    return " or ".join(known) if known else " ".join(name.split())


# --- a quote's words --------------------------------------------------------------------------

# A clause's edge in a quote: what separates a list's items or sentences. A colon and brackets
# are not one ("German: C1", "German (C1)"), nor the period of an abbreviation ("mind. B2").
_EDGES = re.compile(r"([,;.!?\n\r\u2022\u00b7|]+|\s[-\u2013\u2014]+\s|[\u2013\u2014])")
_DASH = re.compile(r"\s*[-\u2013\u2014]+\s*")
_ABBREVIATIONS = re.compile(r"\b(mind|min|bzw|ggf|ggfs|evtl|inkl|ca|resp)\.", re.IGNORECASE)
_WORD = re.compile(r"[^\W_]+")
EDGE = ""


@dataclass(frozen=True)
class _Said:
    """A quote as its words, EDGE between clauses, whether each word had a capital, and whether
    each EDGE is a dash alone ("German, a dash, fluent")."""

    words: tuple[str, ...]
    capital: tuple[bool, ...]
    dash: tuple[bool, ...]


@functools.lru_cache(maxsize=4096)
def _said(quote: str) -> _Said:
    words: list[str] = []
    capital: list[bool] = []
    dash: list[bool] = []
    text = _ABBREVIATIONS.sub(r"\1", unicodedata.normalize("NFKC", quote)).replace("&", " and ")
    for index, part in enumerate(_EDGES.split(text)):
        if index % 2:
            alone = bool(_DASH.fullmatch(part))
            if words and words[-1] == EDGE:
                dash[-1] = dash[-1] and alone
            elif words:
                words.append(EDGE)
                capital.append(False)
                dash.append(alone)
            continue
        for piece in _WORD.findall(part):
            for at, word in enumerate(_split(piece)):
                words.append(word)
                capital.append(at == 0 and piece[0].isupper())
                dash.append(False)
    while words and words[-1] == EDGE:
        words.pop()
        capital.pop()
        dash.pop()
    return _Said(tuple(words), tuple(capital), tuple(dash))


def welcome_said(quote: str) -> bool:
    said = _said(quote).words
    if _hits(_WELCOME_TABLE, said):
        return True
    return any(
        word == "plus" and _PLUS_AFTER & set(said[max(0, at - 2) : at])
        for at, word in enumerate(said)
    )


def negated(quote: str) -> bool:
    return bool(_hits(_NOT_TABLE, _said(quote).words))


Kind = Literal["edge", "name", "level", "level_noun", "need", "link", "noun", "other"]
_BEFORE = frozenset({"edge", "name", "level", "level_noun", "need", "link", "noun"})
_AFTER = frozenset({"edge", "name", "level", "need", "link", "noun"})
_SIGNALS = frozenset({"level", "level_noun", "need"})
_BETWEEN = frozenset({"name", "link", "noun"})


@dataclass(frozen=True)
class _Reading:
    """What rules 2 to 4 read of a quote: each word's kind, the known languages it names, and the
    words of a link phrase ("ainsi que")."""

    said: _Said
    kinds: tuple[Kind, ...]
    names: tuple[_Hit[str], ...]
    joined: frozenset[int]


@functools.lru_cache(maxsize=4096)
def _reading(quote: str) -> _Reading:
    said = _said(quote)
    words = said.words
    kinds: list[Kind] = [_word_kind(word) for word in words]
    for hit in _hits(_REQUIRED_TABLE, words):
        kinds[hit.start : hit.start + hit.size] = ["need"] * hit.size
    for hit in _hits(_LEVEL_TABLE, words):
        noun = hit.size == 1 and words[hit.start] in _LEVEL_NOUNS
        kinds[hit.start : hit.start + hit.size] = ["level_noun" if noun else "level"] * hit.size
    joined: set[int] = set()
    for hit in _hits(_LINK_PHRASE_TABLE, words):
        kinds[hit.start : hit.start + hit.size] = ["link"] * hit.size
        joined.update(range(hit.start, hit.start + hit.size))
    for hit in _hits(_AT_LEAST_TABLE, words):
        if _kind_in(kinds, hit.start + hit.size) == "level":
            kinds[hit.start : hit.start + hit.size] = ["link"] * hit.size
    names = tuple(_hits(_NAMES, words))
    for hit in names:
        kinds[hit.start : hit.start + hit.size] = ["name"] * hit.size
    for hit in names:
        _after_name(kinds, said, hit.start + hit.size)
    return _Reading(said, tuple(kinds), names, frozenset(joined))


def _after_name(kinds: list[Kind], said: _Said, at: int) -> None:
    """The word after a name: a dash alone before a level is a label ("Deutsch - fließend"), and
    "speaking" a noun of the language where _SPEAKING_WORD's comment says."""
    if at >= len(kinds):
        return
    word, then = said.words[at], _kind_in(kinds, at + 1)
    following = said.words[at + 1] if at + 1 < len(kinds) else EDGE
    if word == EDGE and said.dash[at] and then == "level":
        kinds[at] = "link"
    elif word == _SPEAKING_WORD and kinds[at] == "other":
        if then in ("edge", "level", "need") or following in _SPEAKING_COPULAS:
            kinds[at] = "noun"
        elif following in _PERSONS and _kind_in(kinds, at + 2) in ("edge", "link", "level", "need"):
            kinds[at : at + 2] = ["noun", "need"]


def _word_kind(word: str) -> Kind:
    if word == EDGE:
        return "edge"
    if word in _LINKS:
        return "link"
    return "noun" if word in _NOUNS else "other"


def _kind_in(kinds: Sequence[Kind], at: int) -> Kind:
    return kinds[at] if 0 <= at < len(kinds) else "edge"


def _kind(reading: _Reading, at: int) -> Kind:
    return _kind_in(reading.kinds, at)


def _as_language(reading: _Reading, start: int, size: int, spelling: str) -> bool:
    """Rule 3: the spelling at `start` names the language, not a place, a people, a nationality
    or a word."""
    if spelling in _ALSO_PLACE:
        return False
    before, after = _kind(reading, start - 1), _kind(reading, start + size)
    if spelling in _CAPITAL_ONLY and not _capital_language(reading, start, size, before, after):
        return False
    if start > 0 and reading.said.words[start - 1] in _COPULAS and after not in _AFTER_COPULA:
        return False
    return before in _BEFORE and after in _AFTER


_AFTER_COPULA = frozenset({"level", "need", "noun"})
_AFTER_CAPITAL = frozenset({"edge", "level", "noun"})


def _capital_language(reading: _Reading, start: int, size: int, before: Kind, after: Kind) -> bool:
    """A _CAPITAL_ONLY spelling names the language with its capital; at a clause's start, where a
    verb takes one too ("Polish the required UI flows"), only before an edge, a level or a noun,
    with a level close to it."""
    if not reading.said.capital[start]:
        return False
    if before != "edge":
        return True
    return after in _AFTER_CAPITAL and _close(reading, start, size, frozenset({"level"}))


def _close(reading: _Reading, start: int, size: int, signals: frozenset[Kind] = _SIGNALS) -> bool:
    """Rule 4: a level or requirement word within GAP words of the spelling, in its clause."""
    for step, at in ((-1, start - 1), (1, start + size)):
        between = 0
        while between <= GAP:
            kind = _kind(reading, at)
            if kind in signals:
                return True
            if kind not in _BETWEEN:
                break
            at += step
            between += 1
    return False


def _joined(reading: _Reading, low: int, high: int) -> bool:
    words = reading.said.words
    return low < high and all(
        words[at] in _JOINS or at in reading.joined for at in range(low, high)
    )


def _alternative(
    reading: _Reading, quote: str, spots: Sequence[tuple[int, int]], others: Sequence[_Hit[str]]
) -> bool:
    """Rule 2: whether another language she lists (`others`) may be accepted instead of the one
    asked (at `spots`): a slash or an alternative word anywhere in the quote, or a word other
    than _JOINS between them (nothing between them, too: "Bilingue français allemand")."""
    if not others:
        return False
    if "/" in quote or _hits(_OR_TABLE, reading.said.words):
        return True
    marks = sorted({*spots, *((hit.start, hit.size) for hit in reading.names)})
    run_of: dict[int, int] = {}
    run, end = 0, None
    for start, size in marks:
        if end is not None and not _joined(reading, end, start):
            run += 1
        run_of[start] = run
        end = start + size
    return len({run_of[start] for start, _ in spots} | {run_of[hit.start] for hit in others}) > 1


def _plain_spots(reading: _Reading, name: str) -> list[tuple[int, int]]:
    """Where a name the list does not hold stands in the quote, as its own words."""
    words = tuple(_split(name))
    said = reading.said.words
    return [
        (at, len(words))
        for at in range(len(said) - len(words) + 1)
        if words and said[at : at + len(words)] == words
    ]


Status = Literal["required", "welcome", "unsure"]


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

    def status(self, theirs: frozenset[str] = frozenset()) -> Status:
        """Required, welcome or unsure (module docstring, rules 1 to 4); `theirs` are the
        languages the person declares (`keys_of` of each)."""
        welcome = welcome_said(self.quote)
        if not self.required:
            return "welcome" if welcome else "unsure"
        if welcome or negated(self.quote):
            return "unsure"
        reading = _reading(self.quote)
        own = frozenset().union(*(keys_of(name) for name in self.names))
        spots = [
            (hit.start, hit.size, hit.spelling, hit.ending in _SKILLS)
            for hit in reading.names
            if hit.tag in own
        ]
        for name in self.names:
            if not _known(name):
                spots.extend((at, size, "", False) for at, size in _plain_spots(reading, name))
        others = [hit for hit in reading.names if hit.tag in theirs and hit.tag not in own]
        if _alternative(reading, self.quote, [spot[:2] for spot in spots], others):
            return "unsure"
        for start, size, spelling, skill in spots:
            if _as_language(reading, start, size, spelling) and (
                skill or _close(reading, start, size)
            ):
                return "required"
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


# Entries of her profile that name no language ("N/A", "none", "aucune"): as no entry at all.
# Never "no": Norwegian's ISO code.
_NO_LANGUAGE = frozenset({
    "n a", "na", "none", "nothing", "nil", "null", "not applicable", "tbd", "x",
    "aucun", "aucune", "rien", "neant", "non", "non applicable", "sans",
    "keine", "kein", "nichts", "nein",
})  # fmt: skip


def absent(name: str) -> bool:
    """Whether an entry of her profile names no language: no word, or one of _NO_LANGUAGE."""
    plain = _words(name)
    return not plain or plain in _NO_LANGUAGE


def unread(spoken: Sequence[Spoken]) -> list[str]:
    """Her entries the list cannot read, each once, as she first wrote it: each turns a badge
    into a notice."""
    found: dict[str, str] = {}
    for one in spoken:
        if not absent(one.name) and hers(one.name) is None:
            found.setdefault(_words(one.name), " ".join(one.name.split()))
    return list(found.values())


def hers(name: str) -> frozenset[str] | None:
    """The languages one entry of her profile names, None when the list cannot read it: its
    known names, or an ISO code standing alone or before level words."""
    known = _known(name)
    if known:
        return known
    said = _split(name)
    if (
        said
        and said[0] in _CODE_OF
        and (len(said) == 1 or isinstance(level_of(" ".join(said[1:]), "low"), int))
    ):
        return frozenset({_CODE_OF[said[0]]})
    return None


@dataclass(frozen=True)
class _Profile:
    keys: tuple[frozenset[str], ...]  # each entry's languages, an unread one as its own words
    theirs: frozenset[str]
    unread: str | None  # the first entry the list cannot read, as she wrote it


@functools.lru_cache(maxsize=256)
def _profile(spoken: tuple[Spoken, ...]) -> _Profile:
    """Her entries read, `absent` ones already left out by `compare`."""
    keys: list[frozenset[str]] = []
    for one in spoken:
        read = hers(one.name)
        keys.append(read if read is not None else keys_of(one.name))
    first = unread(spoken)
    return _Profile(tuple(keys), frozenset().union(*keys), first[0] if first else None)


# --- the comparison ---------------------------------------------------------------------------


GapKind = Literal["missing", "unread", "unsure", "level", "written_in"]


@dataclass(frozen=True)
class Gap:
    """One thing the comparison found: `missing` (a required language she does not declare: the
    badge), `unread` (one she may declare under a name the list cannot read), `unsure` (one that
    may be required), `level` (asked at a level that may be above hers), `written_in` (the ad's
    language, with no language asked)."""

    kind: GapKind
    language: str  # as the app says it: "German", "German or Dutch"
    quote: str | None = None
    asked_level: str | None = None
    spoken: Spoken | None = None
    unread: str | None = None  # her entry the list cannot read, for `unread`


def level_of(level: str | None, reading: Literal["low", "high"]) -> int | Literal["unknown"] | None:
    """A level on the scale: None when none is written, "unknown" when its words are not the
    scale's or a negation stands next to one; else the lowest reading of its words (a person's
    level) or the highest (a posting's)."""
    if level is None or not _words(level):
        return None
    said = _split(level)
    found = _hits(_LEVEL_TABLE, said)
    if not found:
        return "unknown"
    nots = {hit.start for hit in _hits(_NOT_TABLE, said)}
    if any(at in nots for hit in found for at in range(max(0, hit.start - 2), hit.start)):
        return "unknown"
    points = [hit.tag for hit in found]
    return min(points) if reading == "low" else max(points)


def _declared(names: Iterable[str], spoken: Sequence[Spoken], mine: _Profile) -> list[Spoken]:
    wanted = frozenset().union(*(keys_of(name) for name in names))
    return [one for one, keys in zip(spoken, mine.keys, strict=True) if keys & wanted]


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
    spoken = [one for one in spoken if not absent(one.name)]
    if not spoken or read is None:
        return []
    profile = _profile(tuple(spoken))
    gaps: list[Gap] = []
    asks = False
    for item in read.asked:
        status = item.status(profile.theirs)
        if status == "welcome":
            continue
        asks = True
        language = " or ".join(dict.fromkeys(display(name) for name in item.names))
        mine = _declared(item.names, spoken, profile)
        if mine:
            gap = _level_gap(item, mine, language)
            if gap is not None:
                gaps.append(gap)
        elif status == "unsure":
            gaps.append(Gap("unsure", language, item.quote))
        elif profile.unread is not None:
            gaps.append(Gap("unread", language, item.quote, unread=profile.unread))
        else:
            gaps.append(Gap("missing", language, item.quote))
    if not asks and read.written_in and not _declared([read.written_in], spoken, profile):
        gaps.append(Gap("written_in", display(read.written_in)))
    return gaps


def missing(stored: object, spoken: Sequence[Spoken]) -> list[str]:
    """The required languages she does not declare, as the app says them: the badge's."""
    return list(
        dict.fromkeys(gap.language for gap in compare(stored, spoken) if gap.kind == "missing")
    )


EMPTY: dict[str, Any] = {"written_in": None, "asked": []}


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
