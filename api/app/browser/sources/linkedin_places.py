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
country its name holds. A region of the table also matches what it covers (`COVERS`): the Paris
region saved as "Région parisienne" or "IDF" matches Paris with its suburbs, "Île-de-France,
France" and "Greater Paris Metropolitan Region", never the rest of France.

The last part of a location decides the country (`country_of`): a country, a territory, a state
of the United States or a province of Canada, by name or by LinkedIn's capital code ("Dallas,
TX"). A saved place whose country is known (the table's `HOME`, the gazetteer's `CITY_HOME`)
does not match a location of another one: "Paris, TX" is not Paris, "Reunion Tower, Dallas, TX"
is not La Réunion. A saved country matches every location whose last part names it ("Austin,
TX" for "USA"). A last part naming nothing known leaves the words to decide.

A posting says remote when one part of its place or of its title (`PARTS`: commas, brackets,
spaced dashes) is a remote marker as a whole: "France (Remote)", "AI Engineer - Full remote",
never "Remote Sensing Scientist". A saved place longer than PLACE_MAX characters is judged by the
gazetteer places of its first PLACE_MAX characters; naming none, it cannot be read (`unread`),
and when it is the only place saved (remote aside) every place is kept rather than every card
dropped. An empty location says nothing and is kept: the posting reader and scoring judge it.
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


# The table's places that are not a country or territory of their own, by LinkedIn's name: the
# country they are in.
HOME: dict[str, str] = {
    "Greater Paris Metropolitan Region": "France",
    "Île-de-France, France": "France",
    "London": "United Kingdom",
    "Brussels": "Belgium",
    "Geneva": "Switzerland",
    "Lisbon": "Portugal",
    "Barcelona": "Spain",
}

# What a region of the table covers besides its own name and aliases: the other names of the same
# region (with their aliases) and the gazetteer's cities in it (by name, with their suburbs).
COVERS: dict[str, tuple[str, ...]] = {
    "Greater Paris Metropolitan Region": ("Île-de-France, France", "Paris"),
    "Île-de-France, France": ("Greater Paris Metropolitan Region", "Paris"),
}

# The country of each city of the gazetteer (app/jobs/places.json, by name). A test fails when the
# gazetteer gains a city this does not name.
# fmt: off
CITY_HOME: dict[str, str] = {
    **dict.fromkeys((
        "Paris", "Bordeaux", "Lyon", "Toulouse", "Lille", "Strasbourg", "Nice", "Marseille",
        "Sete", "Nantes", "Rennes", "Niort", "Dijon", "Grenoble", "Brest",
    ), "France"),
    **dict.fromkeys(("London", "Glasgow", "Brighton", "Abingdon"), "United Kingdom"),
    **dict.fromkeys(("Geneva", "Zurich", "Lausanne"), "Switzerland"),
    **dict.fromkeys(("Barcelona", "Madrid"), "Spain"),
    **dict.fromkeys(("Berlin", "Munich", "Hanover", "Stuttgart"), "Germany"),
    **dict.fromkeys((
        "San Francisco Bay Area", "Los Angeles", "San Diego", "Seattle", "Denver", "Phoenix",
        "Dallas", "Minneapolis", "Green Bay", "Chicago", "Bloomington", "Cincinnati", "Atlanta",
        "Washington DC", "New York", "Boston",
    ), "United States"),
    **dict.fromkeys(("Montreal", "Toronto", "Ottawa"), "Canada"),
    **dict.fromkeys(("Sydney", "Melbourne"), "Australia"),
    "Martinique": "Martinique", "Luxembourg": "Luxembourg", "Brussels": "Belgium",
    "Amsterdam": "Netherlands", "Dublin": "Ireland", "Milan": "Italy", "Warsaw": "Poland",
    "Stockholm": "Sweden", "Esbjerg": "Denmark", "Tallinn": "Estonia", "Bucharest": "Romania",
    "Sofia": "Bulgaria", "Singapore": "Singapore", "Bengaluru": "India", "Seoul": "South Korea",
}

# Overseas France: a place there is in France too ("Fort-de-France, Martinique, France").
OVERSEAS = frozenset({
    "French Polynesia", "Reunion", "French Guiana", "New Caledonia", "Martinique", "Guadeloupe",
    "Mayotte",
})

# The regions LinkedIn writes after a city of the United States or Canada: their names, and their
# codes in capitals ("Dallas, TX", "Toronto, ON"), the form LinkedIn gives them.
US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
)
US_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
})
CANADA_PROVINCES = (
    "ontario", "quebec", "british columbia", "alberta", "manitoba", "saskatchewan",
    "nova scotia", "new brunswick", "newfoundland and labrador", "prince edward island",
)
CANADA_CODES = frozenset({"ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE"})
# fmt: on


def _countries() -> dict[str, str]:
    """Every key a location's last part may be that names a country or territory (module
    docstring): the gazetteer's countries, the table's own, the homes above, the regions of the
    United States and Canada, and the nations of the United Kingdom."""
    out: dict[str, str] = {}
    for place in gazetteer.gazetteer().countries:
        out[key(place.name)] = place.name
        out.update((key(alias), place.name) for alias in place.aliases)
    for name, keys in ALIASES.items():
        if name not in HOME:
            out[key(name)] = name
            out.update(dict.fromkeys(keys, name))
    for country in (*HOME.values(), *CITY_HOME.values(), *OVERSEAS):
        out[key(country)] = country
    out.update(dict.fromkeys(US_STATES, "United States"))
    out.update(dict.fromkeys(CANADA_PROVINCES, "Canada"))
    out.update(
        dict.fromkeys(("england", "scotland", "wales", "northern ireland"), "United Kingdom")
    )
    return out


COUNTRIES = _countries()


def _homes(country: str) -> frozenset[str]:
    return frozenset({country, "France"} if country in OVERSEAS else {country})


def country_of(location: str) -> str | None:
    """The country or territory the last part of a location names ("Dallas, TX": "United
    States", "Paris, Île-de-France, France (Remote)": "France"), None when it names none the
    module knows ("Greater Paris Metropolitan Region", "Île-de-France")."""
    last = re.sub(r"\([^)]*\)", " ", location).split(",")[-1].strip()
    if last in US_CODES:
        return "United States"
    if last in CANADA_CODES:
        return "Canada"
    return COUNTRIES.get(key(last))


# A saved place that asks for remote work, and the parts of a posting's place or title that say
# it is remote (keys, as `key` gives them).
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
# What splits a place or a title into parts: a comma, brackets, a bar, a slash, a middle dot, a
# colon, or a dash between spaces ("AI Engineer - Remote", "France (Remote)").
PARTS = re.compile(r"[,;:()\[\]|/·]| [-\u2013\u2014] ")


def says_remote(text: str) -> bool:
    """Whether one part of `text` is a remote marker as a whole: "France (Remote)", "AI Engineer
    - Full remote", "Télétravail"; never a word of a longer part ("Remote Sensing Scientist",
    "Remote Pilot", "Remoteville")."""
    return any(key(part) in REMOTE_PLACES for part in PARTS.split(text[:PLACE_MAX]))


def _words(text: str, term: str) -> bool:
    """Whether `term` (a key) is in `text` (a key) as whole words."""
    return bool(term) and f" {term} " in f" {text} "


class _Place:
    """One saved place as the judge reads it: the words and gazetteer patterns that name it, the
    countries it is in (`homes`, empty when unknown), and those it is all of (`whole`)."""

    def __init__(
        self,
        terms: set[str],
        patterns: list[re.Pattern[str]],
        homes: frozenset[str],
        whole: frozenset[str],
    ) -> None:
        self.terms = frozenset(t for t in terms if t)
        self.patterns = tuple(patterns)
        self.homes = homes
        self.whole = whole

    def holds(self, where: str, folded: str, country: str | None) -> bool:
        if country is not None and self.homes and country not in self.homes:
            return False  # "Paris, TX" for Paris, "Reunion Tower, Dallas, TX" for La Réunion
        if country is not None and country in self.whole:
            return True  # "Austin, TX" for "USA", "Lyon, Auvergne-Rhône-Alpes, France" for France
        if any(_words(where, term) for term in self.terms):
            return True
        return any(pattern.search(folded) for pattern in self.patterns)


def _city(name: str) -> re.Pattern[str]:
    return next(c for c in gazetteer.gazetteer().cities if c.name == name).pattern


def read_place(place: str) -> _Place | None:
    """How a saved place judges (module docstring); None for one longer than PLACE_MAX whose first
    PLACE_MAX characters name no place of the gazetteer: it cannot be read."""
    text = place[:PLACE_MAX]
    own = key(text)
    name = LINKEDIN_NAMES.get(own)
    if name is not None:
        terms = {own}
        patterns: list[re.Pattern[str]] = []
        for covered in (name, *COVERS.get(name, ())):
            if covered in ALIASES:
                # LinkedIn's own name, without its country ("Île-de-France"), and its aliases.
                terms.add(key(covered.split(",")[0]))
                terms.update(ALIASES[covered])
            else:
                patterns.append(_city(covered))
        whole = frozenset() if name in HOME else frozenset({name})
        return _Place(terms, patterns, _homes(HOME.get(name, name)), whole)
    # The gazetteer's cities the saved place names, else its countries: never for a region of
    # the table, whose name holds its country ("Île-de-France").
    found = gazetteer.places_of(text)
    if len(place) > PLACE_MAX and not found:
        return None
    homes: set[str] = set()
    for hit in found:
        homes |= _homes(CITY_HOME[hit.name] if hit.kind == "city" else hit.name)
    # A longer saved place is judged by the places of its first PLACE_MAX characters only: its
    # text, cut, is never a location's.
    terms = {own} if len(place) <= PLACE_MAX else set()
    return _Place(
        terms,
        [hit.pattern for hit in found],
        frozenset(homes),
        frozenset(hit.name for hit in found if hit.kind == "country"),
    )


class WantedPlaces:
    """The saved places a posting's place must match (module docstring). `free` when none is
    saved or one says no limit (`worldwide`, finder.worldwide), or when the only places saved
    besides remote are too long to read (`unread`): every place is kept."""

    def __init__(
        self,
        places: list[str],
        remote_stance: str | None,
        worldwide: Callable[[str], bool],
    ) -> None:
        self.remote = remote_stance == "remote" or any(key(p) in REMOTE_PLACES for p in places)
        located = [p for p in places if key(p) not in REMOTE_PLACES]
        read = [read_place(p) for p in located]
        self.places = tuple(p for p in read if p is not None)
        self.unread = len(read) - len(self.places)
        self.free = (
            not places or any(worldwide(p) for p in places) or (self.unread > 0 and not self.places)
        )

    def matches(self, location: str, title: str = "") -> bool:
        """Whether a posting at `location` (its title for a remote one) is kept."""
        if self.free or not location.strip():
            return True
        if self.remote and (says_remote(location) or says_remote(title)):
            return True
        text = location[:PLACE_MAX]
        where, folded, country = key(text), fold(text), country_of(text)
        return any(place.holds(where, folded, country) for place in self.places)
