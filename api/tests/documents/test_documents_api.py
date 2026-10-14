"""D6: the documents api through the app, two e2e people. Isolation (another person's ids are
404), the save with If-Match and its notes, the render request coalesced, the PDF and page image
headers, versions with their corrections, restore and approve, language and template switches
(unknown, another person's, a frozen latest), the templates and their thumbnails, the settings,
the literal paths before `/api/documents/{id}`, an edit kept while the render queue is full, and
the file name's ASCII spelling."""

from __future__ import annotations

import asyncio
import copy
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.documents import render_job, store
from app.documents.models import Document, DocumentVersion, FreezeReason
from app.documents.router import NAME_MAX, ascii_name
from tests.documents.render_support import CV, FakeRenderer, RecordingQueue, give_profile
from tests.identity.api_helpers import Api, make_user
from tests.identity.conftest import api, app, clock, engine, factory, migrated_url

__all__ = ["api", "app", "clock", "engine", "factory", "migrated_url"]

ALEX = "e2e-docs-api-alex@example.org"
BLAKE = "e2e-docs-api-blake@example.org"


@pytest.fixture
def queue(app: FastAPI) -> RecordingQueue:
    recording = RecordingQueue()
    app.state.queue = recording
    return recording


def _person(
    factory: sessionmaker[Session], api: Api, email: str, clock: Any
) -> tuple[str, uuid.UUID]:
    user = make_user(factory, email=email, name=email.split("@")[0])
    give_profile(factory, user.id, clock())
    return api.login_token(email), user.id


def _posting(
    factory: sessionmaker[Session], user: uuid.UUID, company: str = "Lumen Café"
) -> uuid.UUID:
    with factory() as db:
        found = db.execute(
            sa.text(
                "INSERT INTO jobs (user_id, slug, company, title, posting_text) VALUES "
                "(:u, :s, :c, 'Platform engineer', 'Lumen is hiring a platform engineer.') "
                "RETURNING id"
            ),
            {"u": user, "s": f"lumen-{uuid.uuid4().hex[:8]}", "c": company},
        ).scalar_one()
        db.commit()
    return found


def _document(
    factory: sessionmaker[Session], user: uuid.UUID, posting: uuid.UUID, clock: Any
) -> uuid.UUID:
    with factory() as db:
        document = store.get_or_create(db, user, posting, store.Kind.cv, at=clock())
        db.commit()
        return document.id


def _render(factory: sessionmaker[Session], queue: RecordingQueue, clock: Any) -> None:
    ctx = {
        "sessions": factory,
        "clock": clock,
        "documents_render": FakeRenderer(),
        "documents_retry_after_s": 0,
    }
    for job in queue.jobs:
        asyncio.run(render_job.render_document(ctx, *job["args"]))
    queue.jobs.clear()


@pytest.fixture
def two(
    factory: sessionmaker[Session], api: Api, clock: Any, queue: RecordingQueue
) -> dict[str, Any]:
    alex, alex_id = _person(factory, api, ALEX, clock)
    blake, _ = _person(factory, api, BLAKE, clock)
    posting = _posting(factory, alex_id)
    document = _document(factory, alex_id, posting, clock)
    return {
        "alex": alex,
        "blake": blake,
        "alex_id": alex_id,
        "posting": posting,
        "document": document,
    }


def _save(api: Api, token: str, document: uuid.UUID, content: dict[str, Any], revision: int) -> Any:
    return api.request(
        "PUT",
        f"/api/documents/{document}/draft",
        token=token,
        json=content,
        headers={"If-Match": str(revision)},
    )


def test_save_render_read_the_pdf_and_the_page_image(
    two: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue, clock: Any
) -> None:
    alex, document = two["alex"], two["document"]
    saved = _save(api, alex, document, CV, 1)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["draft_revision"] == 2
    assert body["render"]["state"] == "queued"
    assert saved.headers["cache-control"] == "private, no-store"
    for _ in range(3):  # coalesced: one job
        asked = api.post(f"/api/documents/{document}/render", token=alex)
        assert asked.status_code == 202
    assert len(queue.jobs) == 1
    _render(factory, queue, clock)
    read = api.get(f"/api/documents/{document}", token=alex).json()
    assert read["render"] == {"state": "idle", "revision": 2, "error": None, "message": None}
    assert read["latest"]["number"] == 1
    assert read["latest"]["has_pdf"] is True
    pdf = api.get(f"/api/documents/{document}/versions/1/pdf", token=alex)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"] == 'inline; filename="Alex_Example_Lumen_Cafe_CV.pdf"'
    assert pdf.headers["cache-control"] == "private, no-store"
    assert pdf.headers["x-content-type-options"] == "nosniff"
    png = api.get(f"/api/documents/{document}/versions/1/page1.png?width=200", token=alex)
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG")
    assert png.headers["cache-control"] == "private, no-store"
    listed = api.get(f"/api/jobs/{two['posting']}/documents", token=alex).json()["documents"]
    assert [d["id"] for d in listed] == [str(document)]
    assert listed[0]["sent"] == []
    with factory() as db:
        status = db.execute(
            sa.text("SELECT status FROM jobs WHERE id = :j"), {"j": two["posting"]}
        ).scalar_one()
    assert status == "cv_ready"


def test_another_persons_document_does_not_exist(
    two: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue, clock: Any
) -> None:
    alex, blake, document = two["alex"], two["blake"], two["document"]
    _save(api, alex, document, CV, 1)
    _render(factory, queue, clock)
    for method, path in [
        ("GET", f"/api/documents/{document}"),
        ("POST", f"/api/documents/{document}/render"),
        ("GET", f"/api/documents/{document}/versions"),
        ("GET", f"/api/documents/{document}/versions/1"),
        ("GET", f"/api/documents/{document}/versions/1/pdf"),
        ("GET", f"/api/documents/{document}/versions/1/page1.png"),
        ("POST", f"/api/documents/{document}/versions/1/restore"),
        ("POST", f"/api/documents/{document}/versions/1/approve"),
        ("GET", f"/api/jobs/{two['posting']}/documents"),
    ]:
        assert api.request(method, path, token=blake).status_code == 404, path
    assert _save(api, blake, document, CV, 2).status_code == 404
    patched = api.request(
        "PATCH", f"/api/documents/{document}", token=blake, json={"language": "fr"}
    )
    assert patched.status_code == 404
    assert queue.jobs == []
    with factory() as db:
        found = db.execute(sa.select(Document).where(Document.id == document)).scalar_one()
    assert found.draft_revision == 2
    assert found.language.value == "en"


def test_a_stale_save_is_409_with_the_current_draft_and_a_save_without_revision_428(
    two: dict[str, Any], api: Api
) -> None:
    alex, document = two["alex"], two["document"]
    assert _save(api, alex, document, CV, 1).status_code == 200
    stale = _save(api, alex, document, {**CV, "headline": "Late"}, 1)
    assert stale.status_code == 409
    assert stale.json()["document"]["draft_revision"] == 2
    missing = api.request("PUT", f"/api/documents/{document}/draft", token=alex, json=CV)
    assert missing.status_code == 428
    invalid = _save(api, alex, document, {**CV, "headline": "x" * 5000}, 2)
    assert invalid.status_code == 422
    assert invalid.json()["detail"][0]["path"] == "headline"


def test_a_hand_draft_with_an_issue_is_saved_and_rendered_with_its_note(
    two: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue, clock: Any
) -> None:
    content = copy.deepcopy(CV)
    content["experience"][0]["bullets"][0] = "Cut the cloud bill by 87 percent in one quarter."
    saved = _save(api, two["alex"], two["document"], content, 1)
    assert saved.status_code == 200
    assert saved.json()["notes"], "the unsupported number is noted"
    _render(factory, queue, clock)
    assert (
        api.get(f"/api/documents/{two['document']}/versions/1/pdf", token=two["alex"]).status_code
        == 200
    )


def test_versions_carry_their_corrections_restore_and_approve(
    two: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue, clock: Any
) -> None:
    alex, document = two["alex"], two["document"]
    content = copy.deepcopy(CV)
    content["experience"][0]["bullets"][0] += " 漢字"
    _save(api, alex, document, content, 1)
    _render(factory, queue, clock)
    version = api.get(f"/api/documents/{document}/versions/1", token=alex).json()
    assert version["unprintable"]
    assert version["unprintable"][0]["code"] == "not_printable"
    assert version["corrections_count"] >= 1
    assert version["unprintable_count"] >= 1
    _save(api, alex, document, {**CV, "headline": "Second"}, 2)
    _render(factory, queue, clock)
    restored = api.post(f"/api/documents/{document}/versions/1/restore", token=alex)
    assert restored.status_code == 200
    assert restored.json()["draft_revision"] == 4
    assert len(queue.jobs) == 1
    approved = api.post(f"/api/documents/{document}/versions/1/approve", token=alex)
    assert approved.status_code == 200
    assert approved.json()["approved_at"] is not None
    listed = api.get(f"/api/documents/{document}/versions", token=alex).json()
    assert [v["number"] for v in listed] == [2, 1]
    assert api.get(f"/api/documents/{document}/versions/9/pdf", token=alex).status_code == 404


def test_template_and_language_switches(
    two: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue, clock: Any
) -> None:
    alex, blake, document = two["alex"], two["blake"], two["document"]
    _save(api, alex, document, CV, 1)
    _render(factory, queue, clock)
    with factory() as db:
        (version,) = db.execute(
            sa.select(DocumentVersion).where(DocumentVersion.document_id == document)
        ).scalars()
        store.freeze(db, two["alex_id"], version.id, FreezeReason.marked_sent, clock())
        frozen_sha = version.pdf_sha256
        db.commit()
    unknown = api.request(
        "PATCH", f"/api/documents/{document}", token=alex, json={"template_slug": "nope"}
    )
    assert unknown.status_code == 422
    # A slug longer than the column takes is refused by the schema (the body limit test wants
    # every schema bounded); 64 characters still reach the template lookup.
    for slug, schema in (("a" * 65, True), ("a" * 64, False)):
        refused = api.request(
            "PATCH", f"/api/documents/{document}", token=alex, json={"template_slug": slug}
        )
        assert refused.status_code == 422
        assert (refused.json()["detail"] == unknown.json()["detail"]) is not schema
        assert ("at most 64 characters" in refused.json()["detail"]) is schema
    # Another person's own template: Blake's row, asked for by Alex.
    with factory() as db:
        blake_id = db.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": BLAKE}
        ).scalar_one()
        db.execute(
            sa.text(
                "INSERT INTO document_user_templates (user_id, slug, name, manifest, cv_html, "
                "letter_html, css, font_families) VALUES "
                "(:u, 'upload-abcd1234', 'Mine', '{}', '', '', '', '{}')"
            ),
            {"u": blake_id},
        )
        db.commit()
    theirs = api.request(
        "PATCH", f"/api/documents/{document}", token=alex, json={"template_slug": "upload-abcd1234"}
    )
    assert theirs.status_code == 422
    switched = api.request(
        "PATCH", f"/api/documents/{document}", token=alex, json={"language": "fr"}
    )
    assert switched.status_code == 200
    assert switched.json()["language"] == "fr"
    assert switched.json()["latest"]["number"] == 2  # a new version after the frozen one
    _render(factory, queue, clock)
    with factory() as db:
        versions = list(
            db.execute(
                sa.select(DocumentVersion)
                .where(DocumentVersion.document_id == document)
                .order_by(DocumentVersion.number)
            ).scalars()
        )
    assert versions[0].pdf_sha256 == frozen_sha
    assert versions[0].frozen_at is not None
    assert versions[1].language.value == "fr"
    assert versions[1].pdf_sha256 is not None
    assert (
        api.request(
            "PATCH", f"/api/documents/{document}", token=blake, json={"template_slug": "classic"}
        ).status_code
        == 404
    )


def test_templates_thumbnails_and_settings(two: dict[str, Any], api: Api) -> None:
    alex, blake = two["alex"], two["blake"]
    gallery = api.get("/api/document-templates", token=alex).json()["templates"]
    classic = next(t for t in gallery if t["slug"] == "classic")
    assert classic["user"] is False
    assert set(classic["name"]) == {"en", "fr"}
    thumbnail = api.get(classic["thumbnails"]["cv"], token=blake)  # public data of the app
    assert thumbnail.status_code == 200
    assert thumbnail.content.startswith(b"\x89PNG")
    assert thumbnail.headers["cache-control"].startswith("public, max-age=")
    assert api.get("/api/document-templates/../../etc/thumbnail", token=alex).status_code == 404
    assert (
        api.get("/api/document-templates/upload-abcd1234/thumbnail", token=alex).status_code == 404
    )
    read = api.get("/api/documents/settings", token=alex)
    assert read.status_code == 200
    assert read.json() == {
        "no_dash_separators": False,
        "banned_phrases": [],
    }
    saved = api.request(
        "PUT",
        "/api/documents/settings",
        token=alex,
        json={"no_dash_separators": True, "banned_phrases": ["synergy"]},
    )
    assert saved.status_code == 200
    assert saved.json()["banned_phrases"] == ["synergy"]
    assert api.get("/api/documents/settings", token=blake).json()["no_dash_separators"] is False
    refused = api.request(
        "PUT", "/api/documents/settings", token=alex, json={"no_dash_separators": "yes"}
    )
    assert refused.status_code == 422


def test_the_render_queue_is_bounded_per_person(
    two: dict[str, Any],
    api: Api,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    clock: Any,
) -> None:
    monkeypatch.setattr(render_job, "QUEUED_PER_USER", 1)
    second = _document(factory, two["alex_id"], _posting(factory, two["alex_id"]), clock)
    assert (
        api.post(f"/api/documents/{two['document']}/render", token=two["alex"]).status_code == 202
    )
    refused = api.post(f"/api/documents/{second}/render", token=two["alex"])
    assert refused.status_code == 429
    assert "waiting" in refused.json()["detail"]


@pytest.fixture
def full(
    two: dict[str, Any],
    api: Api,
    factory: sessionmaker[Session],
    queue: RecordingQueue,
    monkeypatch: pytest.MonkeyPatch,
    clock: Any,
) -> dict[str, Any]:
    """Alex's second document with a rendered version 1, and the queue full: a bound of one,
    and a render waiting on the first document."""
    second = _document(factory, two["alex_id"], _posting(factory, two["alex_id"]), clock)
    assert _save(api, two["alex"], second, CV, 1).status_code == 200
    _render(factory, queue, clock)
    monkeypatch.setattr(render_job, "QUEUED_PER_USER", 1)
    assert (
        api.post(f"/api/documents/{two['document']}/render", token=two["alex"]).status_code == 202
    )
    assert api.post(f"/api/documents/{second}/render", token=two["alex"]).status_code == 429
    return {**two, "second": second, "waiting": list(queue.jobs)}


def _deferred(factory: sessionmaker[Session], queue: RecordingQueue, full: dict[str, Any]) -> None:
    """The edit's render waits, not queued now: resend_queued sends it within a minute."""
    with factory() as db:
        state = db.execute(
            sa.select(Document.render_state).where(Document.id == full["second"])
        ).scalar_one()
    assert state.value == "queued"
    assert queue.jobs == full["waiting"]


def test_a_save_is_stored_when_the_render_queue_is_full(
    full: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue
) -> None:
    saved = _save(api, full["alex"], full["second"], {**CV, "headline": "Kept"}, 2)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft_revision"] == 3
    assert saved.json()["draft"]["headline"] == "Kept"
    _deferred(factory, queue, full)


def test_a_restore_is_stored_when_the_render_queue_is_full(
    full: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue
) -> None:
    restored = api.post(f"/api/documents/{full['second']}/versions/1/restore", token=full["alex"])
    assert restored.status_code == 200, restored.text
    assert restored.json()["draft_revision"] == 3
    assert restored.json()["draft_written_by"] == "restore"
    _deferred(factory, queue, full)


@pytest.mark.parametrize(
    "change", [{"template_slug": "sidebar"}, {"language": "fr"}], ids=["template", "language"]
)
def test_a_switch_is_stored_when_the_render_queue_is_full(
    full: dict[str, Any],
    api: Api,
    factory: sessionmaker[Session],
    queue: RecordingQueue,
    change: dict[str, str],
) -> None:
    switched = api.request(
        "PATCH", f"/api/documents/{full['second']}", token=full["alex"], json=change
    )
    assert switched.status_code == 200, switched.text
    for key, value in change.items():
        assert switched.json()[key] == value
    _deferred(factory, queue, full)


def test_a_patch_that_changes_nothing_asks_for_no_render(
    full: dict[str, Any], api: Api, factory: sessionmaker[Session], queue: RecordingQueue
) -> None:
    same = {"template_slug": "classic", "language": "en"}
    for change in (same, {}):
        kept = api.request(
            "PATCH", f"/api/documents/{full['second']}", token=full["alex"], json=change
        )
        assert kept.status_code == 200, kept.text
        assert kept.json()["render"]["state"] == "idle"
        assert {key: kept.json()[key] for key in same} == same
    assert queue.jobs == full["waiting"]
    with factory() as db:
        count = db.execute(
            sa.select(sa.func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == full["second"])
        ).scalar_one()
    assert count == 1


@pytest.mark.parametrize(
    ("parts", "suffix", "name"),
    [
        (("Søren Kierkegaard", "Lumen"), "CV", "Soren_Kierkegaard_Lumen_CV"),
        (("Łukasz Żółć", "Ørsted"), "CV", "Lukasz_Zolc_Orsted_CV"),
        (("Æsa Hæð", "Straße AG"), "Letter", "AEsa_Haed_Strasse_AG_Letter"),
        (("Þóra Ðorđević", "Œuvre"), "CV", "Thora_Dordevic_OEuvre_CV"),
        (("Hélène Müller", "Café Crème"), "CV", "Helene_Muller_Cafe_Creme_CV"),
        (("Ana-María O'Neil", "R&D, Inc."), "CV", "Ana_Maria_O_Neil_R_D_Inc_CV"),
        (("İlkay Işık", "Ħamrun Ŧech"), "CV", "Ilkay_Isik_Hamrun_Tech_CV"),
        (("ﬁona", "①Two"), "CV", "fiona_1Two_CV"),
        (("漢字", "Лумен"), "CV", "CV"),
        (("", ""), "Letter", "Letter"),
        (("   ", "!!!"), "CV", "CV"),
        (("Alex Example", ""), "CV", "Alex_Example_CV"),
        (("Ａｌｅｘ　Ｅｘａｍｐｌｅ", ""), "CV", "Alex_Example_CV"),
        (("Nguyễn Văn An", "Đà Nẵng Co"), "CV", "Nguyen_Van_An_Da_Nang_Co_CV"),
        (("Ce\u0301line", "Zoe\u0308"), "CV", "Celine_Zoe_CV"),
        (("GROẞ", "MÆRSK"), "CV", "GROSS_MAERSK_CV"),
        (("Al\u200dex", "Lu\u00admen"), "CV", "Alex_Lumen_CV"),
        (("Alex 🚀", "Lumen\ufe0f"), "CV", "Alex_Lumen_CV"),
        (("Αλέξης", "Lumen"), "CV", "Lumen_CV"),
        (("Alex ٣", "Lumen ³"), "CV", "Alex_Lumen_3_CV"),
        (("Alex", "../../etc/passwd"), "CV", "Alex_etc_passwd_CV"),
        (('Alex"; x=1', "Lumen\r\nSet-Cookie"), "CV", "Alex_x_1_Lumen_Set_Cookie_CV"),
        (("Bjørn Øster", "Fjærland"), "Letter", "Bjorn_Oster_Fjaerland_Letter"),
        (("Paweł Wałęsa", ""), "CV", "Pawel_Walesa_CV"),
    ],
)
def test_the_file_name_spells_letters_in_ascii(
    parts: tuple[str, str], suffix: str, name: str
) -> None:
    assert ascii_name(*parts, suffix=suffix) == name


@pytest.mark.parametrize("suffix", ["CV", "Letter"])
@pytest.mark.parametrize(
    "parts",
    [("A" * 300, "Lumen"), ("Alex", "B" * 300), ("Ø" * 200, "Æ" * 200), ("a " * 200, "")],
)
def test_the_cut_at_the_name_limit_keeps_the_suffix(parts: tuple[str, str], suffix: str) -> None:
    name = ascii_name(*parts, suffix=suffix)
    assert len(name) <= NAME_MAX
    assert name.endswith(f"_{suffix}")
    assert "__" not in name
