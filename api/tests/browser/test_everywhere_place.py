# ruff: noqa: E501  tables of addresses, one per line
"""No place limit (2026-10-30). A LinkedIn sweep of a person whose saved place was "everywhere"
typed the saved title into LinkedIn's search box, but LinkedIn's place box held a city from the
person's profile on the site, so the results covered that city alone; the agent said it did not
get to clear that place.

When the saved places are empty or one of them says no place limit (discovery.worldwide,
Scope.everywhere; the api's finder.worldwide the same), the agent is told to clear a place the
website set, and browserd also lets it type the website's own worldwide name in a place box
("Worldwide", "Monde entier"), choose it in a `location` select, click the place box's
suggestion that says only it, and open a result list whose `l` or `location` holds it. With only
specific places saved none of that goes through, and a result list whose `l` or `location`
holds a worldwide name is refused, written in any case or spacing (another wide place there
is a declared open point). Each rule is a table of what it
accepts and what it refuses, near misses included (implementer.md, Heuristic code).
"""

from typing import Any

import pytest

from app.browser import finder
from tests.browser.fakes import ScriptedFinder
from tests.browser.test_b19_search import INDEED, JUNGLE, LINKEDIN, control
from tests.browser.test_j18_scope import START, discovery, typed
from tests.browser.test_j18_slips import FoldingSite
from tests.browser.test_multi_search import run
from tests.browser.test_selected_interactive import selected

TITLES = ["GenAI Engineer", "Solution Architect"]

# --- which words say no place limit --------------------------------------------------------------

WORLDWIDE = [
    "everywhere", "Everywhere", "EVERYWHERE", "anywhere", "Anywhere", "worldwide", "Worldwide",
    "World", "partout", "Partout", "n'importe où", "N’importe où", "Monde", "monde entier",
    "Monde entier", "Le monde entier", "Weltweit", "überall", "Überall",
]  # fmt: skip
NOT_WORLDWIDE = [
    "", " ", " Worldwide", "Worldwide ", "Worldwide.", "Paris", "France", "Europe", "Remote", "Télétravail", "Lyon et périphérie",
    "Worldwide, Paris", "Worldwide Paris", "worldwide jobs", "World 1", "everywhere!", "every where",
    "Wоrldwide",  # a Cyrillic small o
    "Ｗｏｒｌｄｗｉｄｅ",  # full width letters
    "World​wide", "monde\nentier", "tout le monde", "international", "any", "all",
    "Worldwide" * 20, None, 42, ["Worldwide"],
]  # fmt: skip


@pytest.mark.parametrize("text", WORLDWIDE)
def test_these_words_say_no_place_limit(text):
    assert discovery.worldwide(text)
    assert finder.worldwide(text)


@pytest.mark.parametrize("text", NOT_WORLDWIDE)
def test_anything_else_is_a_place_or_nothing(text):
    assert not discovery.worldwide(text)
    assert not finder.worldwide(text)


def test_browserd_and_the_api_read_the_same_words():
    assert discovery.WORLDWIDE == finder.WORLDWIDE
    assert discovery.WORLDWIDE_TEXT.pattern == finder.WORLDWIDE_TEXT.pattern


@pytest.mark.parametrize(
    ("places", "everywhere"),
    [
        ([], True),
        (["everywhere"], True),
        (["Partout"], True),
        (["Paris", "worldwide"], True),
        (["Paris"], False),
        (["Paris", "Lyon"], False),
        (["Remote"], False),
        (["Worldwide, Paris"], False),
    ],
)
def test_a_scope_has_no_place_limit_when_no_place_or_a_worldwide_one_is_saved(places, everywhere):
    raw = {"start": LINKEDIN, "titles": TITLES, "places": places, "filters": {}}
    assert discovery.Scope.parse(raw).everywhere is everywhere
    assert finder.InteractiveScope(LINKEDIN, tuple(TITLES), tuple(places)).everywhere is everywhere


# --- browserd: typing, choosing and clicking a worldwide place -----------------------------------

EVERYWHERE = ["everywhere"]
SPECIFIC = ["Paris", "Saint-Étienne"]


def signed_in(places: list[str], start: str = LINKEDIN) -> discovery.Scope:
    raw = {"start": start, "titles": TITLES, "places": places, "filters": {}, "signed_in": True}
    return discovery.Scope.parse(raw)


def judge(shape: dict, tool: str, places: list[str], *, text: Any = None, start=LINKEDIN):
    args = {} if text is None else {"text": text}
    return discovery.verdict(shape, tool, start, scope=signed_in(places, start), args=args)


# LinkedIn's place box as it names itself, in English and in French, and the other platforms'.
PLACE_BOXES = [
    control("City, state, or zip code", role="combobox"),
    control("Ville, département ou code postal", role="combobox"),
    control("Where", role="combobox"),
    control("Où ?"),
    control("Localisation"),
]
TITLE_BOXES = [
    control("Search by title, skill, or company", role="combobox"),
    control("Intitulé de poste, compétence ou entreprise", role="combobox"),
    control("Job title, keywords, or company"),
]
UNKNOWN_BOX = control("Type here")  # no word that tells its kind


@pytest.mark.parametrize("shape", [*PLACE_BOXES, UNKNOWN_BOX])
@pytest.mark.parametrize(
    "text", ["Worldwide", "Monde entier", "monde", "Weltweit", "everywhere", ""]
)
def test_with_no_place_limit_a_place_box_takes_the_website_s_worldwide_name(shape, text):
    assert judge(shape, "browser_type", EVERYWHERE, text=text) is None
    assert judge(shape, "browser_type", [], text=text) is None


@pytest.mark.parametrize("shape", PLACE_BOXES)
@pytest.mark.parametrize("text", ["Worldwide", "Monde entier", "partout", "World"])
def test_with_specific_places_a_worldwide_name_is_refused(shape, text):
    assert judge(shape, "browser_type", SPECIFIC, text=text) == discovery.UNSUPPORTED


@pytest.mark.parametrize("shape", PLACE_BOXES)
@pytest.mark.parametrize("text", ["Paris", "SAINT-ETIENNE", ""])
def test_with_specific_places_the_saved_places_still_go_in(shape, text):
    assert judge(shape, "browser_type", SPECIFIC, text=text) is None


@pytest.mark.parametrize("shape", TITLE_BOXES)
def test_a_title_box_never_takes_a_worldwide_name(shape):
    assert judge(shape, "browser_type", EVERYWHERE, text="Worldwide") == discovery.UNSUPPORTED
    assert judge(shape, "browser_type", EVERYWHERE, text="GenAI Engineer") is None


@pytest.mark.parametrize("shape", PLACE_BOXES)
@pytest.mark.parametrize("text", ["Europe", "France", "Worldwide, Paris", "Wоrldwide"])
def test_no_place_limit_lets_no_other_place_in(shape, text):
    assert judge(shape, "browser_type", EVERYWHERE, text=text) == discovery.UNSUPPORTED


def everywhere_native(places: list[str]) -> discovery.Scope:
    return discovery.Scope.parse(
        {"start": START, "titles": TITLES, "places": places, "filters": {}}
    )


@pytest.mark.parametrize(("places", "takes"), [(EVERYWHERE, True), ([], True), (SPECIFIC, False)])
def test_a_native_location_field_and_select_take_it_only_with_no_place_limit(places, takes):
    scope = everywhere_native(places)
    assert typed("location", "Worldwide", scope) is takes
    assert scope.takes("location", "Monde entier") is takes
    assert scope.chooses("location", "Worldwide") is takes
    assert typed("q", "Worldwide", scope) is False  # never in the search box


def test_a_native_search_request_may_carry_it_only_with_no_place_limit():
    url = START + "search?" + "q=GenAI+Engineer&location=Worldwide"
    assert discovery.read_url(url, START, scope=everywhere_native(EVERYWHERE))
    assert not discovery.read_url(url, START, scope=everywhere_native(SPECIFIC))


def option(*names: str, tag: str = "div", in_form: bool = False, **more: Any) -> dict:
    return control(*names, tag=tag, kind="", role="option", in_form=in_form, **more)


OPTIONS = [
    option("Worldwide"),
    option("Monde entier"),
    option("Weltweit"),
    option("Worldwide", "jobs-search-box-location-id-ember25-typeahead-result-0"),
    option("Monde entier", "icon"),
    option("Worldwide", tag="li"),
]
NOT_OPTIONS = [
    option("Paris"),
    option("Paris, Île-de-France, France"),
    option("Worldwide jobs alert"),
    option("Worldwide", "Follow"),
    option("Worldwide", "save-search-button"),
    option("Wоrldwide"),
    option(),
    control("Worldwide", tag="button", kind="button", role="option", in_form=False),
    control("Worldwide", tag="a", kind="", role="option", in_form=False, href=LINKEDIN + "feed/"),
    option("Worldwide", holds_submit=True),
    option("Worldwide", editable=True),
    option("Worldwide", in_form=True),
    option("Worldwide", in_control=True),  # inside a link or a button: the click would reach it
]


@pytest.mark.parametrize("shape", OPTIONS)
def test_with_no_place_limit_the_worldwide_suggestion_is_chosen(shape):
    assert judge(shape, "browser_click", EVERYWHERE) is None
    assert judge(shape, "browser_click", []) is None


@pytest.mark.parametrize("shape", OPTIONS)
def test_with_specific_places_the_worldwide_suggestion_is_refused(shape):
    assert judge(shape, "browser_click", SPECIFIC) == discovery.UNSUPPORTED


@pytest.mark.parametrize("shape", NOT_OPTIONS)
def test_no_other_suggestion_is_clicked(shape):
    assert judge(shape, "browser_click", EVERYWHERE) is not None


# role=option on a link or a search button (review of 2026-10-30): judged as the link or the
# button it is, as before the worldwide suggestion, whatever the saved places.
OPTION_LINKS_AND_BUTTONS = [
    control(
        "GenAI Engineer jobs",
        tag="a",
        kind="",
        role="option",
        href="https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&geoId=92000000",
    ),
    control("Search", tag="button", kind="button", role="option"),
]


@pytest.mark.parametrize("shape", OPTION_LINKS_AND_BUTTONS)
@pytest.mark.parametrize("places", [SPECIFIC, ["Paris"], EVERYWHERE, []])
def test_a_link_or_search_button_with_role_option_is_still_clicked(shape, places):
    assert judge(shape, "browser_click", places) is None


@pytest.mark.parametrize("places", [SPECIFIC, EVERYWHERE])
def test_any_other_option_is_refused_whatever_the_places(places):
    assert judge(option("Paris"), "browser_click", places) == discovery.UNSUPPORTED
    assert judge(option("Search", in_control=True), "browser_click", places) is not None


def test_facts_report_a_link_or_button_around_the_control():
    assert "in_control: !!(up(control) && closest(up(control), " in discovery.detect.FACTS_JS
    assert '"a, button, [role=button], [role=link]"' in discovery.detect.FACTS_JS


def test_an_anonymous_discovery_never_clicks_a_suggestion():
    scope = discovery.Scope.parse(
        {"start": LINKEDIN, "titles": TITLES, "places": EVERYWHERE, "filters": {}}
    )
    shape = option("Worldwide")
    assert discovery.verdict(shape, "browser_click", LINKEDIN, scope=scope, args={}) is not None


# --- browserd: a result list whose place says worldwide -------------------------------------------

WORLDWIDE_LISTS = [
    (
        LINKEDIN,
        "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&location=Worldwide&geoId=92000000",
    ),
    (
        LINKEDIN,
        "https://fr.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&location=Monde%20entier",
    ),
    (INDEED, "https://fr.indeed.com/jobs?q=GenAI+Engineer&l=Worldwide"),
    (
        JUNGLE,
        "https://www.welcometothejungle.com/fr/jobs?query=GenAI%20Engineer&location=everywhere",
    ),
]
PLACE_LISTS = [
    (
        LINKEDIN,
        "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&location=Paris%2C%20%C3%8Ele-de-France%2C%20France&geoId=105015875",
    ),
    (LINKEDIN, "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer"),
    (INDEED, "https://fr.indeed.com/jobs?q=GenAI+Engineer&l="),
    (INDEED, "https://fr.indeed.com/jobs?q=GenAI+Engineer&l=Paris"),
]


@pytest.mark.parametrize(("start", "url"), WORLDWIDE_LISTS)
def test_a_worldwide_result_list_opens_with_no_place_limit(start, url):
    assert discovery.page_url(url, signed_in(EVERYWHERE, start))
    assert discovery.page_url(url, signed_in([], start))


@pytest.mark.parametrize(("start", "url"), WORLDWIDE_LISTS)
def test_a_worldwide_result_list_never_opens_with_specific_places(start, url):
    assert not discovery.page_url(url, signed_in(SPECIFIC, start))


@pytest.mark.parametrize(("start", "url"), PLACE_LISTS)
def test_other_result_lists_open_as_before_whatever_the_places(start, url):
    for places in (EVERYWHERE, SPECIFIC, []):
        assert discovery.page_url(url, signed_in(places, start)), places


# A worldwide name written another way in a place key: refused with specific places all the same
# (the refusal reads keys in any case and values folded; `worldwide`, which allows, stays strict).
SEARCH = "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&"
LOOSE_WORLDWIDE_LISTS = [
    (LINKEDIN, SEARCH + "location=%20Worldwide"),
    (LINKEDIN, SEARCH + "location=Worldwide."),
    (LINKEDIN, SEARCH + "Location=Worldwide"),
    (LINKEDIN, SEARCH + "LOCATION=monde+ENTIER+"),
    (LINKEDIN, SEARCH + "location=%EF%BC%B7orldwide"),
    (INDEED, "https://fr.indeed.com/jobs?q=GenAI+Engineer&L=%20Partout%21"),
]


@pytest.mark.parametrize(("start", "url"), LOOSE_WORLDWIDE_LISTS)
def test_a_worldwide_name_written_loosely_never_opens_with_specific_places(start, url):
    assert not discovery.page_url(url, signed_in(SPECIFIC, start))


@pytest.mark.parametrize(("start", "url"), LOOSE_WORLDWIDE_LISTS)
def test_a_worldwide_name_written_loosely_opens_with_no_place_limit(start, url):
    assert discovery.page_url(url, signed_in(EVERYWHERE, start))
    assert discovery.page_url(url, signed_in([], start))


def test_another_wide_place_in_a_result_list_is_a_declared_open_point():
    """A place key is not held to the saved places (discovery's declared open point): only a
    worldwide name is refused there."""
    url = "https://www.linkedin.com/jobs/search/?keywords=GenAI%20Engineer&location=Earth"
    assert discovery.page_url(url, signed_in(SPECIFIC))


def test_a_redirect_hop_to_a_worldwide_list_is_still_followed():
    """A hop is held to the hop rule, as before: the website's own redirect is not the agent's
    widening."""
    url = WORLDWIDE_LISTS[0][1]
    assert discovery.page_url(url, signed_in(SPECIFIC), hop=True)


# --- the api: the agent's slips, its searches, its prompt -----------------------------------------


class WorldSite(FoldingSite):
    """FoldingSite whose place box (e4) is judged by browserd's own rule
    (discovery.Scope.takes on `location`), so a worldwide name passes exactly when browserd lets
    it; the place typed is the place of the result list's address."""

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "browser_type" and args.get("ref") == "e4":
            scope = args["_discovery_scope"]
            if not discovery.Scope.parse(scope).takes("location", args["text"]):
                return {"ok": False, "error": discovery.UNSUPPORTED}
            self.sent.append(args["text"])
            self.scopes.append(scope)
            self.actions.append(name)
            self.place = args["text"]
            return {"ok": True}
        return await super().call(name, args)


def search_with(place: str, typed_place: str):
    site = WorldSite(results={"robot": ["Robot Engineer"]})
    answers: list[tuple[str, bool]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        for ref, text in (("e1", "robot"), ("e4", typed_place)):
            answers.append(await session.interact("browser_type", {"ref": ref, "text": text}))
        answers.append(await session.interact("browser_click", {"ref": "e3"}))
        return finder.Ran()

    search = {"title_include": {"terms": ["robot"]}, "location": {"terms": [place]}}
    entry, _, _ = run(selected(**search), site, ScriptedFinder(script))
    return entry, answers, site


@pytest.mark.parametrize("typed_place", ["Worldwide", "Monde entier", "Weltweit"])
@pytest.mark.parametrize("place", ["everywhere", "Partout", "worldwide"])
def test_the_agent_replaces_a_place_the_website_set_with_its_worldwide_name(place, typed_place):
    """The scope that reached browserd lets the worldwide name in, the search ran, and it is
    named by the person's own saved place."""
    entry, answers, site = search_with(place, typed_place)
    assert [error for _, error in answers] == [False, False, False], answers
    assert typed_place in site.sent
    assert entry["searched"] == [{"title": "robot", "place": place}]
    assert not entry.get("refused")


@pytest.mark.parametrize("typed_place", ["Worldwide", "Monde entier", "partout"])
def test_with_a_specific_place_the_worldwide_name_is_the_model_s_slip(typed_place):
    """Never widened: refused before the browser, told to the model as its slip."""
    entry, answers, site = search_with("Paris", typed_place)
    assert answers[1] == (finder.PLACE_BOX_REFUSED, True)
    assert typed_place not in site.sent
    assert entry["refused"] == {finder.MODEL_VALUE: 1}


def session_for(places: tuple[str, ...]) -> finder.FinderSession:
    session = object.__new__(finder.FinderSession)
    session.interactive = finder.InteractiveScope("https://jobs.example.test/", ("robot",), places)
    session.boxes = {"e1": "title", "e4": "place"}
    return session


@pytest.mark.parametrize(
    ("places", "ref", "text", "refusal"),
    [
        (("everywhere",), "e4", "Worldwide", None),
        ((), "e4", "Monde entier", None),
        (("everywhere",), "e9", "Worldwide", None),  # a box the api cannot tell
        (("everywhere",), "e1", "Worldwide", finder.TITLE_BOX_REFUSED),
        (("Paris",), "e4", "Worldwide", finder.PLACE_BOX_REFUSED),
        (("Paris",), "e9", "Worldwide", finder.TYPE_REFUSED),
        (("everywhere",), "e4", "Europe", finder.PLACE_BOX_REFUSED),
    ],
)
def test_the_api_s_pre_check_lets_the_worldwide_name_through_only_with_no_place_limit(
    places, ref, text, refusal
):
    session = session_for(places)
    assert session._slip("browser_type", {"ref": ref, "text": text}) == refusal


@pytest.mark.parametrize(
    ("places", "value", "refusal"),
    [
        (("everywhere",), "Worldwide", None),
        (("Paris",), "Worldwide", finder.CHOICE_REFUSED),
        (("Paris",), "Paris", None),
    ],
)
def test_a_native_select_may_be_set_to_worldwide_only_with_no_place_limit(places, value, refusal):
    session = session_for(places)
    assert session._slip("browser_select_option", {"values": [value]}) == refusal


@pytest.mark.parametrize(
    ("places", "text", "named"),
    [
        (("everywhere",), "Worldwide", "everywhere"),
        (("Paris", "partout"), "Monde entier", "partout"),
        ((), "Worldwide", None),
        (("Paris",), "Paris", "Paris"),
        (("Paris",), "Worldwide", None),
    ],
)
def test_a_worldwide_name_is_named_by_the_person_s_saved_place(places, text, named):
    assert (
        finder.InteractiveScope("https://jobs.example.test/", ("robot",), places).place_of(text)
        == named
    )


def test_the_prompt_tells_the_agent_to_remove_a_place_the_website_set():
    prompt = finder.INTERACTIVE_PROMPT
    for words in (
        "everywhere",
        "leave the place box empty",
        "must not narrow it by itself",
        "the person's profile on it",
        "clear the place box by typing nothing in it",
        '"Worldwide" on LinkedIn',
        '"Monde entier"',
        "Indeed searches one country per site",
        "Welcome to the Jungle",
        "never type a worldwide value unless one of them means\nno place",
        "chooses the\nworldwide suggestion of the place box",
    ):
        assert words in prompt, words
    assert not any(dash in prompt for dash in ("–", "—", " - "))
