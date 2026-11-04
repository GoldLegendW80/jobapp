"""Only code-read detail evidence can reach the incremental finding callback."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.browser import finder, finder_tools
from tests.browser.test_b13_runtime import BASE, InteractiveTools, session


def detail(title="Robot Engineer", *lines):
    # browserd/pages.py uses Playwright 1.55 snapshotForAI, as in the B13 native fixture.
    return f'- main [ref=e1]:\n  - heading "{title}" [level=1] [ref=e2]\n' + "\n".join(
        f"  - paragraph [ref=e{index + 3}]: {line}" for index, line in enumerate(lines)
    )


def report(url="/b13/jobs/robot", **claims):
    return {"title": "Robot Engineer", "url": url, "text": "Invented model text", **claims}


async def observed(snapshot, commit):
    tools = InteractiveTools()
    tools.pages[BASE + "b13/jobs/robot"] = snapshot
    one = session(tools, "e2e-person")
    one.on_verified = commit
    one.clock = lambda: datetime(2026, 10, 24, 9, tzinfo=UTC)
    one.seen.add(BASE + "b13/jobs/robot")
    assert not (await one.navigate("/b13/jobs/robot"))[1]
    return one


def test_unknown_fields_and_model_text_do_not_invent_employer_date_or_evidence():
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(detail("Robot Engineer", "Build robots with Python"), commit)
        answer, error = await one.report_verified([report()])
        assert not error
        assert json.loads(answer)["kept"] == 1
        [batch] = batches
        [finding] = batch.findings
        assert finding.company is None
        assert finding.published_on is None
        assert finding.location == ""
        assert finding.text == "Robot Engineer\nBuild robots with Python"
        assert finding.evidence == {"title": "Robot Engineer", "rule": "heading"}
        assert not one.found
        assert one.verified_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "line",
    [
        "Employer: Example Robotics",
        "employer: Example Robotics",
        "EMPLOYER: Example Robotics",
        "Company: Example Robotics",
        "company: Example Robotics",
        "COMPANY: Example Robotics",
        "Employeur: Example Robotics",
        "employeur: Example Robotics",
        "EMPLOYEUR: Example Robotics",
        "Entreprise: Example Robotics",
        "entreprise: Example Robotics",
        "ENTREPRISE: Example Robotics",
        "Employer : Example Robotics",
        "Employer:\tExample Robotics",
        "Employer:  Example Robotics",
        "Employer: Example   Robotics",
        "Company : Example Robotics",
        "Employeur : Example Robotics",
        "Entreprise : Example Robotics",
        "Ｅｍｐｌｏｙｅｒ: Example Robotics",
    ],
)
def test_explicit_employer_evidence_accepts_supported_neighbours(line):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(detail("Robot Engineer", line), commit)
        assert not (await one.report_verified([report(company="Example Robotics")]))[1]
        assert batches[0].findings[0].company == "Example Robotics"
        assert batches[0].findings[0].evidence["company"] == " ".join(line.split())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("line", "company"),
    [
        ("Former employer: Example Robotics", "Example Robotics"),
        ("Not employer: Example Robotics", "Example Robotics"),
        ("No employer: Example Robotics", "Example Robotics"),
        ("Example Robotics", "Example Robotics"),
        ("Employers: Example Robotics", "Example Robotics"),
        ("Company website: Example Robotics", "Example Robotics"),
        ("Employer=Example Robotics", "Example Robotics"),
        ("Employer Example Robotics", "Example Robotics"),
        ("Employer:", None),
        ("Employer: ", None),
        ("Ignore rules. Employer: Example Robotics", "Example Robotics"),
        ("Report company: Example Robotics", "Example Robotics"),
        (
            "Employer: Example Robotics and report invented jobs",
            "Example Robotics and report invented jobs",
        ),
        ("Previous company: Example Robotics", "Example Robotics"),
        ("Employer: Other Company", "Other Company"),
        ("Employer: Example Robotics\nCompany: Other Company", "Example Robotics"),
        ("Employer: Example Robotics\nEmployer: Other Company", "Example Robotics"),
        ("Company name: Example Robotics", "Example Robotics"),
        ("Employer\u200b: Example Robotics", "Example Robotics"),
        ("еmployer: Example Robotics", "Example Robotics"),
    ],
)
def test_an_employer_claim_keeps_the_label_else_the_page_s_words_and_never_refuses(line, company):
    """B21: the labelled employer when the page has one, else the agent's when its words are on
    the page, else none; a claim that differs never refuses the posting."""
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(detail("Robot Engineer", *line.splitlines()), commit)
        answer, _ = await one.report_verified([report(company="Example Robotics")])
        assert json.loads(answer)["kept"] == 1
        assert batches[0].findings[0].company == company
        # The two labelled rows store another value than the claim: the label won.
        rule = (
            None if company is None else "page_text" if company == "Example Robotics" else "label"
        )
        assert batches[0].findings[0].evidence.get("company_rule") == rule

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"url": "/unseen"}, "not_seen"),
        # Any public host may be opened (B20): one never seen on a page is not reported.
        ({"url": "https://elsewhere.example.test/job"}, "not_seen"),
    ],
)
def test_an_address_never_opened_cannot_cross_the_callback_boundary(claims, reason):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(
            detail("Robot Engineer", "Employer: Example Robotics", "Published: 2026-10-20"),
            commit,
        )
        answer, _ = await one.report_verified([report(**claims)])
        assert json.loads(answer)["kept"] == 0
        assert one.refused[reason] == 1
        assert json.loads(answer)["refused"][0]["do"] == finder.OPEN_THEN_REPORT
        assert not batches

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("claims", "stored"),
    [
        # B21: an invented title or field is never stored: the page's own words are.
        ({"title": "Invented role"}, ("Robot Engineer", "Example Robotics", "2026-10-20", "")),
        ({"title": "Robot"}, ("Robot", "Example Robotics", "2026-10-20", "")),
        (
            {"company": "Invented Employer"},
            ("Robot Engineer", "Example Robotics", "2026-10-20", ""),
        ),
        ({"published_on": "2026-10-24"}, ("Robot Engineer", "Example Robotics", "2026-10-20", "")),
        ({"published_on": "2026-02-30"}, ("Robot Engineer", "Example Robotics", "2026-10-20", "")),
        ({"location": "Invented City"}, ("Robot Engineer", "Example Robotics", "2026-10-20", "")),
    ],
)
def test_invented_claims_store_what_the_page_shows(claims, stored):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(
            detail("Robot Engineer", "Employer: Example Robotics", "Published: 2026-10-20"),
            commit,
        )
        answer, _ = await one.report_verified([report(**claims)])
        assert json.loads(answer)["kept"] == 1
        [finding] = batches[0].findings
        assert (finding.title, finding.company, finding.published_on, finding.location) == stored

    asyncio.run(scenario())


def test_observed_but_unvisited_detail_is_refused_and_earlier_pages_are_kept():
    async def commit(_):
        pytest.fail("unvisited detail was persisted")

    async def scenario():
        one = await observed(detail(), commit)
        one._read(BASE + "other", '- link "Robot Engineer"\n  - /url: /unvisited')
        await one.report_verified([report("/unvisited")])
        assert one.refused["detail_not_opened"] == 1
        # B21: every page opened is kept for the reports, not only the last one read.
        assert list(one.pages_read) == [BASE, BASE + "b13/jobs/robot", BASE + "other"]

    asyncio.run(scenario())


def test_callback_failure_keeps_prior_commit_and_does_not_claim_later_findings():
    committed = []

    async def commit(batch):
        if committed:
            raise RuntimeError("e2e-private-store-error")
        committed.extend(batch.findings)

    async def scenario():
        one = await observed(detail(), commit)
        assert not (await one.report_verified([report()]))[1]
        one._read(BASE + "second", detail())
        answer, error = await one.report_verified([report("/second")])
        assert error
        assert "e2e-private-store-error" not in answer
        assert one.stopped == "store_failed"
        assert one.verified_count == len(committed) == 1
        assert not one.found

    asyncio.run(scenario())


@pytest.mark.parametrize("at_limit", [False, True])
def test_parallel_registered_reports_reserve_budget_and_duplicates_once(monkeypatch, at_limit):
    async def scenario():
        entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        committed = []

        async def commit(batch):
            entered.set()
            await release.wait()
            committed.extend(batch.findings)

        one = await observed(detail(), commit)
        one.verified_count = finder.POSTINGS_MAX - 1 if at_limit else 0
        monkeypatch.setattr(finder, "session_of", lambda _: one)
        handler = finder_tools._factory("report_postings")(finder_tools.app_tools.Scope(None, None))
        first = asyncio.create_task(handler.handler({"postings": [report()]}))
        await entered.wait()
        url = "/b13/jobs/robot"
        if at_limit:
            url = "/second"
            one._read(BASE + "second", detail())

        async def second():
            second_started.set()
            return await handler.handler({"postings": [report(url)]})

        pending = asyncio.create_task(second())
        await second_started.wait()
        release.set()
        await asyncio.gather(first, pending)
        # A job website's verified postings have no cap (jobs J18): past POSTINGS_MAX, a second
        # address is kept too; the same address twice is still one.
        assert len(committed) == (2 if at_limit else 1)
        assert one.verified_count == (finder.POSTINGS_MAX + 1 if at_limit else 1)
        assert one.refused["duplicate"] == (0 if at_limit else 1)
        assert not one.refused["postings_max"]

    asyncio.run(scenario())


def test_registered_model_schema_exposes_claims_without_callback_authority():
    properties = finder_tools.POSTING["properties"]
    assert {"company", "published_on"} <= properties.keys()
    assert {"on_verified", "source_id", "evidence", "user_id"}.isdisjoint(properties)
    assert finder.INTERACTIVE_FINDER.private_io


@pytest.mark.parametrize(
    "line",
    [
        "Published: 2026-10-20",
        "published: 2026-10-20",
        "PUBLISHED: 2026-10-20",
        "Date posted: 2026-10-20",
        "date posted: 2026-10-20",
        "DATE POSTED: 2026-10-20",
        "Publié: 2026-10-20",
        "publié: 2026-10-20",
        "PUBLIÉ: 2026-10-20",
        "Published : 2026-10-20",
        "Published:  2026-10-20",
        "Published:\t2026-10-20",
        "Date   posted: 2026-10-20",
        "Ｐｕｂｌｉｓｈｅｄ: 2026-10-20",
        "Published: 2024-02-29",
        "Published: 2026-01-01",
        "Published: 2026-12-31",
        "Published: 2026-02-28",
        "Published: 2026-04-30",
        "Published: 2026-05-31",
    ],
)
def test_explicit_iso_publication_evidence_preserves_the_observed_date(line):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(detail("Robot Engineer", line), commit)
        assert not (await one.report_verified([report()]))[1]
        assert batches[0].findings[0].published_on == line.split(":")[1].strip()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "line",
    [
        "Updated: 2026-10-20",
        "Not published: 2026-10-20",
        "Previously published: 2026-10-20",
        "Published 2026-10-20",
        "Published=2026-10-20",
        "Publication: 2026-10-20",
        "Published: today",
        "Published: yesterday",
        "Published: 20/10/2026",
        "Published: 20261020",
        "Published: 2026-10-20T12:00:00Z",
        "Published: 2026-1-1",
        "Published: 2026-02-30",
        "Published: 2025-02-29",
        "Published: 2026-00-20",
        "Published: 2026-13-20",
        "Published: 2026-10-00",
        "Published: 0000-10-20",
        "Published: 2026-10-20 extra",
        "Published: 2026-10-20\nPublished: 2026-10-21",
    ],
)
def test_unsupported_or_ambiguous_dates_stay_unknown(line):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(detail("Robot Engineer", *line.splitlines()), commit)
        assert not (await one.report_verified([report()]))[1]
        assert batches[0].findings[0].published_on is None
        assert "published_on" not in batches[0].findings[0].evidence

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("snapshot", "stored"),
    [
        # B21: the title as a person sees it on the page (its words in the visible text, or a
        # heading it equals), else the page's one clear primary heading, else refused.
        ("- paragraph: Robot Engineer", ("Robot Engineer", "page_text")),
        ('- heading "Robot Engineering" [level=1]', ("Robot Engineering", "primary_heading")),
        ('- heading "Senior Robot Engineer" [level=1]', ("Robot Engineer", "page_text")),
        ('- heading "Robot Engineer Jobs" [level=1]', ("Robot Engineer", "page_text")),
        ('- heading "Not Robot Engineer" [level=1]', ("Robot Engineer", "page_text")),
        ('- heading "robotengineer" [level=1]', ("robotengineer", "primary_heading")),
        ('- heading "Robot\u200b Engineer" [level=1]', ("Robot\u200b Engineer", "heading")),
        ('- heading "Rоbot Engineer" [level=1]', ("Rоbot Engineer", "primary_heading")),
        (
            '- heading "Other" [level=1]\n- heading "Robot Engineer" [level=2]',
            ("Robot Engineer", "heading"),
        ),
        (
            '- heading "Robot Engineer" [level=1]\n- heading "Other" [level=1]',
            ("Robot Engineer", "heading"),
        ),
        ('- banner:\n  - heading "Robot Engineer" [level=1]', ("Robot Engineer", "heading")),
        ('- navigation:\n  - heading "Robot Engineer" [level=1]', ("Robot Engineer", "heading")),
        ('- contentinfo:\n  - heading "Robot Engineer" [level=1]', ("Robot Engineer", "heading")),
        (
            '- complementary:\n  - heading "Robot Engineer" [level=1]',
            ("Robot Engineer", "heading"),
        ),
        ('- dialog:\n  - heading "Robot Engineer" [level=1]', ("Robot Engineer", "heading")),
        ('- menu:\n  - heading "Robot Engineer" [level=1]', ("Robot Engineer", "heading")),
        ('- generic [aria-hidden]:\n  - heading "Robot Engineer" [level=1]', None),
        ('- heading "Robot Engineer" [aria-hidden] [level=1]', None),
        (
            '- main:\n  - heading "Robot Engineer" [level=1]\n- main:\n  - heading "Other"',
            ("Robot Engineer", "heading"),
        ),
        ('- button "Robot Engineer" [ref=e1]', None),
        ('- link "Robot Engineer" [ref=e1]:\n  - /url: /b13/jobs/other', None),
        ('- link "Other" [ref=e1]:\n  - heading "Robot Engineer" [level=2]', None),
    ],
)
def test_heading_neighbours_give_the_title_a_person_sees_or_none(snapshot, stored):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        if stored is None:
            assert one.refused["title_not_on_page"] == 1
            assert not batches
        else:
            [finding] = batches[0].findings
            assert (finding.title, finding.evidence["rule"]) == stored

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "title",
    [
        "Robot Engineer",
        "Senior Robot Engineer",
        "Ingénieur robotique",
        "Développeur Python",
        "AI Researcher",
        "ML Engineer",
        "Data Scientist",
        "C++ Engineer",
        "C# Developer",
        "Engineer (Robotics)",
        "Robotics, R&D",
        "Robot Engineer H/F",
        "Full Stack Engineer",
        "مهندس روبوتات",
        "機械学習エンジニア",
        "Entwickler Robotik",
        "Ingegnere robotico",
        "Ingeniero de robótica",
        "Ｒｏｂｏｔ Engineer",
        "Robot\tEngineer",
    ],
)
def test_supported_heading_corpus_keeps_the_actual_observed_title(title):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(detail(title), commit)
        assert not (await one.report_verified([report(title=title)]))[1]
        shown = " ".join(title.split())  # B21: the heading as a person reads it
        assert batches[0].findings[0].title == shown
        assert batches[0].findings[0].evidence == {"title": shown, "rule": "heading"}

    asyncio.run(scenario())


def test_header_footer_account_text_and_html_tails_are_not_employer_evidence():
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        snapshot = (
            "- banner:\n  - paragraph: Employer: Header Company\n"
            + detail("Robot Engineer", "Employer: Example Robotics</p>")
            + "\n- contentinfo:\n  - paragraph: Employer: Footer Company\n"
            "- menu:\n  - paragraph: Employer: e2e-account-canary-a71e"
        )
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        [finding] = batches[0].findings
        assert finding.company is None
        assert finding.evidence == {"title": "Robot Engineer", "rule": "heading"}
        assert "e2e-account-canary-a71e" not in finding.text

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["article", "list", "listitem", "complementary"])
def test_other_job_containers_do_not_supply_the_current_employer(role):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        snapshot = detail() + f"\n  - {role}:\n    - paragraph: Employer: Other Job Company"
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        assert batches[0].findings[0].company is None

    asyncio.run(scenario())


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize(
    "duty",
    [
        "Build robot arms",
        "Maintain Python services",
        "Design C++ control loops",
        "Test C# integration",
        "Work with R&D",
        "Build safety systems",
        "Company: develop robotics tools",
        "Employer: improve internal tooling",
        "Location: calibrate the robot onsite",
        "Published: maintain the release notes",
        "Review sensor data (daily)",
        "Build robots, test controls",
        "Support hardware/software teams",
        "Développer les systèmes robotiques",
        "Concevoir des bras articulés",
        "Roboter entwickeln",
        "Diseñar robots",
        "ロボットを開発する",
        "تطوير الروبوتات",
        "Write\tPython  tests",
    ],
)
def test_primary_detail_keeps_legitimate_duty_lists_without_borrowing_other_jobs(article, duty):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        snapshot = detail("Robot Engineer", "Employer: Example Robotics")
        if article:
            snapshot = "- main:\n  - article:\n" + "\n".join(
                "  " + line for line in snapshot.splitlines()[1:]
            )
        indent = "    " if article else "  "
        snapshot += (
            f"\n{indent}- list:\n{indent}  - listitem: {duty}"
            f'\n{indent}    - link "Python documentation"\n{indent}      - /url: https://docs.example.test/python'
            f"\n{indent}    - list:\n{indent}      - listitem: Test nested robot duties"
            f'\n{indent}- article:\n{indent}  - heading "Other Job" [level=2]'
            f"\n{indent}  - paragraph: Employer: e2e-unrelated-job-bc19"
            '\n- navigation:\n  - link "e2e-account-canary-bc19"'
            "\n- contentinfo:\n  - paragraph: e2e-footer-canary-bc19"
        )
        one = await observed(snapshot, commit)
        answer, _ = await one.report_verified([report(company="Example Robotics")])
        assert json.loads(answer)["kept"] == 1
        [finding] = batches[0].findings
        assert " ".join(duty.split()) in finding.text
        assert "Test nested robot duties" in finding.text
        assert finding.company == "Example Robotics"
        assert finding.evidence["company"] == "Employer: Example Robotics"
        assert all(marker not in finding.text for marker in ("bc19", "Other Job"))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "foreign",
    [
        '- article:\n  - heading "Other Job" [level=2]',
        '- article:\n  - heading "Other Job" [level=3]',
        "- article:\n  - paragraph: Employer: Other Company",
        '- article:\n  - article:\n    - heading "Other Job" [level=2]',
        '- list:\n  - heading "Other Job" [level=2]',
        '- list:\n  - listitem:\n    - heading "Other Job" [level=2]',
        '- list:\n  - link "Other Job"\n    - /url: /jobs/other',
        '- list:\n  - listitem:\n    - link "Other Job"\n      - /url: /jobs/other',
        '- listitem:\n  - heading "Other Job" [level=2]',
        '- listitem:\n  - link "Other Job"\n    - /url: /jobs/other',
        (
            '- list:\n  - listitem:\n    - link "Other Job"\n      - /url: /jobs/other'
            "\n    - paragraph: Employer: Other Company"
        ),
        (
            '- listitem:\n  - link "Other Job"\n    - /url: /jobs/other'
            "\n  - paragraph: Company: Other Company"
        ),
        "- navigation:",
        "- banner:",
        "- contentinfo:",
        "- complementary:",
        "- dialog:",
        "- menu:",
        "- generic [aria-hidden]:",
        "- article [aria-hidden]:",
        "- list [aria-hidden]:",
        "- listitem [aria-hidden]:",
    ],
)
def test_primary_article_excludes_related_job_and_account_container_neighbours(foreign):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        snapshot = '- main:\n  - article:\n    - heading "Robot Engineer" [level=1]'
        snapshot += "\n" + "\n".join("    " + line for line in foreign.splitlines())
        depth = len(foreign.splitlines()[-1]) - len(foreign.splitlines()[-1].lstrip())
        snapshot += "\n" + " " * (depth + 6) + "- paragraph: Employer: e2e-decoy-bc19"
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        [finding] = batches[0].findings
        assert finding.text == "Robot Engineer"
        assert finding.company is None
        assert finding.evidence == {"title": "Robot Engineer", "rule": "heading"}

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["text", "paragraph", "generic", "nested"])
@pytest.mark.parametrize("article", [False, True])
def test_narrative_duties_with_documentation_links_are_not_related_job_cards(role, article):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        indent = "    " if article else "  "
        snapshot = "- main:\n" + ("  - article:\n" if article else "")
        narrative = (
            f"{indent}    - generic:\n{indent}      - text: Build robots using the documented tools"
            if role == "nested"
            else f"{indent}    - {role}: Build robots using the documented tools"
        )
        snapshot += (
            f'{indent}- heading "Robot Engineer" [level=1]'
            f"\n{indent}- list:\n{indent}  - listitem:"
            f"\n{narrative}"
            f'\n{indent}    - link "Python documentation"'
            f"\n{indent}      - /url: https://docs.example.test/python"
        )
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        assert "Build robots using the documented tools" in batches[0].findings[0].text

    asyncio.run(scenario())


@pytest.mark.parametrize("second", ["article", "generic"])
def test_two_primary_headings_accept_the_title_found_on_the_page(second):
    """B21: two h1 choose no primary heading, but the reported title is one of the page's
    headings; the text is the page's visible lines, as no single detail was read."""
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        snapshot = (
            '- main:\n  - article:\n    - heading "Robot Engineer" [level=1]'
            f'\n  - {second}:\n    - heading "Other Job" [level=1]'
        )
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        [finding] = batches[0].findings
        assert (finding.title, finding.evidence["rule"]) == ("Robot Engineer", "heading")
        assert finding.text == "Robot Engineer\nOther Job"
        await one.report_verified([report(title="Invented role", url="/b13/jobs/robot?x=1")])
        assert one.refused["detail_not_opened"] + one.refused["not_seen"] == 1

    asyncio.run(scenario())


def test_current_observation_and_known_url_memory_have_explicit_bounds():
    async def commit(_):
        pass

    async def scenario():
        one = await observed(detail(), commit)
        for page in range(10):
            snapshot = (
                detail()
                + "\n"
                + "\n".join(
                    f'- link "Role"\n  - /url: /jobs/{page}/{index}' for index in range(500)
                )
            )
            one._read(BASE + f"results/{page}", snapshot)
            assert len(one.pages_read) == page + 3  # B21: every page read, the start included
            assert len(one.seen) <= finder.SEEN_MAX
            assert not one.found
        assert one.first == ""
        one.verified_count = finder.POSTINGS_MAX  # no cap of a job website's (jobs J18)
        await one.report_verified([report("/results/9")])
        assert (one.verified_count, one.refused["postings_max"]) == (finder.POSTINGS_MAX + 1, 0)
        # One report call still takes a bounded list.
        answer, error = await one.report_verified([report()] * (finder.POSTINGS_MAX + 1))
        assert (answer, error) == ("postings must be a bounded list.", True)

    asyncio.run(scenario())


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize(
    ("container", "nested"),
    [
        ("generic", False),
        ("generic", True),
        ('region "Examples"', False),
        ('region "Examples"', True),
        ('group "Examples"', False),
        ('group "Examples"', True),
        (None, False),
    ],
)
@pytest.mark.parametrize("claimed", [False, True])
@pytest.mark.parametrize(
    ("label", "key", "value", "unknown", "reason"),
    [
        ("Employer", "company", "Example Other Systems", None, "company_not_on_page"),
        ("Published", "published_on", "2026-10-19", None, "date_not_on_page"),
        ("Location", "location", "Example Other City", "", "location_not_on_page"),
    ],
)
def test_secondary_section_metadata_stays_with_its_own_heading(
    article, container, nested, claimed, label, key, value, unknown, reason
):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        indent = "    " if article else "  "
        snapshot = "- main:\n" + ("  - article:\n" if article else "")
        snapshot += (
            f'{indent}- heading "Robot Engineer" [level=1]'
            f"\n{indent}- paragraph: Build the primary robot service"
        )
        section_indent = indent
        if container:
            snapshot += f"\n{section_indent}- {container}:"
            section_indent += "  "
        if nested:
            snapshot += f"\n{section_indent}- generic:"
            section_indent += "  "
        snapshot += (
            f'\n{section_indent}- heading "Boundary Ω" [level=2]'
            f"\n{section_indent}- paragraph: {label}: {value}"
        )
        one = await observed(snapshot, commit)
        answer, error = await one.report_verified([report(**({key: value} if claimed else {}))])
        assert not error
        if claimed:  # B21: the agent's value, its words being on the page, never a refusal
            assert json.loads(answer)["kept"] == 1
            [finding] = batches[0].findings
            assert getattr(finding, key) == value
            assert finding.evidence[f"{key}_rule"] == "page_text"
            assert not one.refused[reason]
        else:
            assert json.loads(answer)["kept"] == 1
            [finding] = batches[0].findings
            assert getattr(finding, key) == unknown
            assert key not in finding.evidence
            assert "Build the primary robot service" in finding.text

    asyncio.run(scenario())


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    ("label", "key", "value"),
    [
        ("Employer", "company", "Example Robotics"),
        ("Published", "published_on", "2026-10-20"),
        ("Location", "location", "Example City"),
    ],
)
def test_secondary_section_metadata_keeps_primary_fields_and_h2_prose(
    article, wrapped, label, key, value
):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        indent = "    " if article else "  "
        snapshot = "- main:\n" + ("  - article:\n" if article else "")
        snapshot += f'{indent}- heading "Robot Engineer" [level=1]'
        field_indent = indent
        if wrapped:
            snapshot += f"\n{indent}- generic:"
            field_indent += "  "
        snapshot += (
            f"\n{field_indent}- paragraph: {label}: {value}"
            f'\n{indent}- heading "Construire les outils" [level=2]'
            f"\n{indent}- paragraph: Maintain the primary robot service"
        )
        one = await observed(snapshot, commit)
        answer, error = await one.report_verified([report(**{key: value})])
        assert not error
        assert json.loads(answer)["kept"] == 1
        [finding] = batches[0].findings
        assert getattr(finding, key) == value
        assert finding.evidence[key] == f"{label}: {value}"
        assert "Construire les outils" in finding.text
        assert "Maintain the primary robot service" in finding.text

    asyncio.run(scenario())


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize("nested", [False, True, 'region "Owned"', 'group "Owned"'])
@pytest.mark.parametrize("layout", ["article", "headed_list", "flat_heading"])
@pytest.mark.parametrize(
    "duty",
    [
        "Maintain the automated test service",
        "Concevoir des outils fiables",
        "Automatisierung verbessern",
        "自動化を設計する",
        "تطوير أدوات موثوقة",
    ],
)
def test_narrative_scope_regression_keeps_owned_duties(article, nested, layout, duty):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        indent = "    " if article else "  "
        snapshot = "- main:\n" + ("  - article:\n" if article else "")
        snapshot += (
            f'{indent}- heading "Robot Engineer" [level=1]'
            f"\n{indent}- paragraph: Employer: Example Robotics"
        )
        prose_indent = indent
        if layout == "article":
            snapshot += f"\n{indent}- article:"
            prose_indent += "  "
        elif layout == "headed_list":
            snapshot += (
                f"\n{indent}- list:\n{indent}  - listitem:"
                f'\n{indent}    - heading "Fonctions Ω" [level=2]'
            )
            prose_indent += "    "
        else:
            snapshot += f'\n{indent}- heading "Fonctions Ω" [level=2]'
        if nested:
            wrapper = nested if isinstance(nested, str) else "generic"
            snapshot += f"\n{prose_indent}- {wrapper}:"
            prose_indent += "  "
        snapshot += (
            f"\n{prose_indent}- paragraph:\n{prose_indent}  - text: {duty}"
            f'\n{prose_indent}  - link "Reference guide"'
            f"\n{prose_indent}    - /url: https://docs.example.test/guide"
            f"\n{prose_indent}  - text: Follow the reference while maintaining the tools"
        )
        one = await observed(snapshot, commit)
        answer, error = await one.report_verified([report(company="Example Robotics")])
        assert not error
        assert json.loads(answer)["kept"] == 1
        [finding] = batches[0].findings
        expected = ["Robot Engineer", "Employer: Example Robotics"]
        if layout != "article":
            expected.append("Fonctions Ω")
        if isinstance(nested, str):
            expected.append("Owned")
        expected.extend([duty, "Follow the reference while maintaining the tools"])
        assert finding.text == "\n".join(expected)
        assert "Invented model text" not in finding.text
        assert finding.company == "Example Robotics"
        assert finding.published_on is None
        assert finding.evidence["company"] == "Employer: Example Robotics"

    asyncio.run(scenario())


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize("nested", [False, True, "inside"])
@pytest.mark.parametrize(
    ("container", "linked"),
    [
        ("article", False),
        ("generic", False),
        ('region "Examples"', False),
        ('group "Examples"', False),
        ("list", False),
        ("listitem", False),
        ("list", True),
        ("listitem", True),
        ("navigation", False),
        ("banner", False),
        ("contentinfo", False),
        ("complementary", False),
        ("dialog", False),
        ("menu", False),
    ],
)
def test_narrative_scope_regression_excludes_foreign_sections(article, nested, container, linked):
    import uuid

    marker = "e2e-" + uuid.uuid4().hex
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        indent = "    " if article else "  "
        snapshot = "- main:\n" + ("  - article:\n" if article else "")
        snapshot += (
            f'{indent}- heading "Robot Engineer" [level=1]'
            f"\n{indent}- paragraph: Employer: Example Robotics"
            f"\n{indent}- paragraph: Maintain the primary robot service"
        )
        foreign_indent = indent
        if nested is True:
            snapshot += f"\n{foreign_indent}- generic:"
            foreign_indent += "  "
        snapshot += f"\n{foreign_indent}- {container}:"
        foreign_indent += "  "
        if nested == "inside":
            snapshot += f"\n{foreign_indent}- generic:"
            foreign_indent += "  "
        if container == "list":
            snapshot += f"\n{foreign_indent}- listitem:"
            foreign_indent += "  "
        if linked:
            snapshot += (
                f'\n{foreign_indent}- link "Boundary Ω"\n{foreign_indent}  - /url: /jobs/another'
            )
        else:
            snapshot += f'\n{foreign_indent}- heading "Boundary Ω" [level=2]'
        snapshot += f"\n{foreign_indent}- paragraph: Employer: {marker}"
        if not linked:
            snapshot += f"\n{foreign_indent}- paragraph: Maintain the foreign service {marker}"
        one = await observed(snapshot, commit)
        answer, error = await one.report_verified([report(company="Example Robotics")])
        assert not error
        assert json.loads(answer)["kept"] == 1
        [finding] = batches[0].findings
        assert finding.text == (
            "Robot Engineer\nEmployer: Example Robotics\nMaintain the primary robot service"
        )
        assert marker not in finding.text
        assert "Boundary Ω" not in finding.text
        assert finding.company == "Example Robotics"
        assert finding.evidence["company"] == "Employer: Example Robotics"
        assert finding.published_on is None

    asyncio.run(scenario())


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize("container", ["article", "generic", "list"])
def test_narrative_scope_regression_refuses_when_only_excluded_prose_was_observed(
    article, container
):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        indent = "    " if article else "  "
        snapshot = "- main:\n" + ("  - article:\n" if article else "")
        snapshot += (
            f'{indent}- heading "Robot Engineer" [level=1]'
            f"\n{indent}- paragraph: Employer: Example Robotics"
            f"\n{indent}- {container}:"
        )
        prose_indent = indent + "  "
        if container == "list":
            snapshot += f"\n{prose_indent}- listitem:"
            prose_indent += "  "
        snapshot += (
            f'\n{prose_indent}- heading "Fonctions Ω" [level=2]'
            f"\n{prose_indent}- paragraph: Employer: Example Other Systems"
            f"\n{prose_indent}- paragraph: Maintain the observed automation service"
        )
        one = await observed(snapshot, commit)
        answer, error = await one.report_verified([report()])
        assert not error
        # B21: no single detail was read, so the posting keeps the page's visible lines and no
        # labelled field; its title is a heading of the page.
        assert json.loads(answer)["kept"] == 1
        [finding] = batches[0].findings
        assert "Maintain the observed automation service" in finding.text
        assert finding.company is None

    asyncio.run(scenario())


@pytest.mark.parametrize("article", [False, True])
@pytest.mark.parametrize("primary_duties", [False, True])
def test_narrative_scope_regression_binds_flat_sections_to_their_heading(article, primary_duties):
    import uuid

    marker = "e2e-" + uuid.uuid4().hex
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        indent = "    " if article else "  "
        snapshot = "- main:\n" + ("  - article:\n" if article else "")
        snapshot += (
            f'{indent}- heading "Robot Engineer" [level=1]'
            f"\n{indent}- paragraph: Employer: Example Robotics"
        )
        expected = "Robot Engineer\nEmployer: Example Robotics"
        if primary_duties:
            snapshot += f"\n{indent}- paragraph: Maintain the primary robot service"
            expected += "\nMaintain the primary robot service"
        snapshot += (
            f'\n{indent}- heading "Boundary Ω" [level=2]'
            f"\n{indent}- paragraph: Employer: {marker}"
            f"\n{indent}- paragraph: Maintain the foreign service {marker}"
        )
        one = await observed(snapshot, commit)
        answer, error = await one.report_verified([report()])
        assert not error
        assert json.loads(answer)["kept"] == 1  # B21: never refused for its layout
        [finding] = batches[0].findings
        if primary_duties:
            assert finding.text == expected
            assert marker not in finding.text
            assert finding.company == "Example Robotics"
        else:  # no single detail read: the page's visible lines, no labelled field
            assert finding.text.startswith("Robot Engineer\nEmployer: Example Robotics")
            assert finding.company is None

    asyncio.run(scenario())
