"""The lock order of the paths G12 adds, each pair run from two connections on real Postgres.

The order (tasks/ingestion/todo.md, "Lock order"): the `profiles` row (2), the
`ingestion_runs` row (3), `ingestion_documents` rows (4), `proposals` rows in id order (8). The
merge runs inside `advance`, so under the run's row (3), and takes proposals rows only (8): it
reads the profile and the documents without a lock. An accept and an undo take 2 then 8. A
delete of a document takes 4 today (G15 adds 3 before it). Every path goes up the list, so no
pair can wait for each other: each test holds the locks of one path in an open transaction,
starts the other in a thread, asserts that it waits (or that it does not), releases, and asserts
the end state. Nothing here sleeps to synchronise: a path that waits is observed through its
`done` event, as in `test_ingestion_locks.py`."""

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.crypto import Keyring
from app.agents.models import Proposal, ProposalStatus
from app.agents.models import RunStatus as AgentStatus
from app.config import Settings
from app.ingestion import deletion, engine, items
from app.ingestion.models import IngestionDocument
from app.ingestion.runs_models import IngestionRun, RunStatus
from app.profile.models import Profile
from tests.agents.run_helpers import add_user, now, save_credential
from tests.ingestion import engine_helpers as eh
from tests.ingestion import merge_helpers as mh
from tests.ingestion.extract_helpers import add_document, key, keyring, settings
from tests.ingestion.test_ingestion_locks import Path
from tests.ingestion.test_item_proposals import stored

_FIXTURES = (key, keyring, settings)
NAMED = ("identity", "full_name", "Alice Martin", "Alice Martin")
HEADLINE = ("identity", "headline", "Engineer", "an Engineer")
EMAIL = ("identity", "email", "alice@example.org", "alice@example.org")


@pytest.fixture
def alice(factory: sessionmaker[Session], keyring: Keyring) -> uuid.UUID:
    user = add_user(factory, "alice@example.org")
    save_credential(factory, keyring, user)
    return user


def about_to_merge(
    factory: sessionmaker[Session], user: uuid.UUID, settings: Settings
) -> tuple[uuid.UUID, uuid.UUID]:
    """A run over one document whose only step has ended and stored one item: the next
    `advance` closes it and merges. Answers the run and its document."""
    run_id, (document,) = mh.begin(factory, user, settings, [mh.LETTER])
    step = eh.running(factory, run_id)
    mh.store(factory, user, step, *HEADLINE)
    eh.end_agent(factory, step.agent_run_id, AgentStatus.done, None, 0, None)
    return run_id, document


def merging(factory: sessionmaker[Session], run_id: uuid.UUID, settings: Settings) -> Path:
    """The merge as `advance` runs it, in a thread, committed when it ends."""
    return Path(lambda: eh.advance(factory, run_id, settings).status)


def free(db: Session, column: Any, value: uuid.UUID) -> bool:
    """Whether no other transaction holds the row whose `column` is `value`."""
    found = db.scalar(sa.select(column).where(column == value).with_for_update(skip_locked=True))
    db.rollback()
    return found is not None


def status_of(factory: sessionmaker[Session], proposal_id: uuid.UUID) -> ProposalStatus:
    with factory() as db:
        return db.get_one(Proposal, proposal_id).status


def test_a_merge_racing_an_accept_of_an_older_item(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The accept came first: it holds the profile row (2) and the proposal row (8). The merge,
    under its run row (3), waits for that proposal row, then finds the item decided and leaves
    it: what the user accepted stays accepted."""
    first, _ = mh.merged(factory, alice, settings, [mh.CV], [[NAMED]])
    (older,) = mh.proposals_of(factory, alice, first)
    run_id, _ = about_to_merge(factory, alice, settings)
    with factory() as db:
        proposals.accept(db, alice, older.id, now())
        path = merging(factory, run_id, settings)
        path.waits()
        db.commit()
    assert path.ends() is RunStatus.done
    assert status_of(factory, older.id) is ProposalStatus.accepted
    assert stored(factory, alice, "identity")["full_name"] == "Alice Martin"
    assert [one.payload["key"] for one in mh.proposals_of(factory, alice, run_id)] == ["headline"]


def test_an_accept_of_an_older_item_racing_a_merge(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """The merge came first: it holds its run row (3) and the proposals it superseded (8), and
    never the profile row. The accept takes the profile row (2), waits for its proposal row,
    then finds the item superseded: nothing is written to the profile."""
    first, _ = mh.merged(factory, alice, settings, [mh.CV], [[NAMED]])
    (older,) = mh.proposals_of(factory, alice, first)
    run_id, _ = about_to_merge(factory, alice, settings)
    before = mh.revision(factory, alice)

    def accept() -> None:
        with factory() as other, other.begin():
            proposals.accept(other, alice, older.id, now())

    with factory() as db:
        assert engine.advance(db, run_id, now(), settings).status is RunStatus.done
        with factory() as probe:
            assert not free(probe, IngestionRun.id, run_id)  # the merge runs under the run's row
            assert not free(probe, Proposal.id, older.id)
            assert free(probe, Profile.user_id, alice)  # and never takes the profile row
        path = Path(accept)
        path.waits()
        db.commit()
    with pytest.raises(proposals.NotPendingError):
        path.ends()
    assert status_of(factory, older.id) is ProposalStatus.superseded
    assert mh.revision(factory, alice) == before
    assert stored(factory, alice, "identity")["full_name"] is None


def test_the_merge_supersedes_the_older_items_in_id_order(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """Two older pending items: with the one of the higher id held elsewhere, the merge waits
    holding the one of the lower id, which is the order an accept of several items takes."""
    first, _ = mh.merged(factory, alice, settings, [mh.CV], [[NAMED, EMAIL]])
    low, high = sorted(one.id for one in mh.proposals_of(factory, alice, first))
    run_id, _ = about_to_merge(factory, alice, settings)
    with factory() as db:
        db.execute(sa.select(Proposal.id).where(Proposal.id == high).with_for_update())
        path = merging(factory, run_id, settings)
        path.waits()
        with factory() as probe:
            assert not free(probe, Proposal.id, low)
        db.commit()
    assert path.ends() is RunStatus.done
    assert {status_of(factory, low), status_of(factory, high)} == {ProposalStatus.superseded}


def test_the_merge_does_not_wait_for_the_profile_row(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """A profile save or an accept in flight holds the profile row (2). The merge reads the
    profile without a lock, so it ends meanwhile: holding 3, it never asks for 2."""
    mh.save(factory, alice, "identity", {"full_name": "Alice Martin"})
    run_id, _ = about_to_merge(factory, alice, settings)
    with factory() as db:
        db.execute(sa.select(Profile.user_id).where(Profile.user_id == alice).with_for_update())
        assert merging(factory, run_id, settings).ends() is RunStatus.done
        db.commit()
    assert [one.payload["key"] for one in mh.proposals_of(factory, alice, run_id)] == ["headline"]


def test_a_merge_racing_a_document_delete(
    factory: sessionmaker[Session], alice: uuid.UUID, settings: Settings
) -> None:
    """Since G15 the delete takes the user's active run row (3) before its document (4). So a
    delete waits for a merge in flight and then strips what the merge wrote, and a merge waits
    for a delete in flight and then reads without the document."""
    unread = add_document(factory, alice, "A document no run has read.")
    run_id, read = about_to_merge(factory, alice, settings)

    def delete(document_id: uuid.UUID) -> bool:
        with factory() as other, other.begin():
            return deletion.delete(other, alice, document_id, now())

    with factory() as db:
        assert engine.advance(db, run_id, now(), settings).status is RunStatus.done
        path = Path(lambda: delete(read))
        path.waits()  # for the run row the merge holds
        db.commit()
    assert path.ends() is True
    # The only item cited the deleted document alone: pending, it is deleted with it.
    assert mh.proposals_of(factory, alice, run_id) == []
    with factory() as db:
        assert db.get(IngestionDocument, read) is None

    again, (cited,) = mh.begin(factory, alice, settings, [mh.LETTER])
    step = eh.running(factory, again)
    mh.store(factory, alice, step, *HEADLINE)
    eh.end_agent(factory, step.agent_run_id, AgentStatus.done, None, 0, None)
    with factory() as db:
        assert deletion.delete(db, alice, unread, now()) is True  # holds the run row of `again`
        path = merging(factory, again, settings)
        path.waits()
        db.commit()
    assert path.ends() is RunStatus.done
    (one,) = mh.proposals_of(factory, alice, again)
    assert {uuid.UUID(c["document_id"]) for c in one.payload["citations"]} == {cited}
    assert one.kind == items.KIND
