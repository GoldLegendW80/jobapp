"""Profile items as proposals (app/ingestion/item_proposals.py and the dispatch of
app/agents/proposals.py) against a real Postgres: what `propose` stores, drops, merges and
supersedes; accept as exactly one revision, onto a profile that changed since, stale, in
conflict, invalid, with the user's own value; undo; accept all and reject all; isolation between
users; two sessions at once; and a walk over every kind of proposal. Synthetic data only."""

import inspect
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents import proposals
from app.agents.models import Proposal, ProposalDecider, ProposalStatus
from app.ingestion import item_proposals as ip
from app.ingestion import items
from app.jobs.models import Job
from app.profile import schemas
from app.profile import service as profile_service
from app.profile.models import Profile, ProfileRevision
from tests.agents.run_helpers import add_job, add_user, now
from tests.ingestion.test_items import MARKER, SAMPLES, holds
from tests.profile.conftest import FULL, full

DOCUMENT = uuid.UUID("00000000-0000-4000-8000-000000000051")
RUN = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
OTHER_RUN = uuid.UUID("00000000-0000-4000-8000-0000000000a2")
GERMAN = {"name": "German", "level": "B1"}
BLOCK = {"title": "Side projects", "body": "A made up project."}

_ticks = iter(range(1, 1_000_000))


def tick() -> datetime:
    """A later instant at every call: the order of two proposals is the order they were made."""
    return now() + timedelta(seconds=next(_ticks))


def cite(start: int = 0, excerpt: str = "An excerpt.", **more: Any) -> dict[str, Any]:
    return {
        "document_id": str(DOCUMENT),
        "excerpt": excerpt,
        "start": start,
        "end": start + len(excerpt),
        **more,
    }


@pytest.fixture
def alice(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "alice@example.org")


@pytest.fixture
def bob(factory: sessionmaker[Session]) -> uuid.UUID:
    return add_user(factory, "bob@example.org")


def save(factory: sessionmaker[Session], user: uuid.UUID, section: str, data: Any) -> int:
    """Save one section as the profile page does. Returns the new revision."""
    with factory() as db, db.begin():
        profile = profile_service.get_profile(db, user, now())
        valid = schemas.validate_section(section, data)
        saved = profile_service.save_section(db, user, section, valid, profile.revision, now())
        return int(saved.revision)


def save_all(factory: sessionmaker[Session], user: uuid.UUID) -> int:
    return [save(factory, user, section, full(section)) for section in FULL][-1]


def stored(factory: sessionmaker[Session], user: uuid.UUID, section: str) -> dict[str, Any]:
    with factory() as db:
        profile = profile_service.get_profile(db, user, now())
        db.commit()
        return items.canonical(section, (profile.sections or {}).get(section))


def revision(factory: sessionmaker[Session], user: uuid.UUID) -> int:
    with factory() as db:
        profile = profile_service.get_profile(db, user, now())
        db.commit()
        return int(profile.revision)


def revisions(factory: sessionmaker[Session], user: uuid.UUID) -> list[tuple[int, str]]:
    with factory() as db:
        rows = db.execute(
            sa.select(ProfileRevision.revision, ProfileRevision.section)
            .where(ProfileRevision.user_id == user)
            .order_by(ProfileRevision.revision)
        ).all()
        return [(int(number), str(section)) for number, section in rows]


def propose(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    section: str,
    key: str,
    value: Any = None,
    action: str = "add",
    run: uuid.UUID | None = RUN,
    origin: items.Origin = items.Origin.agent,
    **kwargs: Any,
) -> tuple[str, uuid.UUID | None, int]:
    """(what `propose` did, the proposal's id, how many it superseded)."""
    item = items.build(section, key, action, value, origin=origin)
    kwargs.setdefault("citations", [cite()])
    with factory() as db, db.begin():
        outcome = ip.propose(db, user, item, tick(), ingestion_run_id=run, **kwargs)
        found = outcome.proposal.id if outcome.proposal is not None else None
        return outcome.status, found, outcome.superseded


def made(factory: sessionmaker[Session], user: uuid.UUID, *args: Any, **kwargs: Any) -> uuid.UUID:
    status, found, _ = propose(factory, user, *args, **kwargs)
    assert status == ip.CREATED
    assert found is not None
    return found


def raw(factory: sessionmaker[Session], user: uuid.UUID, payload: dict[str, Any]) -> uuid.UUID:
    """A pending profile item written straight into the table, whatever its payload."""
    with factory() as db, db.begin():
        row = Proposal(
            user_id=user,
            run_id=None,
            target=items.TARGET,
            target_id=None,
            kind=items.KIND,
            payload=payload,
            status=ProposalStatus.pending,
            created_at=tick(),
        )
        db.add(row)
        db.flush()
        return row.id


def row(factory: sessionmaker[Session], proposal_id: uuid.UUID | None) -> Proposal:
    with factory() as db:
        found = db.get(Proposal, proposal_id)
        assert found is not None
        db.expunge(found)
        return found


def accept(factory: sessionmaker[Session], user: uuid.UUID, pid: Any, **kwargs: Any) -> Any:
    """Accept through the dispatch of the proposals module, committed like the route does."""
    with factory() as db:
        try:
            done = proposals.accept(db, user, pid, tick(), **kwargs)
        except proposals.ItemInvalidError:
            db.commit()
            raise
        except Exception:
            db.rollback()
            raise
        db.commit()
        return None if done is None else done[1].revision


def undo(factory: sessionmaker[Session], user: uuid.UUID, pid: Any) -> Any:
    with factory() as db, db.begin():
        done = proposals.undo(db, user, pid, tick())
        return None if done is None else done[1].revision


def bulk(factory: sessionmaker[Session], user: uuid.UUID, **kwargs: Any) -> ip.BulkResult:
    with factory() as db, db.begin():
        return ip.accept_many(db, user, tick(), **kwargs)


# --- propose --------------------------------------------------------------------------------------


def test_propose_stores_a_pending_item_of_the_profile(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    base = save(factory, alice, "identity", full("identity"))
    pid = made(factory, alice, "identity", "languages", GERMAN, depends_on=["employers:acme"])
    found = row(factory, pid)
    assert (found.target, found.target_id, found.kind, found.run_id) == (
        proposals.TARGET_PROFILE,
        None,
        proposals.KIND_PROFILE_ITEM,
        None,
    )
    assert (found.status, found.decided_by, found.user_id) == (ProposalStatus.pending, None, alice)
    assert found.payload == {
        "ingestion_run_id": str(RUN),
        "section": "identity",
        "key": "languages:german",
        "action": "add",
        "value": GERMAN,
        "value_hash": items.value_hash(GERMAN),
        "citations": [{**cite(), "transcribed": False}],
        "base_revision": base,
        "base_hash": items.value_hash(None),
        "flags": [],
        "depends_on": ["employers:acme"],
    }
    level = made(factory, alice, "identity", "languages", {"name": "English", "level": "C2"})
    assert row(factory, level).payload["base_hash"] == items.value_hash(
        full("identity")["languages"][1]
    )


def test_an_item_equal_to_what_the_profile_holds_is_not_stored(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    for item in SAMPLES[:-2]:
        with factory() as db, db.begin():
            outcome = ip.propose(db, alice, item, tick(), ingestion_run_id=RUN, citations=[cite()])
        assert (outcome.status, outcome.proposal) == (ip.EQUAL_TO_PROFILE, None), item.key
    assert propose(factory, alice, "identity", "full_name", "  Ada Example ")[0] == (
        ip.EQUAL_TO_PROFILE
    )
    assert propose(factory, alice, "identity", "languages:klingon", action="remove")[0] == (
        ip.EQUAL_TO_PROFILE
    )
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Proposal)) == 0
    with factory() as db, db.begin(), pytest.raises(proposals.NotStoredError) as refused:
        proposals.create(
            db,
            alice,
            target="profile",
            target_id=None,
            kind="profile_item",
            payload={"section": "identity", "key": "city", "action": "add", "value": "Bordeaux"},
            run_id=None,
            now=tick(),
        )
    assert refused.value.reason == ip.EQUAL_TO_PROFILE


def test_another_value_at_the_key_supersedes_and_the_same_value_merges(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    first = made(factory, alice, "identity", "languages", GERMAN)
    other_key = made(factory, alice, "identity", "languages", {"name": "Spanish", "level": "A2"})
    status, second, superseded = propose(
        factory, alice, "identity", "languages", {"name": "german", "level": "C1"}
    )
    assert (status, superseded) == (ip.CREATED, 1)
    old = row(factory, first)
    assert (old.status, old.decided_by, old.reason) == (
        ProposalStatus.superseded,
        ProposalDecider.system,
        proposals.SUPERSEDED,
    )
    assert row(factory, other_key).status is ProposalStatus.pending

    status, merged, superseded = propose(
        factory,
        alice,
        "identity",
        "languages",
        {"name": "german ", "level": "C1"},
        run=OTHER_RUN,
        citations=[cite(), cite(40, transcribed=True)],
        flags=[items.FLAG_INSTRUCTION],
    )
    assert (status, merged, superseded) == (ip.MERGED, second, 0)
    payload = row(factory, second).payload
    assert [citation["start"] for citation in payload["citations"]] == [0, 40]
    assert payload["flags"] == [items.FLAG_INSTRUCTION, items.FLAG_TRANSCRIBED]
    assert payload["ingestion_run_id"] == str(OTHER_RUN)
    many = [cite(start) for start in range(100, 100 + 2 * ip.CITATIONS_MAX)]
    assert propose(factory, alice, "identity", "languages", payload["value"], citations=many)[
        0
    ] == (ip.MERGED)
    assert len(row(factory, second).payload["citations"]) == ip.CITATIONS_MAX
    # a removal is another action at the same key: it replaces the pending value
    assert propose(factory, alice, "facts", "blocks", BLOCK)[0] == ip.CREATED
    save(factory, alice, "facts", {"blocks": [{**BLOCK, "body": "Stored."}]})
    assert propose(factory, alice, "facts", "blocks:side projects", action="remove")[2] == 1


def test_a_value_the_user_rejected_does_not_come_back(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    pid = made(factory, alice, "facts", "blocks", BLOCK)
    with factory() as db, db.begin():
        assert proposals.reject(db, alice, pid, tick()) is not None
    assert propose(factory, alice, "facts", "blocks", BLOCK) == (ip.REJECTED_BEFORE, None, 0)
    assert propose(factory, alice, "facts", "blocks", {**BLOCK, "body": "Another."})[0] == (
        ip.CREATED
    )


def test_two_documents_that_disagree_make_one_flagged_item(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save(factory, alice, "identity", full("identity"))
    english = full("identity")["languages"][1]
    other = items.build("identity", "languages", "add", {"name": "English", "level": "B2"})
    pid = made(
        factory, alice, "identity", "languages", english, conflict=[(other, [cite(7)])]
    )  # equal to the profile, and stored all the same: the user has to choose
    payload = row(factory, pid).payload
    assert payload["flags"] == [items.FLAG_CONFLICT]
    assert payload["conflict"] == [
        {
            "value": other.value,
            "value_hash": other.value_hash,
            "citations": [{**cite(7), "transcribed": False}],
        }
    ]
    with pytest.raises(proposals.InvalidValueError, match="choose one"):
        accept(factory, alice, pid)
    assert row(factory, pid).status is ProposalStatus.pending
    accept(factory, alice, pid, edited_value=other.value)
    assert stored(factory, alice, "identity")["languages"][1] == other.value
    assert row(factory, pid).edited_value == other.value


@pytest.mark.parametrize(
    "kwargs",
    [
        {"citations": [{"excerpt": "x", "start": 0, "end": 1}]},
        {"citations": ["a string"]},
        {"citations": [{**cite(), "document_id": "not an id"}]},
        {"citations": [{**cite(), "document_id": 7}]},
        {"citations": [{**cite(), "start": 5, "end": 1}]},
        {"citations": [{**cite(), "start": True}]},
        {"citations": [{**cite(), "excerpt": "x" * 100_000}]},
        {"flags": ["made up"]},
        {"depends_on": [f"key {number}" for number in range(ip.DEPENDS_ON_MAX + 1)]},
        {"depends_on": ["k" * (items.KEY_MAX + 1)]},
        {"conflict": [(items.build("identity", "city", "add", "Paris"), [cite()])]},
        {
            "conflict": [
                (items.build("identity", "languages", "add", {**GERMAN, "level": str(n)}), [])
                for n in range(ip.CONFLICT_MAX + 1)
            ]
        },
    ],
)
def test_a_caller_mistake_is_a_value_error(
    factory: sessionmaker[Session], alice: uuid.UUID, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match=r"citation|excerpt|flag|depends_on|conflict") as caught:
        propose(factory, alice, "identity", "languages", GERMAN, **kwargs)
    assert "100000" not in str(caught.value)
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Proposal)) == 0


def create(factory: sessionmaker[Session], user: uuid.UUID, payload: Any, **over: Any) -> Any:
    arguments = {
        "target": "profile",
        "target_id": None,
        "kind": "profile_item",
        "payload": payload,
        "run_id": None,
        "now": tick(),
        **over,
    }
    with factory() as db, db.begin():
        return proposals.create(db, user, **arguments).id


def test_create_takes_a_profile_item_as_a_payload(
    factory: sessionmaker[Session], alice: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="app")
    payload = {
        "section": "identity",
        "key": "languages",
        "action": "add",
        "value": {"name": MARKER, "level": "B1"},
        "citations": [cite()],
        "ingestion_run_id": str(RUN),
        "conflict": [{"value": {"name": MARKER, "level": "C1"}, "citations": [cite(9)]}],
    }
    pid = create(factory, alice, payload)
    found = row(factory, pid)
    assert (found.kind, found.target, found.status) == (
        "profile_item",
        "profile",
        ProposalStatus.pending,
    )
    assert found.payload["key"] == f"languages:{MARKER.casefold()}"
    assert found.payload["flags"] == [items.FLAG_CONFLICT]
    years = {"section": "skills", "key": "years_experience", "action": "replace", "value": 4}
    with pytest.raises(proposals.InvalidValueError, match="setting"):
        create(factory, alice, years)
    derived = create(factory, alice, {**years, "flags": [items.FLAG_DERIVED]})
    assert row(factory, derived).payload["flags"] == [items.FLAG_DERIVED]
    for broken in (
        {**payload, "section": "nowhere"},
        {**payload, "value": {"name": MARKER, "evil": MARKER}},
        {**payload, "value": {"name": MARKER, "level": "x" * 100_000}},
        {**payload, "conflict": [{"value": {"evil": MARKER}}]},
    ):
        with pytest.raises(proposals.InvalidValueError) as refused:
            create(factory, alice, broken)
        assert MARKER not in str(refused.value)
    for mistake in (
        {**payload, "flags": "derived"},
        {**payload, "conflict": ["a string"]},
        {**payload, "ingestion_run_id": "not an id"},
    ):
        with pytest.raises(ValueError, match=r"list|object|UUID"):
            create(factory, alice, mistake)
    for over in (
        {"kind": "job_note"},
        {"target_id": uuid.uuid4()},
        {"run_id": uuid.uuid4()},
        {"status": ProposalStatus.accepted},
    ):
        with pytest.raises(ValueError, match="pending profile_item"):
            create(factory, alice, payload, **over)
    assert not [record for record in caplog.records if MARKER in record.getMessage()]


# --- accept ---------------------------------------------------------------------------------------


def test_accept_changes_the_profile_by_exactly_that_item_as_one_revision(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    base = save_all(factory, alice)
    before = {section: stored(factory, alice, section) for section in FULL}
    pid = made(factory, alice, "identity", "languages", GERMAN)
    assert accept(factory, alice, pid) == base + 1
    assert revisions(factory, alice)[-1] == (base + 1, "identity")
    assert len(revisions(factory, alice)) == base + 1
    after = {section: stored(factory, alice, section) for section in FULL}
    assert after["identity"] == {
        **before["identity"],
        "languages": [*before["identity"]["languages"], GERMAN],
    }
    assert {name: data for name, data in after.items() if name != "identity"} == {
        name: data for name, data in before.items() if name != "identity"
    }
    found = row(factory, pid)
    assert (found.status, found.decided_by, found.edited_value) == (
        ProposalStatus.accepted,
        ProposalDecider.user,
        None,
    )
    assert found.payload["applied_revision"] == base + 1
    with pytest.raises(proposals.NotPendingError):
        accept(factory, alice, pid)
    assert revision(factory, alice) == base + 1


def test_the_revision_and_the_status_are_one_transaction(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    pid = made(factory, alice, "facts", "blocks", BLOCK)
    with factory() as db:
        done = proposals.accept(db, alice, pid, tick())
        assert done is not None
        assert done[0].status is ProposalStatus.accepted
        assert done[1].revision == 1
        db.rollback()
    assert row(factory, pid).status is ProposalStatus.pending
    assert (revision(factory, alice), revisions(factory, alice)) == (0, [])
    assert stored(factory, alice, "facts")["blocks"] == []


def test_every_item_is_applied_through_the_dispatch_and_taken_back(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    for item in SAMPLES:
        before = stored(factory, alice, item.section)
        pid = made(factory, alice, item.section, item.key, item.value)
        number = accept(factory, alice, pid)
        after = stored(factory, alice, item.section)
        assert holds(item.section, after, item), item.key
        assert row(factory, pid).payload["applied_revision"] == number
        assert undo(factory, alice, pid) == number + 1
        assert stored(factory, alice, item.section) == before, item.key
        undone = row(factory, pid)
        assert (undone.status, undone.decided_by) == (ProposalStatus.undone, ProposalDecider.user)
        assert undone.payload["undone_revision"] == number + 1


def test_isolation_between_users(
    factory: sessionmaker[Session], alice: uuid.UUID, bob: uuid.UUID
) -> None:
    pid = made(factory, alice, "facts", "blocks", BLOCK)
    assert accept(factory, bob, pid) is None
    assert accept(factory, bob, uuid.uuid4()) is None
    assert undo(factory, bob, pid) is None
    with factory() as db, db.begin():
        assert proposals.reject(db, bob, pid, tick()) is None
        assert ip.accept(db, bob, pid, tick()) is None
        assert ip.undo(db, bob, pid, tick()) is None
        assert ip.list_items(db, bob) == []
        assert proposals.list_proposals(db, bob, proposals.Filters(), 50) == []
        result = ip.accept_many(db, bob, tick(), proposal_ids=[pid])
        assert (result.done, result.skipped) == ([], {pid: ip.NOT_FOUND})
        assert ip.reject_many(db, bob, tick(), proposal_ids=[pid]).skipped == {pid: ip.NOT_FOUND}
        assert ip.supersede_older(db, bob, tick(), keep_run=None) == 0
    assert row(factory, pid).status is ProposalStatus.pending
    assert stored(factory, bob, "facts")["blocks"] == []
    # the same fact for each of them: two proposals, and one profile each
    theirs = made(factory, bob, "facts", "blocks", BLOCK)
    accept(factory, bob, theirs)
    assert stored(factory, alice, "facts")["blocks"] == []
    assert row(factory, pid).status is ProposalStatus.pending


def test_the_profile_changed_elsewhere_since_the_proposal(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    pid = made(factory, alice, "identity", "languages", GERMAN)
    edited = {**full("identity"), "headline": "Typed later", "languages": []}
    edited["languages"] = [{"name": "Italian", "level": "A1"}, *full("identity")["languages"]]
    save(factory, alice, "identity", edited)
    save(factory, alice, "facts", {"blocks": [BLOCK]})
    accept(factory, alice, pid)
    assert stored(factory, alice, "identity") == {
        **items.canonical("identity", edited),
        "languages": [*edited["languages"], GERMAN],
    }
    assert stored(factory, alice, "facts")["blocks"] == [BLOCK]


def test_the_profile_changed_at_the_key_since_the_proposal(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    pid = made(factory, alice, "identity", "languages", {"name": "English", "level": "C2"})
    typed = full("identity")
    typed["languages"][1]["level"] = "Typed by the user"
    current = save(factory, alice, "identity", typed)
    for sent in (None, current - 1):
        with pytest.raises(proposals.StaleItemError) as stale:
            accept(factory, alice, pid, profile_revision=sent)
        assert stale.value.revision == current
        assert stored(factory, alice, "identity")["languages"][1]["level"] == "Typed by the user"
        assert row(factory, pid).status is ProposalStatus.pending
    with factory() as db:
        review = ip.review(row(factory, pid).payload, typed)
        assert (review.stale, review.noop, review.bulk) == (True, False, ip.LOCKED)
        assert review.before == typed["languages"][1]
        assert review.after == {"name": "English", "level": "C2"}
        db.rollback()
    # the user saw the current value beside the proposed one, and accepts
    assert accept(factory, alice, pid, profile_revision=current) == current + 1
    assert stored(factory, alice, "identity")["languages"][1]["level"] == "C2"


def test_the_user_typed_the_same_thing_meanwhile(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    pid = made(factory, alice, "identity", "languages", GERMAN)
    current = save(factory, alice, "identity", {"languages": [GERMAN]})
    assert accept(factory, alice, pid) == current
    found = row(factory, pid)
    assert (found.status, found.payload["applied_revision"], found.payload["undo"]) == (
        ProposalStatus.accepted,
        None,
        None,
    )
    assert undo(factory, alice, pid) == current  # nothing to take back, and nothing is
    assert stored(factory, alice, "identity")["languages"] == [GERMAN]
    assert row(factory, pid).payload["undone_revision"] is None


def test_a_section_that_cannot_take_the_item_leaves_it_pending(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    links = [{"label": f"Link {n}", "url": f"https://example.org/{n}"} for n in range(10)]
    pid = made(factory, alice, "identity", "links", {"label": MARKER, "url": "https://x.example"})
    current = save(factory, alice, "identity", {"links": links})
    with pytest.raises(proposals.ProfileConflictError) as conflict:
        accept(factory, alice, pid)
    assert MARKER not in conflict.value.message
    assert (row(factory, pid).status, revision(factory, alice)) == (ProposalStatus.pending, current)
    review = ip.review(row(factory, pid).payload, {"links": links})
    assert review.locked
    assert (review.bulk, review.after, review.problem) == (
        ip.PROFILE_CONFLICT,
        None,
        conflict.value.message,
    )
    # the user makes room, and the same proposal applies
    save(factory, alice, "identity", {"links": links[:9]})
    accept(factory, alice, pid)
    assert len(stored(factory, alice, "identity")["links"]) == 10


@pytest.mark.parametrize(
    "broken",
    [
        {"section": "nowhere", "key": "full_name", "action": "add", "value": "Ada"},
        {"section": "identity", "key": "nothing", "action": "add", "value": "Ada"},
        {"section": "identity", "key": "full_name", "action": "merge", "value": "Ada"},
        {"section": "search", "key": "min_fit", "action": "replace", "value": 99},
        {"section": "facts", "key": "blocks:t", "action": "add", "value": {"title": "t"}},
        {"key": "full_name"},
        {},
    ],
)
def test_a_stored_item_that_no_longer_fits_becomes_invalid(
    factory: sessionmaker[Session], alice: uuid.UUID, broken: dict[str, Any]
) -> None:
    for kwargs in ({}, {"edited_value": {"title": "t", "body": "The user's own."}}):
        pid = raw(factory, alice, broken)
        review = ip.review(broken, None)
        assert (review.bulk, review.locked) in ((ip.INVALID, True), (ip.INVALID, False))
        assert review.problem
        with pytest.raises(proposals.ItemInvalidError):
            accept(factory, alice, pid, **kwargs)
        found = row(factory, pid)
        assert (found.status, found.decided_by) == (ProposalStatus.invalid, ProposalDecider.system)
        assert found.reason
        assert found.reason.startswith("This value no longer fits the profile")
        assert revision(factory, alice) == 0
        with pytest.raises(proposals.NotPendingError):
            accept(factory, alice, pid)


def test_the_users_own_value_is_validated_by_the_section_model_alone(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    gate = made(factory, alice, "skills", "gates", {"name": "phd", "terms": ["phd"]})
    for value, loc in (
        ({"name": "phd", "terms": ["phd"], "penalty": 1000}, ("penalty",)),
        ({"name": "phd", "terms": [MARKER * 100]}, ("terms", 0)),
        ({"name": "phd", "terms": ["phd"], "evil": MARKER}, None),
        (None, ()),
        (MARKER, ()),
    ):
        with pytest.raises(proposals.EditedValueError) as refused:
            accept(factory, alice, gate, edited_value=value)
        assert loc is None or refused.value.loc == loc
        assert MARKER not in str(refused.value)
    assert (row(factory, gate).status, revision(factory, alice)) == (ProposalStatus.pending, 0)
    # a setting is the user's to set: the restriction is on the agent
    chosen = {"name": "phd", "terms": ["phd", "doctorate"], "penalty": 60}
    accept(factory, alice, gate, edited_value=chosen)
    assert stored(factory, alice, "skills")["gates"] == [{**chosen, "advanced_regex": False}]
    assert row(factory, gate).edited_value == chosen
    # an edit that renames the entry replaces the entry the proposal was about
    save(factory, alice, "identity", full("identity"))
    level = made(factory, alice, "identity", "languages", {"name": "English", "level": "C2"})
    accept(factory, alice, level, edited_value={"name": "Anglais", "level": "C2"})
    names = [language["name"] for language in stored(factory, alice, "identity")["languages"]]
    assert names == ["French", "Anglais"]
    assert undo(factory, alice, level) is not None
    assert stored(factory, alice, "identity") == items.canonical("identity", full("identity"))


def test_a_stored_section_that_no_longer_fits_its_form(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    pid = made(factory, alice, "facts", "blocks", BLOCK)
    with factory() as db, db.begin():
        profile = profile_service.get_profile(db, alice, now())
        profile.sections = {"facts": {"blocks": "not a list"}}
    with pytest.raises(proposals.ProfileConflictError) as conflict:
        accept(factory, alice, pid)
    assert conflict.value.message == ip.STORED_SECTION
    assert ip.review(row(factory, pid).payload, {"blocks": "not a list"}).problem == (
        ip.STORED_SECTION
    )
    result = bulk(factory, alice)
    assert result.failed["facts"].reason == ip.PROFILE_CONFLICT
    assert result.done == []
    with pytest.raises(items.ProfileConflict):
        propose(factory, alice, "facts", "blocks", {**BLOCK, "body": "Another."})


# --- undo -----------------------------------------------------------------------------------------


def test_undo_never_overwrites_a_later_edit_of_the_user(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    pid = made(factory, alice, "identity", "languages", {"name": "English", "level": "C2"})
    accept(factory, alice, pid)
    typed = stored(factory, alice, "identity")
    typed["languages"][1]["level"] = "Typed after the accept"
    current = save(factory, alice, "identity", typed)
    with pytest.raises(ip.UndoError) as refused:
        undo(factory, alice, pid)
    assert refused.value.reason == ip.CHANGED_SINCE
    assert (row(factory, pid).status, revision(factory, alice)) == (
        ProposalStatus.accepted,
        current,
    )
    assert stored(factory, alice, "identity") == typed


def test_undo_takes_back_the_item_and_keeps_edits_made_elsewhere(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    removal = made(factory, alice, "identity", "languages:french", action="remove")
    merge = made(
        factory,
        alice,
        "experience",
        "employers",
        {"organisation": "Clinic", "title": "Lead Developer", "start": "2023-01"},
    )
    accept(factory, alice, removal)
    accept(factory, alice, merge)
    assert stored(factory, alice, "experience")["employers"][1]["title"] == "Lead Developer"
    later = stored(factory, alice, "identity")
    later["headline"] = "Typed after the accept"
    later["languages"] = [{"name": "Italian", "level": "A1"}, *later["languages"]]
    save(factory, alice, "identity", later)
    undo(factory, alice, removal)
    undo(factory, alice, merge)
    identity = stored(factory, alice, "identity")
    assert identity["headline"] == "Typed after the accept"
    assert [language["name"] for language in identity["languages"]] == [
        "French",
        "Italian",
        "English",
    ]
    assert stored(factory, alice, "experience") == items.canonical("experience", full("experience"))
    for decided in (removal, merge):
        with pytest.raises(ip.UndoError) as again:
            undo(factory, alice, decided)
        assert again.value.reason == ip.NOT_ACCEPTED


def test_what_cannot_be_undone(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    pending = made(factory, alice, "facts", "blocks", BLOCK)
    with pytest.raises(ip.UndoError) as refused:
        undo(factory, alice, pending)
    assert refused.value.reason == ip.NOT_ACCEPTED
    assert undo(factory, alice, uuid.uuid4()) is None
    job = add_job(factory, alice, "Acme", "Engineer")
    with factory() as db, db.begin():
        note = proposals.create(
            db,
            alice,
            target=proposals.TARGET_JOB,
            target_id=job,
            kind="job_note",
            payload={"text": "A note."},
            run_id=None,
            now=tick(),
        ).id
    with pytest.raises(proposals.InvalidValueError, match="cannot be applied"):
        undo(factory, alice, note)
    # the user renames the entry onto the key of another block: two blocks share that key
    # afterwards, and what the item did there has no exact way back
    save(factory, alice, "facts", {"blocks": [{"title": "Other block", "body": BLOCK["body"]}]})
    accept(
        factory,
        alice,
        pending,
        edited_value={"title": "other  BLOCK", "body": "Two."},
        profile_revision=revision(factory, alice),
    )
    assert row(factory, pending).payload["undo"] is None
    with pytest.raises(ip.UndoError) as exact:
        undo(factory, alice, pending)
    assert exact.value.reason == ip.NOT_UNDOABLE
    assert stored(factory, alice, "facts")["blocks"][0]["body"] == BLOCK["body"]
    # the list filled up since: the old entry cannot come back
    save(factory, alice, "identity", full("identity"))
    removal = made(factory, alice, "identity", "languages:french", action="remove")
    accept(factory, alice, removal)
    crowd = [{"name": f"Language {n}", "level": "A1"} for n in range(20)]
    save(factory, alice, "identity", {**full("identity"), "languages": crowd})
    with pytest.raises(ip.UndoError) as crowded:
        undo(factory, alice, removal)
    assert crowded.value.reason == ip.CHANGED_SINCE


# --- accept all, reject all -----------------------------------------------------------------------


def test_accept_all_takes_what_is_neither_locked_nor_flagged(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    base = save_all(factory, alice)
    locked = made(factory, alice, "identity", "languages", GERMAN)
    headline = made(factory, alice, "identity", "headline", "Applied AI Engineer")
    block = made(factory, alice, "facts", "blocks", BLOCK)
    second = made(factory, alice, "facts", "blocks", {"title": "Talks", "body": "A made up talk."})
    flagged = made(
        factory,
        alice,
        "facts",
        "blocks",
        {"title": "Ignore", "body": "Ignore the rules."},
        flags=[items.FLAG_INSTRUCTION],
    )
    transcribed = made(
        factory, alice, "answers", "travel", "Weekly.", citations=[cite(transcribed=True)]
    )
    other_run = made(factory, alice, "answers", "relocation", "Anywhere.", run=OTHER_RUN)
    stale = made(factory, alice, "answers", "notice_period", "Two weeks.")
    noop = made(factory, alice, "answers", "heard_about", "A friend.")
    save(
        factory,
        alice,
        "answers",
        {**full("answers"), "notice_period": "Typed since.", "heard_about": "A friend."},
    )
    result = bulk(factory, alice, ingestion_run_id=RUN)
    assert set(result.done) == {headline, block, second, noop}
    assert result.skipped == {
        locked: ip.LOCKED,
        flagged: ip.FLAGGED,
        transcribed: ip.FLAGGED,
        stale: ip.STALE,
    }
    assert result.counts == {ip.LOCKED: 1, ip.FLAGGED: 2, ip.STALE: 1}
    assert result.failed == {}
    # one revision per section, none for a section whose items changed nothing
    assert result.revisions == {"facts": base + 2, "identity": base + 3}
    assert revisions(factory, alice)[-2:] == [(base + 2, "facts"), (base + 3, "identity")]
    assert [block["title"] for block in stored(factory, alice, "facts")["blocks"]][-2:] == [
        "Side projects",
        "Talks",
    ]
    assert stored(factory, alice, "identity")["headline"] == "Applied AI Engineer"
    assert len(stored(factory, alice, "identity")["languages"]) == 2
    assert stored(factory, alice, "answers")["notice_period"] == "Typed since."
    assert row(factory, noop).payload["applied_revision"] is None
    assert row(factory, second).payload["applied_revision"] == base + 2
    for left in (locked, flagged, transcribed, stale, other_run):
        assert row(factory, left).status is ProposalStatus.pending
    # one item of a bulk accept is taken back on its own
    undo(factory, alice, block)
    assert [block["title"] for block in stored(factory, alice, "facts")["blocks"]][-1] == "Talks"
    # by section, and by ids that must be of that section
    by_ids = bulk(factory, alice, section="answers", proposal_ids=[other_run, locked, block])
    assert by_ids.done == [other_run]
    assert by_ids.skipped == {locked: ip.OTHER_SECTION, block: ip.NOT_PENDING}
    wrong_run = bulk(factory, alice, ingestion_run_id=OTHER_RUN, proposal_ids=[stale])
    assert wrong_run.skipped == {stale: ip.OTHER_RUN}
    with factory() as db:
        assert [found.id for found in ip.list_items(db, alice, ingestion_run_id=RUN)] == [
            locked,
            flagged,
            transcribed,
            stale,
        ]
        assert [found.id for found in ip.list_items(db, alice, section="identity")] == [locked]
        accepted = ip.list_items(db, alice, statuses=[ProposalStatus.accepted], section="facts")
        assert [found.id for found in accepted] == [second]


def test_accept_all_is_all_or_nothing_per_section(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    base = save_all(factory, alice)
    rag = made(factory, alice, "skills", "adjacent", {"name": "rag", "terms": ["vector search"]})
    advanced = made(factory, alice, "skills", "have", {"name": "fine_tuning", "terms": ["lora"]})
    gate = made(factory, alice, "skills", "gates", {"name": "clearance", "terms": ["clearance"]})
    block = made(factory, alice, "facts", "blocks", BLOCK)
    result = bulk(factory, alice)
    assert result.done == [block]
    assert result.failed == {
        "skills": ip.SectionFailure(advanced, ip.PROFILE_CONFLICT, items.ADVANCED_LIST)
    }
    assert result.skipped == {rag: ip.SECTION_FAILED, gate: ip.SECTION_FAILED}
    assert result.revisions == {"facts": base + 1}
    assert stored(factory, alice, "skills") == items.canonical("skills", full("skills"))
    assert {row(factory, pid).status for pid in (rag, advanced, gate)} == {ProposalStatus.pending}
    # an item that no longer fits its model is marked invalid, and stops its section too
    broken = raw(
        factory,
        alice,
        {"section": "skills", "key": "none:x", "action": "add", "value": {"name": "x", "evil": 1}},
    )
    again = bulk(factory, alice, section="skills", proposal_ids=[rag, gate, broken])
    assert again.failed["skills"].proposal_id == broken
    assert again.failed["skills"].reason == ip.INVALID
    assert (again.done, again.revisions) == ([], {})
    assert row(factory, broken).status is ProposalStatus.invalid
    assert stored(factory, alice, "skills") == items.canonical("skills", full("skills"))
    final = bulk(factory, alice, proposal_ids=[rag, gate])
    assert (final.done, final.revisions) == ([rag, gate], {"skills": base + 2})


def test_reject_all_has_no_exclusion_and_a_newer_run_supersedes(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    locked = made(factory, alice, "identity", "languages", GERMAN)
    flagged = made(factory, alice, "facts", "blocks", BLOCK, flags=[items.FLAG_INSTRUCTION])
    kept = made(factory, alice, "answers", "travel", "Weekly.", run=OTHER_RUN)
    with factory() as db, db.begin():
        result = ip.reject_many(db, alice, tick(), ingestion_run_id=RUN)
    assert set(result.done) == {locked, flagged}
    for pid in (locked, flagged):
        found = row(factory, pid)
        assert (found.status, found.decided_by) == (ProposalStatus.rejected, ProposalDecider.user)
    assert (row(factory, kept).status, revision(factory, alice)) == (ProposalStatus.pending, 0)
    newer = made(factory, alice, "answers", "relocation", "Anywhere.", run=None)
    with factory() as db, db.begin():
        assert ip.supersede_older(db, alice, tick(), keep_run=OTHER_RUN) == 1
    assert row(factory, newer).status is ProposalStatus.superseded
    assert row(factory, kept).status is ProposalStatus.pending
    with factory() as db, db.begin():
        assert ip.supersede_older(db, alice, tick(), keep_run=None) == 1
    assert row(factory, kept).decided_by is ProposalDecider.system


# --- two sessions at once -------------------------------------------------------------------------


def in_thread(work: Any) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = work()
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


@pytest.mark.parametrize("at_the_key", [False, True])
def test_accept_racing_a_save_of_the_profile_in_another_tab(
    factory: sessionmaker[Session], alice: uuid.UUID, at_the_key: bool
) -> None:
    save_all(factory, alice)
    pid = made(factory, alice, "identity", "languages", {"name": "English", "level": "C2"})
    typed = full("identity")
    typed["headline"] = "Typed in the other tab"
    if at_the_key:
        typed["languages"][1]["level"] = "Typed in the other tab"
    with factory() as tab:
        profile = profile_service.get_profile(tab, alice, now())
        profile_service.save_section(tab, alice, "identity", typed, profile.revision, now())
        thread, box = in_thread(lambda: accept(factory, alice, pid))
        thread.join(0.7)
        assert thread.is_alive()  # the accept waits for the profile row
        tab.commit()
    thread.join(20)
    assert not thread.is_alive()
    after = stored(factory, alice, "identity")
    assert after["headline"] == "Typed in the other tab"
    if at_the_key:
        assert isinstance(box["error"], proposals.StaleItemError)
        assert after["languages"][1]["level"] == "Typed in the other tab"
        assert row(factory, pid).status is ProposalStatus.pending
    else:
        assert "error" not in box
        assert after["languages"][1]["level"] == "C2"
        assert row(factory, pid).status is ProposalStatus.accepted


def test_two_accepts_of_two_items_of_one_section_at_once(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    base = save_all(factory, alice)
    pids = [
        made(factory, alice, "facts", "blocks", {"title": f"Block {n}", "body": "Made up."})
        for n in range(4)
    ]
    start = threading.Barrier(len(pids))

    def one(pid: uuid.UUID) -> Any:
        start.wait(10)
        return accept(factory, alice, pid)

    threads = [in_thread(lambda pid=pid: one(pid)) for pid in pids]  # type: ignore[misc]
    for thread, _ in threads:
        thread.join(30)
    assert sorted(box["value"] for _, box in threads) == [base + n for n in range(1, 5)]
    titles = [block["title"] for block in stored(factory, alice, "facts")["blocks"]]
    assert sorted(titles[2:]) == [f"Block {n}" for n in range(4)]
    assert titles[:2] == ["Hard rules", "What was built"]
    assert revision(factory, alice) == base + 4
    # the same item accepted twice at once: applied once
    twice = made(factory, alice, "facts", "blocks", BLOCK)
    pair = [in_thread(lambda: accept(factory, alice, twice)) for _ in range(2)]
    for thread, _ in pair:
        thread.join(30)
    errors = [box["error"] for _, box in pair if "error" in box]
    assert len(errors) == 1
    assert isinstance(errors[0], proposals.NotPendingError)
    assert revision(factory, alice) == base + 5


# --- every kind -----------------------------------------------------------------------------------


def test_the_dispatch_reads_the_target_before_the_kind() -> None:
    source = inspect.getsource(proposals.accept)
    dispatch = source.index("if proposal.target == TARGET_PROFILE:")
    kind = source.index("kind = ProposalKind(proposal.kind)")
    assert dispatch < kind
    assert source.count("_profile_items().accept(") == 1
    assert "_profile_items()" in inspect.getsource(proposals.create)


def test_a_walk_over_every_kind(factory: sessionmaker[Session], alice: uuid.UUID) -> None:
    job = add_job(factory, alice, "Acme", "Engineer")
    item = {"section": "facts", "key": "blocks", "action": "add", "value": BLOCK}
    makers: dict[str, Any] = {
        "job_note": lambda: create(
            factory, alice, {"text": "A note."}, target="job", target_id=job, kind="job_note"
        ),
        "job_status": lambda: create(
            factory,
            alice,
            {"status": "skipped", "from": "found", "reason": "r"},
            target="job",
            target_id=job,
            kind="job_status",
        ),
        "profile_item": lambda: create(factory, alice, item),
        "mail_link": lambda: create(
            factory, alice, {"thread": "t"}, target="job", target_id=job, kind="mail_link"
        ),
    }
    assert {kind.value for kind in proposals.ProposalKind} | {items.KIND, "mail_link"} == set(
        makers
    )
    first = {kind: make() for kind, make in makers.items()}
    with factory() as db:
        for kind, pid in first.items():
            listed = proposals.list_proposals(db, alice, proposals.Filters(kinds=[kind]), 50)
            assert [found.id for found in listed] == [pid]
        of_profile = proposals.list_proposals(db, alice, proposals.Filters(target="profile"), 50)
        assert [found.id for found in of_profile] == [first["profile_item"]]
        assert len(proposals.list_proposals(db, alice, proposals.Filters(), 50)) == len(makers)
    # a newer one of each kind supersedes the older one (a profile item: by key and value)
    item["value"] = {**BLOCK, "body": "A newer body."}
    second = {kind: make() for kind, make in makers.items()}
    for kind, pid in first.items():
        assert row(factory, pid).status is ProposalStatus.superseded, kind
    # reject is the same for every kind
    for kind, pid in second.items():
        with factory() as db, db.begin():
            rejected = proposals.reject(db, alice, pid, tick())
        assert rejected is not None, kind
        assert row(factory, pid).status is ProposalStatus.rejected, kind
    # accept: each kind changes its own target and nothing else
    item["value"] = {**BLOCK, "body": "The third body."}
    third = {kind: make() for kind, make in makers.items()}
    with pytest.raises(proposals.InvalidValueError, match="cannot be applied"):
        accept(factory, alice, third["mail_link"])
    assert revision(factory, alice) == 0
    for kind in ("job_note", "job_status"):
        with factory() as db, db.begin():
            done = proposals.accept(db, alice, third[kind], tick())
            assert done is not None
            assert isinstance(done[1], Job)
    assert revision(factory, alice) == 0
    with factory() as db, db.begin():
        done = proposals.accept(db, alice, third["profile_item"], tick())
        assert done is not None
        assert isinstance(done[1], Profile)
    assert revision(factory, alice) == 1
    with factory() as db:
        found = db.get(Job, job)
        assert found is not None
        assert (found.status.value, found.notes) == ("skipped", "A note.")
    statuses = {kind: row(factory, pid).status for kind, pid in third.items()}
    assert statuses == {
        "job_note": ProposalStatus.accepted,
        "job_status": ProposalStatus.accepted,
        "profile_item": ProposalStatus.accepted,
        "mail_link": ProposalStatus.pending,
    }


# --- one key per fact, whatever the name (review of PR #109, B1) ---------------------------------

EXPANDING = "\ufdfa" * 30  # one character each, eighteen once NFKC has run
TWICE_WWW = {"label": "Site", "url": "https://www.www.example.com"}


def test_a_name_that_expands_is_proposed_accepted_and_taken_back(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    before = stored(factory, alice, "identity")
    payload = {
        "section": "identity",
        "key": "languages",
        "action": "add",
        "value": {"name": EXPANDING, "level": "B1"},
        "citations": [cite()],
    }
    pid = create(factory, alice, payload)
    assert len(row(factory, pid).payload["key"]) < 60
    accept(factory, alice, pid)
    assert [language["name"] for language in stored(factory, alice, "identity")["languages"]][
        -1
    ] == EXPANDING
    with pytest.raises(proposals.NotStoredError, match=ip.EQUAL_TO_PROFILE):
        create(factory, alice, payload)
    undo(factory, alice, pid)
    assert stored(factory, alice, "identity") == before
    # the same name typed by the user over another proposal: accepted, recorded, taken back
    other = made(factory, alice, "identity", "languages", GERMAN)
    accept(factory, alice, other, edited_value={"name": EXPANDING, "level": "C1"})
    assert len(row(factory, other).payload["undo"]["at"]) < 60
    assert len(stored(factory, alice, "identity")["languages"]) == len(before["languages"]) + 1
    undo(factory, alice, other)
    assert stored(factory, alice, "identity") == before


def test_a_link_that_says_www_twice_is_one_fact_and_its_undo_removes_it(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    before = stored(factory, alice, "identity")
    pid = made(factory, alice, "identity", "links", TWICE_WWW)
    accept(factory, alice, pid)
    assert len(stored(factory, alice, "identity")["links"]) == len(before["links"]) + 1
    # the profile holds it: it is not proposed again, so no accept can add it a second time
    assert propose(factory, alice, "identity", "links", TWICE_WWW)[0] == ip.EQUAL_TO_PROFILE
    assert row(factory, pid).payload["undo"]["before"] == {"entries": []}
    undo(factory, alice, pid)
    assert stored(factory, alice, "identity") == before
    found = row(factory, pid)
    assert found.status is ProposalStatus.undone
    assert found.payload["undone_revision"] == revision(factory, alice)


def test_an_undo_that_finds_nothing_at_its_key_does_not_say_undone(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    pid = made(factory, alice, "identity", "links", TWICE_WWW)
    with factory() as db, db.begin():
        found = db.get(Proposal, pid)
        assert found is not None
        found.status = ProposalStatus.accepted
        found.decided_by = ProposalDecider.user
        found.decided_at = tick()
        found.payload = {
            **found.payload,
            "applied_revision": revision(factory, alice),
            "undo": {
                "at": "links:nowhere.example",
                "before": {"entries": []},
                "after_hash": items.held_hash({"entries": []}),
            },
        }
    with pytest.raises(ip.UndoError) as refused:
        undo(factory, alice, pid)
    assert refused.value.reason == ip.CHANGED_SINCE
    assert row(factory, pid).status is ProposalStatus.accepted


def test_propose_refuses_a_key_that_build_does_not_give(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    value = {"label": "Site", "url": "https://example.com"}
    for key in ("links:www.example.com", "links:" + "x" * 500, "nowhere:x", "links"):
        odd = items.Item("identity", key, items.Action.add, value, items.value_hash(value))
        with factory() as db, pytest.raises(proposals.InvalidValueError):
            ip.propose(db, alice, odd, tick(), ingestion_run_id=RUN, citations=[cite()])
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Proposal)) == 0


# --- one entry per item (review of PR #109, B2) ---------------------------------------------------

SHARED = "employers:globex-2021"


def twins() -> list[dict[str, Any]]:
    """Two positions started the same year at one organisation: one key for both."""
    return [
        {
            "key": "globex-apprentice",
            "organisation": "Globex",
            "title": "Apprentice",
            "start": "2021-09",
            "end": "2022-08",
            "location": None,
            "contract": "apprenticeship",
        },
        {
            "key": "globex-intern",
            "organisation": "Globex",
            "title": "Intern",
            "start": "2021-02",
            "end": "2021-07",
            "location": None,
            "contract": "apprenticeship",
        },
    ]


def save_positions(
    factory: sessionmaker[Session], user: uuid.UUID, employers: list[dict[str, Any]]
) -> None:
    save(factory, user, "experience", {**full("experience"), "employers": employers})


def titles(factory: sessionmaker[Session], user: uuid.UUID) -> list[str]:
    return [employer["title"] for employer in stored(factory, user, "experience")["employers"]]


@pytest.mark.parametrize("action", ["remove", "replace", "add"])
def test_no_item_applies_at_a_key_two_entries_share(
    factory: sessionmaker[Session], alice: uuid.UUID, action: str
) -> None:
    save_all(factory, alice)
    save_positions(factory, alice, twins())
    before = stored(factory, alice, "experience")
    slot = items.SLOTS["experience"]["employers"]
    assert [items.entry_key(slot, entry) for entry in before["employers"]] == [SHARED, SHARED]
    value = {"organisation": "Globex", "title": "Lead", "start": "2021-05"}
    if action == "remove":
        pid = made(factory, alice, "experience", SHARED, action=action)
    else:
        pid = made(factory, alice, "experience", "employers", value, action=action)
    seen = ip.review(row(factory, pid).payload, before)
    assert (seen.after, seen.bulk, seen.problem) == (None, ip.PROFILE_CONFLICT, items.SHARED_KEY)
    assert seen.before == before["employers"][0]
    with pytest.raises(proposals.ProfileConflictError):
        accept(factory, alice, pid, profile_revision=revision(factory, alice))
    assert stored(factory, alice, "experience") == before
    assert row(factory, pid).status is ProposalStatus.pending
    assert revisions(factory, alice)[-1][1] == "experience"  # the save above, no later revision
    assert len(revisions(factory, alice)) == revision(factory, alice)


def test_a_removal_takes_the_one_entry_it_showed_and_is_stale_for_another(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    save_all(factory, alice)
    first, second = twins()
    save_positions(factory, alice, [first, second])
    pid = made(factory, alice, "experience", SHARED, action="remove")
    # the user moves the position the proposal showed to another year: the key now holds the
    # other one, which the item never showed, so the removal is stale and takes nothing
    save_positions(factory, alice, [{**first, "start": "2022-01"}, second])
    with pytest.raises(proposals.StaleItemError):
        accept(factory, alice, pid)
    assert titles(factory, alice) == ["Apprentice", "Intern"]
    # the other way round: the second position is told apart, the key holds the one it showed
    save_positions(factory, alice, [first, {**second, "start": "2020-02", "end": "2020-07"}])
    seen = ip.review(row(factory, pid).payload, stored(factory, alice, "experience"))
    assert (seen.before["title"], seen.after, seen.stale, seen.problem) == (
        "Apprentice",
        None,
        False,
        None,
    )
    accept(factory, alice, pid)
    assert titles(factory, alice) == ["Intern"]
    undo(factory, alice, pid)
    assert titles(factory, alice) == ["Apprentice", "Intern"]


def test_accept_all_applies_nothing_of_a_section_where_a_key_is_shared(
    factory: sessionmaker[Session], alice: uuid.UUID
) -> None:
    blocks = [{"title": "Side projects", "body": "One."}, {"title": "side  PROJECTS", "body": "2."}]
    save(factory, alice, "facts", {"blocks": blocks})
    before = stored(factory, alice, "facts")
    other = made(factory, alice, "facts", "blocks", {"title": "New block", "body": "A body."})
    shared = made(factory, alice, "facts", "blocks", {"title": "SIDE PROJECTS", "body": "New."})
    result = bulk(factory, alice)
    failure = result.failed["facts"]
    assert (failure.proposal_id, failure.reason, failure.message) == (
        shared,
        ip.PROFILE_CONFLICT,
        items.SHARED_KEY,
    )
    assert (result.done, result.skipped) == ([], {other: ip.SECTION_FAILED})
    assert stored(factory, alice, "facts") == before
