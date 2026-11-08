"""Interview preparation (interview_prep.py): the kind and its content, Prepare on the fake
backend (a version, nothing rendered, the debrief kept), the fact check on the stories, the
refusals, the Practice chat (an answer, a failed run, a lost hook), the sent documents and the
kept stories as a proposal the person accepts. Invented people and postings only."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals as agent_proposals
from app.agents.models import AgentRun, ProposalStatus, RunStatus
from app.documents import consent, interview_prep, rules, store, submit
from app.documents import content as c
from app.documents.models import (
    RENDERED_KINDS,
    Author,
    Channel,
    DocumentGeneration,
    FreezeReason,
    GenerationMode,
    GenerationStatus,
    Kind,
    Origin,
)
from app.profile import service as profile_service
from tests.documents.store_support import (
    AT,
    USER_A,
    add_posting,
    db,
    engine,
    factory,
    migrated_url,
)
from tests.documents.writer_support import Person, execute, generation_row, person, versions

__all__ = ["db", "engine", "factory", "migrated_url"]

STORY = {
    "title": "The deployment platform",
    "situation": "At Example Corp the payments team shipped by hand.",
    "task": "Give the team one way to deploy.",
    "action": "I built the deployment platform the team relies on.",
    "result": "Engineers ship every day through it.",
}


@pytest.fixture
def alex(factory: sessionmaker[Session], migrated_url: str) -> Person:
    who = person(factory, migrated_url, USER_A)
    with who.factory() as db:
        consent.record(db, who.user, AT)
        db.commit()
    return who


def prepare(who: Person, script: str | None = None) -> interview_prep.Started:
    with who.factory() as db:
        started = interview_prep.start(
            db, who.user, who.posting, at=AT, settings=who.settings, script=script
        )
        db.commit()
        return started


def draft(who: Person, document_id: uuid.UUID) -> dict[str, Any]:
    with who.factory() as db:
        found = store.get(db, who.user, document_id)
        assert found is not None
        return dict(found.draft)


# --- the kind -----------------------------------------------------------------------------------


def test_the_kind_is_text_only_and_its_checked_fields_are_the_claims() -> None:
    assert Kind("interview_prep") is Kind.interview_prep
    assert Kind.interview_prep not in RENDERED_KINDS
    content = c.parse(
        "interview_prep",
        {
            "expected": [{"question": "Why us? We need 10 years of Go.", "basis": "posting"}],
            "gaps": [{"gap": "Go", "answer": "I have used Python for 3 years."}],
            "stories": [STORY],
            "ask": ["How is success measured?"],
            "debrief": [{"asked": "They asked about 12 services."}],
        },
    )
    assert c.kind_of(content) == "interview_prep"
    paths = [path for path, _ in c.text_fields(content)]
    assert paths == [
        "gaps.0.answer",
        "stories.0.title",
        "stories.0.situation",
        "stories.0.task",
        "stories.0.action",
        "stories.0.result",
    ]


def test_a_preparation_holds_more_than_a_letter_and_has_its_own_ceiling() -> None:
    debrief = [{"asked": "x" * 1_900}] * 12
    content = c.parse("interview_prep", {"debrief": debrief})  # over 20 KB, under 40 KB
    assert len(c.canonical_json(content)) > c.CONTENT_MAX_BYTES
    with pytest.raises(c.ContentInvalidError) as refused:
        c.parse("interview_prep", {"debrief": [{"asked": "x" * 1_900, "notes": "y" * 1_900}] * 12})
    assert [e.code for e in refused.value.errors] == ["content_too_large"]
    with pytest.raises(c.ContentInvalidError):
        c.parse("interview_prep", {"expected": [{"question": "Q", "basis": "they_asked"}]})


# --- Prepare ------------------------------------------------------------------------------------


def test_prepare_writes_a_version_from_the_recorded_stream_and_renders_nothing(
    alex: Person,
) -> None:
    started = prepare(alex)
    assert started.generation.mode is GenerationMode.posting
    execute(alex, started.run_id)
    row = generation_row(alex.factory, started.generation.id)
    assert row.status is GenerationStatus.done, (row.status, row.error)
    [version] = versions(alex.factory, started.document.id)
    assert version.origin is Origin.generated
    assert version.pdf_sha256 is None
    assert row.corrections == len(version.corrections) == 0
    written = draft(alex, started.document.id)
    assert len(written["stories"]) == 2
    assert {q["basis"] for q in written["expected"]} == {"posting", "hiring_panel"}
    assert alex.queue.jobs == []  # nothing rendered
    with alex.factory() as db:
        assert db.get(AgentRun, started.run_id).agent == "documents_interview_prep"


def test_a_new_preparation_keeps_the_debrief_the_person_wrote(alex: Person) -> None:
    first = prepare(alex)
    execute(alex, first.run_id)
    with alex.factory() as db:
        document = store.get(db, alex.user, first.document.id)
        assert document is not None
        content = {**document.draft, "debrief": [{"on": "Round one", "asked": "Why us?"}]}
        store.save_draft(
            db, alex.user, document.id, content, document.draft_revision, Author.user, at=AT
        )
        db.commit()
    second = prepare(alex)
    execute(alex, second.run_id)
    assert generation_row(alex.factory, second.generation.id).status is GenerationStatus.done
    written = draft(alex, first.document.id)
    assert written["debrief"] == [{"on": "Round one", "asked": "Why us?", "notes": ""}]
    assert len(versions(alex.factory, first.document.id)) == 2


def test_a_figure_the_profile_does_not_state_is_corrected_and_recorded(alex: Person) -> None:
    started = prepare(alex)
    binding = {"generation_id": started.generation.id, "document_id": started.document.id}
    with alex.factory() as db:
        context = interview_prep.context(db, alex.user, binding)
    run_id = uuid.uuid4()
    invented = {
        **STORY,
        "result": "Deploy time fell by 73% and we saved 4 million euros.",
    }
    raw = {"schema": 1, "stories": [invented], "gaps": [{"gap": "Go", "answer": "I learn fast."}]}
    answer, error = submit.handle(context, run_id, raw)
    assert error  # the one correction pass
    assert "fact check" in answer
    assert submit.handle(context, run_id, raw) == (submit.STORED, False)
    accepted = submit.take(run_id).accepted
    assert accepted is not None
    result = accepted.content.stories[0].result
    assert "73%" not in result
    assert "4 million" not in result
    assert {r.field_path for r in accepted.corrections} == {"stories.0.result"}


def test_prepare_refuses_without_consent_and_while_one_is_written(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    with who.factory() as db, pytest.raises(interview_prep.PrepRefusedError) as refused:
        interview_prep.start(db, who.user, who.posting, at=AT, settings=who.settings)
    assert refused.value.code == "consent"
    with who.factory() as db:
        consent.record(db, who.user, AT)
        db.commit()
    prepare(who)
    with who.factory() as db, pytest.raises(interview_prep.PrepRefusedError) as busy:
        interview_prep.start(db, who.user, who.posting, at=AT, settings=who.settings)
    assert busy.value.code == "busy"


def test_the_sent_documents_are_the_frozen_versions(alex: Person) -> None:
    with alex.factory() as db:
        cv = store.get_or_create(db, alex.user, alex.posting, Kind.cv, at=AT)
        content = {**cv.draft, "headline": "Platform engineer"}
        store.save_draft(db, alex.user, cv.id, content, cv.draft_revision, Author.user, at=AT)
        sent, _ = store.snapshot(db, alex.user, cv.id, Origin.edited, at=AT)
        store.freeze(db, alex.user, sent.id, FreezeReason.marked_sent, AT)
        later = {**content, "headline": "Changed after sending"}
        cv = store.get(db, alex.user, cv.id)
        assert cv is not None
        store.save_draft(db, alex.user, cv.id, later, cv.draft_revision, Author.user, at=AT)
        store.snapshot(db, alex.user, cv.id, Origin.edited, at=AT)
        db.commit()
        found = interview_prep.sent_documents(db, alex.user, alex.posting)
    assert found["cv"]["content"]["headline"] == "Platform engineer"
    assert found["cv"]["sent"] == AT.date().isoformat()
    assert "letter" not in found


# --- Practice -----------------------------------------------------------------------------------


def practice(who: Person, document_id: uuid.UUID, text: str, script: str | None = None) -> Any:
    with who.factory() as db:
        posted = interview_prep.post_practice(
            db, who.user, document_id, text, at=AT, settings=who.settings, script=script
        )
        db.commit()
        return posted


def chat(who: Person, document_id: uuid.UUID) -> list[tuple[str, str]]:
    with who.factory() as db:
        found = interview_prep.practice_messages(db, who.user, document_id)
        return [(m.role.value, m.text) for m in found]


def test_practice_answers_with_feedback_and_one_question(alex: Person) -> None:
    started = prepare(alex)
    execute(alex, started.run_id)
    posted = practice(alex, started.document.id, "Start, please.")
    execute(alex, posted.run_id)
    assert generation_row(alex.factory, posted.generation.id).status is GenerationStatus.done
    [(role, text), (answer_role, answer)] = chat(alex, started.document.id)
    assert (role, text) == ("user", "Start, please.")
    assert answer_role == "assistant"
    assert answer.endswith("which part of that platform did you build yourself?")
    with alex.factory() as db:
        content = store.messages(db, alex.user, started.document.id, Channel.content)
    assert content == []  # the Practice is its own channel


def test_a_failed_practice_run_says_so_and_frees_the_document(alex: Person) -> None:
    started = prepare(alex)
    execute(alex, started.run_id)
    posted = practice(alex, started.document.id, "Start.", script="crash")
    execute(alex, posted.run_id)
    assert generation_row(alex.factory, posted.generation.id).status is GenerationStatus.failed
    assert chat(alex, started.document.id)[-1] == ("assistant", interview_prep.PRACTICE_FAILED)
    practice(alex, started.document.id, "Again.")  # not refused


def test_a_practice_turn_whose_hook_was_lost_is_closed_by_the_next_read(alex: Person) -> None:
    started = prepare(alex)
    execute(alex, started.run_id)
    posted = practice(alex, started.document.id, "Start.")
    with alex.factory() as db:
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == posted.run_id)
            .values(status=RunStatus.failed, finished_at=AT)
        )
        early = AT + timedelta(seconds=interview_prep.HOOK_GRACE_S - 1)
        assert interview_prep.recover_practice(db, alex.user, started.document.id, early) == 0
        late = AT + timedelta(seconds=interview_prep.HOOK_GRACE_S + 1)
        assert interview_prep.recover_practice(db, alex.user, started.document.id, late) == 1
        db.commit()
        row = db.get(DocumentGeneration, posted.generation.id)
        assert row is not None
        assert row.status is GenerationStatus.failed


def test_a_practice_turn_is_validated_and_kept_once() -> None:
    run_id = uuid.uuid4()
    assert interview_prep.practice_turn(run_id, {"feedback": "", "question": " "})[1] is True
    assert interview_prep.practice_turn(run_id, {"question": "Q?", "extra": 1})[1] is True
    assert interview_prep.practice_turn(run_id, {"question": "Why us?"}) == (
        interview_prep.STORED,
        False,
    )
    assert interview_prep.practice_turn(run_id, {"question": "Again?"})[0] == submit.ALREADY
    turn = interview_prep.take_turn(run_id)
    assert turn is not None
    assert interview_prep.practice_text(turn) == "Why us?"


def _sized(sentence: str, size: int, end: str) -> str:
    return (sentence * (size // len(sentence) + 1))[: size - 1] + end


def test_a_practice_answer_of_the_largest_size_is_kept(alex: Person) -> None:
    started = prepare(alex)
    execute(alex, started.run_id)
    posted = practice(alex, started.document.id, "Start.")
    turn = interview_prep.Turn(
        feedback=_sized("Clear answer, now name the figure. ", interview_prep.FEEDBACK_MAX, "."),
        question=_sized("Which part did you build yourself ", interview_prep.QUESTION_MAX, "?"),
    )
    assert len(interview_prep.practice_text(turn)) == interview_prep.PRACTICE_ANSWER_MAX
    assert alex.settings.documents_chat_max_chars < interview_prep.PRACTICE_ANSWER_MAX
    interview_prep.settle_practice(alex.factory, posted.run_id, RunStatus.done, turn, AT)
    assert generation_row(alex.factory, posted.generation.id).status is GenerationStatus.done
    assert chat(alex, started.document.id)[-1] == ("assistant", interview_prep.practice_text(turn))


def test_a_practice_turn_follows_the_dash_rule() -> None:
    run_id = uuid.uuid4()
    raw = {
        "feedback": "Clear \u2014 but name the figure.",
        "question": "Why us \u2013 and why now?",
    }
    assert interview_prep.practice_turn(run_id, raw) == (interview_prep.STORED, False)
    turn = interview_prep.take_turn(run_id)
    assert turn is not None
    assert (turn.feedback, turn.question) == ("Clear, but name the figure.", "Why us, and why now?")
    half = interview_prep.FEEDBACK_MAX // 2
    longest = {"feedback": "x" * (half - 1) + "\u2014" + "y" * half}  # ", " would pass the limit
    run_id = uuid.uuid4()
    assert interview_prep.practice_turn(run_id, longest) == (interview_prep.STORED, False)
    kept = interview_prep.take_turn(run_id)
    assert kept is not None
    assert len(kept.feedback) <= interview_prep.FEEDBACK_MAX
    assert not rules.separators(kept.feedback)


def test_the_preparation_follows_the_dash_rule_whatever_the_persons_documents_use(
    alex: Person,
) -> None:
    started = prepare(alex)
    binding = {"generation_id": str(started.generation.id), "document_id": str(started.document.id)}
    with alex.factory() as db:
        assert store.settings(db, alex.user).no_dash_separators is False
        context = interview_prep.context(db, alex.user, binding)
    assert context is not None
    assert context.settings.no_dash_separators is True


# --- Keep stories -------------------------------------------------------------------------------


def test_kept_stories_change_the_profile_only_once_accepted(alex: Person) -> None:
    started = prepare(alex)
    execute(alex, started.run_id)
    with alex.factory() as db:
        made = interview_prep.keep_stories(db, alex.user, started.document.id, [1, 0, 9], AT)
        db.commit()
        assert made is not None
        assert made.status is ProposalStatus.pending
        body = made.payload["value"]["body"]
        assert body.startswith("The deployment platform of the payments team\nSituation: ")
        assert "Internal tools for the support team" in body
        blocks = profile_service.get_profile(db, alex.user, AT).sections["facts"]["blocks"]
        assert interview_prep.STORIES_BLOCK not in [b["title"] for b in blocks]
        assert interview_prep.pending_stories(db, alex.user).id == made.id
        agent_proposals.accept(db, alex.user, made.id, AT)
        db.commit()
        blocks = profile_service.get_profile(db, alex.user, AT).sections["facts"]["blocks"]
        [kept] = [b for b in blocks if b["title"] == interview_prep.STORIES_BLOCK]
        assert kept["body"] == body
        assert interview_prep.keep_stories(db, alex.user, started.document.id, [0], AT) is None
        with pytest.raises(ValueError, match="no story chosen"):
            interview_prep.keep_stories(db, alex.user, started.document.id, [7], AT)


def _block(who: Person) -> str:
    with who.factory() as db:
        blocks = profile_service.get_profile(db, who.user, AT).sections["facts"]["blocks"]
    return next(b["body"] for b in blocks if b["title"] == interview_prep.STORIES_BLOCK)


def test_a_second_keep_before_the_accept_keeps_the_stories_of_both(alex: Person) -> None:
    started = prepare(alex)
    execute(alex, started.run_id)
    stories = c.parse("interview_prep", draft(alex, started.document.id)).stories  # type: ignore[union-attr]
    with alex.factory() as db:
        first = interview_prep.keep_stories(db, alex.user, started.document.id, [0], AT)
        db.commit()
        second = interview_prep.keep_stories(db, alex.user, started.document.id, [1], AT)
        db.commit()
        assert first is not None
        assert second is not None
        db.refresh(first)
        assert first.status is ProposalStatus.superseded
        agent_proposals.accept(db, alex.user, second.id, AT)
        db.commit()
    body = _block(alex)
    assert interview_prep.story_text(stories[0]) in body
    assert interview_prep.story_text(stories[1]) in body


def test_a_stories_proposal_shows_only_on_the_postings_whose_stories_it_holds(
    alex: Person,
) -> None:
    started = prepare(alex)
    execute(alex, started.run_id)
    with alex.factory() as db:
        other = add_posting(db, alex.user)
        db.commit()
    elsewhere = prepare(dataclasses.replace(alex, posting=other))
    execute(alex, elsewhere.run_id)
    with alex.factory() as db:
        made = interview_prep.keep_stories(db, alex.user, started.document.id, [0], AT)
        db.commit()
        assert made is not None
        assert interview_prep.pending_stories(db, alex.user, alex.posting).id == made.id  # type: ignore[union-attr]
        assert interview_prep.pending_stories(db, alex.user, other) is None
        both = interview_prep.keep_stories(db, alex.user, elsewhere.document.id, [1], AT)
        db.commit()
        assert both is not None
        assert both.payload[interview_prep.PROPOSAL_POSTINGS] == sorted(
            [str(alex.posting), str(other)]
        )
        assert interview_prep.pending_stories(db, alex.user, alex.posting).id == both.id  # type: ignore[union-attr]
        assert interview_prep.pending_stories(db, alex.user, other).id == both.id  # type: ignore[union-attr]
        agent_proposals.reject(db, alex.user, both.id, AT)
        db.commit()
        assert interview_prep.pending_stories(db, alex.user, alex.posting) is None


def test_a_story_is_appended_to_the_block_never_replacing_it() -> None:
    story = c.Story(title="Tools", action="I built tools.")
    assert interview_prep.stories_body("Older story", [story]) == (
        "Older story\n\nTools\nAction: I built tools."
    )
    assert interview_prep.stories_body("Tools\nAction: I built tools.", [story]) == (
        "Tools\nAction: I built tools."
    )


def test_another_persons_preparation_is_not_found(
    alex: Person, factory: sessionmaker[Session], migrated_url: str
) -> None:
    started = prepare(alex)
    blake = person(factory, migrated_url, "e2e-docs-prep-blake@example.org")
    with blake.factory() as db:
        with pytest.raises(store.NotFoundError):
            interview_prep.own(db, blake.user, started.document.id)
        with pytest.raises(store.NotFoundError):
            interview_prep.keep_stories(db, blake.user, started.document.id, [0], AT)
        with pytest.raises(store.NotFoundError):
            interview_prep.start(db, blake.user, alex.posting, at=AT, settings=blake.settings)
