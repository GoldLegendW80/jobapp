"""A website the person typed into Find jobs (platform custom, mode llm_browser) is kept and
searched by the finder whatever its address: a careers page on Greenhouse, Lever or Ashby, or on a
company's own domain. The company boards and the fixed readers were deleted (owner, 2026-10-28);
the websites a person adds were not (owner, 2026-10-26: "if i want to search on a specific website
i will ad it as websites"). Invented hosts and companies only."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.browser import finder
from app.jobs import sweep
from app.profile.schemas import SearchSection
from tests.browser.fakes import FakeLeases, ScriptedFinder, Tools
from tests.browser.test_b13_runtime import FIRST
from tests.browser.test_selected_interactive import execute

URLS = [
    "https://boards.greenhouse.io/example-robotics",
    "https://job-boards.eu.greenhouse.io/example-robotics",
    "https://jobs.lever.co/example-robotics",
    "https://jobs.ashbyhq.com/example-robotics",
    "https://careers.example-robotics.example/jobs",
]


def search_with(urls: list[str]) -> SearchSection:
    """The websites, and what an older tab still sends beside them (dropped, never a reason to
    refuse the section or lose a website)."""
    return SearchSection.model_validate(
        {
            "title_include": {"terms": ["robot"]},
            "interactive_sites": [
                {"platform": "custom", "url": url, "enabled": True, "mode": "llm_browser"}
                for url in urls
            ],
            "boards": [
                {"company": "Example", "ats": "greenhouse", "org": "example", "enabled": True}
            ],
            "feeds": [{"kind": "remoteok", "enabled": True}],
            "browser_sources": [{"kind": "site", "query": URLS[0], "enabled": True}],
        }
    )


def test_custom_sites_on_any_host_are_kept_and_frozen_into_the_scope() -> None:
    search = search_with(URLS)
    assert [site.url for site in search.interactive_sites] == URLS
    assert {site.effective_platform for site in search.interactive_sites} == {"custom"}
    dumped = search.model_dump(mode="json")
    assert "boards" not in dumped
    assert "feeds" not in dumped
    assert dumped["browser_sources"] == []
    scope = sweep.freeze_scope(
        SimpleNamespace(sections={"search": dumped}, revision=1),
        sweep.SweepTrigger.manual,
        datetime.fromisoformat("2026-10-28T00:00:00+00:00"),
    )
    assert [source["url"] for source in scope["sources"]] == URLS
    assert {source["mode"] for source in scope["sources"]} == {"llm_browser"}
    assert sweep.has_sources(search)


@pytest.mark.parametrize("url", URLS)
def test_each_custom_site_reaches_the_finder(url: str) -> None:
    starts: list[str] = []

    async def script(session: finder.FinderSession) -> finder.Ran:
        starts.append(session.start)
        return finder.Ran()

    class SiteTools(Tools):
        def __init__(self) -> None:
            super().__init__({url: FIRST})

        async def call(self, name, args):
            if name == "browser_navigate":
                return await self.navigate(args["url"])
            return {"ok": True}

    leases = FakeLeases()
    entries, _, _, _, _ = asyncio.run(
        execute(
            search_with([url]),
            "e2e-person",
            ScriptedFinder(script),
            tools=SiteTools(),
            leases=leases,
        )
    )
    [entry] = entries
    assert entry["mode"] == "llm_browser"
    assert entry["kind"] == "custom"
    assert entry["result_pages"] >= 1
    assert starts == [finder.canonical(url)]
    assert leases.acquired
