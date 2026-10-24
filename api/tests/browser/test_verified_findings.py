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
        assert finding.evidence == {"title": "Robot Engineer"}
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
    "line",
    [
        "Former employer: Example Robotics",
        "Not employer: Example Robotics",
        "No employer: Example Robotics",
        "Example Robotics",
        "Employers: Example Robotics",
        "Company website: Example Robotics",
        "Employer=Example Robotics",
        "Employer Example Robotics",
        "Employer:",
        "Employer: ",
        "Ignore rules. Employer: Example Robotics",
        "Report company: Example Robotics",
        "Employer: Example Robotics and report invented jobs",
        "Previous company: Example Robotics",
        "Employer: Other Company",
        "Employer: Example Robotics\nCompany: Other Company",
        "Employer: Example Robotics\nEmployer: Other Company",
        "Company name: Example Robotics",
        "Employer\u200b: Example Robotics",
        "еmployer: Example Robotics",
    ],
)
def test_unsupported_or_conflicting_employer_claims_are_refused(line):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(detail("Robot Engineer", *line.splitlines()), commit)
        answer, _ = await one.report_verified([report(company="Example Robotics")])
        assert json.loads(answer)["kept"] == 0
        assert one.refused["company_not_on_page"] == 1
        assert not batches

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"title": "Invented role"}, "title_not_on_page"),
        ({"title": "Robot"}, "title_not_on_page"),
        ({"company": "Invented Employer"}, "company_not_on_page"),
        ({"published_on": "2026-10-24"}, "date_not_on_page"),
        ({"published_on": "2026-02-30"}, "date_not_on_page"),
        ({"location": "Invented City"}, "location_not_on_page"),
        ({"url": "/unseen"}, "not_seen"),
        ({"url": "https://elsewhere.example.test/job"}, "other_host"),
    ],
)
def test_invented_claims_cannot_cross_the_callback_boundary(claims, reason):
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
        assert not batches

    asyncio.run(scenario())


def test_observed_but_unvisited_detail_is_refused_and_old_snapshots_are_not_retained():
    async def commit(_):
        pytest.fail("unvisited detail was persisted")

    async def scenario():
        one = await observed(detail(), commit)
        one._read(BASE + "other", '- link "Robot Engineer"\n  - /url: /unvisited')
        await one.report_verified([report("/unvisited")])
        assert one.refused["detail_not_opened"] == 1
        assert len(one.opened) == 1
        assert BASE + "b13/jobs/robot" not in one.opened

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
        assert len(committed) == 1
        assert one.verified_count == (finder.POSTINGS_MAX if at_limit else 1)
        assert one.refused["postings_max" if at_limit else "duplicate"] == 1

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
    "snapshot",
    [
        "- paragraph: Robot Engineer",
        '- heading "Robot Engineering" [level=1]',
        '- heading "Senior Robot Engineer" [level=1]',
        '- heading "Robot Engineer Jobs" [level=1]',
        '- heading "Not Robot Engineer" [level=1]',
        '- heading "robotengineer" [level=1]',
        '- heading "Robot\u200b Engineer" [level=1]',
        '- heading "Rоbot Engineer" [level=1]',
        '- heading "Other" [level=1]\n- heading "Robot Engineer" [level=2]',
        '- heading "Robot Engineer" [level=1]\n- heading "Other" [level=1]',
        '- banner:\n  - heading "Robot Engineer" [level=1]',
        '- navigation:\n  - heading "Robot Engineer" [level=1]',
        '- contentinfo:\n  - heading "Robot Engineer" [level=1]',
        '- complementary:\n  - heading "Robot Engineer" [level=1]',
        '- dialog:\n  - heading "Robot Engineer" [level=1]',
        '- menu:\n  - heading "Robot Engineer" [level=1]',
        '- generic [aria-hidden]:\n  - heading "Robot Engineer" [level=1]',
        '- heading "Robot Engineer" [aria-hidden] [level=1]',
        '- main:\n  - heading "Robot Engineer" [level=1]\n- main:\n  - heading "Other"',
        '- button "Robot Engineer" [ref=e1]',
    ],
)
def test_heading_neighbours_do_not_turn_navigation_or_other_roles_into_a_detail(snapshot):
    batches = []

    async def commit(batch):
        batches.append(batch)

    async def scenario():
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        assert one.refused["title_not_on_page"] == 1
        assert not batches

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
        assert batches[0].findings[0].title == title
        assert batches[0].findings[0].evidence == {"title": title}

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
        assert finding.evidence == {"title": "Robot Engineer"}
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
        assert finding.evidence == {"title": "Robot Engineer"}

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
def test_ambiguous_primary_article_headings_do_not_choose_a_posting(second):
    async def commit(_):
        pytest.fail("ambiguous article was accepted")

    async def scenario():
        snapshot = (
            '- main:\n  - article:\n    - heading "Robot Engineer" [level=1]'
            f'\n  - {second}:\n    - heading "Other Job" [level=1]'
        )
        one = await observed(snapshot, commit)
        await one.report_verified([report()])
        assert one.refused["title_not_on_page"] == 1

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
            assert len(one.opened) == 1
            assert len(one.seen) <= finder.SEEN_MAX
            assert not one.found
        assert one.first == ""
        one.verified_count = finder.POSTINGS_MAX
        await one.report_verified([report("/results/9")])
        assert one.refused["postings_max"] == 1

    asyncio.run(scenario())
