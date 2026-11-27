"""Jobs J35 (#858): the languages a posting asks for, compared with the person's
(app/jobs/languages.py). The rule follows the Language Gate of ai-job-search: a required language
the profile does not declare is outside the criteria (the badge); a declared one asked at a level
that may be above hers is a notice, never a silent pass; a welcome one gives nothing; an ad
written in a language she does not declare, with no language asked, is a notice. The accept and
refuse tables below are built from that rule, in the three languages of the postings."""

from typing import Any

import pytest

from app.jobs import languages
from app.jobs.languages import NATIVE, Spoken, compare, keys_of, level_of, missing, named_in

# --- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "keys"),
    [
        ("German", {"German"}),
        ("german", {"German"}),
        ("Deutsch", {"German"}),
        ("allemand", {"German"}),
        ("Allemande", {"German"}),
        ("Deutschkenntnisse", {"German"}),
        ("Schweizerdeutsch", {"German"}),
        ("Swiss German", {"German"}),
        ("English", {"English"}),
        ("ENGLISH", {"English"}),
        ("Anglais", {"English"}),
        ("anglais courant", {"English"}),
        ("Englisch", {"English"}),
        ("Englischkenntnisse", {"English"}),
        ("anglophone", {"English"}),
        ("Français", {"French"}),
        ("FRANÇAIS (langue maternelle)", {"French"}),
        ("Francais", {"French"}),
        ("Französisch", {"French"}),
        ("francophone", {"French"}),
        ("Espagnol", {"Spanish"}),
        ("Spanisch", {"Spanish"}),
        ("Español", {"Spanish"}),
        ("Mandarin", {"Chinese"}),
        ("Chinois", {"Chinese"}),
        ("Mandarin Chinese", {"Chinese"}),
        ("Cantonais", {"Cantonese"}),
        ("Dutch", {"Dutch"}),
        ("Néerlandais", {"Dutch"}),
        ("Niederländisch", {"Dutch"}),
        ("Nederlands", {"Dutch"}),
        ("Flemish", {"Dutch"}),
        ("Portugais", {"Portuguese"}),
        ("Italiano", {"Italian"}),
        ("Polski", {"Polish"}),
        ("Russe", {"Russian"}),
        ("Türkisch", {"Turkish"}),
        ("Grec", {"Greek"}),
        ("Japonais", {"Japanese"}),
        ("Arabe", {"Arabic"}),
        ("Tahitien", {"Tahitian"}),
        ("reo tahiti", {"Tahitian"}),
        ("English and French", {"English", "French"}),
        ("Anglais / Allemand", {"English", "German"}),
        ("Wolof", {"Wolof"}),
        ("Créole réunionnais", {"Creole"}),
        ("Hochdeutsch", {"German"}),
        ("Castillan", {"Spanish"}),
        # The names in Spanish, Italian, Portuguese and Dutch: her profile may hold them.
        ("Inglés", {"English"}),
        ("Tedesco", {"German"}),
        ("Alemão", {"German"}),
        ("Engels", {"English"}),
        # A sign language is its own language.
        ("Langue des signes française", {"French Sign Language"}),
        ("LANGUE DES SIGNES FRANÇAISE (LSF)", {"French Sign Language"}),
        ("Deutsche Gebärdensprache", {"German Sign Language"}),
        ("British Sign Language", {"British Sign Language"}),
        ("Langue des signes", {"Sign language"}),
        # Not in the list: kept as written, case, accents and punctuation aside.
        ("Klingon", {"klingon"}),
        ("Quenya", {"quenya"}),
        ("EN", {"en"}),
        # A German stem with another word glued to it is that word.
        ("Deutschland", {"deutschland"}),
        ("deutschlandweit", {"deutschlandweit"}),
        ("Deutschlehrer", {"deutschlehrer"}),
        # Near misses: a country, a longer word, another language's name inside.
        ("Germany", {"germany"}),
        ("polished", {"polished"}),
        ("Englishman", {"englishman"}),
        ("Thailand", {"thailand"}),
        ("", set()),
        ("   ", set()),
        ("--", set()),
    ],
)
def test_the_languages_a_name_holds(name: str, keys: set[str]) -> None:
    assert keys_of(name) == keys


@pytest.mark.parametrize(
    ("name", "quote", "named"),
    [
        ("German", "Verhandlungssichere Deutschkenntnisse", True),
        ("German", "Fluent German required", True),
        ("German", "allemand courant exigé", True),
        ("German", "Fluent English required", False),
        ("German", "Dutch required", False),
        ("German", "Standort: Germany", False),
        ("Dutch", "Deutsch fließend", False),
        ("Chinese", "Mandarin courant", True),
        ("English", "Environnement anglophone", True),
        ("English", "Fließendes Englisch in Wort und Schrift", True),
        ("French", "Französisch- oder Englischkenntnisse", True),
        ("English", "Französisch- oder Englischkenntnisse", True),
        ("Spanish", "L'espagnol courant est un atout", True),
        ("Wolof", "Wolof parlé", True),
        ("Wolof", "Pulaar parlé", False),
        ("Klingon", "Klingon parlé", True),
        ("German", "Standort: Berlin, Deutschland", False),
        ("German", "Reisetätigkeit deutschlandweit", False),
        ("German", "Die Deutsche Bank sucht", True),
        ("German", "Deutschsprachige Kunden", True),
        ("Italian", "Standort: Mailand, Italien", True),
        ("Tedesco", "Tedesco fluente", True),
        ("Russian", "Verhandlungssichere Deutschkenntnisse", False),
        ("", "German", False),
    ],
)
def test_a_quote_names_the_language(name: str, quote: str, named: bool) -> None:
    assert named_in(name, quote) is named


# --- required, welcome or unsure ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "welcome"),
    [
        ("German is a plus", True),
        ("Knowledge of Dutch is an advantage", True),
        ("Nice to have: Italian", True),
        ("Spanish preferred", True),
        ("L'espagnol courant est un atout", True),
        ("L'allemand serait un plus", True),
        ("Italien apprécié", True),
        ("Anglais souhaité", True),
        ("Deutschkenntnisse von Vorteil", True),
        ("Französischkenntnisse wünschenswert", True),
        ("Idealerweise Französisch", True),
        ("German is a big plus", True),
        ("German is a strong plus", True),
        ("German would be a real plus", True),
        ("German is beneficial", True),
        ("German optional", True),
        ("German good to have", True),
        ("German is welcome", True),
        ("An added advantage: German", True),
        ("l'allemand est un vrai plus", True),
        ("allemand bienvenu", True),
        ("Allemand appréciable", True),
        ("Allemand facultatif", True),
        ("Deutsch erwünscht", True),
        ("Deutsch willkommen", True),
        ("Deutsch bevorzugt", True),
        ("Deutsch gerne gesehen", True),
        ("Deutsch ist ein Plus", True),
        ("Deutsch ist ein Pluspunkt", True),
        ("Fluent German required", False),
        ("Plus: fluent German", False),
        ("German plus English required", False),
        ("Must speak English", False),
        ("Anglais courant exigé", False),
        ("Anglais courant indispensable", False),
        ("Sehr gute Deutschkenntnisse zwingend erforderlich", False),
        ("Plus de 5 ans d'expérience, anglais courant", False),
        ("Deutsch fließend", False),
        ("", False),
    ],
)
def test_the_words_of_a_welcome_language(quote: str, welcome: bool) -> None:
    assert languages.welcome_said(quote) is welcome


# The badge's side (module docstring, rules 1 to 4): the reader says required, and the quote holds
# no welcome word and no negation, names no other language she declares, names the language as a
# language and holds a level or requirement word close to it. The person: French native, English
# C1, no German (the review of #860). Every quote is a refuse when the reader says not required.
THEIRS = frozenset({"French", "English"})

REQUIRED = [
    # English.
    ("German", "Fluent German required"),
    ("German", "FLUENT GERMAN REQUIRED"),
    ("German", "  Fluent   German   required.  "),
    ("German", "Fluent-German-required"),
    ("German", "C1 in German (written and spoken)"),
    ("German", "Must speak German"),
    ("German", "Knowledge of German is mandatory"),
    ("German", "German is required for this role"),
    ("German", "Native German speaker"),
    ("German", "Business German required"),
    ("German", "Languages: German (C1)"),
    ("German", "Very good command of German"),
    ("German", "German language skills are essential"),
    ("German", "Ability to speak German fluently"),
    ("German", "Fluent German and Dutch"),
    ("Dutch", "Fluent German and Dutch"),
    ("Polish", "Fluent Polish required"),
    ("Wolof", "Wolof required"),
    ("Klingon", "Klingon required"),
    # French.
    ("German", "bonne maîtrise de l'allemand"),
    ("German", "Allemand courant exigé"),
    ("German", "ALLEMAND COURANT EXIGÉ"),
    ("German", "Maîtrise de l'allemand indispensable"),
    ("German", "Connaissance de l'allemand obligatoire"),
    ("German", "Allemand : niveau C1 requis"),
    ("German", "Vous parlez allemand couramment"),
    ("Italian", "Langue italienne exigée"),
    ("Spanish", "Espagnol bilingue impératif"),
    # German.
    ("German", "Verhandlungssichere Deutschkenntnisse"),
    ("German", "Deutschkenntnisse zwingend erforderlich"),
    ("German", "Deutsch fließend in Wort und Schrift"),
    ("German", "Sehr gute Deutschkenntnisse (C1)"),
    ("German", "Deutsch: fließend"),
    ("German", "Deutsch als Muttersprache"),
    ("German", "verhandlungssicher deutschsprachig"),
    ("Italian", "Italienisch verhandlungssicher"),
    ("Dutch", "Niederländischkenntnisse sind Voraussetzung"),
]

NOT_REQUIRED = [
    # Rule 1, a welcome word.
    ("German", "German is a plus"),
    ("German", "German is a big plus"),
    ("German", "Fluent German is a strong plus"),
    ("German", "German would be a real plus"),
    ("German", "German is an advantage"),
    ("German", "German is beneficial"),
    ("German", "German optional"),
    ("German", "German good to have"),
    ("German", "Nice to have: German"),
    ("German", "German preferred"),
    ("German", "Ideally fluent German"),
    ("German", "L'allemand serait un plus"),
    ("German", "l'allemand est un vrai plus"),
    ("German", "allemand bienvenu"),
    ("German", "allemand appréciable"),
    ("German", "Allemand courant apprécié"),
    ("German", "Un atout : l'allemand courant"),
    ("German", "allemand souhaité"),
    ("German", "Allemand facultatif"),
    ("German", "Deutsch erwünscht"),
    ("German", "Deutsch willkommen"),
    ("German", "Deutsch bevorzugt"),
    ("German", "Deutschkenntnisse von Vorteil"),
    ("German", "Deutsch wünschenswert"),
    ("German", "Deutsch gerne gesehen"),
    ("German", "Fließend Deutsch ist ein Plus"),
    ("German", "Idealerweise Deutsch fließend"),
    ("German", "Deutsch fließend, Englisch von Vorteil"),
    # Rule 1, a negation.
    ("German", "no German required"),
    ("German", "German not required"),
    ("German", "German isn't required"),
    ("German", "No need to speak German"),
    ("German", "Fluent English, without German"),
    ("German", "Neither German nor Dutch required"),
    ("German", "l'allemand n'est pas exigé"),
    ("German", "sans allemand"),
    ("German", "Aucune connaissance de l'allemand requise"),
    ("German", "kein Deutsch erforderlich"),
    ("German", "Deutsch nicht erforderlich"),
    ("German", "keine Deutschkenntnisse nötig"),
    ("German", "Ohne Deutschkenntnisse"),
    # Rule 2, another language she declares: an alternative the reader may have split.
    ("German", "fluent in German or French"),
    ("German", "German and/or English"),
    ("German", "Fluent German or English required"),
    ("German", "Allemand ou anglais courant exigé"),
    ("German", "Deutsch oder Englisch fließend"),
    ("German", "Gute Deutsch- und Englischkenntnisse"),
    ("German", "Deutschkenntnisse für unsere französischen Kunden"),
    # Rule 3, a people, a market, a place, a company or another word, not the language.
    ("German", "experience of the German market"),
    ("German", "Knowledge of the German market is required"),
    ("German", "you will serve German speaking clients"),
    ("German", "Experience with German-speaking markets is mandatory"),
    ("German", "German law knowledge required"),
    ("German", "German business culture required"),
    ("German", "Strong German clients network required"),
    ("German", "Erfahrung im deutschen Markt"),
    ("German", "Erfahrung im deutschen Markt zwingend erforderlich"),
    ("German", "Die Deutsche Bank sucht"),
    ("German", "Deutsche Kunden betreuen ist Voraussetzung"),
    ("German", "deutschsprachige Kunden zwingend"),
    ("German", "Standort: Berlin, Deutschland"),
    ("German", "Reisetätigkeit deutschlandweit"),
    ("German", "le marché allemand exige de la rigueur"),
    ("German", "Clients allemands, anglais courant exigé"),
    ("Italian", "Standort: Mailand, Italien"),
    ("Italian", "in Italien fließend Englisch sprechen"),
    ("Italian", "Italien courant exigé"),
    ("Corsican", "Poste basé en Corse"),
    ("Corsican", "Poste basé en Corse, maîtrise requise"),
    ("Basque", "Poste basé au Pays basque"),
    ("Catalan", "une entreprise catalane"),
    ("Polish", "you will polish the UI"),
    ("Polish", "Must polish, test and ship"),
    ("Spanish", "Espagne, anglais courant exigé"),
    # Rule 4, no level or requirement word close to it.
    ("German", "German"),
    ("German", "Deutsch"),
    ("German", "Allemand"),
    ("German", "German speaker"),
    ("German", "Our clients speak German"),
    ("German", "Documents are written in German"),
    ("German", "German is spoken in the office"),
    ("German", "Allemand parlé dans l'équipe"),
    ("German", "Fluent Dutch, German"),
    ("German", "Ihr Profil: Deutsch"),
    ("German", "Required: a degree in physics and German"),
    ("German", "Deutschsprachig"),
    ("German", ""),
]


@pytest.mark.parametrize(("name", "quote"), REQUIRED)
def test_a_required_language(name: str, quote: str) -> None:
    assert languages.Asked(name, None, True, (), quote).status(THEIRS) == "required"
    assert languages.Asked(name, None, False, (), quote).status(THEIRS) == "unsure"


@pytest.mark.parametrize(("name", "quote"), NOT_REQUIRED)
def test_a_language_that_is_not_required(name: str, quote: str) -> None:
    assert languages.Asked(name, None, True, (), quote).status(THEIRS) == "unsure"
    assert languages.Asked(name, None, False, (), quote).status(THEIRS) != "required"


@pytest.mark.parametrize(
    ("required", "quote", "status"),
    [
        (True, "Fluent German required", "required"),
        (True, "German is a plus", "unsure"),
        (False, "German is a plus", "welcome"),
        (False, "Fluent German", "unsure"),
    ],
)
def test_required_welcome_or_unsure(required: bool, quote: str, status: str) -> None:
    assert languages.Asked("German", None, required, (), quote).status() == status


def test_an_alternative_she_does_not_declare_keeps_the_badge() -> None:
    either = languages.Asked("German", None, True, ("Dutch",), "German or Dutch required")
    assert either.status(THEIRS) == "required"
    assert either.status(frozenset({"Dutch"})) == "required"
    split = languages.Asked("German", None, True, (), "German or Dutch required")
    assert split.status(frozenset({"Dutch"})) == "unsure"


# --- levels -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "low", "high"),
    [
        (None, None, None),
        ("", None, None),
        ("   ", None, None),
        ("A1", 1, 1),
        ("A2", 2, 2),
        ("B1", 3, 3),
        ("B2", 4, 4),
        ("C1", 5, 5),
        ("c1", 5, 5),
        ("C1+", 5, 5),
        ("C2", 6, 6),
        ("B2/C1", 4, 5),
        ("B2-C1", 4, 5),
        ("B2 (working towards C1)", 4, 5),
        ("fluent", 5, 5),
        ("Fluent", 5, 5),
        ("fluently", 5, 5),
        ("courant", 5, 5),
        ("Courante", 5, 5),
        ("couramment", 5, 5),
        ("fließend", 5, 5),
        ("fliessend", 5, 5),
        ("Fließendes", 5, 5),
        ("business level", 5, 5),
        ("Business fluent", 5, 5),
        ("full professional proficiency", 5, 5),
        ("professional working proficiency", 4, 4),
        ("advanced", 5, 5),
        ("avancé", 5, 5),
        ("très bon niveau", 5, 5),
        ("bon niveau", 4, 4),
        ("sehr gute Kenntnisse", 4, 4),
        ("gute Kenntnisse", 3, 3),
        ("intermediate", 3, 3),
        ("intermédiaire", 3, 3),
        ("conversational", 3, 3),
        ("scolaire", 2, 2),
        ("Grundkenntnisse", 2, 2),
        ("notions de base", 2, 2),
        ("notions", 1, 1),
        ("bilingual", 6, 6),
        ("bilingue", 6, 6),
        ("zweisprachig", 6, 6),
        ("near native", 6, 6),
        ("quasi bilingue", 6, 6),
        ("verhandlungssicher", 6, 6),
        ("Verhandlungssichere", 6, 6),
        ("Native or bilingual proficiency", 6, 6),
        ("native", NATIVE, NATIVE),
        ("Native speaker", NATIVE, NATIVE),
        ("mother tongue", NATIVE, NATIVE),
        ("langue maternelle", NATIVE, NATIVE),
        ("Langue maternelle", NATIVE, NATIVE),
        ("natif", NATIVE, NATIVE),
        ("Muttersprache", NATIVE, NATIVE),
        ("muttersprachlich", NATIVE, NATIVE),
        ("Muttersprachniveau", NATIVE, NATIVE),
        ("fluent, ideally native", 5, NATIVE),
        # Words the scale does not know, and a negation next to a word it knows.
        ("TOEIC 850", "unknown", "unknown"),
        ("IELTS 7.0", "unknown", "unknown"),
        ("level 4", "unknown", "unknown"),
        ("lu, écrit", "unknown", "unknown"),
        ("non native", "unknown", "unknown"),
        ("non-native", "unknown", "unknown"),
        ("not fluent", "unknown", "unknown"),
        ("pas courant", "unknown", "unknown"),
        ("nicht fließend", "unknown", "unknown"),
        ("keine Muttersprache", "unknown", "unknown"),
    ],
)
def test_a_level_on_the_scale(level: str | None, low: Any, high: Any) -> None:
    assert level_of(level, "low") == low
    assert level_of(level, "high") == high


# --- the comparison -----------------------------------------------------------------------------

ME = (
    Spoken("Français", "Langue maternelle"),
    Spoken("Anglais", "B2"),
    Spoken("Espagnol", None),
)


def asked(
    name: str,
    level: str | None = None,
    required: bool = True,
    quote: str | None = None,
    alternatives: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "name": name,
        "level": level,
        "required": required,
        "alternatives": list(alternatives),
        "quote": quote or " ".join(part for part in (level, name, "required") if part),
    }


def stored(*items: dict[str, Any], written_in: str | None = None) -> dict[str, Any]:
    return {"written_in": written_in, "asked": list(items)}


@pytest.mark.parametrize(
    ("languages_read", "kinds"),
    [
        # A required language she does not declare: the badge, whatever its level.
        (stored(asked("German", "fluent")), ["missing"]),
        (stored(asked("German")), ["missing"]),
        (stored(asked("Deutsch", "Muttersprache")), ["missing"]),
        (stored(asked("Wolof")), ["missing"]),
        # Welcome: nothing; unsure (the reader and the quote disagree): a notice, never the badge.
        (stored(asked("German", required=False, quote="German is a plus")), []),
        (stored(asked("German", "courant", False, "L'allemand courant est un atout")), []),
        (stored(asked("German", quote="German is a plus")), ["unsure"]),
        (stored(asked("German", required=False, quote="German")), ["unsure"]),
        # Declared: the level asked against hers.
        (stored(asked("English")), []),
        (stored(asked("English", "B2")), []),
        (stored(asked("English", "B1")), []),
        (stored(asked("English", "conversational")), []),
        (stored(asked("English", "bon niveau")), []),
        (stored(asked("English", "C1")), ["level"]),
        (stored(asked("English", "fluent")), ["level"]),
        (stored(asked("Englisch", "verhandlungssicher")), ["level"]),
        (stored(asked("English", "bilingue")), ["level"]),
        (stored(asked("English", "TOEIC 850")), ["level"]),
        (stored(asked("English", "fluent", required=False, quote="Fluent English")), ["level"]),
        (stored(asked("French", "native")), []),
        (stored(asked("French", "C2")), []),
        (stored(asked("French", "excellent niveau rédactionnel")), []),
        (stored(asked("French", "TOEIC 900")), []),
        (stored(asked("Spanish")), []),
        (stored(asked("Spanish", "courant")), ["level"]),
        # One of several accepted languages.
        (stored(asked("German", alternatives=("English",))), []),
        (stored(asked("German", "fluent", alternatives=("English",))), ["level"]),
        (stored(asked("German", alternatives=("Dutch",))), ["missing"]),
        # Several languages, in the posting's order.
        (stored(asked("German", "fluent"), asked("English", "C1")), ["missing", "level"]),
        (stored(asked("English"), asked("Dutch")), ["missing"]),
        # The ad's own language: a notice only when no language is asked.
        (stored(written_in="German"), ["written_in"]),
        (stored(written_in="Deutsch"), ["written_in"]),
        (stored(written_in="French"), []),
        (stored(written_in="anglais"), []),
        (stored(asked("English"), written_in="German"), []),
        (stored(asked("German", quote="German is a plus"), written_in="German"), ["unsure"]),
        (
            stored(
                asked("Italian", required=False, quote="Italian is a plus"), written_in="German"
            ),
            ["written_in"],
        ),
        (stored(), []),
        # Never read, or not the stored shape: nothing.
        (None, []),
        ("German", []),
        ({"asked": "German"}, []),
        ({"asked": [1, {"name": 3}, {"name": "German", "quote": "German"}]}, []),
    ],
)
def test_the_comparison(languages_read: Any, kinds: list[str]) -> None:
    assert [gap.kind for gap in compare(languages_read, ME)] == kinds


@pytest.mark.parametrize(
    ("mine", "name", "level", "passes"),
    [
        (Spoken("Deutsch", "Muttersprache"), "German", "verhandlungssicher", True),
        (Spoken("Allemand", "bilingue"), "German", "verhandlungssicher", True),
        (Spoken("German", "C1"), "German", "verhandlungssicher", False),
        (Spoken("German", "fließend"), "German", "verhandlungssicher", False),
        (Spoken("English", "fluent"), "English", "business fluent", True),
        (Spoken("English", "Full professional proficiency"), "English", "fluent", True),
        (Spoken("English", "Professional working proficiency"), "English", "fluent", False),
        (Spoken("English", "Native or bilingual proficiency"), "English", "native", False),
        (Spoken("English", "non native"), "English", "fluent", False),
        (Spoken("English", "B2 (working towards C1)"), "English", "C1", False),
        # With no level written, the level her name says.
        (Spoken("Anglais courant", None), "English", "fluent", True),
        (Spoken("English (native)", None), "English", "bilingual", True),
        (Spoken("Anglais", None), "English", "fluent", False),
        (Spoken("Anglais", None), "English", None, True),
        (Spoken("Anglais", "TOEIC 950"), "English", "B2", False),
        (Spoken("Anglais", "TOEIC 950"), "English", None, True),
    ],
)
def test_her_level_against_the_one_asked(
    mine: Spoken, name: str, level: str | None, passes: bool
) -> None:
    assert (compare(stored(asked(name, level)), [mine]) == []) is passes


def test_a_declared_language_counts_at_its_best_entry() -> None:
    mine = [Spoken("English", "B1"), Spoken("Anglais", "C2")]
    assert compare(stored(asked("English", "fluent")), mine) == []


def test_a_profile_with_no_language_gives_nothing() -> None:
    assert compare(stored(asked("German", "fluent"), written_in="German"), []) == []
    assert missing(stored(asked("German")), []) == []


def test_the_badges_languages_and_the_level_gap_keep_both_sides() -> None:
    read = stored(
        asked("German", "fluent"),
        asked("Deutsch", "C1"),
        asked("Dutch", alternatives=("Allemand",)),
        asked("English", "C1", quote="English C1 required"),
    )
    assert missing(read, ME) == ["German", "Dutch or German"]
    [gap] = [gap for gap in compare(read, ME) if gap.kind == "level"]
    assert (gap.language, gap.asked_level, gap.quote) == ("English", "C1", "English C1 required")
    assert gap.spoken == Spoken("Anglais", "B2")


def test_an_unknown_language_is_said_as_written() -> None:
    assert missing(stored(asked("  Klingon ")), ME) == ["Klingon"]
    assert compare(stored(asked("Klingon")), [Spoken("klingon", "native")]) == []
    assert compare(stored(asked("Wolof")), [Spoken("Wolof", None)]) == []


REVIEWED = (Spoken("Français", "natif"), Spoken("English", "C1"))


@pytest.mark.parametrize(("name", "quote"), NOT_REQUIRED)
def test_what_is_not_required_never_gives_the_badge(name: str, quote: str) -> None:
    read = stored(asked(name, quote=quote or " "))
    assert missing(read, REVIEWED) == []
    assert "missing" not in [gap.kind for gap in compare(read, REVIEWED)]


@pytest.mark.parametrize(("name", "quote"), [row for row in REQUIRED if row[0] != "French"])
def test_what_is_required_gives_the_badge(name: str, quote: str) -> None:
    assert missing(stored(asked(name, quote=quote)), REVIEWED) == [languages.display(name)]


# --- her side: a name the list cannot read --------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "name"),
    [
        ("Inglés", "English"),
        ("EN", "English"),
        ("eng", "English"),
        ("EN C1", "English"),
        ("en (native)", "English"),
        ("Hochdeutsch", "German"),
        ("Tedesco", "German"),
        ("Alemán", "German"),
        ("Duits", "German"),
        ("DE", "German"),
        ("deu", "German"),
        ("Castillan", "Spanish"),
        ("Spagnolo", "Spanish"),
        ("ES", "Spanish"),
        ("Portugais brésilien", "Portuguese"),
        ("Brazilian Portuguese", "Portuguese"),
        ("Mandarin (Putonghua)", "Chinese"),
        ("LSF", "French Sign Language"),
    ],
)
def test_her_spellings_of_a_listed_language(entry: str, name: str) -> None:
    assert languages.hers(entry) == {name}
    assert compare(stored(asked(name)), [Spoken(entry, "C2")]) == []


@pytest.mark.parametrize("entry", ["Klingon", "UK", "BR", "EN FR", "Lingua franca", "Native"])
def test_an_entry_the_list_cannot_read_gives_a_notice_not_the_badge(entry: str) -> None:
    mine = (Spoken("Français", "natif"), Spoken(entry, "C1"))
    assert languages.hers(entry) is None
    gaps = compare(stored(asked("German", "fluent")), mine)
    assert [(gap.kind, gap.language, gap.unread) for gap in gaps] == [("unread", "German", entry)]
    assert missing(stored(asked("German", "fluent")), mine) == []


def test_an_entry_of_no_word_is_no_language() -> None:
    mine = (Spoken("Français", "natif"), Spoken("---", "C1"))
    assert missing(stored(asked("German", "fluent")), mine) == ["German"]


def test_a_profile_of_known_names_still_gives_the_badge() -> None:
    mine = (Spoken("Français", "natif"), Spoken("Inglés", "C1"), Spoken("Wolof", None))
    assert missing(stored(asked("German", "fluent")), mine) == ["German"]
    assert compare(stored(asked("Wolof")), mine) == []
    unsure = stored(asked("German", quote="German is a plus"))
    assert [gap.kind for gap in compare(unsure, (Spoken("Klingon"),))] == ["unsure"]


def test_a_sign_language_is_its_own_language() -> None:
    mine = (Spoken("Langue des signes française", "natif"),)
    assert missing(stored(asked("French", "courant")), mine) == ["French"]
    assert compare(stored(asked("French Sign Language")), mine) == []
    assert missing(stored(asked("Langue des signes française")), (Spoken("Français"),)) == [
        "French Sign Language"
    ]


def test_the_profiles_languages_read_defensively() -> None:
    sections = {"identity": {"languages": [{"name": "Français", "level": "C2"}]}}
    assert languages.spoken_from(sections) == (Spoken("Français", "C2"),)
    assert languages.spoken_from({}) == ()
    assert languages.spoken_from(None) == ()
    assert languages.spoken_from({"identity": "x"}) == ()
    assert languages.spoken_from({"identity": {"languages": "French"}}) == ()


def test_a_long_input_stays_linear() -> None:
    quote = " ".join(["Deutschkenntnisse"] * 2000)
    assert named_in("German", quote)
    assert level_of(" ".join(["fluent"] * 2000), "high") == 5
    assert languages.Asked("German", None, True, (), quote).status() == "required"
    glued = "deutsch" + "x" * 5000
    assert languages.Asked("German", None, True, (), glued).status() == "unsure"
    many = " ".join(["the German market of"] * 500)
    assert languages.Asked("German", None, True, (), many).status() == "unsure"
