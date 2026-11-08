"""Reading an offer (SPEC-documents.md, "Reading an offer"): an invented offer letter uploaded to
ingestion, read into the posting's offer notes on the fake backend (no model, no key); a French
letter read in French; nothing stored or sent without ingestion's consent or a credential; the
uploads that cannot be read; a verdict refused at submit, then the notes without it; the dash
rule on every field but the quotes; the pay compared; the chat of the notes; the letter deleted;
the binding; and the canary. Real Postgres at head."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import credentials
from app.agents.models import AgentRun, RunStatus
from app.agents.tools import Scope
from app.documents import chat, offer_notes, store, writer_base
from app.documents import content as c
from app.documents.models import (
    Document,
    DocumentMessage,
    GenerationMode,
    GenerationStatus,
    Kind,
    Language,
    Origin,
    Role,
)
from app.ingestion.estimate_router import record_consent
from app.ingestion.models import IngestionDocument
from app.profile.models import Profile
from tests.agents.g6_helpers import everything_stored
from tests.documents.canary import find, marker
from tests.documents.store_support import AT, USER_A, USER_B, db, engine, factory, migrated_url
from tests.documents.writer_support import (
    Person,
    document_row,
    execute,
    generation_row,
    person,
    run_row,
    versions,
)

__all__ = ["db", "engine", "factory", "migrated_url"]

LETTER = (
    "Lumen Example\nOffer of employment\n\nWe are pleased to offer you the position of Senior "
    "Platform Engineer on a permanent contract, starting on 1 December 2026.\n\nPay. Your gross "
    "monthly salary will be 4,500 EUR, paid over twelve months.\n\nTrial period. The trial "
    "period is four months, renewable once.\n\nNon-compete. For twelve months after you leave, "
    "you will not work for a competitor in France.\n\nPlease sign and return this letter by 20 "
    "November 2026.\n"
)
FRENCH = (
    "Lumen Example\nPromesse d'embauche\n\nNous avons le plaisir de vous proposer le poste "
    "d'ingénieur plateforme en contrat à durée indéterminée, à compter du 1er décembre 2026.\n\n"
    "Rémunération. Votre salaire brut mensuel sera de 4 500 euros, versé sur douze mois.\n\n"
    "Période d'essai. La période d'essai est de quatre mois, renouvelable une fois.\n\nNous "
    "vous prions de nous retourner ce courrier signé avant le 20 novembre 2026.\n"
)


def upload(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    text: str,
    *,
    mime: str = "application/pdf",
    status: str = "done",
) -> uuid.UUID:
    """An ingestion document of `user` holding the letter's text."""
    with factory() as db:
        found = db.execute(
            sa.text(
                "INSERT INTO ingestion_documents (user_id, name, mime, size, sha256, text, "
                "text_source, text_status, parser_version) VALUES (:u, 'offer.pdf', :m, :s, :h, "
                ":t, 'parsed', CAST(:status AS ingestion_text_status), 2) RETURNING id"
            ),
            {
                "u": user,
                "m": mime,
                "s": len(text.encode()),
                "h": hashlib.sha256(f"{user}{text}{mime}".encode()).digest(),
                "t": text,
                "status": status,
            },
        ).scalar_one()
        db.commit()
        return found


def consented(who: Person) -> Person:
    with who.factory() as db:
        record_consent(db, who.user)
        db.commit()
    return who


@pytest.fixture
def alex(factory: sessionmaker[Session], migrated_url: str) -> Person:
    return consented(person(factory, migrated_url, USER_A))


def read(
    who: Person, letter: uuid.UUID, script: str | None = None
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """The notes read from `letter`, their run not executed: (document, generation, run)."""
    with who.factory() as db:
        started = offer_notes.read(
            db, who.user, who.posting, letter, at=AT, script=script, settings=who.settings
        )
        db.commit()
        return started.document.id, started.generation.id, started.run_id


def test_a_letter_is_read_into_notes_on_the_posting(alex: Person) -> None:
    letter = upload(alex.factory, alex.user, LETTER)
    document_id, generation_id, run_id = read(alex, letter)
    run = run_row(alex.factory, run_id)
    assert run.agent == offer_notes.READ
    assert run.job_id == alex.posting
    assert set(run.input) == {"generation_id", "mode", "language", "token_budget", "script"}
    execute(alex, run_id)
    generation = generation_row(alex.factory, generation_id)
    assert generation.status is GenerationStatus.done, generation.error
    document = document_row(alex.factory, document_id)
    assert (document.kind, document.source_document_id) == (Kind.offer_notes, letter)
    assert document.language is Language.en
    notes = c.parse("offer_notes", document.draft)
    assert isinstance(notes, c.OfferNotesContent)
    assert [clause.title for clause in notes.clauses] == ["Trial period", "Pay", "Non-compete"]
    assert notes.questions
    assert notes.differences
    [version] = versions(alex.factory, document_id)
    assert version.origin is Origin.generated
    with alex.factory() as db:
        shown = offer_notes.view(db, alex.user, alex.posting)
    assert shown is not None
    assert (shown.latest, shown.busy, shown.letter) == (1, False, (letter, "offer.pdf"))
    assert shown.reading is not None
    assert shown.reading.status is GenerationStatus.done


def test_a_french_letter_is_read_in_french(alex: Person) -> None:
    document_id, _, run_id = read(alex, upload(alex.factory, alex.user, FRENCH))
    assert document_row(alex.factory, document_id).language is Language.fr
    data = run_row(alex.factory, run_id).input
    assert data["language"] == "fr"
    assert "Language of the letter and of the notes: French." in offer_notes.READER.prompt(
        data, alex.posting
    )


def test_without_the_consent_or_a_credential_nothing_is_stored_or_sent(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    letter = upload(factory, who.user, LETTER)
    with factory() as db, pytest.raises(offer_notes.OfferRefusedError) as refused:
        offer_notes.read(db, who.user, who.posting, letter, at=AT, settings=who.settings)
    assert refused.value.reason == "consent"
    consented(who)
    with factory() as db:
        credentials.delete(db, who.user)
        db.commit()
    with factory() as db, pytest.raises(offer_notes.OfferRefusedError) as refused:
        offer_notes.read(db, who.user, who.posting, letter, at=AT, settings=who.settings)
    assert refused.value.reason == "credential"
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Document)) == 1  # the base CV


@pytest.mark.parametrize(
    ("mime", "status", "code"),
    [
        ("application/pdf", "pending", "reading"),
        ("application/pdf", "failed", "failed"),
        ("image/png", "done", "not_a_cv"),
    ],
)
def test_a_letter_that_cannot_be_read_is_refused(
    alex: Person, mime: str, status: str, code: str
) -> None:
    letter = upload(alex.factory, alex.user, LETTER, mime=mime, status=status)
    with alex.factory() as db, pytest.raises(writer_base.UploadRefusedError) as refused:
        offer_notes.read(db, alex.user, alex.posting, letter, at=AT, settings=alex.settings)
    assert refused.value.code == code


def test_another_persons_letter_or_posting_is_not_found(
    alex: Person, factory: sessionmaker[Session], migrated_url: str
) -> None:
    blake = consented(person(factory, migrated_url, USER_B))
    theirs = upload(factory, blake.user, LETTER)
    with factory() as db, pytest.raises(writer_base.UploadRefusedError) as refused:
        offer_notes.read(db, alex.user, alex.posting, theirs, at=AT, settings=alex.settings)
    assert refused.value.code == "not_found"
    mine = upload(factory, alex.user, LETTER)
    with factory() as db, pytest.raises(store.NotFoundError):
        offer_notes.read(db, alex.user, blake.posting, mine, at=AT, settings=alex.settings)
    with factory() as db:
        assert offer_notes.view(db, alex.user, blake.posting) is None


def test_a_verdict_is_refused_and_the_notes_without_it_are_stored(alex: Person) -> None:
    document_id, generation_id, run_id = read(
        alex, upload(alex.factory, alex.user, LETTER), "verdict"
    )
    execute(alex, run_id)
    assert generation_row(alex.factory, generation_id).status is GenerationStatus.done
    summary = document_row(alex.factory, document_id).draft["summary"]
    assert "sign" not in summary


NOTES = {
    "summary": "Lumen Example offers the role of Senior Platform Engineer.",
    "clauses": [
        {"title": "Pay", "quote": "Pay - 4,500 EUR a month", "plain": "You are paid monthly."}
    ],
    "differences": [],
    "questions": ["Pay: when does the first payment come?"],
}


@pytest.mark.parametrize(
    "said",
    [
        "It is safe to sign.",
        "You should sign it this week.",
        "We recommend signing it.",
        "Vous pouvez signer sans crainte.",
        "Vous devriez la signer.",
        "Je vous recommande de ne pas signer.",
    ],
)
def test_a_verdict_on_signing_is_refused_in_any_written_field(said: str) -> None:
    run_id = uuid.uuid4()
    for raw in (
        {**NOTES, "summary": said},
        {**NOTES, "questions": [said]},
        {**NOTES, "clauses": [{**NOTES["clauses"][0], "plain": said}]},
    ):
        answer, error = offer_notes.handle(run_id, raw)
        assert error
        assert "Not stored" in answer
    assert offer_notes.take(run_id).accepted is None


@pytest.mark.parametrize(
    "said",
    [
        "The letter asks you to sign and return it by 20 November.",
        "Vous devez retourner la lettre signée avant le 20 novembre.",
        "You may sign it electronically, the letter says.",
    ],
)
def test_describing_what_the_letter_asks_is_no_verdict(said: str) -> None:
    run_id = uuid.uuid4()
    answer, error = offer_notes.handle(run_id, {**NOTES, "summary": said})
    assert (answer, error) == ("Stored. Your work is done: answer with one short line.", False)


def test_a_quote_is_the_letters_own_words_and_the_rest_follows_the_dash_rule() -> None:
    run_id = uuid.uuid4()
    quoted = {
        **NOTES["clauses"][0],
        "title": "Pay \u2014 monthly",
        "quote": "It is safe to sign - the employer",
    }
    raw = {**NOTES, "summary": "A permanent role - in Lyon.", "clauses": [quoted]}
    assert offer_notes.handle(run_id, raw)[1] is False
    accepted = offer_notes.take(run_id).accepted
    assert accepted is not None
    notes = accepted.content
    assert isinstance(notes, c.OfferNotesContent)
    assert " - " not in notes.summary
    assert notes.clauses[0].title == "Pay, monthly"
    assert notes.clauses[0].quote == "It is safe to sign - the employer"
    assert [record.field_path for record in accepted.corrections] == ["summary", "clauses.0.title"]


def test_a_verdict_in_a_clause_title_is_refused() -> None:
    run_id = uuid.uuid4()
    titled = {**NOTES["clauses"][0], "title": "Safe to sign"}
    answer, error = offer_notes.handle(run_id, {**NOTES, "clauses": [titled]})
    assert error
    assert "clauses.0.title" in answer


def test_a_long_field_with_a_dash_keeps_its_comma_up_to_its_own_limit() -> None:
    run_id = uuid.uuid4()
    plain = "a" * 600 + " - " + "b" * 100  # 703 characters, over a line but under OFFER_PLAIN_MAX
    raw = {**NOTES, "clauses": [{**NOTES["clauses"][0], "plain": plain}]}
    assert offer_notes.handle(run_id, raw)[1] is False
    accepted = offer_notes.take(run_id).accepted
    assert accepted is not None
    assert accepted.content.clauses[0].plain == "a" * 600 + ", " + "b" * 100  # type: ignore[union-attr]


def test_a_letter_longer_than_what_is_read_says_so(alex: Person) -> None:
    pytest.importorskip("claude_agent_sdk")
    from app.documents import offer_tools

    clause = "Annex. The employer may update the travel policy at any time. "
    long_text = (LETTER + clause * 500)[:31_000]
    assert len(long_text) == 31_000
    letter = upload(alex.factory, alex.user, long_text)
    read(alex, letter)
    with alex.factory() as db:
        found = offer_notes.offer_text(db, alex.user, {"upload_id": str(letter)})
        assert found is not None
        assert found.truncated
        assert (len(found.text), found.total) == (writer_base.UPLOAD_MAX_CHARS, 31_000)
        shown = offer_notes.view(db, alex.user, alex.posting)
        assert shown is not None
        assert shown.read_chars == writer_base.UPLOAD_MAX_CHARS
    told = offer_tools._letter(Scope(alex.factory, alex.user, binding={"upload_id": str(letter)}))
    assert told is not None
    data = json.loads(told)
    assert (data["truncated"], data["read_chars"], data["total_chars"]) == (True, 30_000, 31_000)
    assert '"truncated": true' in offer_notes.READER.system_prompt

    short = upload(alex.factory, alex.user, FRENCH)
    with alex.factory() as db:
        found = offer_notes.offer_text(db, alex.user, {"upload_id": str(short)})
        assert found is not None
        assert not found.truncated
    told = offer_tools._letter(Scope(alex.factory, alex.user, binding={"upload_id": str(short)}))
    assert told is not None
    assert json.loads(told)["truncated"] is False


def test_a_chat_change_whose_hook_was_lost_is_settled_by_the_next_read(alex: Person) -> None:
    document_id, _, run_id = read(alex, upload(alex.factory, alex.user, LETTER))
    execute(alex, run_id)
    with alex.factory() as db:
        posted = chat.post(db, alex.user, document_id, "Shorter.", at=AT, settings=alex.settings)
        db.commit()
    with alex.factory() as db:
        db.execute(
            sa.update(AgentRun)
            .where(AgentRun.id == posted.run_id)
            .values(status=RunStatus.failed, finished_at=AT)
        )
        db.commit()
        early = offer_notes.view(
            db, alex.user, alex.posting, AT + timedelta(seconds=chat.HOOK_GRACE_S - 1)
        )
        assert early is not None
        assert early.busy
        late = offer_notes.view(
            db, alex.user, alex.posting, AT + timedelta(seconds=chat.HOOK_GRACE_S + 1)
        )
        assert late is not None
        assert not late.busy
    assert generation_row(alex.factory, posted.generation.id).status is GenerationStatus.failed


def test_a_score_has_no_field_to_go_in() -> None:
    answer, error = offer_notes.handle(uuid.uuid4(), {**NOTES, "score": 8})
    assert error
    assert "score: unknown_field" in answer


def test_the_pay_is_compared_with_the_posting_and_the_pay_sought(alex: Person) -> None:
    with alex.factory() as db:
        db.execute(
            sa.text(
                "UPDATE jobs SET pay_min = 55000, pay_max = 65000, pay_currency = 'EUR', "
                "pay_period = 'year' WHERE id = :j"
            ),
            {"j": alex.posting},
        )
        sections = db.scalar(sa.select(Profile.sections).where(Profile.user_id == alex.user))
        search = dict(sections.get("search") or {})
        search["compensation"] = {"salary": {"min": 60000, "max": 70000, "currency": "EUR"}}
        db.execute(
            sa.update(Profile)
            .where(Profile.user_id == alex.user)
            .values(sections={**sections, "search": search})
        )
        db.commit()
        context = offer_notes.pay_context(db, alex.user, alex.posting)
    assert context is not None
    assert context["posting"]["pay"] == "55,000 to 65,000 EUR a year"
    assert context["pay_sought"] == ["60,000 to 70,000 EUR a year"]


def test_the_chat_changes_the_notes_and_answers(alex: Person) -> None:
    document_id, _, run_id = read(alex, upload(alex.factory, alex.user, LETTER))
    execute(alex, run_id)
    with alex.factory() as db:
        posted = chat.post(
            db,
            alex.user,
            document_id,
            "Add a question about when the pay starts.",
            at=AT,
            settings=alex.settings,
        )
        db.commit()
    assert run_row(alex.factory, posted.run_id).agent == offer_notes.CHAT
    execute(alex, posted.run_id)
    generation = generation_row(alex.factory, posted.generation.id)
    assert (generation.status, generation.mode) == (GenerationStatus.done, GenerationMode.chat)
    assert [v.origin for v in versions(alex.factory, document_id)] == [
        Origin.generated,
        Origin.chat,
    ]
    assert len(document_row(alex.factory, document_id).draft["questions"]) == 3
    with alex.factory() as db:
        said = db.scalars(
            sa.select(DocumentMessage)
            .where(DocumentMessage.document_id == document_id)
            .order_by(DocumentMessage.id)
        ).all()
    assert [(m.role, m.text) for m in said][1] == (
        Role.assistant,
        offer_notes.CHANGED.format(number=2),
    )


def test_the_chat_of_the_notes_needs_the_same_consent(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = consented(person(factory, migrated_url, USER_A))
    document_id, _, run_id = read(who, upload(factory, who.user, LETTER))
    execute(who, run_id)
    with factory() as db:
        db.execute(sa.text("DELETE FROM ingestion_consents WHERE user_id = :u"), {"u": who.user})
        db.commit()
    with factory() as db, pytest.raises(chat.ChatRefusedError) as refused:
        chat.post(db, who.user, document_id, "Shorter, please.", at=AT, settings=who.settings)
    assert refused.value.reason == "consent"


def test_the_letter_deleted_leaves_the_notes(alex: Person) -> None:
    letter = upload(alex.factory, alex.user, LETTER)
    document_id, _, run_id = read(alex, letter)
    execute(alex, run_id)
    kept = document_row(alex.factory, document_id).draft
    with alex.factory() as db:
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == letter))
        db.commit()
        shown = offer_notes.view(db, alex.user, alex.posting)
    assert shown is not None
    assert shown.letter is None
    assert document_row(alex.factory, document_id).draft == kept
    with alex.factory() as db:
        assert offer_notes.offer_text(db, alex.user, {"upload_id": str(letter)}) is None


def test_the_run_is_bound_to_its_own_notes_and_mode(alex: Person) -> None:
    _, generation_id, run_id = read(alex, upload(alex.factory, alex.user, LETTER))
    with alex.factory() as db:
        run = run_row(alex.factory, run_id)
        bound = offer_notes.bind_for(db, run, GenerationMode.posting)
        assert bound is not None
        assert bound["generation_id"] == str(generation_id)
        assert offer_notes.bind_for(db, run, GenerationMode.chat) is None
        run.job_id = uuid.uuid4()
        assert offer_notes.bind_for(db, run, GenerationMode.posting) is None


def test_the_letter_reaches_no_log_no_event_and_no_job(
    alex: Person, caplog: pytest.LogCaptureFixture
) -> None:
    planted = marker()
    caplog.set_level(logging.DEBUG)
    _, generation_id, run_id = read(
        alex, upload(alex.factory, alex.user, LETTER + f"\nPlanted line {planted}.\n")
    )
    execute(alex, run_id)
    assert generation_row(alex.factory, generation_id).status is GenerationStatus.done
    stored = everything_stored(alex.factory, run_id)
    assert planted not in stored
    assert "4,500 EUR" not in stored
    assert not find(
        [planted, "4,500 EUR"],
        logs=caplog.records,
        values=[job["args"] for job in alex.queue.jobs],
    )
