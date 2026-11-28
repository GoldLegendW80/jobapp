"""The length a person's letters aim at (D32, issue #862): `letter_words` of the writing rules,
read and saved by the settings route with its bounds, kept by a save of an earlier page, stored
with a default the release before migration 0054 never writes, and followed by the notes of a
letter at its next save, never by a rewrite of a letter already written.

Every person here is invented (`e2e-*@example.org`)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.documents import rules, store
from app.documents.models import Author, Kind
from app.documents.settings import DocumentSettings
from app.documents.sources import PostingView, sources_for
from tests.documents.conftest import view_of, written
from tests.documents.render_support import give_profile
from tests.documents.store_support import add_posting
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-length-alex@example.org"
BLAKE = "e2e-length-blake@example.org"
ROUTE = "/api/documents/settings"


def _person(factory: sessionmaker[Session], api: Api, email: str, clock: Any) -> tuple[str, Any]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    return api.login_token(email), user.id


def test_the_route_reads_the_default_and_saves_a_length(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    alex, _ = _person(factory, api, ALEX, clock)
    blake, _ = _person(factory, api, BLAKE, clock)
    assert api.get(ROUTE, token=alex).json()["letter_words"] == 350
    body = {"no_dash_separators": False, "banned_phrases": [], "letter_words": 480}
    saved = api.request("PUT", ROUTE, token=alex, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["letter_words"] == 480
    assert api.get(ROUTE, token=alex).json()["letter_words"] == 480
    assert api.get(ROUTE, token=blake).json()["letter_words"] == 350


def test_a_save_of_an_earlier_page_keeps_the_length(
    factory: sessionmaker[Session], api: Api, clock: Any
) -> None:
    alex, _ = _person(factory, api, ALEX, clock)
    api.request("PUT", ROUTE, token=alex, json={"letter_words": 200, "banned_phrases": ["synergy"]})
    older = {"no_dash_separators": True, "banned_phrases": ["synergy"], "starter_phrases": False}
    saved = api.request("PUT", ROUTE, token=alex, json=older)
    assert saved.status_code == 200, saved.text
    read = api.get(ROUTE, token=alex).json()
    assert (read["letter_words"], read["no_dash_separators"]) == (200, True)
    # A rule left out keeps its saved value too.
    api.request("PUT", ROUTE, token=alex, json={"letter_words": 600})
    read = api.get(ROUTE, token=alex).json()
    assert (read["letter_words"], read["banned_phrases"]) == (600, ["synergy"])


@pytest.mark.parametrize("value", [149, 601, 0, -350, "400", 400.0, True, None, [400]])
def test_a_length_out_of_its_bounds_is_refused_and_nothing_changes(
    factory: sessionmaker[Session], api: Api, clock: Any, value: object
) -> None:
    alex, _ = _person(factory, api, ALEX, clock)
    api.request("PUT", ROUTE, token=alex, json={"letter_words": 420})
    refused = api.request("PUT", ROUTE, token=alex, json={"letter_words": value})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Invalid settings: letter_words"
    assert api.get(ROUTE, token=alex).json()["letter_words"] == 420


@pytest.mark.parametrize("value", [150, 151, 350, 599, 600])
def test_the_bounds_are_saved(
    factory: sessionmaker[Session], api: Api, clock: Any, value: int
) -> None:
    alex, _ = _person(factory, api, ALEX, clock)
    saved = api.request("PUT", ROUTE, token=alex, json={"letter_words": value})
    assert saved.status_code == 200, saved.text
    assert api.get(ROUTE, token=alex).json()["letter_words"] == value


def test_a_row_the_previous_release_writes_reads_the_default(
    factory: sessionmaker[Session], clock: Any
) -> None:
    user = make_user(factory, email=ALEX, name="alex").id
    with factory() as db:
        # The insert of the release before 0054, which names no letter_words.
        db.execute(
            sa.text(
                "INSERT INTO document_settings (user_id, no_dash_separators, banned_phrases, "
                "starter_phrases, updated_at) VALUES (:u, true, '{}', false, :at)"
            ),
            {"u": user, "at": clock()},
        )
        db.commit()
        assert store.settings(db, user) == DocumentSettings(no_dash_separators=True)
        assert store.settings(db, user).letter_words == 350


def test_the_column_refuses_a_length_out_of_its_bounds(
    factory: sessionmaker[Session], clock: Any
) -> None:
    user = make_user(factory, email=ALEX, name="alex").id
    with factory() as db:
        store.save_settings(db, user, DocumentSettings(letter_words=150), clock())
        db.commit()
        with pytest.raises(IntegrityError, match="ck_document_settings_letter_words_bounds"):
            db.execute(
                sa.text("UPDATE document_settings SET letter_words = 149 WHERE user_id = :u"),
                {"u": user},
            )
        db.rollback()
        assert store.settings(db, user).letter_words == 150


def _letter(paragraphs: int) -> dict[str, Any]:
    """A letter of `paragraphs` paragraphs of 100 words each."""
    data = copy.deepcopy(written("letter_typical"))
    data["paragraphs"] = [" ".join(["word"] * 100)] * paragraphs
    return data


def test_a_letter_already_written_keeps_its_text_and_its_notes_follow_at_its_next_save(
    factory: sessionmaker[Session], clock: Any
) -> None:
    user = make_user(factory, email=ALEX, name="alex").id
    seen = PostingView("Example Corp", "Platform engineer")
    sources = sources_for(view_of("alex"), None, None, seen, kind="letter")
    with factory() as db:
        posting = add_posting(db, user)
        document = store.get_or_create(db, user, posting, Kind.letter, at=clock())
        saved = store.save_draft(
            db, user, document.id, _letter(4), document.draft_revision, Author.user,
            at=clock(), sources=sources,
        )  # fmt: skip
        draft, revision, document_id = copy.deepcopy(saved.draft), saved.draft_revision, saved.id
        assert [n["code"] for n in saved.checks or [] if n["field"] == "paragraphs"] == []
        store.save_settings(db, user, DocumentSettings(letter_words=600), clock())
        db.commit()
    with factory() as db:
        kept = store.lock_document(db, user, document_id)
        assert (kept.draft, kept.draft_revision) == (draft, revision)
        assert [n["code"] for n in kept.checks or [] if n["field"] == "paragraphs"] == []
        again = store.save_draft(
            db, user, document_id, kept.draft, revision, Author.user, at=clock(),
            sources=sources,
        )  # fmt: skip
        assert [n["code"] for n in again.checks or [] if n["field"] == "paragraphs"] == [
            rules.LETTER_UNDER_LENGTH
        ]
        db.commit()
