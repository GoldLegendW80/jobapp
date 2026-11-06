"""D29: the hiring panel on a real Postgres at head, with fake model calls (`panel.Steps`): no
model, no network, no clock (every step gets AT).

The seven reviews, the merge, the rebuild as a new version of origin `panel` (the first draft
kept in the history) and the screen again; a reviewer that fails is recorded missing and the
panel goes on; a merge that fails keeps the first version and says so; the readers' prompts
hold only the document, the brief and their own role (the recruiter the top third only); the
merged list keeps the merge's order with duplicates merged; `[ADD: ...]` never prints; the apply
module waits for a running panel; the brief holds every field; the estimate includes the panel;
the catalog lists the eight agents under Documents; the record and the logs."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.models import AgentRun, RunStatus
from app.documents import (
    brief,
    corrections,
    estimate,
    freeze,
    generate,
    panel,
    panel_hold,
    store,
    writer_base,
    writer_cv,
    writer_letter,
)
from app.documents import content as c
from app.documents.models import (
    DocumentGeneration,
    DocumentPanel,
    DocumentVersion,
    GenerationMode,
    GenerationTrigger,
    Kind,
    Origin,
)
from tests.documents.canary import marker
from tests.documents.render_support import CV
from tests.documents.store_support import AT, USER_A, engine, factory, migrated_url, short_locks
from tests.documents.test_tick import World

__all__ = ["engine", "factory", "migrated_url", "short_locks"]


@pytest.fixture
def world(factory: sessionmaker[Session], migrated_url: str) -> World:
    return World(factory, migrated_url, USER_A)


def written_cv(world: World, posting: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A posting CV whose writer stored version 1, and the panel it opens: (document, panel,
    the CV generation's run id stand in)."""
    with world.factory() as db:
        store.lock_posting(db, world.user, posting)
        document = store.get_or_create(db, world.user, posting, Kind.cv, at=AT)
        made = generate.create(
            db,
            document,
            GenerationMode.posting,
            GenerationTrigger.click,
            world.base,
            {},
            100_000,
            at=AT,
        )
        generate.start(db, made, at=AT, settings=world.settings)
        generate.finish(db, made, c.parse(Kind.cv, CV), [], at=AT)
        opened = panel.begin(db, made, at=AT)
        assert opened is not None
        store.commit(db)
        return document.id, opened.id, made.id


def review(role: panel.Role, verdict: str, line: str) -> panel.Review:
    quoted = panel.Quoted(quote=line, why="Vague.")
    if role in panel.READERS:
        return panel.Review(verdict=verdict, hurt_most=quoted, problems=[quoted], note="Short.")
    return panel.Review(verdict=verdict, problems=[quoted])


HEADLINE = CV["headline"]
FIXED_HEADLINE = "Platform engineer for payments teams"


class Fakes:
    """The model calls and the rebuild of one panel, recorded."""

    def __init__(
        self,
        world: World,
        *,
        fail: set[str] = frozenset(),
        merge: bool = True,
        cancel: str | None = None,
        rebuild_lands: bool = True,
    ):
        self.world = world
        self.fail = fail
        self.merge = merge
        self.cancel = cancel  # the reviewer whose call is cancelled (arq's timeout, a deploy)
        self.rebuild_lands = rebuild_lands
        self.prompts: dict[str, list[str]] = {}
        self.after: list[tuple[uuid.UUID | None, str]] = []
        self.handovers: list[uuid.UUID] = []
        self.instructions: list[str] = []  # the rebuild's held first message
        self.screens = iter(("REJECT", "PASS"))

    async def call(self, agent: str, user_id: uuid.UUID, prompt: str, budget: int) -> panel.Reply:
        assert user_id == self.world.user
        assert budget > 0
        self.prompts.setdefault(agent, []).append(prompt)
        key = agent.removeprefix("documents_panel_")
        if key == self.cancel:
            raise asyncio.CancelledError
        if key in self.fail:
            if key == "reader_ceo":
                raise RuntimeError("the provider is down")
            return panel.Reply(None, None, "token_budget")
        if agent == panel.MERGE_AGENT:
            if not self.merge:
                return panel.Reply(None, None, "no_report")
            return panel.Reply(
                panel.Merged(
                    mistakes=[
                        panel.Mistake(
                            quote=HEADLINE, why="Generic.", flagged_by=["screen"], fix="Name it."
                        ),
                        panel.Mistake(
                            quote=f"  {HEADLINE.upper()} ",
                            why="Same line.",
                            flagged_by=["reader_rival", "nobody"],
                            fix="Same.",
                        ),
                        panel.Mistake(
                            quote="Second line", why="Weak.", flagged_by=["manager"], fix="Cut."
                        ),
                    ],
                    questions=[panel.Question(question="How many services?", section="experience")],
                    closest="screen",
                    closest_why="Missing terms.",
                )
            )
        role = panel.ROLE_OF[key]
        verdict = next(self.screens) if key == "screen" else role.good
        return panel.Reply(review(role, verdict, HEADLINE))

    async def execute(self, run_id: uuid.UUID) -> None:
        def step() -> None:
            with self.world.factory() as db:
                made = db.scalar(
                    sa.select(DocumentGeneration).where(DocumentGeneration.run_id == run_id)
                )
                assert made is not None
                assert made.mode is GenerationMode.panel
                held = db.get(AgentRun, run_id)
                assert held is not None
                self.instructions.append(panel_hold.read(held.input.get("panel")))
                if self.rebuild_lands:
                    content = c.parse(Kind.cv, {**CV, "headline": FIXED_HEADLINE})
                    generate.finish(db, made, content, [], at=AT)
                db.execute(
                    sa.update(AgentRun).where(AgentRun.id == run_id).values(status=RunStatus.done)
                )
                store.commit(db)

        await asyncio.to_thread(step)

    async def done(self, cv_run: uuid.UUID | None, status: str) -> None:
        self.after.append((cv_run, status))

    async def handover(self, turn: uuid.UUID) -> None:
        self.handovers.append(turn)

    def steps(self) -> panel.Steps:
        return panel.Steps(
            call=self.call,
            execute=self.execute,
            sessions=self.world.factory,
            clock=lambda: AT,
            settings=self.world.settings,
            after=self.done,
            handover=self.handover,
        )


def row(world: World, panel_id: uuid.UUID) -> DocumentPanel:
    with world.factory() as db:
        found = db.get(DocumentPanel, panel_id)
        assert found is not None
        db.expunge(found)
        return found


def versions(world: World, document_id: uuid.UUID) -> list[tuple[int, Origin]]:
    with world.factory() as db:
        return [
            (v.number, v.origin)
            for v in db.scalars(
                sa.select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.number)
            )
        ]


def test_the_panel_reviews_merges_rebuilds_and_screens_again(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    posting = world.posting(70)
    document, panel_id, generation = written_cv(world, posting)
    fakes = Fakes(world)
    caplog.set_level(logging.DEBUG)
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "done"
    found = row(world, panel_id)
    assert set(found.reviews) == {role.key for role in panel.ROLES}
    assert (found.screen_before, found.screen_after) == ("REJECT", "PASS")
    assert [m["number"] for m in found.mistakes] == [1, 2]
    assert found.mistakes[0]["flagged_by"] == ["screen", "reader_rival"]
    assert found.questions == [{"question": "How many services?", "section": "experience"}]
    assert (found.reviewed_version, found.rebuilt_version) == (1, 2)
    assert versions(world, document) == [(1, Origin.generated), (2, Origin.panel)]
    assert found.status == "done"
    assert found.failed == []
    with world.factory() as db:
        source = db.get(DocumentGeneration, generation)
        assert source is not None
        assert fakes.after == [(source.run_id, "done")]  # the letter follows the CV's panel
    shown = panel.view(found)
    assert (shown["readers_hire"], shown["readers_answered"]) == (5, 5)
    assert shown["closest"]["label"] == "The automated screen"
    assert HEADLINE not in caplog.text  # quoted lines never reach the logs
    assert FIXED_HEADLINE not in caplog.text


def test_each_reader_reads_only_the_document_the_brief_and_its_own_role(world: World) -> None:
    planted = marker()
    posting = world.posting(70, title=f"Platform engineer {planted}")
    _, panel_id, _ = written_cv(world, posting)
    fakes = Fakes(world)
    asyncio.run(panel.run(fakes.steps(), panel_id))
    for role in panel.ROLES:
        [prompt] = fakes.prompts[role.agent][:1]
        assert "<document>" in prompt
        assert "<job>" in prompt
        assert "<role>" in prompt
        assert "<reviews>" not in prompt
        assert planted in prompt  # the brief reached it
        assert "Vague." not in prompt  # no other reviewer's answer
        mine = panel.you_are(role, "cv", "Platform engineer", "Example Corp")[:40]
        others = [panel.you_are(o, "cv", "x", "y")[:12] for o in panel.ROLES if o is not role]
        assert mine.split(" Platform")[0] in prompt
        assert sum(other in prompt for other in others) <= 1  # "the hiring manager" is twice
    recruiter = fakes.prompts["documents_panel_reader_recruiter"][0]
    manager = fakes.prompts["documents_panel_reader_manager"][0]
    assert len(recruiter) < len(manager)  # the top third only
    assert "the CEO of Example Corp" in fakes.prompts["documents_panel_reader_ceo"][0]
    merge = fakes.prompts[panel.MERGE_AGENT][0]
    assert "<reviews>" in merge


def test_a_failing_reviewer_is_recorded_missing_and_the_panel_goes_on(world: World) -> None:
    _, panel_id, _ = written_cv(world, world.posting(70))
    fakes = Fakes(world, fail={"reader_ceo", "manager"})
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "done"
    found = row(world, panel_id)
    assert found.reviews["reader_ceo"] == {"missing": "error"}
    assert found.reviews["manager"] == {"missing": "token_budget"}
    assert found.rebuilt_version == 2
    assert "reader_ceo" not in fakes.prompts[panel.MERGE_AGENT][0]


def test_a_failing_merge_keeps_the_first_version_and_says_so(world: World) -> None:
    document, panel_id, _ = written_cv(world, world.posting(70))
    fakes = Fakes(world, merge=False)
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "failed"
    found = row(world, panel_id)
    assert found.failed == ["merge"]
    assert found.rebuilt_version is None
    assert versions(world, document) == [(1, Origin.generated)]
    assert [status for _, status in fakes.after] == ["failed"]  # the letter still follows


def test_no_review_at_all_fails_the_panel_without_a_merge(world: World) -> None:
    _, panel_id, _ = written_cv(world, world.posting(70))
    fakes = Fakes(world, fail={role.key for role in panel.ROLES})
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "failed"
    assert row(world, panel_id).failed == ["reviews"]
    assert panel.MERGE_AGENT not in fakes.prompts


def test_the_apply_module_waits_for_a_running_panel(world: World) -> None:
    posting = world.posting(70)
    document, panel_id, _ = written_cv(world, posting)
    with world.factory() as db:
        db.execute(
            sa.update(DocumentVersion)
            .where(DocumentVersion.document_id == document)
            .values(pdf_sha256=b"\x01" * 32)
        )
        db.commit()
        now = freeze.current(db, world.user, posting)
        assert (now.cv.version, now.cv.coming) == (None, freeze.WRITING)
    asyncio.run(panel.run(Fakes(world, merge=False).steps(), panel_id))
    with world.factory() as db:
        after = freeze.current(db, world.user, posting)
        assert after.cv.version is not None  # the first version, the panel over
        assert after.cv.coming == freeze.RENDERING  # its own render, nothing of the panel


def test_a_stale_panel_is_closed_by_the_tick_and_its_letter_follows(world: World) -> None:
    from datetime import timedelta

    _, panel_id, generation = written_cv(world, world.posting(70))
    with world.factory() as db:
        assert panel.recover(db, world.user, AT + timedelta(days=1)) == []  # never started
        assert panel.claim(db, panel_id, AT) == ("run", world.user)  # its job took the slot
        db.commit()
    with world.factory() as db:
        assert panel.recover(db, world.user, AT + timedelta(seconds=panel.STALE_S - 1)) == []
        letters = panel.recover(db, world.user, AT + timedelta(seconds=panel.STALE_S + 1))
        source = db.get(DocumentGeneration, generation)
        assert source is not None
        assert letters == [source.run_id]
        db.commit()
    assert row(world, panel_id).failed == ["panel_error"]


# --- pure parts ---------------------------------------------------------------------------------


def test_the_merged_list_keeps_the_merges_order_and_merges_duplicates() -> None:
    merged = panel.Merged(
        mistakes=[
            panel.Mistake(quote="B line", why="1", flagged_by=["manager"], fix=""),
            panel.Mistake(quote="A line", why="2", flagged_by=["screen"], fix=""),
            panel.Mistake(quote="b  LINE", why="3", flagged_by=["reader_ceo", "x"], fix=""),
        ]
    )
    out = panel.merged_mistakes(merged, ["manager", "screen", "reader_ceo"])
    assert [(m["number"], m["quote"], m["flagged_by"]) for m in out] == [
        (1, "B line", ["manager", "reader_ceo"]),
        (2, "A line", ["screen"]),
    ]


@pytest.mark.parametrize(
    ("role", "raw", "broken"),
    [
        ("screen", {"verdict": "HIRE"}, "verdict"),
        ("reader_ceo", {"verdict": "HIRE"}, "hurt_most"),
        ("manager", {"verdict": "NO", "problems": [{"quote": "a", "why": ""}] * 4}, "problems"),
        (
            "reader_rival",
            {"verdict": "PASS", "hurt_most": {"quote": "a", "why": ""}, "note": "word " * 120},
            "note",
        ),
        ("screen", {"verdict": "REJECT"}, None),
    ],
)
def test_each_role_keeps_the_skills_limits(
    role: str, raw: dict[str, Any], broken: str | None
) -> None:
    found = panel.check_review(panel.ROLE_OF[role], panel.Review.model_validate(raw))
    assert (found.split(":")[0] if found else None) == broken


def test_a_placeholder_never_reaches_a_cv() -> None:
    content = c.parse(
        Kind.cv,
        {**CV, "summary": "Runs the platform [ADD: team size] for payments.", "headline": "[add x"},
    )
    fixed, records = corrections.drop_placeholders(Kind.cv, content)
    assert all("[ADD" not in text.upper() for _, text in c.text_fields(fixed))
    assert fixed.summary == "Runs the platform for payments."
    assert {r.code for r in records} == {corrections.PLACEHOLDER}
    assert {r.reason for r in records} == {corrections.PLACEHOLDER_REASON}


@pytest.mark.parametrize(
    "placeholder",
    [
        "[ADD: team size]",
        "[add the team size",
        "[À AJOUTER : taille de l'équipe]",
        "[a ajouter]",
        "[AJOUTER le nombre]",
        "[À compléter]",
        "[INSERT number]",
        "[TBD]",
        "[TBC: dates]",
        "[TODO]",
        "[to do: metrics]",
        "[Your title]",
        "[Votre poste]",
        "[Nom de l'entreprise]",
        "[XX]",
        "[COMPANY NAME]",
        "[NOMBRE D'ANNÉES]",
        "{ADD: a number}",
        "[ à préciser ]",
        "[Insérer le chiffre]",
    ],
)
def test_every_bracketed_placeholder_in_english_or_french_is_dropped(placeholder: str) -> None:
    text = f"Runs the platform {placeholder} for payments."
    content = c.parse(Kind.cv, {**CV, "summary": text})
    fixed, records = corrections.drop_placeholders(Kind.cv, content)
    assert fixed.summary is not None
    assert "[" not in fixed.summary
    assert "{" not in fixed.summary
    assert fixed.summary.startswith("Runs the platform")
    assert [r.code for r in records] == [corrections.PLACEHOLDER]


@pytest.mark.parametrize(
    "kept",
    [
        "Moved 12 services to Kubernetes [2023].",
        "Led the API guild [internal], then the platform team.",
        "Wrote the billing service in Go.",
        "Added tracing to every service.",  # "Added" outside a bracket is a fact
    ],
)
def test_a_line_without_a_placeholder_is_kept(kept: str) -> None:
    content = c.parse(Kind.cv, {**CV, "summary": kept})
    fixed, records = corrections.drop_placeholders(Kind.cv, content)
    assert (fixed.summary, records) == (kept, [])


def test_the_brief_holds_every_field_of_an_invented_posting(world: World) -> None:
    text = "About Example Corp.\n- Five years of Go\n* Kubernetes in production\nWe pay well."
    posting = world.posting(72, text=text)
    world.set_job(
        posting,
        location="Lyon",
        pay_min=50_000,
        pay_max=60_000,
        pay_currency="EUR",
        pay_period="year",
    )
    with world.factory() as db:
        found = brief.for_posting(db, world.user, posting)
        assert brief.for_posting(db, world.user, uuid.uuid4()) is None
    assert found is not None
    assert found["title"] == "Platform engineer"
    assert found["company"]["name"] == "Example Corp"
    assert found["location"] == "Lyon"
    assert found["pay"]["min"] == 50_000
    assert found["text"] == text
    assert found["text_cut"] is False
    assert found["requirements"] == ["Five years of Go", "Kubernetes in production"]
    assert found["fit"]["score"] == 72
    assert {"contract", "work_mode", "language", "source", "url"} <= set(found)


def test_a_posting_estimate_includes_both_panels() -> None:
    limits = estimate.limits()
    pair = estimate.posting(9_000, 4_000, 6_000, **limits)
    one = panel.cost(estimate.CONTENT_LOW_CHARS, 6_000, **limits)
    shown = estimate.with_panels(pair, 6_000, **limits)
    assert shown.high == pair.high + 2 * one.high
    assert shown.low > pair.low


def test_the_catalog_lists_the_eight_panel_agents_under_documents() -> None:
    from app.agents import catalog

    found = catalog.load()
    for name in panel.AGENT_NAMES:
        assert name in found
        assert catalog.CATEGORY_OF[name] == "documents"
        assert found[name].model == "claude-sonnet-5-5"
        assert "Adapted from hiring-panel-skill by Alex Chen" in found[name].system_prompt


def test_a_version_that_no_longer_reads_fails_the_panel_and_the_letter_still_follows(
    world: World,
) -> None:
    document, panel_id, generation = written_cv(world, world.posting(70))
    with world.factory() as db:
        db.execute(
            sa.update(DocumentVersion)
            .where(DocumentVersion.document_id == document)
            .values(content={"schema": 1, "unknown_field": "x"})
        )
        db.commit()
        source = db.get(DocumentGeneration, generation)
        assert source is not None
        source_run = source.run_id
    fakes = Fakes(world)
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "failed"
    assert row(world, panel_id).failed == ["panel_error"]
    assert fakes.after == [(source_run, "failed")]
    assert fakes.prompts == {}


INVENTED_POSTING = """Platform engineer at Example Robotics

About Example Robotics
We build warehouse robots for small shops in Lyon.

About the role
You run the deployment platform.

What you bring:
- Five years of Go
- Kubernetes in production

Benefits
Lunch, a train pass and 30 days off.

Salary: 50,000 to 60,000 EUR a year
Contract: permanent, full time
"""


def test_the_brief_names_what_the_posting_says_of_the_company_the_offer_and_the_pay() -> None:
    parts = brief.parts_of(INVENTED_POSTING)
    assert parts == {
        "about_company": "We build warehouse robots for small shops in Lyon.",
        "responsibilities": "You run the deployment platform.",
        "requirements_text": "- Five years of Go\n- Kubernetes in production",
        "benefits": "Lunch, a train pass and 30 days off.",
        "salary_text": "50,000 to 60,000 EUR a year",
        "contract_text": "permanent, full time",
    }
    assert brief.parts_of("No headings here, only one line of text.") == {}
    # A heading in French, accents and case folded; the first part of a name is kept.
    french = "QUI SOMMES-NOUS ?\nUne entreprise de Lyon.\nAvantages\nTickets.\nAvantages\nAutre."
    assert brief.parts_of(french) == {
        "about_company": "Une entreprise de Lyon.",
        "benefits": "Tickets.",
    }


# --- review of D29, round 1 ---------------------------------------------------------------------


def test_a_cancelled_panel_job_still_starts_the_letter_through_the_tick(world: World) -> None:
    """Item 3: arq cancels the job at its timeout (a worker stop or a deploy too). The panel
    stays running, marked for the next tick, whose recover closes it and starts the letter."""
    _, panel_id, generation = written_cv(world, world.posting(70))
    fakes = Fakes(world, cancel="screen")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(panel.run(fakes.steps(), panel_id))
    found = row(world, panel_id)
    assert (found.status, found.pending, found.failed) == ("running", panel.PENDING_CLOSE, [])
    assert fakes.after == []
    with world.factory() as db:
        source = db.get(DocumentGeneration, generation)
        assert source is not None
        assert panel.recover(db, world.user, AT) == [source.run_id]
        db.commit()
    assert (row(world, panel_id).status, row(world, panel_id).failed) == ("failed", ["panel_error"])
    # And through the tick itself, past STALE_S, for a job that died without its cancel landing.
    second = world.posting(71)
    _, lost, _ = written_cv(world, second)
    with world.factory() as db:
        assert panel.claim(db, lost, AT)[0] == "run"
        db.commit()
    world.tick(AT + timedelta(seconds=panel.STALE_S + 1))
    assert row(world, lost).failed == ["panel_error"]
    assert any(g["kind"] is Kind.letter for g in world.generations(second))  # the letter


def test_the_rebuild_reads_the_reviewed_version_and_names_only_the_writers_tools(
    world: World,
) -> None:
    """Item 4: the version the panel read is in the rebuild's first message, for every kind,
    and every tool it names is one the writer of that kind has."""
    _, panel_id, _ = written_cv(world, world.posting(70))
    fakes = Fakes(world)
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "done"
    [held] = fakes.instructions
    assert "<reviewed>" in held
    assert HEADLINE in held.split("<mistakes>")[0]
    tools = {"cv": writer_cv.TOOLS, "letter": writer_letter.TOOLS, "base": writer_base.TOOLS}
    for kind, has in tools.items():
        planted = marker()
        text = panel.rebuild_instructions(kind, 1, [{"number": 1}], {"body": planted})
        assert planted in text.split("<mistakes>")[0]
        named = set(re.findall(r"\b(?:get|read|submit)_[a-z_]+", text))
        assert named, kind
        assert named <= set(has), (kind, named - set(has))
        assert "get_draft" not in text or "get_draft" in has


def test_a_rebuild_that_fails_keeps_the_first_version_and_says_so(world: World) -> None:
    """Item 8 (a), spec item 12: the rebuild never lands (`_rebuilt` is None), or cannot start
    (`rebuild_busy`): version 1 stays the document, and `failed` names it."""
    document, panel_id, _ = written_cv(world, world.posting(70))
    fakes = Fakes(world, rebuild_lands=False)
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "failed"
    found = row(world, panel_id)
    assert (found.failed, found.rebuilt_version) == (["rebuild"], None)
    assert versions(world, document) == [(1, Origin.generated)]
    assert panel.view(found)["failed"] == ["rebuild"]
    assert [status for _, status in fakes.after] == ["failed"]  # the letter still follows

    posting = world.posting(72)
    busy_document, busy_panel, _ = written_cv(world, posting)
    with world.factory() as db:
        store.lock_posting(db, world.user, posting)
        held = store.get(db, world.user, busy_document)
        assert held is not None
        generate.create(  # a generation of the document is open when the rebuild is due
            db, held, GenerationMode.posting, GenerationTrigger.click, None, {}, 1000, at=AT
        )
        store.commit(db)
    assert asyncio.run(panel.run(Fakes(world).steps(), busy_panel)) == "failed"
    assert row(world, busy_panel).failed == ["rebuild_busy"]
    assert versions(world, busy_document) == [(1, Origin.generated)]


def test_a_posting_applied_during_the_panel_gets_no_rebuild(world: World) -> None:
    posting = world.posting(70)
    document, panel_id, _ = written_cv(world, posting)
    world.set_job(posting, status="applied")  # Apply myself, or Mark as sent
    fakes = Fakes(world)
    assert asyncio.run(panel.run(fakes.steps(), panel_id)) == "failed"
    assert row(world, panel_id).failed == ["posting_applied"]
    assert versions(world, document) == [(1, Origin.generated)]
    assert fakes.instructions == []


def test_a_second_panel_waits_its_turn_and_the_first_hands_the_slot_over(world: World) -> None:
    """Two panels at once would need six runs of the person's four: the second waits, queued,
    instead of running with its reviewers busy; the end of a panel queues the next one."""
    _, first, _ = written_cv(world, world.posting(70))
    _, second, _ = written_cv(world, world.posting(71))
    with world.factory() as db:
        assert panel.claim(db, first, AT) == ("run", world.user)
        db.commit()
    waiting = Fakes(world)
    assert asyncio.run(panel.run(waiting.steps(), second)) == "queued"
    assert waiting.prompts == {}
    assert (row(world, second).status, row(world, second).started_at) == ("running", None)
    with world.factory() as db:
        assert panel.next_turn(db, world.user, AT) is None  # the first holds the slot
        db.execute(sa.update(DocumentPanel).where(DocumentPanel.id == first).values(status="done"))
        db.commit()
    with world.factory() as db:
        assert panel.next_turn(db, world.user, AT) == second
        db.commit()
    assert panel.turns(world.factory, AT) == [second]  # the tick queues a lost turn
    _, third, _ = written_cv(world, world.posting(72))
    fakes = Fakes(world)
    assert asyncio.run(panel.run(fakes.steps(), second)) == "done"
    assert len(fakes.prompts[panel.ROLE_OF["screen"].agent]) == 2
    assert fakes.handovers == [third]  # its end queues the next one
    assert asyncio.run(panel.run(Fakes(world).steps(), second)) == "gone"  # a second job


def test_strong_only_when_no_reviewer_chose_its_rejecting_verdict() -> None:
    good = {role.key: {"verdict": role.good} for role in panel.ROLES}
    done = DocumentPanel(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        kind="cv",
        status="done",
        reviewed_version=1,
        reviews=good,
        mistakes=[],
        questions=[],
        failed=[],
        run_ids=[],
        created_at=AT,
    )
    assert panel.view(done)["strong"] is True
    for role in panel.ROLES:
        done.reviews = {**good, role.key: {"verdict": role.rejects}}
        assert panel.view(done)["strong"] is False, role.key
    done.reviews = {**good, "reader_ceo": {"verdict": "MAYBE"}}
    assert panel.view(done)["strong"] is True


def test_the_writers_brief_leaves_the_whole_text_to_get_job() -> None:
    from app.documents import writer_tools

    planted = marker()
    found = {"title": "Platform engineer", "text": f"{planted} " * 5_000, "requirements": ["Go"]}
    shown = writer_tools.for_writer(found)
    assert planted not in str(shown)
    assert shown["text"] == writer_tools.BRIEF_TEXT_WHERE
    assert (shown["title"], shown["requirements"]) == ("Platform engineer", ["Go"])
