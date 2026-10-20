"""font_map (task D25a): a font of an uploaded CV to a face of the bundled library.

Tables from the spec's step 2 in both directions: the known names and their spellings in PDFs
and in Word; names that hold a known name inside a longer one and must not take it; the genre
words, the flags and the default; the face CSS font matching picks. Then the property: whatever
the name and the flags, the face returned is one the library has, of a free family.
"""

import random
import string

import pytest

from app.documents import font_map, templates
from app.documents.font_map import FAMILIES, GENRES, KNOWN, face, nearest_face


def test_the_table_of_families_is_the_library_and_every_family_is_free():
    library = {key: family for key, family in templates.fonts().items() if not family.fallback}
    assert set(FAMILIES) == set(library)
    for key, family in FAMILIES.items():
        assert family.name == library[key].name
        assert family.faces == {(f.weight, f.style == "italic") for f in library[key].faces}
        assert library[key].licence in templates.LICENCES


def test_every_target_of_the_tables_is_a_family_or_a_genre():
    assert set(GENRES.values()) <= set(FAMILIES)
    assert set(KNOWN.values()) <= set(FAMILIES) | set(GENRES) | {font_map.SYMBOL}
    assert set(font_map.GENRE_WORDS.values()) <= set(GENRES)


KNOWN_CASES = [
    # name as a PDF embeds it or Word names it, flags, family, weight, italic
    ("Calibri", {}, "carlito", 400, False),
    ("ABCDEF+Calibri-BoldItalic", {}, "carlito", 700, True),
    ("Calibri,Bold", {}, "carlito", 700, False),
    ("CalibriLight", {}, "carlito", 400, False),
    ("Calibri Light", {"italic": True}, "carlito", 400, True),
    ("Cambria", {}, "caladea", 400, False),
    ("QWERTY+Cambria-Italic", {}, "caladea", 400, True),
    ("Arial", {}, "arimo", 400, False),
    ("ArialMT", {}, "arimo", 400, False),
    ("Arial-BoldMT", {}, "arimo", 700, False),
    ("Arial-BoldItalicMT", {}, "arimo", 700, True),
    ("ARIALBOLD", {}, "arimo", 700, False),
    ("arial narrow", {}, "arimo", 400, False),
    ("Helvetica", {}, "arimo", 400, False),
    ("Helvetica-Oblique", {}, "arimo", 400, True),
    ("HelveticaNeue-Medium", {}, "arimo", 400, False),
    ("Helvetica Neue LT Std 45 Light", {}, "arimo", 400, False),
    ("TimesNewRomanPSMT", {}, "tinos", 400, False),
    ("TimesNewRomanPS-BoldItalicMT", {}, "tinos", 700, True),
    ("Times New Roman", {"weight": 700}, "tinos", 700, False),
    ("Times-Roman", {}, "tinos", 400, False),
    ("Times-Bold", {}, "tinos", 700, False),
    ("LiberationSerif-Italic", {}, "tinos", 400, True),
    ("Ｃａｌｉｂｒｉ", {}, "carlito", 400, False),
    ("Inter-SemiBold", {}, "inter", 600, False),
    ("Inter-SemiBoldItalic", {}, "inter", 700, True),
    ("Inter Display", {}, "inter", 400, False),
    ("EBGaramond-Regular", {}, "eb-garamond", 400, False),
    ("IBMPlexSans-Medium", {}, "ibm-plex-sans", 400, False),
    ("IBMPlexMono-Bold", {}, "ibm-plex-mono", 700, False),
    ("SourceSerif4-Light", {}, "source-serif-4", 400, False),
    ("Source Serif Pro", {}, "source-serif-4", 400, False),
    ("PlayfairDisplay-Black", {}, "playfair-display", 700, False),
    ("Playfair Display SC", {}, "playfair-display", 400, False),
    ("NunitoSans-ExtraBold", {}, "nunito-sans", 700, False),
    ("Avenir-Heavy", {}, "nunito-sans", 700, False),
    ("AvenirNext-DemiBold", {}, "nunito-sans", 600, False),
    ("Roboto-MediumItalic", {}, "inter", 400, True),
    ("SegoeUI-Semibold", {}, "inter", 600, False),
    ("Aptos Display", {}, "inter", 400, False),
    ("OpenSans-CondBold", {}, "ibm-plex-sans", 700, False),
    ("Lato-Hairline", {}, "ibm-plex-sans", 400, False),
    ("Gill Sans MT", {}, "ibm-plex-sans", 400, False),
    ("Montserrat-ExtraLight", {}, "dm-sans", 400, False),
    ("Futura-Medium", {}, "dm-sans", 400, False),
    ("Georgia-BoldItalic", {}, "source-serif-4", 700, True),
    ("Garamond", {}, "eb-garamond", 400, False),
    ("BookAntiqua-Bold", {}, "eb-garamond", 700, False),
    ("Palatino-Roman", {}, "eb-garamond", 400, False),
    ("Didot", {}, "playfair-display", 400, False),
    ("RobotoSlab-Bold", {}, "source-serif-4", 700, False),
    ("CourierNewPSMT", {}, "ibm-plex-mono", 400, False),
    ("Courier-Oblique", {}, "ibm-plex-mono", 400, True),
    ("Consolas", {"monospace": False, "serif": True}, "ibm-plex-mono", 400, False),
    ("DejaVuSansMono-Bold", {}, "ibm-plex-mono", 700, False),
    ("RobotoMono-Italic", {}, "ibm-plex-mono", 400, True),
]


@pytest.mark.parametrize(("name", "flags", "family", "weight", "italic"), KNOWN_CASES)
def test_a_known_name_goes_to_its_family_keeping_weight_and_style(
    name, flags, family, weight, italic
):
    mapped = face(name, **flags)
    assert (mapped.family, mapped.weight, mapped.italic) == (family, weight, italic)
    assert not mapped.symbol


NEAR_MISSES = [
    # a known name inside a longer one, or followed by a word that is not a style: not that family
    ("Times Square", {}, "arimo"),
    ("TimesSquare-Bold", {"serif": True}, "source-serif-4"),
    ("Arial Rounded MT Bold", {}, "arimo"),
    ("Interstate-Light", {}, "arimo"),
    ("Inter Tight", {"serif": True}, "source-serif-4"),
    ("Calibrix", {"serif": True}, "source-serif-4"),
    ("Cambria Math", {}, "arimo"),
    ("Georgian", {}, "arimo"),
    ("Lorem", {"serif": True}, "source-serif-4"),
    ("Courier Prime Sans", {}, "arimo"),
    ("Merriweather Sans", {}, "arimo"),
    ("Noto Sans Mono", {}, "ibm-plex-mono"),
    ("Lucida Sans Typewriter", {}, "ibm-plex-mono"),
    ("ITC Garamond Std", {}, "eb-garamond"),
    ("Bauer Bodoni", {}, "playfair-display"),
    ("Franklin Gothic Book", {}, "arimo"),
    ("Some Slab", {}, "source-serif-4"),
    ("PT Sans Serif", {}, "arimo"),
    ("Alegreya Sans", {"serif": True}, "arimo"),
    ("Alegreya Serif", {}, "source-serif-4"),
    ("ABCDEF+Foo", {"monospace": True, "serif": True}, "ibm-plex-mono"),
    ("ABCDEF+Foo", {"serif": True}, "source-serif-4"),
    ("ABCDEF+Foo", {}, "arimo"),
    ("", {}, "arimo"),
    ("+", {}, "arimo"),
    ("MS-Mincho", {"serif": True}, "source-serif-4"),
    ("宋体", {}, "arimo"),
    ("12345", {}, "arimo"),
    ("Bold", {}, "arimo"),
    ("Italic", {"serif": True}, "source-serif-4"),
]


@pytest.mark.parametrize(("name", "flags", "family"), NEAR_MISSES)
def test_a_name_that_is_not_a_known_one_maps_by_genre_word_then_flags_then_default(
    name, flags, family
):
    mapped = face(name, **flags)
    assert mapped.family == family
    assert mapped.notes
    assert not mapped.notes[0].startswith("known name")


@pytest.mark.parametrize(
    "name", ["Wingdings-Regular", "ZapfDingbats", "SymbolMT", "FontAwesome5Free-Solid"]
)
def test_a_symbol_font_is_marked_and_drawn_by_the_default(name):
    mapped = face(name, serif=True)
    assert mapped.symbol
    assert mapped.family == GENRES[font_map.DEFAULT_GENRE]


def test_the_weight_said_by_the_name_wins_over_the_file_and_the_file_over_the_default():
    assert face("Calibri-Bold", weight=400).weight == 700
    assert face("ABCDEF+Calibri", weight=700).weight == 700
    assert face("Calibri", weight=0).weight == 400
    assert face("Calibri", weight=50).weight == 400
    assert face("Calibri", italic=True).italic
    assert face("Calibri-Italic", italic=False).italic


def test_a_direct_hit_records_nothing_and_every_substitution_says_why():
    assert face("Lora-Bold").notes == ()
    assert face("Calibri").notes == ("known name: Carlito",)
    assert face("Calibri Light").notes[-1] == "weight 300 drawn as 400"
    assert face("Inter-SemiBoldItalic").notes == ("weight 600 italic drawn as 700",)


@pytest.mark.parametrize(
    ("faces", "weight", "italic", "expected"),
    [
        # CSS Fonts 4, 5.2: 400 to 500 look up to 500, then down, then up; under 400 down then
        # up; over 500 up then down. The italic first, when the family has one.
        ("_FULL", 400, False, (400, False)),
        ("_FULL", 450, False, (400, False)),
        ("_FULL", 500, False, (400, False)),
        ("_FULL", 550, False, (600, False)),
        ("_FULL", 600, True, (700, True)),
        ("_FULL", 650, False, (700, False)),
        ("_FULL", 300, False, (400, False)),
        ("_FULL", 100, True, (400, True)),
        ("_FULL", 900, False, (700, False)),
        ("_FOUR", 500, False, (400, False)),
        ("_FOUR", 600, False, (700, False)),
        ("_FOUR", 350, True, (400, True)),
        ("_FOUR", 800, True, (700, True)),
    ],
)
def test_the_face_is_the_one_css_font_matching_picks(faces, weight, italic, expected):
    family = "inter" if faces == "_FULL" else "tinos"
    assert FAMILIES[family].faces == getattr(font_map, faces)
    assert nearest_face(family, weight, italic) == expected


def _names(rng: random.Random) -> list[str]:
    styles = ["", "-Bold", "-Italic", ",BoldItalic", " Light", "-BlackOblique", "PSMT", "-W3"]
    found = [key + suffix for key in KNOWN for suffix in rng.sample(styles, 3)]
    found += [f.name + "-SemiBoldItalic" for f in FAMILIES.values()]
    letters = string.ascii_letters + string.digits + " -,+_éß明"
    found += ["".join(rng.choice(letters) for _ in range(rng.randint(0, 40))) for _ in range(400)]
    found.append("ABCDEF+" + "Bold" * 5000)
    return found


def test_whatever_the_name_and_flags_the_face_is_one_the_library_has():
    rng = random.Random(25)  # noqa: S311, a repeatable draw of names, nothing secret
    for name in _names(rng):
        flags = {
            "weight": rng.choice([0, 100, 250, 400, 500, 600, 700, 900]),
            "italic": rng.random() < 0.3,
            "serif": rng.random() < 0.3,
            "monospace": rng.random() < 0.1,
        }
        mapped = face(name, **flags)
        assert (mapped.weight, mapped.italic) in FAMILIES[mapped.family].faces
        assert face(name, **flags) == mapped


def test_every_family_of_the_library_maps_to_itself_by_its_own_name():
    for key, family in FAMILIES.items():
        for weight, italic in family.faces:
            mapped = face(family.name, weight=weight, italic=italic)
            assert (mapped.family, mapped.weight, mapped.italic, mapped.notes) == (
                key,
                weight,
                italic,
                (),
            )
