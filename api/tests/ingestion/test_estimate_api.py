"""POST /api/ingestion/runs/estimate and /api/ingestion/consent against a real Postgres: the
figures of a selection computed by hand, the model and the costs per kind of credential, the
documents that cannot be read, the limits of a run, what another user never sees or counts,
hostile bodies, consent the first time, again, for a new text and twice at once, and what never
reaches a log."""

import logging
import math
import secrets
import uuid
from collections.abc import Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import KEY_BYTES, Keyring
from app.agents.models import CredentialKind
from app.identity.deps import UNAUTHORIZED
from app.ingestion import estimate, estimate_router, plan, pricing
from app.ingestion.models import IngestionConsent, IngestionDocument, TextStatus
from tests.agents.run_helpers import save_credential
from tests.identity.api_helpers import Api
from tests.ingestion.conftest import ALICE, BOB
from tests.ingestion.helpers import (
    add_image,
    confirm,
    paste,
    pdf,
    secret_like,
    set_columns,
    uploaded,
    user_id,
)

ESTIMATE = "/api/ingestion/runs/estimate"
CONSENT = "/api/ingestion/consent"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
KEYRING = Keyring(secrets.token_bytes(KEY_BYTES))


def ask(api: Api, token: str | None, ids: Sequence[str], **more: Any) -> httpx.Response:
    return api.post(ESTIMATE, token=token, json={"document_ids": list(ids), **more})


def answer(api: Api, token: str, ids: Sequence[str], **more: Any) -> dict[str, Any]:
    response = ask(api, token, ids, **more)
    assert response.status_code == 200, response.text
    return response.json()


def text_file(api: Api, token: str, letter: str, size: int, name: str = "notes.md") -> str:
    """A text document of exactly `size` characters; another letter is another file."""
    return str(uploaded(api, token, (letter * size).encode(), name)["id"])


def dossier(api: Api, token: str) -> list[str]:
    """The typical dossier of "Tokens": three documents of 50,000 characters."""
    return [text_file(api, token, letter, 50_000, f"{letter}.md") for letter in "abc"]


def credential(
    factory: sessionmaker[Session],
    email: str,
    kind: CredentialKind = CredentialKind.anthropic_api_key,
) -> None:
    save_credential(factory, KEYRING, user_id(factory, email), kind=kind)


def consents(factory: sessionmaker[Session]) -> list[tuple[uuid.UUID, int]]:
    with factory() as db:
        rows = db.execute(
            sa.select(IngestionConsent.user_id, IngestionConsent.text_version).order_by(
                IngestionConsent.text_version
            )
        )
        return [(row[0], row[1]) for row in rows]


# --- signed out


def test_signed_out_answers_401_on_every_route(api: Api) -> None:
    for response in (
        ask(api, None, [str(uuid.uuid4())]),
        api.get(CONSENT, token=None),
        api.post(CONSENT, token=None, json={"version": 1}),
    ):
        assert response.status_code == 401
        assert response.json() == {"detail": UNAUTHORIZED}
        assert response.headers["cache-control"] == "no-store"


# --- the figures


def test_an_api_key_reads_the_range_the_ceiling_and_the_three_costs(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    ids = dossier(api, alice)
    credential(factory, ALICE)
    response = ask(api, alice, ids)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    # By hand ("Tokens"): L = 263,503.5 and H = 851,011.5, each rounded up once, C = 2 * H.
    assert body["tokens"] == {"low": 263_504, "high": 851_012, "ceiling": 1_702_024}
    assert (body["credential_kind"], body["model"]) == ("anthropic_api_key", SONNET)
    assert body["model_reason"] == (
        "Your credential is an Anthropic API key, so the run uses claude-sonnet-5-5."
    )
    assert body["cost"] == {
        "currency": "USD",
        "low": "0.24",
        "high": "1.89",
        "ceiling": "3.40",
        "prices_as_of": "2026-10-13",
        "note": pricing.ESTIMATE_NOTE,
    }
    assert body["cost_note"] is None
    assert [one["id"] for one in body["documents"]] == ids
    assert body["documents"][0] == {
        "id": ids[0],
        "name": "a.md",
        "characters": 50_000,
        "images": 0,
        "steps": 1,
        "reused_steps": 0,
    }
    assert (body["characters"], body["images"], body["steps"], body["reused_steps"]) == (
        150_000,
        0,
        3,
        0,
    )
    assert body["unreadable"] == []
    assert body["blockers"] == []
    assert body["can_start"] is True
    assert body["consent"] == {"version": 1, "accepted": False}
    assert body["limits"] == {
        "documents": 30,
        "characters": 1_500_000,
        "images": 60,
        "steps": 60,
        "step_minutes": 15,
        "run_minutes": 60,
    }


def test_a_subscription_reads_tokens_and_no_amount(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    ids = dossier(api, alice)
    credential(factory, ALICE, CredentialKind.claude_subscription)
    body = answer(api, alice, ids)
    assert body["tokens"] == {"low": 263_504, "high": 851_012, "ceiling": 1_702_024}
    assert (body["credential_kind"], body["model"]) == ("claude_subscription", OPUS)
    assert body["cost"] is None
    assert body["cost_note"] == pricing.QUOTA_NOTE
    assert body["can_start"] is True


def test_without_a_credential_the_estimate_still_answers_and_says_what_is_missing(
    api: Api, alice: str
) -> None:
    body = answer(api, alice, dossier(api, alice))
    assert body["tokens"] == {"low": 263_504, "high": 851_012, "ceiling": 1_702_024}
    assert body["credential_kind"] is None
    assert body["model"] is None
    assert body["cost"] is None
    assert body["can_start"] is False
    assert body["blockers"] == [
        {"code": "no_credential", "reason": estimate_router.BLOCKERS["no_credential"]}
    ]
    assert body["model_reason"] == estimate_router.BLOCKERS["no_credential"]


def test_a_long_text_is_counted_as_the_parts_the_run_will_cut(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    text = "a" * 90_000 + "\n\n" + "b" * 30_000
    document_id = str(uploaded(api, alice, text.encode())["id"])
    credential(factory, ALICE)
    body = answer(api, alice, [document_id])
    assert body["documents"][0]["characters"] == 120_002
    assert body["steps"] == 2
    # The cut is at the paragraph end, 90,002 and 30,000 characters, not two halves. By hand:
    # 90,002 characters are T = 30,001 and R = 4, so 112,000 + 30,001 * 29 / 2 + 8,000 =
    # 555,014.5; 30,000 are 178,000; the sum is rounded up once.
    first, second = estimate.extraction(90_002), estimate.extraction(30_000)
    assert body["tokens"]["high"] == math.ceil(first.high + second.high) == 733_015
    assert body["tokens"]["ceiling"] == 2 * 733_015


def test_the_same_id_many_times_is_one_document(api: Api, alice: str) -> None:
    document_id = text_file(api, alice, "a", 30_000)
    body = answer(api, alice, [document_id] * 200, reread_document_ids=[document_id])
    assert len(body["documents"]) == 1
    assert body["tokens"] == {"low": 52_500, "high": 178_000, "ceiling": 356_000}


# --- reuse, through the provider G10 will register


def test_finished_steps_are_reused_at_no_cost_unless_read_again(
    api: Api, factory: sessionmaker[Session], alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = dossier(api, alice)
    credential(factory, ALICE)
    asked: list[tuple[uuid.UUID, int, str]] = []

    def everything_finished(
        db: Session, owner: uuid.UUID, selected: Sequence[plan.Document], model: str
    ) -> Collection[plan.Identity]:
        asked.append((owner, len(selected), model))
        return {step.identity for step in plan.build(selected, model=model).steps}

    assert answer(api, alice, ids)["reused_steps"] == 0  # nothing is registered in this task
    monkeypatch.setattr(estimate_router, "_finished_provider", everything_finished)
    body = answer(api, alice, ids)
    assert asked == [(user_id(factory, ALICE), 3, SONNET)]
    assert (body["steps"], body["reused_steps"]) == (3, 3)
    assert body["tokens"] == {"low": 0, "high": 0, "ceiling": 0}
    assert (body["cost"]["low"], body["cost"]["high"], body["cost"]["ceiling"]) == (
        "0.00",
        "0.00",
        "0.00",
    )
    again = answer(api, alice, ids, reread_document_ids=[ids[1]])
    assert [one["reused_steps"] for one in again["documents"]] == [1, 0, 1]
    assert again["tokens"]["high"] == 283_671


def test_the_provider_is_registered_by_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    before = estimate_router._finished_provider
    assert before(None, uuid.uuid4(), [], OPUS) == frozenset()  # type: ignore[arg-type]

    def provider(*_: object) -> Collection[plan.Identity]:
        return frozenset()

    monkeypatch.setattr(estimate_router, "_finished_provider", before)
    estimate_router.register_finished_provider(provider)
    assert estimate_router._finished_provider is provider


def test_a_document_to_read_again_must_be_selected(api: Api, alice: str) -> None:
    document_id = text_file(api, alice, "a", 100)
    other = text_file(api, alice, "b", 100)
    response = ask(api, alice, [document_id], reread_document_ids=[other])
    assert response.status_code == 422
    assert response.json() == {"detail": estimate_router.REREAD_NOT_SELECTED}


# --- the documents that cannot be read


def test_documents_that_cannot_be_read_are_named_left_out_and_counted(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    credential(factory, ALICE)
    good = text_file(api, alice, "a", 30_000, "good.md")
    waiting = str(uploaded(api, alice, pdf(), "scan.pdf")["id"])  # no reader in this version
    being_read = text_file(api, alice, "b", 500, "being-read.md")
    set_columns(factory, being_read, text_status=TextStatus.pending)
    failed = text_file(api, alice, "c", 500, "failed.md")
    set_columns(factory, failed, text_status=TextStatus.failed)
    secret = str(uploaded(api, alice, f"key {secret_like()}\n".encode(), "secret.md")["id"])
    images = text_file(api, alice, "d", 500, "pages.md")
    set_columns(factory, images, text_status=TextStatus.awaiting_transcription)
    add_image(factory, images)

    body = answer(api, alice, [waiting, good, being_read, failed, secret, images])
    assert [(one["name"], one["code"]) for one in body["unreadable"]] == [
        ("scan.pdf", "not_readable_yet"),
        ("being-read.md", "pending"),
        ("failed.md", "failed"),
        ("secret.md", "secret_unconfirmed"),
        ("pages.md", "awaiting_transcription"),
    ]
    assert all(one["reason"] == estimate_router.REASONS[one["code"]] for one in body["unreadable"])
    # Only the readable one is counted: one step over 30,000 characters.
    assert [one["id"] for one in body["documents"]] == [good]
    assert body["tokens"] == {"low": 52_500, "high": 178_000, "ceiling": 356_000}
    assert (body["characters"], body["images"], body["steps"]) == (30_000, 0, 1)
    assert body["can_start"] is False
    assert [one["code"] for one in body["blockers"]] == ["unreadable_documents"]

    # Once the user has confirmed the warning, the document is read like any other.
    assert confirm(api, alice, secret).status_code == 200
    confirmed = answer(api, alice, [good, secret])
    assert confirmed["unreadable"] == []
    assert confirmed["steps"] == 2
    assert confirmed["can_start"] is True


def test_a_selection_with_nothing_to_read_is_zero_and_cannot_start(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    credential(factory, ALICE)
    waiting = str(uploaded(api, alice, pdf(), "scan.pdf")["id"])
    body = answer(api, alice, [waiting])
    assert body["documents"] == []
    assert body["steps"] == 0
    assert body["tokens"] == {"low": 0, "high": 0, "ceiling": 0}
    assert [one["code"] for one in body["blockers"]] == ["nothing_to_read", "unreadable_documents"]
    assert body["can_start"] is False


def test_a_document_emptied_of_its_text_is_named_as_empty(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    document_id = text_file(api, alice, "a", 100)
    set_columns(factory, document_id, text=None)
    body = answer(api, alice, [document_id])
    assert [one["code"] for one in body["unreadable"]] == ["empty"]


# --- with transcription on (G14): images are planned and counted


def images_document(
    api: Api, factory: sessionmaker[Session], token: str, letter: str, images: int
) -> str:
    """A document whose pages are images still to transcribe, as G8 will leave it."""
    document_id = text_file(api, token, letter, 1, f"{letter}.md")
    set_columns(factory, document_id, text=None, text_status=TextStatus.awaiting_transcription)
    for position in range(images):
        add_image(factory, document_id, position)
    return document_id


def test_nine_images_are_two_transcription_steps_and_their_text_counted_in_advance(
    api: Api, factory: sessionmaker[Session], alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan, "TRANSCRIPTION", True)
    document_id = images_document(api, factory, alice, "a", 9)
    body = answer(api, alice, [document_id])
    assert body["documents"][0]["images"] == 9
    assert body["images"] == 9
    assert body["steps"] == 3
    # Eight images: 248,500 to 472,800. One image: 27,300 to 68,200. Their text, 27,000
    # characters counted in advance: 49,500 to 168,000.
    assert body["tokens"] == {"low": 325_300, "high": 709_000, "ceiling": 1_418_000}


def test_sixty_one_images_are_over_the_limit_of_a_run(
    api: Api, factory: sessionmaker[Session], alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan, "TRANSCRIPTION", True)
    ids = [
        images_document(api, factory, alice, "a", 31),
        images_document(api, factory, alice, "b", 30),
    ]
    response = ask(api, alice, ids)
    assert response.status_code == 422
    assert "at most 60 page images" in response.json()["detail"]
    assert "61" in response.json()["detail"]


def test_the_document_of_the_sixty_first_step_is_left_out_and_the_others_can_start(
    api: Api, factory: sessionmaker[Session], alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plan, "TRANSCRIPTION", True)
    credential(factory, ALICE)
    letters = "abcdefghijklmnopqrstuvwxyzABC"
    ids = [images_document(api, factory, alice, letter, 2) for letter in letters]  # 58 steps
    large = text_file(api, alice, "z", 250_000, "large.md")  # three parts
    body = answer(api, alice, [*ids, large])
    assert body["steps"] == 58
    assert len(body["documents"]) == 29
    assert [(one["name"], one["code"]) for one in body["unreadable"]] == [
        ("large.md", "over_steps")
    ]
    assert body["can_start"] is True
    assert body["blockers"] == []


# --- the limits of a run


def test_no_document_is_refused(api: Api, alice: str) -> None:
    response = ask(api, alice, [])
    assert response.status_code == 422
    assert response.json() == {"detail": "Select at least one document."}


def test_thirty_one_documents_are_over_the_limit_of_a_run(api: Api, alice: str) -> None:
    ids = []
    for number in range(31):
        response = paste(api, alice, f"Pasted note number {number}.")
        assert response.status_code == 201, response.text
        ids.append(str(response.json()["id"]))
    assert answer(api, alice, ids[:30])["steps"] == 30
    response = ask(api, alice, ids)
    assert response.status_code == 422
    assert response.json()["detail"].startswith("A run reads at most 30 documents, and 31 are")


def test_more_than_the_characters_of_a_run_is_refused(api: Api, alice: str) -> None:
    ids = [text_file(api, alice, letter, 500_000, f"{letter}.md") for letter in "abc"]
    assert answer(api, alice, ids)["steps"] == 15
    ids.append(text_file(api, alice, "d", 1, "d.md"))
    response = ask(api, alice, ids)
    assert response.status_code == 422
    assert "at most 1,500,000 characters" in response.json()["detail"]
    assert "1,500,001" in response.json()["detail"]


# --- another user


def test_another_users_document_answers_like_one_that_does_not_exist(
    api: Api, alice: str, bob: str
) -> None:
    mine = text_file(api, alice, "a", 100)
    theirs = text_file(api, bob, "b", 100)
    answers = [
        ask(api, alice, [theirs]),
        ask(api, alice, [mine, theirs]),
        ask(api, alice, [str(uuid.uuid4())]),
        ask(api, alice, ["not-a-uuid"]),
        ask(api, alice, [mine], reread_document_ids=["not-a-uuid"]),
    ]
    for response in answers:
        assert response.status_code == 404
        assert response.json() == {"detail": "Document not found."}
        assert response.headers["cache-control"] == "no-store"


def test_another_users_documents_credential_and_consent_never_count(
    api: Api, factory: sessionmaker[Session], alice: str, bob: str
) -> None:
    mine = text_file(api, alice, "a", 30_000)
    for letter in "bcd":
        text_file(api, bob, letter, 50_000)
    credential(factory, BOB)
    assert api.post(CONSENT, token=bob, json={"version": 1}).status_code == 200
    body = answer(api, alice, [mine])
    assert body["tokens"] == {"low": 52_500, "high": 178_000, "ceiling": 356_000}
    assert body["credential_kind"] is None
    assert body["cost"] is None
    assert body["consent"] == {"version": 1, "accepted": False}
    # The same file in both accounts is two documents: each user plans their own.
    same = text_file(api, bob, "a", 30_000)
    assert same != mine
    assert ask(api, alice, [same]).status_code == 404


# --- hostile bodies


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"document_ids": "x" * 5_000},
        {"document_ids": [1, 2]},
        {"document_ids": ["x" * 65]},
        {"document_ids": [str(uuid.uuid4())] * 201},
        {"document_ids": [str(uuid.uuid4())], "reread_document_ids": ["y" * 65]},
        {"document_ids": [str(uuid.uuid4())], "user_id": str(uuid.uuid4())},
        {"document_ids": [str(uuid.uuid4())], "model": "claude-opus-5-5"},
    ],
)
def test_a_body_out_of_bounds_is_refused_without_an_echo(
    api: Api, alice: str, body: dict[str, Any]
) -> None:
    response = api.post(ESTIMATE, token=alice, json=body)
    assert response.status_code == 422
    assert "xxxx" not in response.text
    assert "yyyy" not in response.text
    assert len(response.text) < 500


# --- consent


def test_consent_the_first_time_then_read_back_then_given_again(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    first = api.get(CONSENT, token=alice)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {
        "version": 1,
        "text": estimate_router.CONSENT_TEXT,
        "reminder": estimate_router.CONSENT_REMINDER,
        "accepted": False,
        "accepted_at": None,
    }
    given = api.post(CONSENT, token=alice, json={"version": 1})
    assert given.status_code == 200, given.text
    assert given.json()["accepted"] is True
    accepted_at = given.json()["accepted_at"]
    assert accepted_at is not None
    assert api.get(CONSENT, token=alice).json()["accepted_at"] == accepted_at
    # Given again: one row, and the first date stays.
    again = api.post(CONSENT, token=alice, json={"version": 1})
    assert again.status_code == 200
    assert again.json()["accepted_at"] == accepted_at
    assert consents(factory) == [(user_id(factory, ALICE), 1)]
    assert answer(api, alice, [text_file(api, alice, "a", 10)])["consent"] == {
        "version": 1,
        "accepted": True,
    }


def test_the_consent_of_one_user_is_not_the_consent_of_another(
    api: Api, factory: sessionmaker[Session], alice: str, bob: str
) -> None:
    assert api.post(CONSENT, token=alice, json={"version": 1}).status_code == 200
    assert api.get(CONSENT, token=bob).json()["accepted"] is False
    with factory() as db:
        assert estimate_router.has_consented(db, user_id(factory, ALICE)) is True
        assert estimate_router.has_consented(db, user_id(factory, BOB)) is False
        assert estimate_router.has_consented(db, uuid.uuid4()) is False


def test_a_new_text_asks_again_and_keeps_what_was_accepted_before(
    api: Api, factory: sessionmaker[Session], alice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert api.post(CONSENT, token=alice, json={"version": 1}).status_code == 200
    monkeypatch.setattr(estimate_router, "CONSENT_VERSION", 2)
    state = api.get(CONSENT, token=alice).json()
    assert (state["version"], state["accepted"], state["accepted_at"]) == (2, False, None)
    # The version the page still shows is the old one: refused, nothing recorded.
    old = api.post(CONSENT, token=alice, json={"version": 1})
    assert old.status_code == 422
    assert old.json() == {"detail": estimate_router.CONSENT_OUTDATED}
    assert api.get(CONSENT, token=alice).json()["accepted"] is False
    assert api.post(CONSENT, token=alice, json={"version": 2}).json()["accepted"] is True
    owner = user_id(factory, ALICE)
    assert consents(factory) == [(owner, 1), (owner, 2)]


def test_a_version_that_does_not_exist_yet_is_refused(api: Api, alice: str) -> None:
    response = api.post(CONSENT, token=alice, json={"version": 2})
    assert response.status_code == 422
    assert response.json() == {"detail": estimate_router.CONSENT_OUTDATED}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": "1"},
        {"version": True},
        {"version": 1.5},
        {"version": 0},
        {"version": -1},
        {"version": 2**31},
        {"version": 10**40},
        {"version": None},
        {"version": 1, "user_id": str(uuid.uuid4())},
        {"version": 1, "accepted_at": "2020-01-01T00:00:00Z"},
    ],
)
def test_a_consent_body_out_of_bounds_records_nothing(
    api: Api, factory: sessionmaker[Session], alice: str, body: dict[str, Any]
) -> None:
    response = api.post(CONSENT, token=alice, json=body)
    assert response.status_code == 422
    assert consents(factory) == []


def test_two_consents_at_once_are_one_row(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    owner = user_id(factory, ALICE)
    together = Barrier(2)

    def give() -> Any:
        with factory() as db:
            together.wait(timeout=10)
            consent = estimate_router.record_consent(db, owner)
            accepted_at = consent.accepted_at
            db.commit()
            return accepted_at

    with ThreadPoolExecutor(max_workers=2) as pool:
        dates = [task.result(timeout=30) for task in [pool.submit(give), pool.submit(give)]]
    assert consents(factory) == [(owner, 1)]
    assert dates[0] == dates[1]


def test_the_consent_goes_with_the_user(
    api: Api, factory: sessionmaker[Session], alice: str
) -> None:
    assert api.post(CONSENT, token=alice, json={"version": 1}).status_code == 200
    with factory() as db, db.begin():
        db.execute(sa.text("DELETE FROM users WHERE email = :email"), {"email": ALICE})
    assert consents(factory) == []


def test_the_consent_text_says_what_is_sent_to_whom_and_what_deleting_does_not_undo() -> None:
    text = estimate_router.CONSENT_TEXT
    for words in ("text and the page images", "without your contact details", "Anthropic"):
        assert words in text
    assert "own Claude subscription or Anthropic API key" in text
    assert "does not delete what Anthropic has already received" in text
    shown = [text, estimate_router.CONSENT_REMINDER, estimate_router.CONSENT_OUTDATED]
    shown += [*estimate_router.REASONS.values(), *estimate_router.BLOCKERS.values()]
    for sentence in shown:
        assert " - " not in sentence
        assert "—" not in sentence
        assert "–" not in sentence


# --- the log


def test_nothing_of_a_document_reaches_the_log_or_an_error(
    api: Api, factory: sessionmaker[Session], alice: str, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "Zanzibar-7731"
    text = f"{marker} " * 12_000  # longer than one part: the text is loaded to be cut
    document_id = str(uploaded(api, alice, text.encode(), f"{marker}.md")["id"])
    with factory() as db:
        stored = db.get(IngestionDocument, uuid.UUID(document_id))
        assert stored is not None
        assert stored.name == f"{marker}.md"
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert answer(api, alice, [document_id])["steps"] == 2
        assert ask(api, alice, [document_id, document_id + "0"]).status_code == 404
        refused = ask(api, alice, [document_id], reread_document_ids=[str(uuid.uuid4())])
        assert refused.status_code == 422
        assert marker not in refused.text
        assert api.post(CONSENT, token=alice, json={"version": 1}).status_code == 200
    events = [record for record in caplog.records if record.name == "app.ingestion"]
    assert {record.getMessage() for record in events} == {
        "ingestion_estimate",
        "ingestion_estimate_refused",
        "ingestion_consent",
    }
    for record in caplog.records:
        assert marker not in record.getMessage()
        assert marker not in repr(record.__dict__)
    counted = next(record for record in events if record.getMessage() == "ingestion_estimate")
    assert counted.__dict__["steps"] == 2
    assert counted.__dict__["documents"] == 1
