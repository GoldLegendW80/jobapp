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
        # Not in the list: kept as written, case, accents and punctuation aside.
        ("Wolof", {"wolof"}),
        ("Créole réunionnais", {"creole reunionnais"}),
        ("Klingon", {"klingon"}),
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
        ("Fluent German required", False),
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
    assert languages.Asked("German", None, required, (), quote).status == status


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
    assert missing(stored(asked("  Wolof ")), ME) == ["Wolof"]
    assert compare(stored(asked("Wolof")), [Spoken("wolof", "native")]) == []


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
