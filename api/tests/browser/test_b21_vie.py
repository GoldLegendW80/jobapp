"""B21 through the website Runner and a scripted model, on a stand in of the VIE website
(tests/browser_pages/b21/, served on `mon-vie-via.businessfrance.test`; these snapshots are the
pages as Playwright's snapshotForAI reads them): an offers list with a "Voir plus d'offres"
button, offer pages with several headings, a title in an h2 or in a div, "(H/F)" in titles and
an offer number. The agent reports a posting whose title is no saved title and it is stored;
a VIE posting is stored with the saved contract types CDI and CDD (the website's contract by
nature, `interactive.nature_contract`); unwanted titles still drop a posting after reading, and
elsewhere the contract rule is unchanged. Invented hosts and offers only."""

import asyncio
import random
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser import finder, sources
from app.browser.sources import interactive
from app.jobs import never_apply, sweep
from app.profile.schemas import SearchSection
from tests.browser.fakes import FakeLeases, ScriptedFinder

VIE = "https://mon-vie-via.businessfrance.test/"
LIST = VIE + "offres/recherche?query=genai"
OFFER_1 = VIE + "offres/246286"
OFFER_2 = VIE + "offres/246285"
OFFER_3 = VIE + "offres/246281"
OFFER_4 = VIE + "offres/246270"
OFFER_5 = VIE + "offres/246271"
HOME = '- searchbox "Rechercher une offre" [ref=e1]\n- button "Rechercher" [ref=e2]'


def offers(more: bool) -> str:
    cards = [
        ("246286", "AI Engineer Consultant (H/F)"),
        ("246285", "AI Engineer Consultant (H/F)"),
        ("246290", "Business Developer (H/F)"),
    ] + ([("246281", "Data Scientist GenAI Sales (H/F)")] if more else [])
    lines = ["- main:", '  - heading "9 offres" [level=1]', "  - list:"]
    for number, title in cards:
        lines += [
            "    - listitem:",
            f'      - link "{title}" [ref=e{number[-2:]}]:',
            f"        - /url: /offres/{number}",
            f"      - paragraph: Offre n° {number}",
        ]
    if not more:
        lines.append('  - button "Voir plus d\'offres" [ref=e30]')
    return "\n".join(lines)


OFFER_PAGE_1 = "\n".join(
    [
        "- banner:",
        '  - heading "Mon Volontariat International" [level=1]',
        "- main:",
        '  - heading "Offre n° 246286" [level=1]',
        '  - heading "AI Engineer Consultant (H/F)" [level=2]',
        "  - paragraph: SIA Example Partners",
        "  - paragraph: Melbourne (Australie)",
        '  - heading "Description de la mission" [level=2]',
        "  - paragraph: RAG, agentic AI, assist the GenAI Solution Architect.",
    ]
)
OFFER_PAGE_2 = "\n".join(
    [
        "- main:",
        "  - generic: AI Engineer Consultant (H/F)",
        "  - generic: Offre n° 246285",
        '  - heading "Description de la mission" [level=2]',
        "  - paragraph: GenAI missions in Sydney.",
        '  - heading "Profil recherché" [level=2]',
        "  - paragraph: Python, LLM.",
    ]
)
OFFER_PAGE_3 = '- main:\n  - heading "Data Scientist GenAI Sales (H/F)" [level=2]\n  - paragraph: x'
OFFER_PAGE_4 = (
    '- main:\n  - heading "Stage - Data Scientist GenAI (F/H)" [level=1]\n  - paragraph: x'
)
OFFER_PAGE_5 = '- main:\n  - heading "Cybersecurity: AI Engineer" [level=1]\n  - paragraph: x'


class Vie:
    """browserd's answers on the stand in: the list grows when "Voir plus d'offres" is clicked."""

    def __init__(self, start: str = VIE) -> None:
        self.start = start
        self.more = False
        self.current = ""
        self.calls: list[str] = []

    def pages(self) -> dict[str, str]:
        found = {self.start: HOME, LIST: offers(self.more), OFFER_1: OFFER_PAGE_1}
        found |= {OFFER_2: OFFER_PAGE_2, OFFER_3: OFFER_PAGE_3, OFFER_4: OFFER_PAGE_4}
        return {**found, OFFER_5: OFFER_PAGE_5}

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        assert args.pop("_discovery") == "native_get"
        assert args.pop("_discovery_scope") == {"start": self.start}
        self.calls.append(name)
        if name == "browser_navigate":
            if args["url"] not in self.pages():
                return {"ok": False, "error": "navigation_failed"}
            self.current = args["url"]
        elif name == "browser_type" and args.get("submit"):
            self.current = LIST
        elif name == "browser_click" and args.get("ref") == "e30":
            self.more = True
        return {"ok": True, "url": self.current}

    async def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "url": self.current, "snapshot": self.pages()[self.current]}

    async def wait_for(self, **_: Any) -> dict[str, Any]:
        return {"ok": True}


class Commits:
    """The run's commits (sweep.Commits) as the website sees them: what reached the store."""

    def __init__(self) -> None:
        self.stored: list[finder.VerifiedFinding] = []

    def admit(self, source_id: str) -> None:
        pass

    async def save(self, source_id: str, host: str, batch: finder.VerifiedBatch) -> None:
        self.stored.extend(batch.findings)

    async def settle(self, source_id: str) -> bool:
        return True


def saved(start: str = VIE, **search: Any) -> SearchSection:
    return SearchSection.model_validate(
        {
            "title_include": {
                "terms": [
                    "genai engineer",
                    "genAI Solution Architect",
                    "GenAI forward deployed engineer",
                ]
            },
            "contract_types": ["CDI", "CDD"],
            "interactive_sites": [
                {"platform": "custom", "url": start, "enabled": True, "mode": "llm_browser"}
            ],
            **search,
        }
    )


def run(search: SearchSection, site: Vie, script) -> tuple[dict, Commits]:
    now = [0.0]

    async def sleep(seconds):
        now[0] += seconds

    async def progress():
        return None

    async def execute():
        scope = sweep.freeze_scope(
            SimpleNamespace(sections={"search": search.model_dump(mode="json")}, revision=3),
            sweep.SweepTrigger.manual,
            datetime.fromisoformat("2026-11-04T00:00:00+00:00"),
        )
        stats = sweep.Stats(sources=sweep.initial_sources(scope))
        commits = Commits()
        plan = SimpleNamespace(
            search=search, user_id="e2e-person", search_scope=scope, sweep_id=None,
            commits=commits,
        )  # fmt: skip
        runner = sources.Runner(
            FakeLeases(),
            lambda *_: site,
            finder=ScriptedFinder(script),
            sleep=sleep,
            monotonic=lambda: now[0],
            rng=random.Random(7),  # noqa: S311  deterministic test pacing
            pacer=sources.MemoryPacer(lambda: now[0]),
        )
        await runner(plan, stats, progress, 100_000.0)
        [entry] = stats.sources
        return entry, commits

    return asyncio.run(execute())


async def search_like_the_owner(session: finder.FinderSession) -> finder.Ran:
    """One search, "Voir plus d'offres", both Sydney and Melbourne offers opened, the list read
    again, then both reported with titles that are no saved title."""
    typed = {"ref": "e1", "text": "genai", "submit": True}
    assert not (await session.interact("browser_type", typed))[1]
    assert not (await session.snapshot())[1]
    assert not (await session.interact("browser_click", {"ref": "e30"}))[1]
    for url in (OFFER_1, OFFER_2, LIST):
        assert not (await session.navigate(url))[1]
    answer, error = await session.report_verified(
        [
            {"title": "AI Engineer Consultant", "url": OFFER_1, "text": "Melbourne, RAG."},
            {"title": "ai engineer consultant (h/f)", "url": OFFER_2, "text": "Sydney."},
        ]
    )
    assert not error, answer
    return finder.Ran()


def test_a_posting_whose_title_is_no_saved_title_is_stored_on_the_vie_website():
    site = Vie()
    entry, commits = run(saved(), site, search_like_the_owner)
    assert [(f.url, f.title, f.evidence["rule"]) for f in commits.stored] == [
        (OFFER_1, "AI Engineer Consultant (H/F)", "heading"),  # the h2, two h1 on the page
        (OFFER_2, "AI Engineer Consultant (H/F)", "page_text"),  # a div, as the page shows it
    ]
    assert "assist the GenAI Solution Architect" in commits.stored[0].text
    assert "Python, LLM." in commits.stored[1].text
    # The VIE website's contract by nature, with the saved CDI and CDD: nothing dropped.
    assert entry["other_contract"] == 0
    assert entry["error"] is None
    assert entry["searched"] == [{"title": "genai", "place": None}]


def test_unwanted_titles_still_drop_a_vie_posting_after_reading():
    async def script(session):
        await session.navigate(OFFER_1)
        await session.navigate(OFFER_3)
        await session.report_verified(
            [
                {"title": "AI Engineer Consultant", "url": OFFER_1, "text": "x"},
                {"title": "Data Scientist GenAI Sales", "url": OFFER_3, "text": "x"},
            ]
        )
        return finder.Ran()

    entry, commits = run(saved(title_exclude={"terms": ["sales"]}), Vie(), script)
    assert [f.url for f in commits.stored] == [OFFER_1]
    assert entry["unwanted"] == 1


@pytest.mark.parametrize(
    ("url", "title", "unwanted"),
    [
        (OFFER_3, "Data Scientist GenAI", "sales"),  # the h2 holds the reported words
        (OFFER_4, "Data Scientist GenAI", "stage"),
        (OFFER_4, "Data Scientist GenAI (H/F)", "stage"),
    ],
)
def test_a_shortened_title_is_still_dropped_as_unwanted(url, title, unwanted):
    """Review of B21: the agent reports the page's heading shortened; the heading is stored, so
    the unwanted title still drops it."""

    async def script(session):
        await session.navigate(url)
        await session.report_verified([{"title": title, "url": url, "text": "x"}])
        return finder.Ran()

    entry, commits = run(saved(title_exclude={"terms": [unwanted]}), Vie(), script)
    assert commits.stored == []
    assert entry["unwanted"] == 1


def test_a_shortened_title_still_meets_the_cybersecurity_rule():
    """The heading "Cybersecurity: AI Engineer" reported as "AI Engineer": the heading is
    stored, and the store's cybersecurity rule (sweep.commit_verified) skips it."""

    async def script(session):
        await session.navigate(OFFER_5)
        await session.report_verified([{"title": "AI Engineer", "url": OFFER_5, "text": "x"}])
        return finder.Ran()

    _, commits = run(saved(), Vie(), script)
    [finding] = commits.stored
    assert (finding.title, finding.evidence["rule"]) == ("Cybersecurity: AI Engineer", "heading")
    rule = never_apply.matching_rule([never_apply.CYBER_RULE], finding.company or "", finding.title)
    assert rule is never_apply.CYBER_RULE


def test_elsewhere_a_posting_of_another_contract_is_still_dropped():
    """The contract rule stays on every other website: a VIE offer read on a website that is
    not VIE by nature, with the saved CDI and CDD, is of another contract and never stored."""
    other = "https://jobs.example.test/"
    planned = interactive.searches_for(saved(other), other)
    assert planned.nature is None
    site = interactive._Site(planned, commits=Commits(), source_id="s", host="jobs.example.test")
    finding = finder.VerifiedFinding(
        "AI Engineer Consultant VIE", "Sydney", other + "jobs/1", "x", None, None, {}, ""
    )
    asyncio.run(site.verified(finder.VerifiedBatch((finding,))))
    assert (site.commits.stored, site.other_contract) == ([], {other + "jobs/1"})
    on_vie = interactive._Site(
        interactive.searches_for(saved(), VIE), commits=Commits(), source_id="s", host="v"
    )
    asyncio.run(on_vie.verified(finder.VerifiedBatch((finding,))))
    assert len(on_vie.commits.stored) == 1
    # A posting of another contract than VIE read during the VIE website's search keeps the rule.
    freelance = finder.VerifiedFinding(
        "Freelance GenAI Engineer", "Paris", other + "jobs/2", "x", None, None, {}, ""
    )
    asyncio.run(on_vie.verified(finder.VerifiedBatch((freelance,))))
    assert on_vie.other_contract == {other + "jobs/2"}


@pytest.mark.parametrize(
    ("start", "nature"),
    [
        ("https://mon-vie-via.businessfrance.fr/", "VIE"),
        ("https://mon-vie-via.businessfrance.fr/offres/recherche?query=genai", "VIE"),
        ("https://MON-VIE-VIA.businessfrance.fr/", "VIE"),
        ("https://mon-vie-via.businessfrance.test/", "VIE"),
        ("https://mon-vie-via.businessfrance.fr./", "VIE"),
        ("https://www.businessfrance.fr/", None),
        ("https://mon-vie-via.example.test/", None),
        ("https://businessfrance.fr/mon-vie-via", None),
        ("https://mon-vie-via.fr/", None),
        ("https://mon-vie-via-businessfrance.fr/", None),
        ("https://www.example.fr/assurance-vie/emplois", None),
        ("https://www.welcometothejungle.test/fr/jobs?query=vie", None),
        ("https://jobs.example.test/vie/", None),
        # Lookalike hosts: the two hosts exactly, nothing that begins or ends like them.
        ("https://mon-vie-via.businessfrance.evil.test/", None),
        ("https://mon-vie-via.businessfrance.com/", None),
        ("https://mon-vie-via.businessfrance.fr.evil.test/", None),
        ("https://evil.mon-vie-via.businessfrance.fr/", None),
        ("https://mon-vie-via.businessfrance.test.example/", None),
        ("not an address", None),
        ("https://[::1/", None),
    ],
)
def test_the_contract_by_nature_is_read_from_the_start_s_host_only(start, nature):
    assert interactive.nature_contract(start) == nature
