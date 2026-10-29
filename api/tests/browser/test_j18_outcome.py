"""How a job website's search ends (2026-10-28, issue 716), from the sweep of Welcome to the
Jungle that ended `unsupported_interaction` with nothing read: its home has no search box, its
search page sits behind a cookie banner that covers every click until declined, and its search
updates the list in place. The website was searched when the agent reported a verified posting,
or said after a search that the website listed no matching job; no kind of action is required.
A click the page did not let finish (browserd's `page_error`) is told to the agent as something
it can recover from; a blocker it names (no list, a cookie wall, a sign in wall, no search
control) ends the website with its own code; nothing read is never a success.
"""

from typing import Any

import pytest

from app.browser import finder
from app.browser.sources import interactive
from tests.browser.fakes import ScriptedFinder
from tests.browser.test_b13_runtime import BASE
from tests.browser.test_multi_search import run
from tests.browser.test_selected_interactive import selected

JOBS = BASE + "fr/jobs"
HOME = (
    '- paragraph: The best companies are hiring.\n- link "Find a job" [ref=e30]\n  - /url: /fr/jobs'
)
BANNER = (
    '- dialog "C is for COOKIE":\n'
    '  - button "OK for me" [ref=e21]\n'
    '  - button "No, thanks" [ref=e20]'
)
FORM = '- searchbox "What job are you looking for?" [ref=e1]{typed}\n- button "Search" [ref=e3]'


class Jungle:
    """A website like Welcome to the Jungle: `/` a marketing page with a "Find a job" link, its
    search page `/fr/jobs` behind a cookie banner (any click or typing there fails as browserd
    answers it, `page_error`, until "No, thanks" is clicked), a search that lists the postings
    of the title typed on the same address, or only a count when `listed` is off."""

    def __init__(
        self,
        results: dict[str, list[str]] | None = None,
        listed: bool = True,
        covers: bool = True,
        broken: dict[str, str] | None = None,
    ):
        self.results, self.listed = results or {}, listed
        # `covers` off: the banner covers the search button only, not the box; `broken`: refs
        # whose click fails with that error whatever the banner.
        self.covers, self.broken = covers, broken or {}
        self.current, self.typed, self.searched = "", "", None
        self.banner = True
        self.calls: list[tuple[str, Any]] = []

    def slug(self, title: str) -> str:
        return BASE + "fr/companies/example-robotics/jobs/" + title.lower().replace(" ", "-")

    def page(self, url: str) -> str | None:
        if url == BASE:
            return HOME
        if url == JOBS:
            shown = [BANNER] if self.banner else []
            shown.append(FORM.replace("{typed}", f": {self.typed}" if self.typed else ""))
            if self.searched is not None and not self.listed:
                shown.append("- paragraph: 9756 jobs found. Yes, we counted.")
            elif self.searched is not None:
                titles = self.results.get(self.searched, [])
                shown += [
                    f'- link "{t}" [ref=e{40 + n}]\n  - /url: {self.slug(t)}'
                    for n, t in enumerate(titles)
                ] or ["- paragraph: No job matches your search."]
            return "\n".join(shown)
        for titles in self.results.values():
            for title in titles:
                if url == self.slug(title):
                    return f'- heading "{title}" [level=1]\n- paragraph: What the {title} does.'
        return None

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert args.pop("_discovery") == "native_get"
        args.pop("_discovery_scope")
        self.calls.append((name, args.get("ref") or args.get("url")))
        if name == "browser_navigate":
            if self.page(args["url"]) is None:
                return {"ok": False, "error": "navigation_failed"}
            self.current = args["url"]
            return {"ok": True}
        if args.get("ref") in self.broken:
            return {"ok": False, "error": self.broken[args["ref"]]}
        covered = self.covers or args.get("ref") == "e3"
        if self.current == JOBS and self.banner and covered and args.get("ref") != "e20":
            return {"ok": False, "error": "page_error"}
        if name == "browser_click" and args["ref"] == "e20":
            self.banner = False
            return {"ok": True, "declined": True}  # as browserd says a decline
        elif name == "browser_click" and args["ref"] == "e3":
            self.searched = self.typed
        elif name == "browser_type":
            self.typed = args["text"]
        return {"ok": True}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "url": self.current, "snapshot": self.page(self.current)}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


SEARCH = {"title_include": {"terms": ["genai engineer"]}, "location": {"terms": ["everywhere"]}}


def as_a_person(site: Jungle, answers: list, reason: str | None = None):
    """The model: from the home to the search page by its link, a click through the banner that
    fails, the banner declined, the saved title searched, each result opened and reported; or,
    when nothing is listed, `reason` reported."""

    async def script(session: finder.FinderSession) -> finder.Ran:
        answers.append(await session.snapshot())
        answers.append(await session.navigate("/fr/jobs"))
        answers.append(await session.interact("browser_click", {"ref": "e3"}))  # covered
        answers.append(await session.interact("browser_click", {"ref": "e20"}))  # No, thanks
        answers.append(
            await session.interact("browser_type", {"ref": "e1", "text": "GenAI Engineer"})
        )
        answers.append(await session.interact("browser_click", {"ref": "e3"}))
        for title in site.results.get("GenAI Engineer", []):
            answers.append(await session.navigate(site.slug(title)))
            answers.append(
                await session.report_verified(
                    [{"title": title, "url": site.slug(title), "text": "x"}]
                )
            )
        if reason:
            answers.append(session.report_none(reason))
        return finder.Ran()

    return script


def test_a_website_like_the_jungle_is_searched_through_its_banner_and_its_link():
    site = Jungle(results={"GenAI Engineer": ["GenAI Engineer", "Applied AI Engineer"]})
    answers: list = []
    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(as_a_person(site, answers)))
    assert answers[2] == (finder.ACTION_FAILED, True)  # told, not refused
    assert [error for _, error in answers[3:]] == [False] * 7, answers
    # Searched: no error of the website (without the store's commits, the postings read wait for
    # them, as in test_j18_slips).
    assert entry["error"] == "finding_persistence_pending"
    assert entry["found"] == 2
    assert entry["refused"] == {}
    assert entry["searched"] == [{"title": "genai engineer", "place": None}]


def test_a_failed_click_is_told_and_never_counted_as_a_refusal_of_the_website():
    site = Jungle()
    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        answers.append(await session.interact("browser_click", {"ref": "e3"}))
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert answers == [(finder.ACTION_FAILED, True)]
    assert "cookie banner" in finder.ACTION_FAILED
    assert entry["refused"] == {}
    assert entry["error"] == "nothing_reported"  # nothing read: no success either


@pytest.mark.parametrize("code", sorted(finder.RECOVERABLE))
def test_every_failure_of_the_page_is_recoverable(code):
    class Failing(Jungle):
        async def call(self, name, args):
            if name == "browser_click":
                return {"ok": False, "error": code}
            return await super().call(name, args)

    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        answers.append(await session.interact("browser_click", {"ref": "e20"}))
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Failing(), ScriptedFinder(script))
    assert answers == [(finder.ACTION_FAILED, True)]
    assert entry["refused"] == {}


def test_a_failed_action_past_the_websites_time_ends_it_as_a_slip_does():
    """Round 2 of PR 720: the recoverable path reads Pages._act's bounds after the failure, so a
    model that only fails actions still ends at the website's share of the sweep's time."""
    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        call = session.pages.tools.call

        async def failing(name, args):
            if name != "browser_click":
                return await call(name, args)
            session.pages.deadline = float("-inf")  # the click took the website's last second
            return {"ok": False, "error": "element_covered"}

        session.pages.tools.call = failing
        answers.append(await session.interact("browser_click", {"ref": "e20"}))
        answers.append(await session.interact("browser_click", {"ref": "e20"}))
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Jungle(), ScriptedFinder(script))
    stopped = ("Stopped: time_budget. Report what you found, then stop.", True)
    assert answers == [stopped, stopped]
    assert entry["refused"] == {}
    assert entry["error"] == "time_budget"


def test_a_guard_browserd_could_not_restore_ends_the_website_with_its_own_code():
    """Round 2 of PR 720: browserd's `guard_failed` (a tab off the list it could not close, a
    page it could not take back) is no page to recover on: the website ends there."""

    class Unguarded(Jungle):
        async def call(self, name, args):
            if name == "browser_click":
                return {"ok": False, "error": "guard_failed"}
            return await super().call(name, args)

    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        answers.append(await session.interact("browser_click", {"ref": "e20"}))
        answers.append(await session.navigate("/fr/jobs"))
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Unguarded(), ScriptedFinder(script))
    stopped = ("Stopped: guard_failed. Report what you found, then stop.", True)
    assert answers == [stopped, stopped]
    assert "guard_failed" not in finder.RECOVERABLE
    assert entry["error"] == "guard_failed"


def test_a_navigation_whose_guard_browserd_could_not_restore_ends_the_website_too():
    """A page browserd could not take back after a navigation (its `guard_failed` on
    browser_navigate, routes.call_tool) ends the website as a click's does, never "The page did
    not load." for the agent to go on from."""

    class Unguarded(Jungle):
        async def call(self, name, args):
            if name == "browser_navigate" and args.get("url", "").endswith("/fr/jobs"):
                return {"ok": False, "error": "guard_failed"}
            return await super().call(name, args)

    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        answers.append(await session.navigate("/fr/jobs"))
        answers.append(await session.interact("browser_click", {"ref": "e20"}))
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Unguarded(), ScriptedFinder(script))
    stopped = ("Stopped: guard_failed. Report what you found, then stop.", True)
    assert answers == [stopped, stopped]
    assert entry["error"] == "guard_failed"


def test_a_search_that_lists_no_matching_job_said_honestly_is_a_searched_website():
    site = Jungle(results={})
    answers: list = []
    agent = ScriptedFinder(as_a_person(site, answers, "no_match"))
    entry, _, _ = run(selected(**SEARCH), site, agent)
    assert answers[-1] == ("Noted. Report any posting you opened, then stop.", False)
    assert (entry["error"], entry["found"], entry["searches"]) == (None, 0, 1)


def test_no_match_without_any_search_is_never_a_success():
    site = Jungle()

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        session.report_none("no_match")
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert (entry["error"], entry["searches"]) == ("nothing_reported", 0)


class Linked(Jungle):
    """The same website, its search page with two links besides (round 2, review of PR 720):
    "Home" (e31, the home, no saved title in its address) and "GenAI Engineer jobs" (e32, a
    search by address carrying the saved title); `clears` empties the box when Search runs."""

    SEARCHED = JOBS + "?query=genai+engineer"
    LINKS = (
        '\n- link "Home" [ref=e31]\n  - /url: /'
        '\n- link "GenAI Engineer jobs" [ref=e32]\n  - /url: /fr/jobs?query=genai+engineer'
    )

    def __init__(self, clears: bool = False, **more: Any):
        super().__init__(**more)
        self.clears = clears

    def page(self, url: str) -> str | None:
        if url == self.SEARCHED:
            return "- paragraph: No job matches your search."
        shown = super().page(url)
        return shown + self.LINKS if url == JOBS and shown else shown

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ref = args.get("ref")
        if name == "browser_click" and ref in {"e31", "e32"} and not self.banner:
            args.pop("_discovery"), args.pop("_discovery_scope")
            self.calls.append((name, ref))
            self.current = BASE if ref == "e31" else self.SEARCHED
            return {"ok": True}
        answer = await super().call(name, args)
        if name == "browser_click" and ref == "e3" and answer["ok"] and self.clears:
            self.typed = ""
        return answer


def typed_then(steps: list[tuple[str, str]], answers: list):
    """The model on the search page: the saved title typed in the box the banner leaves free,
    then these clicks (`decline` first when listed), then "no match" said."""

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        answers.append(
            await session.interact("browser_type", {"ref": "e1", "text": "GenAI Engineer"})
        )
        for _, ref in steps:
            answers.append(await session.interact("browser_click", {"ref": ref}))
        answers.append(session.report_none("no_match"))
        return finder.Ran()

    return script


@pytest.mark.parametrize("failure", ["page_error", "element_covered", "stale_ref"])
@pytest.mark.parametrize("declined", [True, False])
def test_a_decline_and_a_failed_search_click_are_no_search(failure, declined):
    """Typed in a box the banner leaves free, the banner declined (its click went through), the
    search button's click failed: no search ran, so "no match" is no success."""
    site = Linked(covers=False, broken={"e3": failure})
    answers: list = []
    steps = [("decline", "e20")] if declined else []
    script = typed_then([*steps, ("search", "e3")], answers)
    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert [error for _, error in answers] == [False, *([False] if declined else []), True, False]
    assert answers[-2] == (finder.ACTION_FAILED, True)
    assert (entry["error"], entry["searches"], entry["refused"]) == ("nothing_reported", 0, {})


def test_a_decline_click_alone_is_no_search_even_with_the_title_in_the_box():
    site = Linked(covers=False)
    answers: list = []
    script = typed_then([("decline", "e20")], answers)
    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert "GenAI Engineer" in site.page(JOBS)  # the box still shows it
    assert (entry["error"], entry["searches"]) == ("nothing_reported", 0)


@pytest.mark.parametrize(
    ("ref", "searched"),
    [
        ("e31", False),  # a link to an address with no saved title
        ("e32", True),  # a link whose address carries the saved title
    ],
)
def test_a_link_click_counts_only_when_its_address_carries_a_saved_title(ref, searched):
    site = Linked(covers=False)
    answers: list = []
    script = typed_then([("decline", "e20"), ("link", ref)], answers)
    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert [error for _, error in answers] == [False, False, False, False]
    expected = (None, 1) if searched else ("nothing_reported", 0)
    assert (entry["error"], entry["searches"]) == expected


@pytest.mark.parametrize(("clears", "searched"), [(False, True), (True, False)])
def test_a_button_click_counts_only_when_the_page_then_shows_the_typed_title(clears, searched):
    """A search in place keeps the title in its box (counted); a button after which the box no
    longer holds it, the address unchanged, ran no search the session can see."""
    site = Linked(covers=False, clears=clears)
    answers: list = []
    script = typed_then([("decline", "e20"), ("search", "e3")], answers)
    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert [error for _, error in answers] == [False, False, False, False]
    expected = (None, 1) if searched else ("nothing_reported", 0)
    assert (entry["error"], entry["searches"]) == expected


def test_a_page_that_shows_only_a_count_is_reported_as_no_list():
    """Welcome to the Jungle signed out: "9756 jobs found. Yes, we counted." and no list."""
    site = Jungle(listed=False)
    answers: list = []
    entry, _, _ = run(
        selected(**SEARCH), site, ScriptedFinder(as_a_person(site, answers, "no_list"))
    )
    assert entry["error"] == "no_job_list"
    assert entry["searches"] == 1


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("no_list", "no_job_list"),
        ("cookie_wall", "cookie_wall"),
        ("signin_wall", "signin_wall"),
        ("no_search_control", "no_search_box"),
    ],
)
def test_a_blocker_the_agent_names_ends_the_website_with_its_own_code(reason, code):
    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        assert session.report_none(reason) == (
            "Noted. Report any posting you opened, then stop.",
            False,
        )
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Jungle(), ScriptedFinder(script))
    assert entry["error"] == code
    assert entry["found"] == 0


@pytest.mark.parametrize("reason", ["", "No_match", "accepted", None, 3, ["no_list"]])
def test_an_unknown_reason_is_refused_and_changes_nothing(reason):
    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.snapshot()
        assert session.report_none(reason) == ("Refused: give one reason of the list.", True)
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), Jungle(), ScriptedFinder(script))
    assert entry["error"] == "nothing_reported"


def test_a_posting_reported_wins_over_a_blocker_said_later():
    site = Jungle(results={"GenAI Engineer": ["GenAI Engineer"]})
    entry, _, _ = run(
        selected(**SEARCH), site, ScriptedFinder(as_a_person(site, [], "signin_wall"))
    )
    assert (entry["error"], entry["found"]) == ("finding_persistence_pending", 1)


def test_a_refusal_with_nothing_read_stays_unsupported():
    site = Jungle()

    async def script(session: finder.FinderSession) -> finder.Ran:
        await session.navigate("/fr/jobs")
        await session.interact("browser_click", {"ref": "e20"})

        async def refuse(name, args):
            return {"ok": False, "error": "unsupported_discovery"}

        site.call = refuse
        answers = await session.interact("browser_click", {"ref": "e3"})
        assert answers == ('{"error": "unsupported_discovery"}', True)
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert entry["error"] == "unsupported_interaction"
    assert entry["refused"] == {"unsupported_interaction": 1}


@pytest.mark.parametrize(
    ("path", "opened"),
    [
        ("/fr/jobs", True),
        ("/jobs", False),  # allowed, but this website has no such page: did not load
        ("/fr/jobs?query=genai+engineer", None),  # a search by address: from a link only
        ("/fr/companies", None),
        ("/fr/jobs/../account", None),
        ("/settings/jobs", None),  # an endpoint at another depth (review of PR 720, round 2)
        ("/me/jobs", None),
        ("/admin/jobs", None),
        ("/inbox/search", None),
        ("/fr/mon-compte/jobs", None),
        ("/oauth/authorize/jobs", None),
        ("/JOBS", None),  # compared as written, as browserd does
        ("https://elsewhere.example.test/jobs", None),
    ],
)
def test_the_search_page_may_be_opened_by_its_address(path, opened):
    """A person opens /jobs by hand; any other address no page linked to stays refused."""
    answers: list = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        answers.append(await session.navigate(path))
        return finder.Ran()

    site = Jungle()
    run(selected(**SEARCH), site, ScriptedFinder(script))
    [(text, error)] = answers
    if opened is None:
        assert error
        assert text.startswith("Refused"), text
        assert [c for c in site.calls if c[0] == "browser_navigate"] == [("browser_navigate", BASE)]
    else:
        assert error is (not opened), text


@pytest.mark.parametrize(
    ("url", "searched"),
    [
        ("search?q=GenAI%20Engineer", [{"title": "genai engineer", "place": None}]),
        ("jobs?query=genai+engineer&location=everywhere", [
            {"title": "genai engineer", "place": "everywhere"}
        ]),
        ("search?q=other", []),
        ("search?q=genai+engineer&query=genai+engineer", []),
        ("search", []),
    ],
)  # fmt: skip
def test_a_search_run_by_its_address_is_counted(url, searched):
    site = Jungle()
    site.page = lambda u: "- paragraph: Results" if u.startswith(BASE) else None

    async def script(session: finder.FinderSession) -> finder.Ran:
        session.seen.add(finder.canonical(BASE + url))
        assert (await session.navigate(BASE + url))[1] is False
        return finder.Ran()

    entry, _, _ = run(selected(**SEARCH), site, ScriptedFinder(script))
    assert entry["searched"] == searched


def test_the_prompt_teaches_the_way_a_person_searches():
    prompt = finder.INTERACTIVE_PROMPT
    for words in (
        "/jobs",
        "Find a job",
        "decline",
        "never accept cookies",
        "one saved title at a time",
        "report_no_postings",
        "no_match",
        "no_list",
        "cookie_wall",
        "signin_wall",
        "no_search_control",
        "Never report a reason you did\nnot see",
    ):
        assert words in prompt, words
    assert not any(dash in prompt for dash in ("–", "—", " - "))
    assert "report_no_postings" in finder.INTERACTIVE_TOOLS
    assert interactive.outcome.__doc__
