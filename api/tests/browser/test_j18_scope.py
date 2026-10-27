"""What the agent of a job website may type and choose (jobs J18), browserd's rule as tables.

The rule (discovery.Scope): a search box (q, query, search, keyword, keywords) takes one saved
title, the `location` box one saved place, either of them nothing to clear it; a native filter
select takes its one saved value, the `location` select one saved place. A value is matched
against the allow list with its case and accents aside (owner, 2026-10-28: the agent reads the
person's words as the person would, so "GenAI Engineer" is the saved "genai engineer"): the same
letters in the same order, nothing else; any other value, near as it may be, is refused. A
search request carries a saved title; it may leave the place empty, or carry no place at all.
"""

import random
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "browser"))

from browserd.tools import discovery

from tests.browser.test_b5_actions import facts

START = "https://jobs.example.test/"
TITLES = ["robot engineer", "Ingénieur robotique", "chargé d'affaires", "机器人工程师", "c3po"]
PLACES = ["Paris", "Lyon", "Saint-Étienne", "São Paulo"]
FILTERS = {"remote": "remote", "contract": "CDI"}
SCOPE = discovery.Scope.parse(
    {"start": START, "titles": TITLES, "places": PLACES, "filters": FILTERS}
)
FORM = [
    {"tag": "input", "type": "search", "name": "q", "value": ""},
    {"tag": "input", "type": "text", "name": "location", "value": ""},
    {"tag": "select", "type": "select-one", "name": "remote", "value": ""},
    {"tag": "select", "type": "select-one", "name": "contract", "value": ""},
]


def control(name: str, tag: str = "input", kind: str = "text", fields=None) -> dict:
    shape = facts("Search", tag=tag, kind=kind)
    shape["discovery"] = {
        "native": True,
        "action": START + "search",
        "method": "get",
        "target": "",
        "fields": fields if fields is not None else FORM,
        "name": name,
        "href": "",
    }
    return shape


def typed(name: str, text: object, scope=SCOPE) -> bool:
    kind = "search" if name == "q" else "text"
    shape = control(name, kind=kind)
    return discovery.verdict(shape, "browser_type", START, scope=scope, args={"text": text}) is None


def chosen(name: str, values: object, scope=SCOPE) -> bool:
    shape = control(name, tag="select", kind="select-one")
    args = {"values": values}
    return discovery.verdict(shape, "browser_select_option", START, scope=scope, args=args) is None


# --- the accept table: every saved value in its own field, and nothing to clear one ----------

# The saved values with another case or accents (owner, 2026-10-28): the same letters.
VARIANT_TITLES = [
    "Robot engineer",
    "ROBOT ENGINEER",
    "robot Engineer",
    "Ingenieur robotique",  # the accent dropped
    unicodedata.normalize("NFD", "Ingénieur robotique"),  # the same letters, decomposed
    "ingénieur robotique",
    "INGÉNIEUR ROBOTIQUE",
    "charge d'affaires",
    "Chargé D'Affaires",
    "C3PO",
]
VARIANT_PLACES = [
    "paris",
    "PARIS",
    "lyon",
    "Saint-Etienne",
    "saint-étienne",
    unicodedata.normalize("NFD", "Saint-Étienne"),
    "Sao Paulo",
    "SÃO PAULO",
]
ACCEPT_TYPED = [
    *[(name, title) for title in TITLES + VARIANT_TITLES for name in sorted(discovery.QUERY)],
    *[("location", place) for place in PLACES + VARIANT_PLACES],
    *[(name, "") for name in (*sorted(discovery.QUERY), "location")],
]


@pytest.mark.parametrize(("name", "text"), ACCEPT_TYPED)
def test_a_saved_value_is_typed_in_its_own_field(name, text):
    assert typed(name, text)


@pytest.mark.parametrize(
    ("name", "values"),
    [
        *[("location", [place]) for place in PLACES + VARIANT_PLACES],
        ("remote", ["remote"]),
        ("remote", ["Remote"]),
        ("contract", ["CDI"]),
        ("contract", ["cdi"]),
    ],
)
def test_a_saved_value_is_chosen_in_its_own_select(name, values):
    assert chosen(name, values)


# --- the refuse table: near misses on every axis ---------------------------------------------

NEAR_TITLES = [
    " robot engineer",  # spacing
    "robot engineer ",
    "robot  engineer",
    "robot\tengineer",
    "robot\nengineer",
    "robot-engineer",  # glued or joined otherwise
    "robotengineer",
    "robot_engineer",
    "robot engineers",  # plural, singular, inside a longer one
    "robot",
    "engineer",
    "senior robot engineer",
    "robot engineer Paris",
    "robot engineer, Lyon",
    "engineer robot",  # word order
    "ｒｏｂｏｔ engineer",  # full width
    "rоbot engineer",  # a Cyrillic o
    "robot​engineer",  # a zero width space
    "robot engineer­",  # a soft hyphen
    "robot\u00a0engineer",  # a no break space
    "ROBOT\tENGINEER",
    "Ingenieur  robotique",
    "Ingénieurs robotique",
    "chargé d’affaires",  # a typographic apostrophe
    "robot engineer\u0085",  # a control character, folded or not
    "机器人",
    "c3p0",
    "c3po ",
    '"robot engineer"',
    "robot engineer*",
    "robot.*",
    "drone pilot",  # not saved at all
    "sales",  # an unwanted title: context only
    "Send my application",
    "postuler",
    "x" * 5000,
]


@pytest.mark.parametrize("text", NEAR_TITLES)
@pytest.mark.parametrize("name", sorted(discovery.QUERY))
def test_a_near_title_is_refused_in_a_search_box(name, text):
    assert not typed(name, text)


NEAR_PLACES = [
    "Paris ",
    " Paris",
    "Paris, France",
    "Paris 75001",
    "Pari",
    "Lyons",
    "Saint Etienne",
    "St-Étienne",
    "Sao  Paulo",
    "São  Paulo",
    "PARIS\n",
    "Ｐａｒｉｓ",
    "Раris",  # a Cyrillic P and a
    "Paris​",
    "Marseille",
    "Berlin",
    "",  # "" clears the box: it is accepted only by typing, tested above
]


@pytest.mark.parametrize("text", [t for t in NEAR_PLACES if t])
def test_a_near_place_is_refused_in_the_location_box(text):
    assert not typed("location", text)


@pytest.mark.parametrize("title", TITLES)
def test_a_title_never_goes_in_the_location_box_nor_a_place_in_a_search_box(title):
    assert not typed("location", title)
    for place in PLACES:
        assert not typed("q", place)


@pytest.mark.parametrize("name", ["remote", "contract", "category", "sort", "page", "name", ""])
@pytest.mark.parametrize("text", ["robot engineer", "Paris", "remote", "CDI", ""])
def test_no_other_field_takes_any_typing(name, text):
    assert not typed(name, text)


@pytest.mark.parametrize(
    "text",
    [None, 7, 7.0, True, ["robot engineer"], ("robot engineer",), {"robot engineer"}, b"c3po"],
)
def test_a_typed_value_that_is_not_text_is_refused_and_never_raises(text):
    assert not typed("q", text)
    assert not typed("location", text)


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("location", ["Paris "]),
        ("location", [""]),
        ("location", ["Paris", "Lyon"]),
        ("location", []),
        ("location", "Paris"),
        ("location", [["Paris"]]),
        ("location", [None]),
        ("location", ["robot engineer"]),
        ("remote", ["1"]),
        ("remote", ["remotely"]),
        ("remote", [""]),
        ("remote", ["remote", "remote"]),
        ("contract", ["CDD"]),
        ("contract", ["CDI", "CDD"]),
        ("category", ["robotics"]),
        ("sort", ["date"]),
        ("q", ["robot engineer"]),
    ],
)
def test_a_select_takes_only_its_one_saved_value(name, values):
    assert not chosen(name, values)


def test_a_scope_without_places_takes_no_place_but_clears_the_box():
    scope = discovery.Scope.parse(
        {"start": START, "titles": ["robot"], "places": [], "filters": {}}
    )
    assert typed("location", "", scope)
    assert not typed("location", "Paris", scope)
    assert not chosen("location", ["Paris"], scope)
    assert not chosen("location", [""], scope)


# --- an independent generator: 200 neighbours of each saved value, all refused ---------------


def fold(text: str) -> str:
    """An independent reading of "case and accents aside": lower case, no combining mark."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(ch)
    )


SAVED = {fold(value) for value in [*TITLES, *PLACES]}


def neighbours(value: str, rng: random.Random, count: int = 200) -> set[str]:
    """Edits of one character or one fold, none a saved value with its case or accents aside."""
    alphabet = "aeiouéèçàAEIOU -'.,_*0123456789 ​’ｒоа"
    found: set[str] = set()
    for _ in range(100 * count):  # bounded: an edit that gives a saved value is drawn again
        if len(found) == count:
            break
        at = rng.randrange(len(value) + 1)
        edit = rng.randrange(8)
        if edit == 0 and value:  # drop a character
            near = value[: max(0, at - 1)] + value[at:]
        elif edit == 1:  # add one
            near = value[:at] + rng.choice(alphabet) + value[at:]
        elif edit == 2 and value:  # change one
            near = value[: max(0, at - 1)] + rng.choice(alphabet) + value[at:]
        elif edit == 3:
            near = value.swapcase()
        elif edit == 4:
            near = unicodedata.normalize(rng.choice(["NFD", "NFKD"]), value) + rng.choice(["", " "])
        elif edit == 5:
            near = value.upper() if rng.random() < 0.5 else value.lower()
        elif edit == 6:
            near = rng.choice(["", " "]) + value + rng.choice(["s", " ", ".", "!", ""])
        else:
            near = value + " " + rng.choice([*TITLES, *PLACES])
        if fold(near) not in SAVED and near != "":
            found.add(near)
    return found


@pytest.mark.parametrize("value", [*TITLES, *PLACES])
def test_two_hundred_generated_neighbours_of_each_saved_value_are_refused(value):
    rng = random.Random(f"j18-{value}")  # noqa: S311  a seeded generator, not a secret
    near = neighbours(value, rng)
    assert len(near) == 200
    taken = [text for text in near if typed("q", text) or typed("location", text)]
    assert taken == []


# --- the search request: one saved title, one saved place when places are saved ---------------


def request(title: str, place: str | None, **more: str) -> list[tuple[str, str]]:
    pairs = [("q", title), *([("location", place)] if place is not None else [])]
    return [*pairs, ("remote", "remote"), ("contract", "CDI"), *more.items()]


@pytest.mark.parametrize(
    ("pairs", "allowed"),
    [
        *[(request(t, p), True) for t in TITLES for p in PLACES],
        (
            [("query", "c3po"), ("location", "Lyon"), *request("c3po", None)[1:], ("page", "2")],
            True,
        ),
        # The place box left empty, or no place box at all, is always allowed (owner,
        # 2026-10-28); a search still carries a saved title.
        (request("c3po", None), True),
        (request("c3po", ""), True),
        (request("", "Paris"), False),
        (request("", ""), False),
        (request("C3PO", "paris"), True),  # case and accents aside
        (request("Ingenieur robotique", "SAO PAULO"), True),
        (request("c3po", "Marseille"), False),
        (request("c3po", "Paris "), False),
        (request("c3p0", "Paris"), False),
        (request("drone", "Paris"), False),
        (request("Paris", "Paris"), False),
        (request("c3po", "c3po"), False),
        (request("c3po", "Paris")[:-1], False),  # the saved contract left out
        ([*request("c3po", "Paris")[:-1], ("contract", "CDD")], False),
        ([("query", "c3po"), *request("c3po", "Paris")], False),
        ([*request("c3po", "Paris"), ("location", "Lyon")], False),
        ([*request("c3po", "Paris"), ("q", "")], False),
        ([("location", "Paris"), ("remote", "remote"), ("contract", "CDI")], False),  # no title
        (request("c3po", "Paris\x85"), False),
    ],
)
def test_a_search_request_carries_saved_values_only(pairs, allowed):
    url = START + "search?" + urlencode(pairs)
    assert discovery.read_url(url, START, scope=SCOPE) is allowed
    fields = [{"tag": "input", "type": "hidden", "name": k, "value": v} for k, v in pairs]
    submit = control("", tag="button", kind="submit", fields=fields)
    verdict = discovery.verdict(submit, "browser_click", START, scope=SCOPE)
    assert (verdict is None) is allowed


@pytest.mark.parametrize(
    "raw",
    [
        {"start": START, "query": "robot", "filters": {}},  # the one query scope before J18
        {"start": START, "titles": [], "places": [], "filters": {}},
        {"start": START, "titles": "robot", "places": [], "filters": {}},
        {"start": START, "titles": ["robot"], "places": "Paris", "filters": {}},
        {"start": START, "titles": ["robot", "robot"], "places": [], "filters": {}},
        {"start": START, "titles": ["robot"], "places": ["Paris", "Paris"], "filters": {}},
        {"start": START, "titles": [" "], "places": [], "filters": {}},
        {"start": START, "titles": ["robot\n"], "places": [], "filters": {}},
        {"start": START, "titles": ["robot"], "places": ["Paris\x85"], "filters": {}},
        {"start": START, "titles": [7], "places": [], "filters": {}},
        {"start": START, "titles": ["r" * 301], "places": [], "filters": {}},
        {"start": START, "titles": [f"r{n}" for n in range(101)], "places": [], "filters": {}},
        {"start": START, "titles": ["robot"], "places": [], "filters": {"location": "Paris"}},
        {"start": START, "titles": ["robot"], "places": [], "filters": {"q": "robot"}},
        {"start": START, "titles": ["robot"], "places": [], "filters": {"remote": 1}},
        {"start": START, "titles": ["robot"], "places": [], "filters": {}, "extra": 1},
    ],
)
def test_a_malformed_scope_is_refused_whole(raw):
    assert discovery.Scope.parse(raw) is None


def test_the_person_s_own_saved_words_are_read_as_theirs_in_a_signed_in_query():
    """A saved title or place that holds an apply word (a search for "apply engineer") is the
    person's own search, not an application; any other apply word in the query still is."""
    scope = discovery.Scope.parse(
        {
            "start": START,
            "titles": ["apply engineer", "robot"],
            "places": ["Postel"],
            "filters": {},
            "signed_in": True,
        }
    )
    assert not discovery.query_applies("q=apply+engineer&location=Postel", scope)
    assert discovery.query_applies("q=robot&action=apply", scope)
    assert discovery.query_applies("q=apply+engineer", None)
