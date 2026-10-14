"""The review routes of G12 through the api, on real Postgres with two users: the proposals of
a run grouped by section, stale computed on read, a citation into a deleted document, the undo
of an accept, and isolation. The runs are driven by the real engine (`merge_helpers`)."""

import secrets
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.config import Settings
from app.ingestion import item_proposals, items, merge, review_router
from app.ingestion.models import IngestionDocument
from app.profile.models import ProfileRevision
from tests.agents.run_helpers import save_credential
from tests.identity.api_helpers import Api
from tests.ingestion import merge_helpers as mh
from tests.ingestion.conftest import ALICE, BOB
from tests.ingestion.extract_helpers import key, keyring, settings
from tests.ingestion.helpers import user_id
from tests.ingestion.test_item_proposals import stored

_FIXTURES = (key, keyring, settings)
ACME_2021 = "employers:acme-group-2021"
EMPLOYED = ("experience", "employers", mh.employer("2021-03", "2023-06"), "March 2021")
NAMED = ("identity", "full_name", "Alice Martin", "Alice Martin")


@pytest.fixture
def me(factory: sessionmaker[Session], alice: str, keyring: Keyring) -> uuid.UUID:
    user = user_id(factory, ALICE)
    save_credential(factory, keyring, user)
    return user


@pytest.fixture
def other(factory: sessionmaker[Session], bob: str, keyring: Keyring) -> uuid.UUID:
    user = user_id(factory, BOB)
    save_credential(factory, keyring, user)
    return user


def review(api: Api, token: str | None, run_id: Any) -> Any:
    return api.get(f"/api/ingestion/runs/{run_id}/proposals", token=token)


def undo(api: Api, token: str | None, proposal_id: Any) -> Any:
    return api.post(f"/api/ingestion/proposals/{proposal_id}/undo", token=token)


def accept(api: Api, token: str | None, proposal_id: Any, **body: Any) -> Any:
    return api.post(f"/api/proposals/{proposal_id}/accept", token=token, json=body)


def reject(api: Api, token: str | None, proposal_id: Any) -> Any:
    return api.post(f"/api/proposals/{proposal_id}/reject", token=token)


def item_at(body: dict[str, Any], key: str) -> dict[str, Any]:
    (one,) = [
        item for section in body["sections"] for item in section["items"] if item["key"] == key
    ]
    return one


def revisions(factory: sessionmaker[Session], user: uuid.UUID) -> int:
    with factory() as db:
        return int(
            db.scalar(
                sa.select(sa.func.count())
                .select_from(ProfileRevision)
                .where(ProfileRevision.user_id == user)
            )
            or 0
        )


def test_the_proposals_of_a_run_are_grouped_by_section_with_their_citations(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    run_id, documents = mh.merged(
        factory,
        me,
        settings,
        [mh.CV, mh.LETTER],
        [
            [NAMED, EMPLOYED, ("identity", "headline", "Engineer", "Engineer at Acme")],
            [NAMED, ("experience", "employers", mh.employer("2021-05", "2023-06"), "May 2021")],
        ],
    )
    answer = review(api, alice, run_id)
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    body = answer.json()
    assert (body["status"], body["partial"]) == ("done", False)
    assert [section["section"] for section in body["sections"]] == ["identity", "experience"]
    assert body["counts"] == {**body["counts"], "total": 3, "pending": 3}
    assert [(one["id"], one["name"], one["deleted"]) for one in body["documents"]] == [
        (str(document), "document.txt", False) for document in documents
    ]
    name = item_at(body, "full_name")
    assert (name["value"], name["before"], name["after"]) == ("Alice Martin", None, "Alice Martin")
    assert (name["stale"], name["locked"], name["bulk"], name["free_text"]) == (
        False,
        True,
        "locked",
        False,
    )
    # One item, two citations, each naming its document.
    assert [cited["document_id"] for cited in name["citations"]] == [str(one) for one in documents]
    assert {cited["document"]["name"] for cited in name["citations"]} == {"document.txt"}
    assert name["citations"][0]["excerpt"] == "Alice Martin"
    # The two start dates are one conflict, each value with its source.
    employer = item_at(body, ACME_2021)
    assert employer["flags"] == ["conflict"]
    assert len(employer["conflict"]) == 1
    assert employer["conflict"][0]["citations"][0]["document"]["name"] == "document.txt"
    # The run's own answer counts them.
    run = api.get(f"/api/ingestion/runs/{run_id}", token=alice).json()
    assert run["counts"]["proposals"] == 3


def test_user_b_reading_a_proposals_is_a_404(
    api: Api,
    alice: str,
    bob: str,
    me: uuid.UUID,
    other: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    run_id, _ = mh.merged(factory, me, settings, [mh.CV], [[NAMED]])
    (mine,) = mh.proposals_of(factory, me, run_id)
    unknown = review(api, alice, uuid.uuid4())
    assert unknown.status_code == 404
    for answer in (review(api, bob, run_id), review(api, bob, "not-an-id")):
        assert (answer.status_code, answer.json()) == (404, unknown.json())
    assert "Alice Martin" not in review(api, bob, run_id).text
    assert review(api, None, run_id).status_code == 401
    # The decisions too: another user's proposal is an unknown one.
    gone = undo(api, alice, uuid.uuid4())
    assert gone.status_code == 404
    for answer in (undo(api, bob, mine.id), accept(api, bob, mine.id), reject(api, bob, mine.id)):
        assert answer.status_code == 404
    assert undo(api, bob, mine.id).json() == gone.json()
    assert undo(api, None, mine.id).status_code == 401
    assert mh.proposals_of(factory, me, run_id)[0].status.value == "pending"
    assert revisions(factory, me) == revisions(factory, other) == 0
    # And B's own run shows nothing of A's.
    theirs, _ = mh.merged(factory, other, settings, [mh.LETTER], [[]])
    assert review(api, bob, theirs).json()["sections"] == []
    assert len(mh.proposals_of(factory, me, run_id)) == 1  # B's merge superseded nothing of A's


def test_a_citation_into_a_deleted_document_says_so_from_the_data(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    run_id, documents = mh.merged(factory, me, settings, [mh.CV, mh.LETTER], [[NAMED], [NAMED]])
    with factory() as db, db.begin():  # what G15's delete will do to the document row
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == documents[0]))
    body = review(api, alice, run_id).json()
    gone, kept = body["documents"]
    assert (gone["id"], gone["name"], gone["deleted"]) == (str(documents[0]), None, True)
    assert kept["deleted"] is False
    first, second = item_at(body, "full_name")["citations"]
    assert (first["document_id"], first["document"]) == (str(documents[0]), None)
    assert first["excerpt"] == "Alice Martin"
    assert second["document"]["id"] == str(documents[1])


def test_stale_is_computed_on_read_against_the_profile_as_it_is_now(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    run_id, _ = mh.merged(factory, me, settings, [mh.CV], [[NAMED]])
    assert item_at(review(api, alice, run_id).json(), "full_name")["stale"] is False
    current = mh.save(factory, me, "identity", {"full_name": "A. Martin"})
    body = review(api, alice, run_id).json()
    name = item_at(body, "full_name")
    assert (name["stale"], name["stale_reason"], name["bulk"]) == (
        True,
        "profile_changed",
        "locked",
    )
    assert (name["before"], name["after"]) == ("A. Martin", "Alice Martin")
    assert body["profile_revision"] == current
    # The accept is refused until the user has seen the value the profile holds now.
    refused = accept(api, alice, name["id"])
    assert (refused.status_code, refused.json()["reason"]) == (409, "stale")
    assert accept(api, alice, name["id"], profile_revision=current).status_code == 200
    assert stored(factory, me, "identity")["full_name"] == "Alice Martin"


def test_the_derived_years_are_stale_once_an_item_they_depend_on_is_rejected_or_edited(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    run_id, _ = mh.merged(factory, me, settings, [mh.CV], [[EMPLOYED]])
    body = review(api, alice, run_id).json()
    assert [section["section"] for section in body["sections"]] == ["experience", "skills"]
    years = item_at(body, merge.YEARS_KEY)
    assert (years["flags"], years["depends_on"], years["stale"]) == (
        ["derived"],
        [ACME_2021],
        False,
    )
    assert (years["value"], years["bulk"]) == (2.2, "flagged")
    assert reject(api, alice, item_at(body, ACME_2021)["id"]).status_code == 200
    years = item_at(review(api, alice, run_id).json(), merge.YEARS_KEY)
    assert (years["stale"], years["stale_reason"]) == (True, "depends_on_changed")
    assert years["bulk"] == "flagged"


def test_the_derived_years_are_stale_once_their_employer_is_accepted_with_an_edit(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    run_id, _ = mh.merged(factory, me, settings, [mh.CV], [[EMPLOYED]])
    employer = item_at(review(api, alice, run_id).json(), ACME_2021)
    edited = {**employer["value"], "end": "2022-06"}
    assert accept(api, alice, employer["id"], edited_value=edited).status_code == 200
    body = review(api, alice, run_id).json()
    assert item_at(body, merge.YEARS_KEY)["stale_reason"] == "depends_on_changed"
    assert item_at(body, ACME_2021)["value"]["end"] == "2022-06"  # the value that was applied


def test_an_excerpt_reaches_the_page_as_bounded_text(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    marker = "zq" + secrets.token_hex(8)
    long = f"<script>alert('{marker}')</script> " + "padding " * 200 + f"tail{marker}"
    text = f"{long}\nAlice Martin.\n"
    run_id, _ = mh.merged(
        factory, me, settings, [text], [[("identity", "headline", f"<b>{marker}</b>", long)]]
    )
    answer = review(api, alice, run_id)
    assert answer.headers["content-type"].startswith("application/json")
    cited = item_at(answer.json(), "headline")["citations"][0]
    assert len(cited["excerpt"]) == review_router.EXCERPT_MAX
    assert cited["excerpt_cut"] is True
    assert cited["excerpt"].startswith("<script>")  # data: the page renders it as text
    assert f"tail{marker}" not in answer.text
    assert (cited["start"], cited["end"]) == (0, len(long))


def test_accept_applies_exactly_that_item_as_one_revision_and_undo_takes_it_back(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    mh.save(factory, me, "identity", {"headline": "Developer"})
    before = revisions(factory, me)
    run_id, _ = mh.merged(
        factory,
        me,
        settings,
        [mh.CV],
        [[NAMED, ("identity", "headline", "Engineer", "Engineer at Acme")]],
    )
    assert revisions(factory, me) == before  # the merge wrote nothing to the profile
    headline = item_at(review(api, alice, run_id).json(), "headline")
    assert (headline["before"], headline["after"]) == ("Developer", "Engineer")
    pending = undo(api, alice, headline["id"])
    assert (pending.status_code, pending.json()["reason"]) == (409, "not_accepted")
    assert accept(api, alice, headline["id"]).status_code == 200
    assert revisions(factory, me) == before + 1
    held = stored(factory, me, "identity")
    assert (held["headline"], held["full_name"]) == ("Engineer", None)  # that item and no other
    body = review(api, alice, run_id).json()
    done = item_at(body, "headline")
    assert (done["status"], done["applied_revision"]) == ("accepted", body["profile_revision"])
    assert item_at(body, "full_name")["status"] == "pending"
    taken_back = undo(api, alice, headline["id"])
    assert taken_back.status_code == 200
    assert taken_back.headers["cache-control"] == "no-store"
    assert taken_back.json()["status"] == "undone"
    assert stored(factory, me, "identity")["headline"] == "Developer"
    assert revisions(factory, me) == before + 2
    again = undo(api, alice, headline["id"])
    assert (again.status_code, again.json()["reason"]) == (409, "not_accepted")
    assert again.json()["detail"] == review_router.UNDO_REFUSED[item_proposals.NOT_ACCEPTED]
    assert item_at(review(api, alice, run_id).json(), "headline")["status"] == "undone"


def test_an_undo_after_a_later_edit_leaves_the_edit(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    run_id, _ = mh.merged(
        factory, me, settings, [mh.CV], [[("identity", "headline", "Engineer", "Engineer at Acme")]]
    )
    (one,) = mh.proposals_of(factory, me, run_id)
    assert accept(api, alice, one.id).status_code == 200
    mh.save(factory, me, "identity", {"headline": "Staff engineer"})
    answer = undo(api, alice, one.id)
    assert (answer.status_code, answer.json()["reason"]) == (409, "changed_since")
    assert stored(factory, me, "identity")["headline"] == "Staff engineer"


def test_a_free_text_item_is_marked_for_the_sentence_about_quotes(
    api: Api, alice: str, me: uuid.UUID, factory: sessionmaker[Session], settings: Settings
) -> None:
    run_id, _ = mh.merged(
        factory,
        me,
        settings,
        [mh.CV],
        [[NAMED, ("identity", "headline", "Engineer", "Engineer at Acme")]],
    )
    body = review(api, alice, run_id).json()
    types = items.build("identity", "headline", "add", "Engineer").field_types
    assert item_at(body, "headline")["free_text"] is ("text" in types.values())
    assert item_at(body, "full_name")["free_text"] is False
