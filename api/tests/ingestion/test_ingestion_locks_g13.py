"""The lock order of the paths G13 adds, each pair run from two connections on real Postgres.

The order (tasks/ingestion/todo.md, "Lock order"): the `profiles` row (2), the `ingestion_runs`
row (3), `proposals` rows in id order (8). The bulk accept takes 2 then 8, like the accept of
one item; the bulk reject takes 8 only; the merge of a newer run, which supersedes the pending
items of the older one, runs under its run row (3) and takes 8. Every path goes up the list, so
no pair can wait for each other, and whoever comes second reads each proposal after the first
has committed and finds it decided: two tabs never apply one item twice, and a decision and a
rerun never both win.

Each test holds the locks of one path in an open transaction, starts the other in a thread,
asserts that it waits, releases, and asserts the end state. Nothing here sleeps to synchronise:
a path that waits is observed through its `done` event, as in `test_ingestion_locks.py`."""

import uuid
from collections.abc import Sequence

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.crypto import Keyring
from app.agents.models import Proposal, ProposalStatus
from app.config import Settings
from app.ingestion import engine, review
from app.ingestion.runs_models import IngestionRun, RunStatus
from app.profile.models import Profile
from tests.agents.run_helpers import add_user, now, save_credential
from tests.ingestion import merge_helpers as mh
from tests.ingestion.extract_helpers import key, keyring, settings
from tests.ingestion.test_ingestion_locks import Path
from tests.ingestion.test_ingestion_locks_g12 import about_to_merge, free, merging, status_of
from tests.ingestion.test_item_proposals import revisions, stored

_FIXTURES = (key, keyring, settings)
Factory = sessionmaker[Session]
TRAVEL = ("answers", "travel", "Sometimes", "Alice Martin")
START = ("answers", "earliest_start", "In January", "June 2023")
NOT_PENDING = "not_pending"


@pytest.fixture
def user(factory: Factory, keyring: Keyring) -> uuid.UUID:
    made = add_user(factory, "e2e-g13-locks@example.org")
    save_credential(factory, keyring, made)
    return made


def reviewed(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A finished run with two pending items of the answers section. Answers the run and the
    ids of its proposals, in id order."""
    run_id, _ = mh.merged(factory, user, settings, [mh.CV], [[TRAVEL, START]])
    found = [one.id for one in mh.proposals_of(factory, user, run_id)]
    assert len(found) == 2
    return run_id, found


def accepting(
    factory: Factory, user: uuid.UUID, run_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Path:
    """The bulk accept as its route runs it, in a thread, committed when it ends."""

    def work() -> review.Decided:
        with factory() as db, db.begin():
            return review.accept_section(db, user, run_id, "answers", ids, now())

    return Path(work)


def rejecting(
    factory: Factory, user: uuid.UUID, run_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> Path:
    def work() -> review.Decided:
        with factory() as db, db.begin():
            return review.reject_section(db, user, run_id, "answers", ids, now())

    return Path(work)


def test_two_tabs_accepting_the_same_section(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The first tab holds the profile row (2) and the proposals (8). The second waits for the
    profile row, then finds every item decided: one save, one revision, never two."""
    run_id, ids = reviewed(factory, user, settings)
    with factory() as db:
        first = review.accept_section(db, user, run_id, "answers", ids, now())
        assert (sorted(first.done), first.revision) == (ids, 1)
        with factory() as probe:
            assert not free(probe, Profile.user_id, user)
            assert not free(probe, Proposal.id, ids[0])
            assert free(probe, IngestionRun.id, run_id)  # the run row is never taken
        path = accepting(factory, user, run_id, ids)
        path.waits()
        db.commit()
    second = path.ends()
    assert (second.done, second.revision) == ([], None)
    assert second.skipped == dict.fromkeys(ids, NOT_PENDING)
    assert revisions(factory, user) == [(1, "answers")]
    assert stored(factory, user, "answers")["travel"] == "Sometimes"


def test_one_tab_accepting_an_item_and_another_the_whole_section(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The single accept came first: the bulk accept waits, leaves that item out as decided and
    applies the other, as a revision of its own."""
    run_id, ids = reviewed(factory, user, settings)
    with factory() as db:
        proposals.accept(db, user, ids[0], now())
        path = accepting(factory, user, run_id, ids)
        path.waits()
        db.commit()
    done = path.ends()
    assert (done.done, done.skipped, done.revision) == ([ids[1]], {ids[0]: NOT_PENDING}, 2)
    assert revisions(factory, user) == [(1, "answers"), (2, "answers")]
    assert {status_of(factory, one) for one in ids} == {ProposalStatus.accepted}


def test_the_whole_section_accepted_in_one_tab_and_one_item_in_another(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The bulk accept came first: the single accept waits, then is refused as decided."""
    run_id, ids = reviewed(factory, user, settings)

    def accept() -> None:
        with factory() as other, other.begin():
            proposals.accept(other, user, ids[1], now())

    with factory() as db:
        review.accept_section(db, user, run_id, "answers", ids, now())
        path = Path(accept)
        path.waits()
        db.commit()
    with pytest.raises(proposals.NotPendingError):
        path.ends()
    assert revisions(factory, user) == [(1, "answers")]


def test_a_bulk_accept_racing_the_supersede_of_a_rerun(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The merge of the newer run came first: it holds its run row (3) and the proposals it
    superseded (8), never the profile row. The bulk accept takes the profile row (2), waits for
    the proposals, then finds them superseded: nothing is written to the profile."""
    older, ids = reviewed(factory, user, settings)
    run_id, _ = about_to_merge(factory, user, settings)
    before = mh.revision(factory, user)
    with factory() as db:
        assert engine.advance(db, run_id, now(), settings).status is RunStatus.done
        with factory() as probe:
            assert not free(probe, Proposal.id, ids[0])
            assert free(probe, Profile.user_id, user)
        path = accepting(factory, user, older, ids)
        path.waits()
        db.commit()
    done = path.ends()
    assert (done.done, done.revision) == ([], None)
    assert done.skipped == dict.fromkeys(ids, NOT_PENDING)
    assert {status_of(factory, one) for one in ids} == {ProposalStatus.superseded}
    assert mh.revision(factory, user) == before
    assert stored(factory, user, "answers")["travel"] is None


def test_the_supersede_of_a_rerun_racing_a_bulk_accept(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The bulk accept came first: the merge waits for the proposals rows, then finds the items
    decided and leaves them. What the user accepted stays accepted."""
    older, ids = reviewed(factory, user, settings)
    run_id, _ = about_to_merge(factory, user, settings)
    with factory() as db:
        review.accept_section(db, user, older, "answers", ids, now())
        path = merging(factory, run_id, settings)
        path.waits()
        db.commit()
    assert path.ends() is RunStatus.done
    assert {status_of(factory, one) for one in ids} == {ProposalStatus.accepted}
    held = stored(factory, user, "answers")
    assert (held["travel"], held["earliest_start"]) == ("Sometimes", "In January")
    assert [one.payload["key"] for one in mh.proposals_of(factory, user, run_id)] == ["headline"]


def test_a_bulk_reject_racing_the_supersede_of_a_rerun(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """Both take proposals rows only, in id order. The merge came first: the reject waits, then
    finds the items superseded and rejects none."""
    older, ids = reviewed(factory, user, settings)
    run_id, _ = about_to_merge(factory, user, settings)
    with factory() as db:
        assert engine.advance(db, run_id, now(), settings).status is RunStatus.done
        path = rejecting(factory, user, older, ids)
        path.waits()
        db.commit()
    done = path.ends()
    assert (done.done, done.skipped) == ([], dict.fromkeys(ids, NOT_PENDING))
    assert {status_of(factory, one) for one in ids} == {ProposalStatus.superseded}


def test_accept_all_in_one_tab_after_reject_in_another(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The reject came first: it holds its proposal (8) and never the profile row. The accept
    takes the profile row, waits for that proposal, then finds it rejected and applies the
    other one alone."""
    run_id, ids = reviewed(factory, user, settings)
    with factory() as db:
        review.reject_section(db, user, run_id, "answers", ids[:1], now())
        with factory() as probe:
            assert not free(probe, Proposal.id, ids[0])
            assert free(probe, Profile.user_id, user)
        path = accepting(factory, user, run_id, ids)
        path.waits()
        db.commit()
    done = path.ends()
    assert (done.done, done.skipped, done.revision) == ([ids[1]], {ids[0]: NOT_PENDING}, 1)
    assert status_of(factory, ids[0]) is ProposalStatus.rejected
    assert status_of(factory, ids[1]) is ProposalStatus.accepted
    assert revisions(factory, user) == [(1, "answers")]


def test_reject_the_rest_in_one_tab_after_accept_all_in_another(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The accept came first: the reject waits for the proposals, then finds them accepted and
    rejects none. What was written to the profile stays."""
    run_id, ids = reviewed(factory, user, settings)
    with factory() as db:
        review.accept_section(db, user, run_id, "answers", ids, now())
        path = rejecting(factory, user, run_id, ids)
        path.waits()
        db.commit()
    done = path.ends()
    assert (done.done, done.skipped) == ([], dict.fromkeys(ids, NOT_PENDING))
    assert {status_of(factory, one) for one in ids} == {ProposalStatus.accepted}
    assert revisions(factory, user) == [(1, "answers")]


def test_a_bulk_reject_does_not_wait_for_the_profile_row(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """Another tab is saving the profile: the reject touches no profile row, so it goes
    through at once."""
    run_id, ids = reviewed(factory, user, settings)
    mh.revision(factory, user)  # the profile row exists
    with factory() as db:
        db.execute(sa.select(Profile.user_id).where(Profile.user_id == user).with_for_update())
        with factory() as other, other.begin():
            done = review.reject_section(other, user, run_id, "answers", ids, now(), wait_ms=1)
        assert sorted(done.done) == ids
        db.rollback()
    assert {status_of(factory, one) for one in ids} == {ProposalStatus.rejected}


def test_a_lock_that_is_not_free_in_time_is_busy_and_applies_nothing(
    factory: Factory, user: uuid.UUID, settings: Settings
) -> None:
    """The lock timeout is Postgres' own, set for the transaction: no clock is read here. The
    first tab holds; the second gives up instead of waiting for ever, with nothing applied."""
    run_id, ids = reviewed(factory, user, settings)
    with factory() as db:
        usual = db.scalar(sa.text("select current_setting('lock_timeout')"))
        db.rollback()
        review.accept_section(db, user, run_id, "answers", ids, now())
        for decide in (review.accept_section, review.reject_section):
            with factory() as other, pytest.raises(review.ReviewBusyError):
                decide(other, user, run_id, "answers", ids, now(), wait_ms=1)
        db.rollback()
    assert {status_of(factory, one) for one in ids} == {ProposalStatus.pending}
    assert revisions(factory, user) == []
    # The timeout was the transaction's: the next one on a pooled connection waits as usual.
    with factory() as db, db.begin():
        assert db.scalar(sa.text("select current_setting('lock_timeout')")) == usual
