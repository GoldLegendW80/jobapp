"""The fonts of an uploaded CV, mapped to the bundled library (task D25a; SPEC-documents.md,
"Design capture of an upload", step 2). Pure: no file is read and nothing is logged.

    face(name, weight=0, italic=False, serif=False, monospace=False) -> Mapped

`name` is a font as a PDF embeds it ("ABCDEF+Calibri-BoldItalic", "TimesNewRomanPS-BoldMT",
"Arial,Bold") or as Word names it ("Calibri Light"); `weight`, `italic`, `serif` and `monospace`
are what the file says besides (parser_client.DesignFont; a DOCX gives only bold and italic).

A name is read as words (NFKC, a subset prefix dropped, words cut at separators, at changes of
case and at digits) made of family words followed by style words: weights, italics, widths,
optical sizes and vendor marks (STYLE). The family is, in this order:
- KNOWN: the longest start of the name's words joined in lower case that names a family of the
  table, when all that follows it is style words ("Times Square" is not Times). Calibri,
  Cambria, Arial and Helvetica, Times New Roman go to Carlito, Caladea, Arimo and Tinos, their
  metric compatible free stand ins; the library's own families to themselves; widely used
  faces to the nearest family of their genre;
- a genre word of the name (GENRE_WORDS: Mono, Serif, Sans, Slab, Garamond, Bodoni...);
- the file's flags, monospace then serif;
- else `sans`, the named default.
Each genre has one family (GENRES). The weight is the name's style word, else the file's weight,
else 400; the face is then the one the CSS font matching algorithm picks among the family's
faces (CSS Fonts 4, 5.2), so the face returned is one the renderer has. FAMILIES states the
library's families; a test keeps it equal to `templates.fonts()`, and every family of it is free
to redistribute, so a font that is not can never be chosen. `Mapped.notes` says why for every
choice that is not a direct hit. A symbol font (icons) is mapped to the default and marked
`symbol`, so a caller leaves it out of the families it counts.

Declared open points: the PDF's `symbolic` flag is not read (PDFium sets it on many text fonts);
a width word (Narrow, Condensed) is dropped, the family keeps its own width; GENRES picks one
family per genre, a closer one may exist for a given face.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise

REGULAR, BOLD = 400, 700
_FULL = frozenset({(400, False), (400, True), (600, False), (700, False), (700, True)})
_FOUR = frozenset({(400, False), (400, True), (700, False), (700, True)})


@dataclass(frozen=True)
class Family:
    id: str  # its folder in documents/fonts/
    name: str  # its CSS name
    faces: frozenset[tuple[int, bool]]  # (weight, italic)


FAMILIES = {
    family.id: family
    for family in (
        Family("arimo", "Arimo", _FULL),
        Family("caladea", "Caladea", _FOUR),
        Family("carlito", "Carlito", _FOUR),
        Family("dm-sans", "DM Sans", _FULL),
        Family("eb-garamond", "EB Garamond", _FULL),
        Family("ibm-plex-mono", "IBM Plex Mono", _FULL),
        Family("ibm-plex-sans", "IBM Plex Sans", _FULL),
        Family("inter", "Inter", _FULL),
        Family("lora", "Lora", _FULL),
        Family("nunito-sans", "Nunito Sans", _FULL),
        Family("playfair-display", "Playfair Display", _FULL),
        Family("source-serif-4", "Source Serif 4", _FULL),
        Family("tinos", "Tinos", _FOUR),
        Family("work-sans", "Work Sans", _FULL),
    )
}

GENRES = {
    "sans": "arimo",  # a neutral grotesque, and the default
    "ui": "inter",  # the screen grotesques: Roboto, Segoe UI, San Francisco, Aptos
    "humanist": "ibm-plex-sans",
    "geometric": "dm-sans",
    "serif": "source-serif-4",
    "oldstyle": "eb-garamond",
    "didone": "playfair-display",
    "mono": "ibm-plex-mono",
}
DEFAULT_GENRE = "sans"
SYMBOL = "symbol"


def _key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _names(genre: str, *names: str) -> dict[str, str]:
    return dict.fromkeys(names, genre)


# A name, its words joined in lower case, to a family of FAMILIES or a genre of GENRES.
KNOWN: dict[str, str] = {
    **{key: family.id for family in FAMILIES.values() for key in [_key(family.name)]},
    **_names("carlito", "calibri"),
    **_names("caladea", "cambria"),
    **_names("arimo", "arial", "helvetica", "helveticaneue", "liberationsans", "nimbussans",
             "nimbussansl", "texgyreheros", "freesans"),
    **_names("tinos", "timesnewroman", "times", "timesroman", "liberationserif", "nimbusroman",
             "nimbusromno9l", "texgyretermes", "freeserif"),
    **_names("nunito-sans", "avenir", "avenirnext", "nunito", "mulish", "muli"),
    **_names("source-serif-4", "sourceserifpro", "sourceserif"),
    **_names("eb-garamond", "garamond", "ebgaramond12"),
    **_names("ui", "roboto", "segoeui", "sfprotext", "sfprodisplay", "sanfrancisco", "aptos",
             "systemui", "notosansui"),
    **_names("humanist", "opensans", "lato", "sourcesanspro", "sourcesans3", "notosans",
             "firasans", "ptsans", "ubuntu", "myriadpro", "myriad", "frutiger", "verdana",
             "tahoma", "trebuchetms", "trebuchet", "lucidagrande", "lucidasans", "gillsans",
             "candara", "corbel", "dejavusans", "segoe", "optima", "seravek"),
    **_names("geometric", "futura", "centurygothic", "avantgarde", "itcavantgardegothic",
             "montserrat", "poppins", "gotham", "proximanova", "raleway", "questrial",
             "quicksand", "comfortaa", "kumbhsans", "outfit", "urbanist", "manrope"),
    **_names("serif", "georgia", "baskerville", "librebaskerville", "charter", "merriweather",
             "ptserif", "notoserif", "constantia", "centuryschoolbook", "newcenturyschlbk",
             "utopia", "iowanoldstyle", "droidserif", "crimsontext", "crimson", "dejavuserif",
             "rockwell", "robotoslab", "zillaslab", "arvo", "bookmanoldstyle", "cochin",
             "sitkatext", "cambo", "libreserif"),
    **_names("oldstyle", "adobegaramondpro", "garamondpremierpro", "sabon", "bembo", "minionpro",
             "minion", "palatino", "palatinolinotype", "bookantiqua", "caslon", "adobecaslonpro",
             "goudyoldstyle", "hoeflertext", "centaur", "jenson", "cormorantgaramond",
             "ebgaramond08"),
    **_names("didone", "didot", "bodoni", "bodonimt", "bodoni72", "playfair", "abrilfatface"),
    **_names("mono", "courier", "couriernew", "consolas", "menlo", "monaco", "sourcecodepro",
             "firacode", "firamono", "jetbrainsmono", "robotomono", "dejavusansmono",
             "lucidaconsole", "andalemono", "sfmono", "cascadiacode", "cascadiamono",
             "ubuntumono", "inconsolata", "courierprime"),
    **_names(SYMBOL, "symbol", "wingdings", "wingdings2", "wingdings3", "webdings",
             "zapfdingbats", "dingbats", "fontawesome", "fontawesome5free", "fontawesome6free",
             "fontawesome5brands", "materialicons", "materialsymbols", "segoeuisymbol",
             "segoemdl2assets", "marlett", "mtextra"),
}  # fmt: skip

# A word of a name that says its genre wherever it stands ("ITC Garamond", "Foo Sans Mono").
GENRE_WORDS = {
    **_names("mono", "mono", "monospace", "monospaced", "code", "console", "typewriter"),
    **_names("serif", "serif", "slab", "antiqua"),
    **_names("sans", "sans", "grotesk", "grotesque", "gothic", "grot"),
    **_names("oldstyle", "garamond", "caslon", "jenson", "bembo", "sabon"),
    **_names("didone", "bodoni", "didot"),
}

# The style words of a name: a weight, an italic, or None for a word that says neither (a
# width, an optical size, a vendor mark). Read by longest match over the joined words.
STYLE: dict[str, tuple[int | None, bool]] = {
    **dict.fromkeys(("thin", "hairline"), (100, False)),
    **dict.fromkeys(("extralight", "ultralight"), (200, False)),
    **dict.fromkeys(("light", "semilight", "demilight"), (300, False)),
    **dict.fromkeys(("regular", "normal", "book", "roman", "plain", "upright"), (400, False)),
    "medium": (500, False),
    **dict.fromkeys(("semibold", "demibold", "demi"), (600, False)),
    "bold": (700, False),
    **dict.fromkeys(("extrabold", "ultrabold"), (800, False)),
    **dict.fromkeys(("black", "heavy", "ultra"), (900, False)),
    **dict.fromkeys(("italic", "oblique", "it", "obl", "ital", "inclined", "slanted", "kursiv"),
                    (None, True)),
    **dict.fromkeys(("condensed", "cond", "narrow", "compressed", "semicondensed", "extended",
                     "expanded", "wide", "display", "text", "caption", "micro", "subhead",
                     "deck", "poster", "mt", "ps", "psmt", "std", "pro", "lt", "ltstd", "ot",
                     "com", "w", "sc", "smcp", "caps", "smallcaps", "solid"), (None, False)),
}  # fmt: skip
_STYLE_LONGEST = max(map(len, STYLE))

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
NAME_MAX = 128


@dataclass(frozen=True)
class Mapped:
    family: str  # a key of FAMILIES
    weight: int  # with `italic`, a face the family has
    italic: bool
    symbol: bool = False  # an icon font: drawn by nothing, counted by nobody
    notes: tuple[str, ...] = ()

    @property
    def css_name(self) -> str:
        return FAMILIES[self.family].name


def words(name: str) -> list[str]:
    """The words of a font name: NFKC, the subset prefix dropped, cut at every separator, at a
    change of case and between letters and digits."""
    text = _SUBSET.sub("", unicodedata.normalize("NFKC", name[:NAME_MAX]))
    return _WORDS.findall(text)


def style(rest: list[str]) -> tuple[int | None, bool, bool]:
    """The weight (None: not said) and the italic that `rest` says, and whether every one of
    its letters belongs to a style word (digits are a weight class, "Univers 55", and pass)."""
    text = "".join(rest).casefold()
    weight: int | None = None
    italic = False
    at = 0
    while at < len(text):
        if text[at].isdigit():
            at += 1
            continue
        for size in range(min(_STYLE_LONGEST, len(text) - at), 0, -1):
            found = STYLE.get(text[at : at + size])
            if found is not None:
                weight = found[0] if found[0] is not None else weight
                italic = italic or found[1]
                at += size
                break
        else:
            return weight, italic, False
    return weight, italic, True


def _known(key: str) -> tuple[str, str, int | None, bool] | None:
    """The family of the table that the longest start of `key` names, when the rest of it is
    style words only, with that start, the weight and the italic."""
    for cut in range(len(key), 0, -1):
        target = KNOWN.get(key[:cut])
        if target is None:
            continue
        weight, italic, whole = style([key[cut:]])
        if whole:
            return key[:cut], target, weight, italic
    return None


def _style_tail(parts: list[str]) -> tuple[list[str], int | None, bool]:
    """The family words and the style of a name no table knows: the longest run of last words
    that are all style words is its style."""
    for start in range(len(parts) + 1):
        weight, italic, whole = style(parts[start:])
        if whole:
            return parts[:start], weight, italic
    return parts, None, False  # unreachable: an empty tail is always whole


def nearest_face(family: str, weight: int, italic: bool) -> tuple[int, bool]:
    """The face of `family` that CSS font matching picks for `weight` and `italic`: the style
    first (an italic when one exists), then the weight (CSS Fonts 4, 5.2)."""
    faces = FAMILIES[family].faces
    styled = [w for w, i in faces if i == italic] or [w for w, _ in faces]
    style_found = italic if any(i == italic for _, i in faces) else not italic
    below = sorted((w for w in styled if w < weight), reverse=True)
    above = sorted(w for w in styled if w > weight)
    if weight in styled:
        order = [weight]
    elif 400 <= weight <= 500:
        order = [w for w in above if w <= 500] + below + [w for w in above if w > 500]
    elif weight < 400:
        order = below + above
    else:
        order = above + below
    return order[0], style_found


def face(
    name: str,
    weight: int = 0,
    italic: bool = False,
    serif: bool = False,
    monospace: bool = False,
) -> Mapped:
    """The bundled face for a font of the file (see the module docstring)."""
    parts = words(name)
    notes: list[str] = []
    known = _known(_key("".join(parts)))
    if known is not None:
        matched, target, said_weight, said_italic = known
        how = "known name"
    else:
        family_words, said_weight, said_italic = _style_tail(parts)
        genres = [GENRE_WORDS[w.casefold()] for w in family_words if w.casefold() in GENRE_WORDS]
        if "mono" in genres:
            target, how = "mono", "genre word"
        elif genres:
            sans_serif = any(
                a.casefold() == "sans" and b.casefold() == "serif"
                for a, b in pairwise(family_words)
            )
            target, how = ("sans" if sans_serif else genres[0]), "genre word"
        elif monospace:
            target, how = "mono", "monospace flag"
        elif serif:
            target, how = "serif", "serif flag"
        else:
            target, how = DEFAULT_GENRE, "unknown name, the default"
    symbol = target == SYMBOL
    if symbol:
        target, how = DEFAULT_GENRE, "symbol font, the default"
    family = GENRES.get(target, target)
    if known is None or symbol or matched != _key(FAMILIES[family].name):
        notes.append(f"{how}: {FAMILIES[family].name}")
    wanted = said_weight or (weight if 100 <= weight <= 900 else 0)
    if not wanted:
        wanted = REGULAR
    slanted = said_italic or italic
    got_weight, got_italic = nearest_face(family, wanted, slanted)
    if (got_weight, got_italic) != (wanted, slanted):
        notes.append(f"weight {wanted}{' italic' if slanted else ''} drawn as {got_weight}")
    return Mapped(family, got_weight, got_italic, symbol, tuple(notes))
