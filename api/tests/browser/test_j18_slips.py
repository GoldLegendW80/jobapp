"""A model's slip does not end the whole website (jobs J18, review follow up 2): a value the agent
types that its box does not take (a saved place in the title box, a value never saved) or a
choice that is no saved value is refused before the browser, told to the model as a tool error it
can correct, and counted apart (`refused["model_value"]`). A refusal of the website or of
browserd's guards still ends the website `unsupported_interaction`, and browserd still refuses
whatever is not allowed (test_j18_scope.py). Repeated slips end with the website's share of the
sweep's time.

The box is told from the page's snapshot (`finder.box_of`: the role and the accessible name);
when the api cannot tell, any saved title or place goes on to browserd, as before (fail closed).
"""

import asyncio
from typing import Any
from urllib.parse import urlencode

import pytest

from app.browser import finder
from tests.browser.test_b9_site import ScriptedFinder
from tests.browser.test_multi_search import SearchSite, run
from tests.browser.test_selected_interactive import execute, selected

# The website's search form, on its start page and on every result page.
FORM = "\n".join(
    [
        '- searchbox "Job title, keywords, or company" [ref=e1]',
        '- combobox "Location" [ref=e2]',
        '- button "Search" [ref=e3]',
        '- textbox "Where?" [ref=e4]',
        "- textbox [ref=e5]",
        '- textbox "Salary" [ref=e6]',
        '- button "Apply now" [ref=e7]',
    ]
)


class FormSite(SearchSite):
    """SearchSite with a search form browserd judges by each field's own name: e1 is `q` (a saved
    title or nothing), e4 is `location` (a saved place or nothing), e2 the location select (a
    saved place); e5 and e6 are other fields and e7 an apply button, refused whatever is typed or
    clicked. What browserd refuses never reaches the page."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.reached: list[tuple[str, Any]] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        scope = args["_discovery_scope"]
        ref = args.get("ref")
        if name == "browser_type":
            takes = {"e1": scope["titles"], "e4": scope["places"]}.get(ref)
            if takes is None or args["text"] not in [*takes, ""]:
                return {"ok": False, "error": "unsupported_discovery"}
        if name == "browser_select_option" and (
            ref != "e2" or args["values"][0] not in scope["places"]
        ):
            return {"ok": False, "error": "unsupported_discovery"}
        if name == "browser_click" and ref != "e3":
            return {"ok": False, "error": "unsupported_discovery"}
        self.reached.append((name, args.get("text", args.get("values"))))
        if name == "browser_type" and ref == "e4":  # the place box: its words are the place
            args.pop("_discovery"), args.pop("_discovery_scope")
            self.scopes.append(scope)
            self.actions.append(name)
            self.place = args["text"]
            return {"ok": True}
        answer = await super().call(name, args)
        page = self.pages.get(self.current, "")
        if self.current == self.base:
            self.pages[self.base] = FORM
        elif not page.startswith(FORM) and "/jobs/" not in self.current:
            self.pages[self.current] = FORM + "\n" + page
        return answer


def search_all(site: FormSite):
    """The model after its slips: each saved title in e1, each saved place in e4, Search, then
    every result opened and reported."""

    async def go(session: finder.FinderSession) -> bool:
        for title in session.interactive.titles:
            for place in session.interactive.places or (None,):
                assert (await session.interact("browser_type", {"ref": "e1", "text": title}))[
                    1
                ] is False
                if place is not None:
                    typed = await session.interact("browser_type", {"ref": "e4", "text": place})
                    assert typed[1] is False, typed
                assert (await session.interact("browser_click", {"ref": "e3"}))[1] is False
                for posting in site.results.get(title, []):
                    url = f"{site.base}jobs/{site.slug(posting)}"
                    assert (await session.navigate(url))[1] is False
                    session.report(
                        [{"title": posting, "location": "Paris", "url": url, "text": "x"}]
                    )
        return True

    return go


SEARCH = {"title_include": {"terms": ["robot", "drone"]}, "location": {"terms": ["Paris"]}}

# (tool, args, the answer): each a slip of the model, refused by the api before the browser.
SLIPS = [
    ("browser_type", {"ref": "e1", "text": "Paris"}, finder.TITLE_BOX_REFUSED),  # a place
    ("browser_type", {"ref": "e1", "text": "Robots"}, finder.TITLE_BOX_REFUSED),  # not as saved
    ("browser_type", {"ref": "e1", "text": "ROBOT\n"}, finder.TITLE_BOX_REFUSED),  # a control
    ("browser_type", {"ref": "e1", "text": "r0bot"}, finder.TITLE_BOX_REFUSED),
    ("browser_type", {"ref": "e1", "text": "sales"}, finder.TITLE_BOX_REFUSED),
    ("browser_type", {"ref": "e4", "text": "robot"}, finder.PLACE_BOX_REFUSED),  # a title
    ("browser_type", {"ref": "e4", "text": "Lyon"}, finder.PLACE_BOX_REFUSED),
    ("browser_type", {"ref": "e4", "text": "Paris, France"}, finder.PLACE_BOX_REFUSED),
    ("browser_type", {"ref": "e5", "text": "Lyon"}, finder.TYPE_REFUSED),  # an unnamed box
    ("browser_type", {"ref": "e9", "text": "robot Paris"}, finder.TYPE_REFUSED),  # no such ref
    ("browser_type", {"ref": "e1", "text": None}, finder.TITLE_BOX_REFUSED),
    ("browser_type", {"ref": "e1", "text": ["robot"]}, finder.TITLE_BOX_REFUSED),
    ("browser_type", {"ref": ["e1"], "text": 7}, finder.TYPE_REFUSED),
    ("browser_select_option", {"ref": "e2", "values": ["Lyon"]}, finder.CHOICE_REFUSED),
    ("browser_select_option", {"ref": "e2", "values": ["Paris", "remote"]}, finder.CHOICE_REFUSED),
    ("browser_select_option", {"ref": "e2", "values": []}, finder.CHOICE_REFUSED),
    ("browser_select_option", {"ref": "e2", "values": "Paris"}, finder.CHOICE_REFUSED),
    ("browser_select_option", {"ref": "e2", "values": [None]}, finder.CHOICE_REFUSED),
    ("browser_select_option", {"ref": "e2", "values": ["any"]}, finder.CHOICE_REFUSED),
]


@pytest.mark.parametrize(("tool", "args", "answer"), SLIPS)
def test_a_slip_is_told_to_the_model_and_the_good_searches_after_it_are_kept(tool, args, answer):
    site = FormSite(results={"robot": ["Robot Engineer"], "drone": ["Drone Pilot"]})
    answers: list[tuple[str, bool]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        answers.append(await session.interact(tool, dict(args)))
        await search_all(site)(session)
        return finder.Ran()

    entry, _, leases = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert answers == [(answer, True)]
    # The slip never reached the browser; the searches after it ran and their postings are kept.
    assert site.reached == [
        ("browser_navigate", None),
        ("browser_type", "robot"),
        ("browser_type", "Paris"),
        ("browser_click", None),
        ("browser_navigate", None),
        ("browser_type", "drone"),
        ("browser_type", "Paris"),
        ("browser_click", None),
        ("browser_navigate", None),
    ]
    assert entry["searched"] == [
        {"title": "robot", "place": "Paris"},
        {"title": "drone", "place": "Paris"},
    ]
    assert (entry["searches"], entry["found"]) == (2, 2)
    assert entry["error"] == "finding_persistence_pending"  # not unsupported_interaction
    assert entry["refused"] == {finder.MODEL_VALUE: 1}
    assert len(leases.acquired) == 1


def test_every_slip_together_is_counted_apart_and_the_website_is_searched():
    site = FormSite(results={"robot": ["Robot Engineer"], "drone": ["Drone Pilot"]})

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        for tool, args, answer in SLIPS:
            assert await session.interact(tool, dict(args)) == (answer, True)
        await search_all(site)(session)
        results = site.base + "search?" + urlencode({"q": "drone", "location": "Paris"})
        assert (await session.navigate(results))[1] is False
        for tool, args, answer in SLIPS:  # on a result page too, its form read again
            assert await session.interact(tool, dict(args)) == (answer, True)
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert entry["refused"] == {finder.MODEL_VALUE: 2 * len(SLIPS)}
    assert (entry["searches"], entry["found"], entry["error"]) == (
        2,
        2,
        "finding_persistence_pending",
    )


def test_repeated_slips_end_with_the_website_s_share_of_the_sweep_s_time():
    """A model that only slips is answered from the api, with no browser call that could reach
    the deadline: the slip itself reads the website's time and stops the run there."""
    site = FormSite(results={"robot": ["Robot Engineer"]})
    now = [0.0]
    slips: list[str] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        for _ in range(10_000):  # bounded here only so a broken rule fails instead of hanging
            answer, error = await session.interact("browser_type", {"ref": "e1", "text": "Paris"})
            assert error
            slips.append(answer)
            if answer.startswith("Stopped"):
                break
            now[0] += 10.0  # a model turn
        return finder.Ran()

    entries, _, leases, _, _ = asyncio.run(
        execute(
            selected(**SEARCH),
            "e2e-person",
            ScriptedFinder(script),
            tools=site,
            deadline=600,
            now=now,
        )
    )
    [entry] = entries
    assert slips[-1] == "Stopped: time_budget. Report what you found, then stop."
    assert len(slips) == 55  # at 540 s, a page could no longer start (sources.PAGE_BUDGET_S)
    assert set(slips[:-1]) == {finder.TITLE_BOX_REFUSED}
    assert entry["error"] == "time_budget"
    assert entry["refused"] == {finder.MODEL_VALUE: 55}
    assert site.reached == [("browser_navigate", None)]
    assert leases.released == ["lease-1"]


def test_slips_after_the_lease_s_time_stop_the_run_too():
    site = FormSite()
    now = [0.0]

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        now[0] = session.pages.lease_until - 10.0  # a page load could no longer fit
        answer = await session.interact("browser_type", {"ref": "e4", "text": "robot"})
        assert answer == ("Stopped: lease_budget. Report what you found, then stop.", True)
        return finder.Ran()

    entries, _, _, _, _ = asyncio.run(
        execute(
            selected(**SEARCH),
            "e2e-person",
            ScriptedFinder(script),
            tools=site,
            deadline=5000,
            now=now,
        )
    )
    assert (entries[0]["error"], entries[0]["refused"]) == ("lease_budget", {finder.MODEL_VALUE: 1})


# (tool, args): what the api lets go on (a saved value where it cannot tell the box, a click),
# and browserd or the website refuses: that still ends the website, even after good searches.
REFUSED_THERE = [
    ("browser_type", {"ref": "e5", "text": "Paris"}),  # an unnamed box: not told apart
    ("browser_type", {"ref": "e6", "text": "robot"}),  # Salary: neither a title nor a place box
    ("browser_type", {"ref": "e9", "text": "robot"}),  # a ref of no page read
    ("browser_click", {"ref": "e7"}),  # Apply now: browserd's apply guard
    ("browser_select_option", {"ref": "e5", "values": ["Paris"]}),  # a saved place, not a select
]


@pytest.mark.parametrize(("tool", "args"), REFUSED_THERE)
def test_a_refusal_of_browserd_or_the_website_still_ends_the_website(tool, args):
    site = FormSite(results={"robot": ["Robot Engineer"], "drone": ["Drone Pilot"]})
    answers: list[tuple[str, bool]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        await search_all(site)(session)
        answers.append(await session.interact(tool, dict(args)))
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert answers == [('{"error": "unsupported_discovery"}', True)]
    assert entry["error"] == "unsupported_interaction"
    assert entry["refused"] == {"unsupported_interaction": 1}
    assert (entry["searches"], entry["found"]) == (2, 2)  # what was read before stays counted


def test_a_slip_and_a_website_refusal_are_counted_apart():
    site = FormSite(results={"robot": ["Robot Engineer"]})

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        await session.interact("browser_type", {"ref": "e1", "text": "Paris"})
        await search_all(site)(session)
        await session.interact("browser_click", {"ref": "e7"})
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert entry["refused"] == {finder.MODEL_VALUE: 1, "unsupported_interaction": 1}
    assert entry["error"] == "unsupported_interaction"


def test_only_slips_and_no_search_is_still_an_unsupported_website():
    """The rule of a website the agent never searched is unchanged: no typing and no click that
    worked, the website is said unsupported (a slip is no reason, and no excuse either)."""
    site = FormSite()

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        await session.interact("browser_type", {"ref": "e1", "text": "Paris"})
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert (entry["error"], entry["refused"]) == (
        "unsupported_interaction",
        {finder.MODEL_VALUE: 1},
    )
    assert site.reached == [("browser_navigate", None)]


def test_a_box_s_name_is_read_from_the_page_last_read_and_never_kept_past_it():
    site = FormSite()
    boxes: list[dict[str, str]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        boxes.append(dict(session.boxes))
        site.pages[site.current] = '- textbox "Keywords" [ref=e4]\n- textbox "Où ?" [ref=e1]'
        await session.snapshot()
        boxes.append(dict(session.boxes))
        # e1 is now the place box: a title there is the slip, a place goes on.
        assert await session.interact("browser_type", {"ref": "e1", "text": "robot"}) == (
            finder.PLACE_BOX_REFUSED,
            True,
        )
        return finder.Ran()

    run(selected(**SEARCH), site, ScriptedFinder(script))
    assert boxes == [{"e1": "title", "e2": "place", "e4": "place"}, {"e4": "title", "e1": "place"}]


# --- the box, as the api tells it from the snapshot (finder.box_of) ---------------------------

TITLE_BOXES = [
    "Keywords", "Keyword", "Job title", "Job title, keywords, or company", "Search jobs",
    "Search", "What", "What?", "Search by title, skill, or company", "Query", "Role",
    "Skills", "Company", "Quoi ?", "Métier", "Métier, compétence, mot-clé", "Mots-clés",
    "Mot-clé", "Poste", "Intitulé du poste", "Rechercher un emploi", "Emploi", "Entreprise",
    "Compétences", "Stellenangebote durchsuchen: Beruf", "KEYWORDS", "job TITLE",
    "Poste ou mot-clé",  # "ou" is the French "or", never "où"
    "ｊｏｂ ｔｉｔｌｅ",
]  # fmt: skip
PLACE_BOXES = [
    "Location", "Where", "Where?", "City, state, zip code, or \"remote\"", "City",
    "City or postcode", "Town", "Region", "Country", "Où ?", "Où", "Lieu", "Ville",
    "Ville, département, région", "Localisation", "Code postal", "Pays", "Commune", "Adresse",
    "Address", "Standort", "Ort", "Stadt", "LOCATION", "lieu de travail", "Place",
    "Ｌｏｃａｔｉｏｎ",
]  # fmt: skip
NOT_TOLD = [
    "", "   ", "Search by title or location", "Job location",
    "Where do you want to work? Job title", "Quoi, où ?", "Salary", "Remote", "Télétravail",
    "Contract type", "Type de contrat", "Email",
    "Distance", "Radius", "Sort by", "e1", "Filter", "ou", "Ou ?", "Locate me", "Joblessness",
    "Cityscape", "Titled", "Keywording", "Placement", "Employer branding",
]  # fmt: skip


@pytest.mark.parametrize("name", TITLE_BOXES)
def test_a_title_box_is_told_from_its_name(name):
    for role in ("searchbox", "textbox", "combobox"):
        assert finder.box_of(role, name) == "title", (role, name)


@pytest.mark.parametrize("name", PLACE_BOXES)
def test_a_place_box_is_told_from_its_name(name):
    for role in ("searchbox", "textbox", "combobox"):
        assert finder.box_of(role, name) == "place", (role, name)


@pytest.mark.parametrize("name", NOT_TOLD)
def test_a_box_whose_name_says_both_or_neither_is_not_told_apart(name):
    for role in ("searchbox", "textbox", "combobox"):
        assert finder.box_of(role, name) is None, (role, name)


@pytest.mark.parametrize(
    "role", ["button", "link", "checkbox", "radio", "option", "text", "", None]
)
def test_an_element_that_is_no_text_entry_is_never_a_box(role):
    for name in ("Keywords", "Location"):
        assert finder.box_of(role, name) is None


@pytest.mark.parametrize("name", [None, 7, ["Location"], b"Location"])
def test_a_name_that_is_no_text_is_never_a_box(name):
    assert finder.box_of("textbox", name) is None


def test_every_word_of_the_rule_tells_its_box_alone_and_nothing_with_one_of_the_other():
    for word in finder.TITLE_WORDS:
        assert finder.box_of("textbox", word.title()) == "title", word
    for word in finder.PLACE_WORDS:
        assert finder.box_of("textbox", word.upper()) == "place", word
    for title in finder.TITLE_WORDS:
        for place in finder.PLACE_WORDS:
            assert finder.box_of("textbox", f"{title} or {place}") is None, (title, place)


def test_a_huge_name_is_read_in_its_first_characters_only():
    assert finder.box_of("textbox", "Location " + "x" * 1_000_000) == "place"
    assert finder.box_of("textbox", "x" * finder.BOX_NAME_MAX + " Location") is None


# --- the person's words read as the person meant them (owner, 2026-10-28) ----------------------


class FoldingSite(FormSite):
    """FormSite whose boxes take a saved value with its case or accents aside, as browserd's
    discovery.saved_as does; `sent` keeps the text the model typed, as it reached the page."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.sent: list[str] = []

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        scope = args["_discovery_scope"]
        if name == "browser_type" and args["text"]:
            self.sent.append(args["text"])
            saved = {"e1": scope["titles"], "e4": scope["places"]}.get(args.get("ref"), [])
            key = finder.fold(args["text"])
            args = {**args, "text": next((v for v in saved if finder.fold(v) == key), "?")}
        return await super().call(name, args)


def test_a_case_or_accent_variant_is_typed_and_its_search_named_by_the_saved_value():
    site = FoldingSite(results={"genai engineer": ["GenAI Engineer"]})
    answers: list[tuple[str, bool]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        for ref, text in (("e1", "GenAI Engineer"), ("e4", "ILE-DE-FRANCE")):
            answers.append(await session.interact("browser_type", {"ref": ref, "text": text}))
        answers.append(await session.interact("browser_click", {"ref": "e3"}))
        return finder.Ran()

    search = {
        "title_include": {"terms": ["genai engineer"]},
        "location": {"terms": ["Île-de-France"]},
    }
    entry, _, _ = run(selected(**search), site, ScriptedFinder(script))
    assert [error for _, error in answers] == [False, False, False], answers
    assert site.sent == ["GenAI Engineer", "ILE-DE-FRANCE"]
    assert entry["searched"] == [{"title": "genai engineer", "place": "Île-de-France"}]
    assert "refused" not in entry or not entry["refused"]


@pytest.mark.parametrize("place", ["everywhere", "Anywhere", "worldwide", "partout"])
def test_a_place_read_as_no_place_leaves_the_box_empty_and_still_searches(place):
    """Places "everywhere": the agent leaves the place box empty (the prompt says so); the empty
    box is allowed and the search is named with no place."""
    site = FoldingSite(results={"robot": ["Robot Engineer"]})
    answers: list[tuple[str, bool]] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        for ref, text in (("e1", "robot"), ("e4", "")):
            answers.append(await session.interact("browser_type", {"ref": ref, "text": text}))
        answers.append(await session.interact("browser_click", {"ref": "e3"}))
        return finder.Ran()

    search = {"title_include": {"terms": ["robot"]}, "location": {"terms": [place]}}
    entry, _, _ = run(selected(**search), site, ScriptedFinder(script))
    assert [error for _, error in answers] == [False, False, False], answers
    assert entry["searched"] == [{"title": "robot", "place": None}]


def test_the_prompt_states_every_criterion_and_asks_to_interpret_them():
    prompt = finder.INTERACTIVE_PROMPT
    for words in (
        "`titles`",
        "`places`",
        "`filters`",
        "`criteria`",
        "untrusted_person_instructions",
        "as the person meant",
        "everywhere",
        "partout",
        "leave the place box empty",
        "Leaving a box empty is always allowed",
        "case or accents",
        "translation",
    ):
        assert words in prompt, words
    assert not any(dash in prompt for dash in ("\u2013", "\u2014", " - "))
