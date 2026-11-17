"""The place LinkedIn's public job search is sent for a saved place (jobs J20, #793): the name
LinkedIn's own location box knows, when the person wrote another one.

LinkedIn reads its `location` parameter as a place of its own list: "Tahiti" or "La Réunion"
written in French find little or nothing, "French Polynesia" and "Reunion" find the postings
there. So a saved place whose whole text is one of the aliases below is sent as LinkedIn's name;
any other place is sent as written (fail closed: an unknown place is never guessed into another
one). The saved place itself is unchanged: the summary and the person's search still say it as
saved.

Matching, the gazetteer's fold (app/jobs/gazetteer.py, `fold`: lower case, accents aside) on the
compatibility form, then every run of characters other than a to z and 0 to 9 read as one space,
the ends trimmed: "Île-de-France", "ile de france" and "ILE DE FRANCE" are one key. The whole
text must be the alias: "Paris" stays "Paris", "Tahiti, Papeete" is sent as written (a list of
places in one saved place is the person's own wording, never split here).

The wanted places also judge what LinkedIn answers (`WantedPlaces`, jobs J20, the sweep of
2026-11-17): LinkedIn reads a location it does not know as no location at all and answers
postings of elsewhere (three French overseas places saved, seven postings stored, all in
"Miami, FL"). So a card or posting whose place matches none of the saved places is not stored,
unless no place is saved or one says no limit, or the posting says remote and remote is wanted
(a saved place such as "remote" or "télétravail", or the remote stance). A place matches a
saved one when its text names, as whole words: the saved place itself, LinkedIn's name for it
and every alias of that name ("Papeete, French Polynesia" for "Tahiti"), or a place of the
gazetteer the saved place names (its aliases: "Courbevoie, Île-de-France, France" for
"Paris", "Lyon, Auvergne-Rhône-Alpes, France" for "France"), never a place of the table by a
country its name holds. An empty place says nothing and is
kept: the posting reader and scoring judge it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from app.jobs import gazetteer
from app.jobs.gazetteer import fold

PLACE_MAX = 100  # characters of a saved place looked at; a longer one is sent as written

# LinkedIn's name, then the keys (folded, as `key` gives them) that stand for it. Only places a
# person writes otherwise than LinkedIn names them: French names, overseas territories, regions.
ALIASES: dict[str, tuple[str, ...]] = {
    # Overseas France: LinkedIn names the territory, not its islands or towns.
    "French Polynesia": (
        "tahiti",
        "papeete",
        "moorea",
        "bora bora",
        "polynesie",
        "polynesie francaise",
        "french polynesia",
    ),
    "Reunion": (
        "reunion",
        "la reunion",
        "ile de la reunion",
        "reunion island",
        "la reunion 974",
        "reunion 974",
        "974",
    ),
    "French Guiana": ("guyane", "guyane francaise", "french guiana"),
    "New Caledonia": ("nouvelle caledonie", "noumea", "new caledonia"),
    # The Paris region: LinkedIn's metropolitan area, and the administrative region.
    "Greater Paris Metropolitan Region": (
        "region parisienne",
        "la region parisienne",
        "paris region",
        "paris et sa region",
        "paris et region parisienne",
        "paris et alentours",
        "paris area",
        "paris metropolitan area",
        "greater paris",
        "grand paris",
        "agglomeration parisienne",
    ),
    "Île-de-France, France": ("ile de france", "idf"),
    # Countries and cities a French speaker writes in French.
    "Germany": ("allemagne",),
    "Spain": ("espagne",),
    "Italy": ("italie",),
    "Belgium": ("belgique",),
    "Netherlands": ("pays bas", "hollande"),
    "Switzerland": ("suisse",),
    "Austria": ("autriche",),
    "Ireland": ("irlande",),
    "Poland": ("pologne",),
    "Sweden": ("suede",),
    "Denmark": ("danemark",),
    "Norway": ("norvege",),
    "Finland": ("finlande",),
    "United Kingdom": ("royaume uni", "uk", "grande bretagne"),
    "United States": ("etats unis", "usa", "us", "etats unis d amerique"),
    "United Arab Emirates": ("emirats arabes unis", "uae"),
    "Japan": ("japon",),
    "Australia": ("australie",),
    "Singapore": ("singapour",),
    "London": ("londres",),
    "Brussels": ("bruxelles",),
    "Geneva": ("geneve",),
    "Lisbon": ("lisbonne",),
    "Barcelona": ("barcelone",),
}

LINKEDIN_NAMES: dict[str, str] = {key: name for name, keys in ALIASES.items() for key in keys}


def key(place: str) -> str:
    """The text a saved place is looked up by (module docstring); "" for none."""
    if len(place) > PLACE_MAX:
        return ""
    folded = fold(unicodedata.normalize("NFKC", place))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def linkedin_name(place: str) -> str:
    """The location LinkedIn is sent for `place`: its own name from ALIASES, else as written."""
    return LINKEDIN_NAMES.get(key(place), place)


# A saved place that asks for remote work, and the words of a posting that says it is remote
# (keys, as `key` gives them).
REMOTE_PLACES = frozenset(
    {
        "remote",
        "full remote",
        "fully remote",
        "100 remote",
        "remote only",
        "teletravail",
        "teletravail complet",
        "full teletravail",
        "100 teletravail",
        "a distance",
    }
)
REMOTE_WORDS = ("remote", "teletravail", "full remote", "a distance")


def _words(text: str, term: str) -> bool:
    """Whether `term` (a key) is in `text` (a key) as whole words."""
    return bool(term) and f" {term} " in f" {text} "


class WantedPlaces:
    """The saved places a posting's place must match (module docstring). `free` when none is
    saved or one says no limit (`worldwide`, finder.worldwide): every place is kept."""

    def __init__(
        self,
        places: list[str],
        remote_stance: str | None,
        worldwide: Callable[[str], bool],
    ) -> None:
        self.remote = remote_stance == "remote" or any(key(p) in REMOTE_PLACES for p in places)
        located = [p for p in places if key(p) not in REMOTE_PLACES]
        self.free = not places or any(worldwide(p) for p in places)
        terms: set[str] = set()
        patterns: list[re.Pattern[str]] = []
        for place in located:
            own = key(place)
            terms.add(own)
            name = LINKEDIN_NAMES.get(own)
            if name is not None:
                # LinkedIn's own name, without its country ("Île-de-France"), and its aliases.
                terms.add(key(name.split(",")[0]))
                terms.update(ALIASES[name])
            else:
                # The gazetteer's cities the saved place names, else its countries: never for
                # a region of the table, whose name holds its country ("Île-de-France").
                patterns.extend(found.pattern for found in gazetteer.places_of(place))
        self.terms = frozenset(t for t in terms if t)
        self.patterns = tuple(patterns)

    def matches(self, location: str, title: str = "") -> bool:
        """Whether a posting at `location` (its title for a remote one) is kept."""
        if self.free or not location.strip():
            return True
        where = key(location[:PLACE_MAX])
        if self.remote and any(
            _words(where, word) or _words(key(title), word) for word in REMOTE_WORDS
        ):
            return True
        if any(_words(where, term) for term in self.terms):
            return True
        folded = fold(location[:PLACE_MAX])
        return any(pattern.search(folded) for pattern in self.patterns)
