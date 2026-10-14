"""Deleting a document (G15): what goes, what stays, who waits for whom, and what reruns remember.

The lock order (tasks/ingestion/todo.md, "Lock order"): the delete takes the user's active run
row (3), the document row (4), the rows of its steps at work (5), their agent runs (7), then the
proposals that cite it in id order (8). Each race holds the locks of one path in an open
transaction, starts the other in a thread, asserts that it waits, releases, and asserts the end
state; nothing sleeps to synchronise (`Path` of `test_ingestion_locks.py`).

What a delete removes is counted on every table whose name starts with `ingestion_`, listed by
reflection, so a table a later task adds fails here until its expectation is written down."""

import asyncio
import hashlib
import json
import secrets
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents import runs as agent_runs
from app.agents.crypto import Keyring
from app.agents.models import AgentEvent, AgentRun, Proposal, ProposalStatus
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.identity.models import User
from app.ingestion import agents, deletion, engine, estimate_router, items, merge, review
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRun, IngestionRunStep, RunStatus, StepStatus
from app.profile import service as profile_service
from tests.agents.g6_helpers import run_context
from tests.agents.run_helpers import add_user, execute, now, save_credential
from tests.identity.api_helpers import Api
from tests.ingestion import engine_helpers as eh
from tests.ingestion import journey_streams
from tests.ingestion import merge_helpers as mh
from tests.ingestion.conftest import signed_in
from tests.ingestion.extract_helpers import add_document, key, keyring, settings
from tests.ingestion.helpers import delete as api_delete
from tests.ingestion.helpers import user_id
from tests.ingestion.test_ingestion_locks import Path
from tests.ingestion.test_ingestion_locks_g12 import free

_FIXTURES = (key, keyring, settings)
Factory = sessionmaker[Session]
NAMED = ("identity", "full_name", "Alice Martin", "Alice Martin")
RENAMED = ("identity", "full_name", "Alice Marie Martin", "Alice Martin")
EMAIL = ("identity", "email", "alice@example.org", "alice@example.org")
HEADLINE = ("identity", "headline", "Engineer", "an Engineer")
TRAVEL = ("answers", "travel", "Sometimes", "Alice Martin")
START = ("answers", "earliest_start", "In January", "Alice Martin")
EMPLOYER = ("experience", "employers", mh.employer("2021-03", "2023-06"), "March 2021")


@pytest.fixture
def alice(factory: Factory, keyring: Keyring) -> uuid.UUID:
    made = add_user(factory, "e2e-g15-alice@example.org")
    save_credential(factory, keyring, made)
    return made


def marked(text: str) -> tuple[str, str]:
    """A text with a random token of its own in it: the token is found nowhere else."""
    token = f"tok{secrets.token_hex(8)}"
    return f"{text}Reference {token}.\n", token


def counts(factory: Factory) -> dict[str, int]:
    """Rows of every ingestion table, of the events and of the profile items."""
    with factory() as db:
        names = sorted(
            name
            for name in sa.inspect(db.get_bind()).get_table_names()
            if name.startswith("ingestion_")
        )
        found = {
            name: int(db.scalar(sa.text(f'select count(*) from "{name}"')) or 0) for name in names
        }
        found["agent_events"] = int(db.scalar(sa.select(sa.func.count()).select_from(AgentEvent)))
        found["proposals"] = int(db.scalar(sa.select(sa.func.count()).select_from(Proposal)))
        return found


def event(factory: Factory, agent_run_id: uuid.UUID | None, data: dict[str, Any]) -> None:
    """One event as the runner writes it (the model's words, which may quote the document)."""
    assert agent_run_id is not None
    with factory() as db, db.begin():
        seq = agent_runs.next_seq(db, agent_run_id)
        agent_runs.append_event(db, agent_run_id, seq, "text", data, now())


def delete(factory: Factory, user: uuid.UUID, document_id: uuid.UUID) -> bool:
    with factory() as db, db.begin():
        return deletion.delete(db, user, document_id, now())


def everything(factory: Factory) -> str:
    """Every proposal, event, step and run as text, for a search of a planted token."""
    with factory() as db:
        rows: list[Any] = [one.payload for one in db.scalars(sa.select(Proposal))]
        rows += [one.data for one in db.scalars(sa.select(AgentEvent))]
        rows += [
            [str(one.id), one.status.value, one.tokens_spent, one.document_ids]
            for one in db.scalars(sa.select(IngestionRun))
        ]
        return json.dumps(rows, default=str)


def by_key(found: list[Proposal]) -> dict[str, Proposal]:
    return {one.payload["key"]: one for one in found}


def decide(
    factory: Factory, user: uuid.UUID, accept: list[uuid.UUID], reject: list[uuid.UUID]
) -> None:
    with factory() as db, db.begin():
        for one in accept:
            assert proposals.accept(db, user, one, now()) is not None
        for one in reject:
            assert proposals.reject(db, user, one, now()) is not None


# --- what goes and what stays ---------------------------------------------------------------------


def test_the_pending_items_lose_the_document_and_every_table_says_what_went(
    factory: Factory, alice: uuid.UUID, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    cv, token = marked(mh.CV)
    run_id, (first, second) = mh.begin(factory, alice, settings, [cv, mh.LETTER])
    with factory() as db, db.begin():  # the file name holds a token of its own too
        db.execute(
            sa.update(IngestionDocument)
            .where(IngestionDocument.id == first)
            .values(name=f"{token}.txt")
        )
    step = eh.running(factory, run_id)
    event(factory, step.agent_run_id, {"text": f"It says {token}."})
    mh.finish(
        factory, alice, settings, run_id, [first, second], [[NAMED, EMAIL], [NAMED, HEADLINE]]
    )
    before = counts(factory)
    quoted = eh.events_of(factory, step.agent_run_id)
    assert quoted >= 1
    pending = by_key(mh.proposals_of(factory, alice, run_id))
    assert len(pending["full_name"].payload["citations"]) == 2
    revision = mh.revision(factory, alice)

    with caplog.at_level("DEBUG"):
        assert delete(factory, alice, first) is True
    after = counts(factory)

    # Every table, by name: a table a later task adds has to be written down here.
    expected = dict(before)
    expected["ingestion_documents"] -= 1
    expected["ingestion_items"] -= 2  # the document's two items, by the foreign key
    expected["agent_events"] -= quoted  # every event of the step that read it
    expected["proposals"] -= 1  # the email had no other source
    assert after == expected
    assert set(before) >= {
        "ingestion_documents",
        "ingestion_images",
        "ingestion_items",
        "ingestion_runs",
        "ingestion_run_steps",
        "ingestion_consents",
    }
    with factory() as db:  # the stored file left with its row
        assert db.get(IngestionDocument, first) is None
    kept = by_key(mh.proposals_of(factory, alice, run_id))
    assert set(kept) == {"full_name", "headline"}
    assert [one["document_id"] for one in kept["full_name"].payload["citations"]] == [str(second)]
    assert kept["headline"].payload == pending["headline"].payload
    # The run and its steps stay, with their statuses and tokens; the profile is untouched.
    assert eh.statuses(factory, run_id) == ["done", "done"]
    assert eh.run_of(factory, run_id).document_ids == [first, second]
    assert mh.revision(factory, alice) == revision
    # Nothing of the document is left, and nothing of it reached a log.
    assert token not in everything(factory)
    logged = caplog.text + "".join(json.dumps(vars(one), default=str) for one in caplog.records)
    assert token not in logged
    assert "document deleted" in [one.getMessage() for one in caplog.records]


def test_decided_items_keep_what_reruns_need_and_a_rerun_remembers(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    """Accepted: the value stays, its citation says "source deleted". Rejected and superseded:
    section, key and value hash only. Then the same file uploaded again and read again: the
    rejected value is not proposed, the accepted one is equal to the profile, a changed value
    is proposed, and a value that was only superseded comes back."""
    cv, token = marked(mh.CV)
    run_id, (document,) = mh.merged(factory, alice, settings, [cv], [[NAMED, EMAIL, TRAVEL]])
    found = by_key(mh.proposals_of(factory, alice, run_id))
    decide(factory, alice, [found["email"].id], [found["full_name"].id])
    mh.merged(factory, alice, settings, [mh.LETTER], [[HEADLINE]])
    assert mh.proposals_of(factory, alice, run_id) == []  # travel was superseded by the newer run
    revision = mh.revision(factory, alice)

    assert delete(factory, alice, document) is True

    with factory() as db:
        email, named, travel = (
            db.get_one(Proposal, found[name].id) for name in ("email", "full_name", "travel")
        )
    assert email.status is ProposalStatus.accepted
    assert email.payload["value"] == "alice@example.org"
    assert email.payload["citations"] == [{"document_id": str(document), "source_deleted": True}]
    for decided in (named, travel):
        assert set(decided.payload) == {"section", "key", "action", "value_hash"}
    assert named.payload["value_hash"] == found["full_name"].payload["value_hash"]
    assert travel.status is ProposalStatus.superseded
    assert token not in everything(factory)
    assert mh.revision(factory, alice) == revision  # nothing accepted is taken back

    again, _ = mh.merged(factory, alice, settings, [cv], [[NAMED, RENAMED, EMAIL, TRAVEL]])
    proposed = by_key(mh.proposals_of(factory, alice, again))
    assert sorted(proposed) == ["full_name", "travel"]
    assert proposed["full_name"].payload["value"] == "Alice Marie Martin"  # changed: proposed
    assert "conflict" not in proposed["full_name"].payload  # the rejected one stays out


def fixture(factory: Factory, user: uuid.UUID, name: str) -> uuid.UUID:
    """A stored document with the bytes of a journey fixture, so its step replays the recorded
    stream named after its hash (tests/ingestion/journey_streams.py)."""
    data = (journey_streams.FIXTURES / name).read_bytes()
    return add_document(factory, user, data.decode("utf-8"), digest=hashlib.sha256(data).digest())


def replayed(
    factory: Factory,
    user: uuid.UUID,
    settings: Settings,
    keyring: Keyring,
    documents: list[uuid.UUID],
) -> tuple[uuid.UUID, list[AgentRun]]:
    """A whole run through the real runner under the fake backend, each step replaying its
    recorded stream and its end calling the hook. Answers the run and the agent runs it made."""
    fake = settings.model_copy(update={"agents_fake_backend": True})
    before = {one.id for one in eh.agent_runs_of(factory, user)}
    run_id = eh.start(factory, user, documents)
    queue: list[uuid.UUID] = []

    async def enqueue(agent_run_id: uuid.UUID) -> None:
        queue.append(agent_run_id)

    asyncio.run(engine.drive(factory, enqueue, run_id, now, fake))
    context = run_context(
        fake, factory, keyring, agents.EXTRACT, services={engine.ENQUEUE_SERVICE: enqueue}
    )
    while queue:
        assert execute(context, queue.pop(0)) is AgentStatus.done
    assert eh.run_of(factory, run_id).status is RunStatus.done
    return run_id, [one for one in eh.agent_runs_of(factory, user) if one.id not in before]


def proposed(factory: Factory, user: uuid.UUID, run_id: uuid.UUID) -> list[dict[str, Any]]:
    """The pending items of a run as they read, without the id of the run that proposed them,
    by key (the merge may insert them in another order)."""
    found = [
        {name: value for name, value in one.payload.items() if name != "ingestion_run_id"}
        for one in mh.proposals_of(factory, user, run_id)
    ]
    return sorted(found, key=lambda one: str(one["key"]))


def test_the_same_documents_run_twice_propose_the_same_items_without_a_model_call(
    factory: Factory, alice: uuid.UUID, settings: Settings, keyring: Keyring
) -> None:
    documents = [
        fixture(factory, alice, "g12_first.txt"),
        fixture(factory, alice, "g12_second.txt"),
    ]
    first, made = replayed(factory, alice, settings, keyring, documents)
    assert len(made) == 2
    assert eh.run_of(factory, first).tokens_spent > 0
    before = proposed(factory, alice, first)
    assert {one["section"] for one in before} >= {"identity", "experience"}

    second, made = replayed(factory, alice, settings, keyring, documents)
    assert made == []  # no agent run, so no model call
    assert eh.statuses(factory, second) == ["reused", "reused"]
    assert eh.run_of(factory, second).tokens_spent == 0
    assert proposed(factory, alice, second) == before
    assert mh.proposals_of(factory, alice, first) == []  # the older ones were superseded


def test_one_document_added_runs_only_its_own_step(
    factory: Factory, alice: uuid.UUID, settings: Settings, keyring: Keyring
) -> None:
    read = fixture(factory, alice, "g12_first.txt")
    first, made = replayed(factory, alice, settings, keyring, [read])
    assert len(made) == 1
    names = {one["key"] for one in proposed(factory, alice, first)}

    added = fixture(factory, alice, "g12_second.txt")
    second, made = replayed(factory, alice, settings, keyring, [read, added])
    assert eh.statuses(factory, second) == ["reused", "done"]
    (one,) = made
    (step,) = [step for step in eh.steps_of(factory, second) if step.document_id == added]
    assert step.agent_run_id == one.id
    assert one.input["script"].startswith(f"ingest_extract_{bytes(step.sha256).hex()[:12]}")
    run = eh.run_of(factory, second)
    assert run.tokens_spent == (one.input_tokens or 0) + (one.output_tokens or 0) > 0
    # What the added document says is merged with what the reused step had stored.
    assert {one["key"] for one in proposed(factory, alice, second)} >= names


def test_a_rejected_conflict_is_remembered_with_each_of_its_values(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    run_id, (cv, _) = mh.merged(factory, alice, settings, [mh.CV, mh.LETTER], [[NAMED], [RENAMED]])
    (conflict,) = mh.proposals_of(factory, alice, run_id)
    assert conflict.payload["conflict"]
    decide(factory, alice, [], [conflict.id])
    assert delete(factory, alice, cv) is True
    with factory() as db:
        kept = db.get_one(Proposal, conflict.id).payload
    assert set(kept) == {"section", "key", "action", "value_hash", "conflict"}
    assert kept["conflict"] == [{"value_hash": conflict.payload["conflict"][0]["value_hash"]}]
    again, _ = mh.merged(factory, alice, settings, [mh.CV], [[NAMED, RENAMED]])
    assert mh.proposals_of(factory, alice, again) == []  # neither value comes back


def test_a_pending_conflict_whose_first_value_loses_its_only_source(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    run_id, (cv, letter) = mh.merged(
        factory, alice, settings, [mh.CV, mh.LETTER], [[NAMED], [RENAMED]]
    )
    (conflict,) = mh.proposals_of(factory, alice, run_id)
    first = conflict.payload["citations"][0]["document_id"]
    assert delete(factory, alice, uuid.UUID(first)) is True
    (kept,) = mh.proposals_of(factory, alice, run_id)
    assert kept.id == conflict.id
    assert "conflict" not in kept.payload
    assert "conflict" not in kept.payload["flags"]
    assert {one["document_id"] for one in kept.payload["citations"]} == {str(cv), str(letter)} - {
        first
    }


def test_the_derived_years_go_with_the_employer_they_counted(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    run_id, (cv,) = mh.merged(factory, alice, settings, [mh.CV], [[EMPLOYER]])
    assert merge.YEARS_KEY in by_key(mh.proposals_of(factory, alice, run_id))
    assert delete(factory, alice, cv) is True
    assert mh.proposals_of(factory, alice, run_id) == []


def test_a_locked_accepted_item_keeps_its_value_and_says_source_deleted(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    run_id, (cv, letter) = mh.merged(
        factory, alice, settings, [mh.CV, mh.LETTER], [[NAMED], [NAMED]]
    )
    (named,) = mh.proposals_of(factory, alice, run_id)
    decide(factory, alice, [named.id], [])
    assert delete(factory, alice, cv) is True
    with factory() as db:
        kept = db.get_one(Proposal, named.id).payload
        profile = profile_service.get_profile(db, alice, now())
        assert items.read("identity", profile.sections["identity"], "full_name") == "Alice Martin"
    assert kept["citations"][0] == {"document_id": str(cv), "source_deleted": True}
    assert kept["citations"][1]["document_id"] == str(letter)
    assert kept["citations"][1]["excerpt"] == "Alice Martin"


# --- a step at work ------------------------------------------------------------------------------


def test_a_document_deleted_during_its_own_step_leaves_no_event(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    cv, token = marked(mh.CV)
    run_id, (document, other) = mh.begin(factory, alice, settings, [cv, mh.LETTER])
    step = eh.running(factory, run_id)
    eh.set_agent(factory, step.agent_run_id, status=AgentStatus.running)
    event(factory, step.agent_run_id, {"text": token})
    assert delete(factory, alice, document) is True
    with factory() as db:
        agent = db.get_one(AgentRun, step.agent_run_id)
        assert agent.cancel_requested_at is not None
        assert db.get_one(IngestionRunStep, step.id).purge_events is True
    assert eh.events_of(factory, step.agent_run_id) == 0
    # The runner writes once more before it stops, then ends the run cancelled.
    event(factory, step.agent_run_id, {"text": token})
    eh.end_agent(factory, step.agent_run_id, AgentStatus.cancelled)
    eh.advance(factory, run_id, settings)
    assert eh.events_of(factory, step.agent_run_id) == 0
    assert eh.statuses(factory, run_id)[0] == StepStatus.failed.value  # the chain goes on
    assert eh.running(factory, run_id).document_id == other
    # A write that lands after the end is purged by the next tick of the cron.
    event(factory, step.agent_run_id, {"text": token})
    with factory() as db, db.begin():
        assert engine.purge(db, now()) == 1
    assert eh.events_of(factory, step.agent_run_id) == 0
    assert token not in everything(factory)


# --- races, two connections each ----------------------------------------------------------------


def test_a_delete_and_a_run_start_on_the_same_document(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    """The delete holds the document (4): the start waits, then finds it gone and refuses."""
    (document,) = eh.documents(factory, alice, 1)
    with factory() as db, db.begin():
        quote = estimate_router.quote(db, alice, [str(document)], [])

    def start() -> uuid.UUID:
        with factory() as db, db.begin():
            return engine.create(db, alice, quote, now()).id

    with factory() as db:
        assert deletion.delete(db, alice, document, now()) is True
        path = Path(start)
        path.waits()
        db.commit()
    with pytest.raises(engine.TextChangedError):
        path.ends()
    assert eh.agent_runs_of(factory, alice) == []


def test_a_run_start_and_then_a_delete_of_its_document(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    """The start holds its run row and its documents `FOR KEY SHARE`: the delete waits, then
    finds the run and its planned step, which fails without an agent run."""
    selected = eh.documents(factory, alice, 2)
    with factory() as db, db.begin():
        quote = estimate_router.quote(db, alice, [str(one) for one in selected], [])
    with factory() as db:
        run = engine.create(db, alice, quote, now())
        path = Path(lambda: delete(factory, alice, selected[1]))
        path.waits()
        db.commit()
    assert path.ends() is True
    with factory() as db:
        assert free(db, IngestionRun.id, run.id)
    eh.advance(factory, run.id, settings)
    eh.finish_step(factory, run.id, settings)
    assert eh.statuses(factory, run.id) == ["done", "failed"]
    assert len(eh.agent_runs_of(factory, alice)) == 1


def test_a_delete_while_a_bulk_accept_holds_its_proposals(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    """The bulk accept holds the profile (2) and the proposals (8): the delete waits for the
    proposals, then finds them accepted and marks their citations."""
    run_id, (cv,) = mh.merged(factory, alice, settings, [mh.CV], [[TRAVEL, START]])
    ids = [one.id for one in mh.proposals_of(factory, alice, run_id)]
    with factory() as db:
        done = review.accept_section(db, alice, run_id, "answers", ids, now())
        assert sorted(done.done) == sorted(ids)
        path = Path(lambda: delete(factory, alice, cv))
        path.waits()
        db.commit()
    assert path.ends() is True
    accepted = mh.proposals_of(factory, alice, run_id, ProposalStatus.accepted)
    assert len(accepted) == 2
    assert all(one.payload["citations"][0].get("source_deleted") for one in accepted)


def test_a_bulk_accept_after_a_delete_in_flight_finds_nothing_to_accept(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    run_id, (cv,) = mh.merged(factory, alice, settings, [mh.CV], [[TRAVEL, START]])
    ids = [one.id for one in mh.proposals_of(factory, alice, run_id)]

    def accept() -> review.Decided:
        with factory() as db, db.begin():
            return review.accept_section(db, alice, run_id, "answers", ids, now())

    with factory() as db:
        assert deletion.delete(db, alice, cv, now()) is True
        path = Path(accept)
        path.waits()
        db.commit()
    decided = path.ends()
    assert decided.done == []
    assert decided.revision is None
    assert mh.proposals_of(factory, alice, None, None) == []


def test_two_deletes_of_one_document(factory: Factory, alice: uuid.UUID) -> None:
    document = add_document(factory, alice, "A short note.")
    with factory() as db:
        assert deletion.delete(db, alice, document, now()) is True
        path = Path(lambda: delete(factory, alice, document))
        path.waits()
        db.commit()
    assert path.ends() is False


def test_a_lock_held_too_long_deletes_nothing(
    factory: Factory, alice: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = add_document(factory, alice, "A short note.")
    monkeypatch.setattr(deletion, "LOCK_WAIT_MS", 1)
    with factory() as db, db.begin():
        db.execute(
            sa.select(IngestionDocument.id)
            .where(IngestionDocument.id == document)
            .with_for_update(key_share=True)
        )
        with pytest.raises(deletion.DeletionBusyError) as raised:
            delete(factory, alice, document)
    assert str(raised.value) == ""
    with factory() as db:
        assert db.get(IngestionDocument, document) is not None


# --- who may delete -------------------------------------------------------------------------------


def test_another_users_document_answers_as_if_it_did_not_exist(factory: Factory, api: Api) -> None:
    owner = signed_in(factory, api, "e2e-g15-owner@example.org")
    other = signed_in(factory, api, "e2e-g15-other@example.org")
    mine = add_document(factory, user_id(factory, "e2e-g15-owner@example.org"), "Mine.")
    theirs = api_delete(api, other, str(mine))
    unknown = api_delete(api, other, str(uuid.uuid4()))
    assert (theirs.status_code, theirs.json()) == (unknown.status_code, unknown.json())
    assert theirs.status_code == 404
    with factory() as db:
        assert db.get(IngestionDocument, mine) is not None
    assert api_delete(api, owner, str(mine)).status_code == 204


def test_deleting_the_account_leaves_nothing_of_the_module(
    factory: Factory, alice: uuid.UUID, settings: Settings
) -> None:
    run_id, _ = mh.merged(factory, alice, settings, [mh.CV], [[NAMED, EMAIL]])
    step = eh.steps_of(factory, run_id)[0]
    event(factory, step.agent_run_id, {"text": "a word"})
    with factory() as db, db.begin():
        db.execute(sa.delete(User).where(User.id == alice))
    assert {name: count for name, count in counts(factory).items() if count} == {}
