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
"""

from __future__ import annotations

import re
import unicodedata

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
