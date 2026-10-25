"""D24: an uploaded CV converted into a base CV, end to end on the fake backend (no model, no
key). The conversion of an invented CV whose claims and fixed facts the file states (no
correction, the profile's default replaced), what the file does not state removed by code and
recorded (a team size, a title's seniority word, an employer and its entry, a month, a
language), the same claim passing with the file and corrected without it, a French CV written in
French, a CV with little text, a second upload, the file deleted before and after, waiting for
the consent and the credential, the design capture's hook, the bound run and its tool, and the
canary in the file. Real Postgres at head."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app import queue as queues
from app.agents import credentials
from app.agents.crypto import Keyring
from app.agents.definitions import FinalRun
from app.agents.models import AgentRun, CredentialKind
from app.agents.tools import Scope
from app.documents import (  # noqa: F401
    consent,
    design_capture,
    generate,
    store,
    writer_base,
    writers,
)
from app.documents import content as c
from app.documents.models import (
    BaseSource,
    Document,
    DocumentGeneration,
    GenerationMode,
    GenerationStatus,
    GenerationWait,
    Language,
    Origin,
)
from app.documents.upload import FIXED_NOT_IN_UPLOAD
from app.ingestion.models import IngestionDocument
from app.profile import service as profile_service
from app.profile.schemas import parse_document
from tests.agents.conftest import API_KEY, new_key
from tests.agents.g6_helpers import everything_stored
from tests.conftest import make_settings
from tests.documents.canary import find, marker
from tests.documents.conftest import FIXTURES, load
from tests.documents.store_support import AT, USER_A, USER_B, db, engine, factory, migrated_url
from tests.documents.writer_support import (
    Person,
    document_row,
    execute,
    generation_row,
    person,
    versions,
)

__all__ = ["db", "engine", "factory", "migrated_url"]

MORGAN = load("upload_morgan.txt")
CAMILLE = load("upload_camille_fr.txt")
SCANNED = load("upload_scanned.txt")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def add_upload(
    factory: sessionmaker[Session],
    user: uuid.UUID,
    text: str,
    *,
    mime: str = "application/pdf",
    name: str = "morgan-cv.pdf",
) -> uuid.UUID:
    """An ingestion document of `user` whose text ingestion read."""
    with factory() as db:
        found = db.execute(
            sa.text(
                "INSERT INTO ingestion_documents (user_id, name, mime, size, sha256, text, "
                "text_source, text_status, parser_version) VALUES (:u, :n, :m, :s, :h, :t, "
                "'parsed', 'done', 2) RETURNING id"
            ),
            {
                "u": user,
                "n": name,
                "m": mime,
                "s": len(text.encode()),
                "h": hashlib.sha256(f"{user}{text}{mime}".encode()).digest(),
                "t": text,
            },
        ).scalar_one()
        db.commit()
        return found


def printed(content: dict[str, Any]) -> str:
    return "\n".join(text for _, text in c.text_fields(c.parse("base", content)))


def convert(
    who: Person,
    upload_id: uuid.UUID,
    script: str,
    *,
    consented: bool = True,
    run: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    """The base CV converted from `upload_id` and its run executed: (document, generation)."""
    with who.factory() as db:
        if consented and not consent.has_consented(db, who.user):
            consent.record(db, who.user, AT)
        built = writer_base.build_from_upload(db, who.user, upload_id, at=AT)
        assert built.generation is not None
        runs = writer_base.start_ready(db, who.user, at=AT, script=script, settings=who.settings)
        db.commit()
        document_id, generation_id = built.document.id, built.generation.id
    if run:
        for run_id in runs:
            execute(who, run_id)
    return document_id, generation_id


@pytest.fixture
def hooks(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    called: list[uuid.UUID] = []

    async def record(final: FinalRun, generation_id: uuid.UUID) -> None:
        with final.sessions() as db:
            generation = db.get(DocumentGeneration, generation_id)
            assert generation is not None
            assert generation.user_id == final.user_id
            assert generation.mode is GenerationMode.template_capture
            assert generation.run_id is None
            called.append(generation.document_id)

    monkeypatch.setattr(design_capture, "enqueue", record)
    return called


def test_the_writer_reads_the_upload_through_its_own_tool() -> None:
    assert writer_base.BASE_WRITER.tools == (
        "read_profile_section",
        "get_upload_text",
        "submit_base",
    )


def test_an_uploaded_cv_becomes_the_default_base_cv_with_the_files_fixed_facts(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN)
    document_id, generation_id = convert(who, upload, "documents_base_upload_valid")
    generation = generation_row(factory, generation_id)
    assert generation.mode is GenerationMode.base_upload
    assert generation.status is GenerationStatus.done, generation.error
    (version,) = versions(factory, document_id)
    assert version.origin is Origin.generated
    assert version.corrections == []  # every claim and fixed fact is the file's
    document = document_row(factory, document_id)
    assert document.draft == version.content
    assert (document.base_source, document.source_document_id) == (BaseSource.uploaded, upload)
    assert (document.name, document.language) == ("morgan-cv", Language.en)
    assert document.is_default  # the default was the one built from the profile
    assert not document_row(factory, who.base).is_default
    fixed = document.draft["fixed"]
    assert [(e["organisation"], e["title"], e["start"], e["end"]) for e in fixed["employers"]] == [
        ("Quillmere Example Works", "Platform Engineer", "2023-02", None),
        ("Tarnwick Sample Studio", "Software Developer", "2021-01", "2023-01"),
    ]
    assert [s["school"] for s in fixed["schools"]] == ["Example Institute of Technology"]
    assert [(x["name"], x["level"]) for x in fixed["languages"]] == [
        ("English", "fluent"),
        ("French", "native"),
    ]
    assert "Moved 30 services to Kubernetes with Terraform." in printed(document.draft)
    assert [job["function"] for job in who.queue.jobs] == ["render_document"]
    assert hooks == [document_id]


def test_what_the_file_does_not_state_is_removed_by_code_and_recorded(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN)
    document_id, generation_id = convert(who, upload, "documents_base_upload_invented")
    generation = generation_row(factory, generation_id)
    assert generation.status is GenerationStatus.done, generation.error
    (version,) = versions(factory, document_id)
    content = version.content
    assert "12 engineers" not in printed(content)
    fixed = content["fixed"]
    assert [e["organisation"] for e in fixed["employers"]] == [
        "Quillmere Example Works",
        "Tarnwick Sample Studio",
    ]
    assert fixed["employers"][0]["title"] == ""  # "Senior Platform Engineer" is not the file's
    assert fixed["employers"][1]["start"] == "2021"  # the file says 01/2021, not March
    assert [x["name"] for x in fixed["languages"]] == ["English", "French"]
    assert [e["employer_key"] for e in content["experience"]] == [
        "quillmere",
        "tarnwick",
    ]
    assert "Globex" not in str(content)
    records = {(r["field_path"], r["before"], r["after"]) for r in version.corrections}
    assert {
        ("fixed.employers.2.organisation", "Globex Example Systems", ""),
        ("fixed.employers.0.title", "Senior Platform Engineer", ""),
        ("fixed.employers.1.start", "2021-03", "2021"),
        ("fixed.languages.2.name", "German", ""),
        ("experience.2.bullets.0", "Designed the billing system of Globex.", ""),
    } <= records
    assert all(
        r["by"] == "code" and r["reason"] == "Not in your uploaded CV"
        for r in version.corrections
        if r["code"] == FIXED_NOT_IN_UPLOAD
    )
    assert any(r["code"] != FIXED_NOT_IN_UPLOAD for r in version.corrections)  # the team size
    assert generation.corrections == len(version.corrections)
    assert hooks == [document_id]


def test_a_claim_of_the_file_passes_and_the_same_claim_without_the_file_is_corrected(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    # The same stream twice: it keeps "30 services" after the correction pass both times.
    who = person(factory, migrated_url, USER_A)
    with_it, _ = convert(
        who, add_upload(factory, who.user, MORGAN), "documents_base_upload_invented"
    )
    without = MORGAN.replace("Moved 30 services to Kubernetes with Terraform.\n", "")
    upload = add_upload(factory, who.user, without, name="shorter.pdf")
    without_it, _ = convert(who, upload, "documents_base_upload_invented")
    (kept,) = versions(factory, with_it)
    assert "30 services" in printed(kept.content)
    (corrected,) = versions(factory, without_it)
    assert "30 services" not in printed(corrected.content)
    assert any("30" in r["before"] and r["by"] == "code" for r in corrected.corrections)
    assert "Run the deployment platform of the billing team" in printed(corrected.content)


def test_a_french_cv_is_converted_in_french(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, CAMILLE, mime=DOCX, name="cv-camille.docx")
    document_id, generation_id = convert(who, upload, "documents_base_upload_french")
    assert generation_row(factory, generation_id).status is GenerationStatus.done
    document = document_row(factory, document_id)
    assert document.language is Language.fr
    (version,) = versions(factory, document_id)
    assert version.corrections == []
    fixed = version.content["fixed"]
    assert fixed["employers"][0]["start"] == "2022-03"  # "Depuis mars 2022"
    assert fixed["schools"][0]["school"] == "École Exemple d'Ingénieurs"
    assert [x["name"] for x in fixed["languages"]] == ["Français", "Anglais"]


def test_a_cv_with_little_text_gives_a_short_base_cv_and_nothing_is_refused(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, SCANNED, name="scan.pdf")
    document_id, generation_id = convert(who, upload, "documents_base_upload_scanned")
    assert generation_row(factory, generation_id).status is GenerationStatus.done
    (version,) = versions(factory, document_id)
    fixed = version.content["fixed"]
    assert fixed["employers"][0]["organisation"] == "Northwind Example"
    assert fixed["employers"][0]["start"] is None  # "2020" alone does not say the role goes on
    assert [(r["field_path"], r["before"]) for r in version.corrections] == [
        ("fixed.employers.0.start", "2020")
    ]
    assert (fixed["schools"], fixed["languages"]) == ([], [])
    assert version.content["experience"] == []


def test_a_second_upload_is_a_second_base_cv_and_the_first_is_untouched(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    first, _ = convert(who, add_upload(factory, who.user, MORGAN), "documents_base_upload_valid")
    before = document_row(factory, first)
    second, _ = convert(
        who,
        add_upload(factory, who.user, CAMILLE, name="autre.pdf"),
        "documents_base_upload_french",
    )
    assert second != first
    after = document_row(factory, first)
    assert (after.draft, after.draft_revision) == (before.draft, before.draft_revision)
    # The default was an upload: a new upload does not take it.
    assert (after.is_default, document_row(factory, second).is_default) == (True, False)
    assert hooks == [first, second]


def test_a_built_default_stays_the_default(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    with factory() as db:
        db.execute(
            sa.update(Document).where(Document.id == who.base).values(base_source=BaseSource.built)
        )
        db.commit()
    document_id, _ = convert(
        who, add_upload(factory, who.user, MORGAN), "documents_base_upload_valid"
    )
    assert not document_row(factory, document_id).is_default
    assert document_row(factory, who.base).is_default


def test_the_file_deleted_in_ingestion_leaves_the_base_cv(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN)
    document_id, _ = convert(who, upload, "documents_base_upload_valid")
    kept = document_row(factory, document_id).draft
    with factory() as db:
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == upload))
        db.commit()
    document = document_row(factory, document_id)
    assert (document.source_document_id, document.draft) == (None, kept)
    assert len(versions(factory, document_id)) == 1


def test_the_file_deleted_before_the_run_fails_the_generation_and_leaves_the_base_cv(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN)
    document_id, generation_id = convert(
        who, upload, "documents_base_upload_valid", consented=False
    )
    with factory() as db:
        db.execute(sa.delete(IngestionDocument).where(IngestionDocument.id == upload))
        consent.record(db, who.user, AT)
        assert writer_base.start_ready(db, who.user, at=AT, settings=who.settings) == []
        db.commit()
    generation = generation_row(factory, generation_id)
    assert (generation.status, generation.error) == (GenerationStatus.failed, "binding_gone")
    assert document_row(factory, document_id).base_source is BaseSource.uploaded
    assert hooks == []


def test_without_consent_or_credential_the_conversion_waits_and_nothing_is_sent(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN)
    _, generation_id = convert(who, upload, "documents_base_upload_valid", consented=False)
    generation = generation_row(factory, generation_id)
    assert (generation.status, generation.wait_reason) == (
        GenerationStatus.waiting,
        GenerationWait.consent,
    )
    with factory() as db:
        consent.record(db, who.user, AT)
        credentials.delete(db, who.user)
        assert writer_base.start_ready(db, who.user, at=AT, settings=who.settings) == []
        db.commit()
    generation = generation_row(factory, generation_id)
    assert (generation.status, generation.wait_reason) == (
        GenerationStatus.waiting,
        GenerationWait.credential,
    )
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AgentRun)) == 0
    assert hooks == []


def test_the_run_is_bound_to_its_own_file_and_the_tool_reads_only_that_one(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    alex = person(factory, migrated_url, USER_A)
    blake = person(factory, migrated_url, USER_B)
    upload = add_upload(factory, alex.user, MORGAN)
    add_upload(factory, blake.user, CAMILLE)
    _, generation_id = convert(alex, upload, "documents_base_upload_valid", run=False)
    run_id = generation_row(factory, generation_id).run_id
    assert run_id is not None
    with factory() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        binding = writer_base.bind_generation(db, run)
        assert binding is not None
        assert (binding["mode"], binding["upload_id"]) == ("base_upload", str(upload))
        run.user_id = blake.user
        assert writer_base.bind_generation(db, run) is None
        db.rollback()
    with factory() as db:
        assert writer_base.upload_text(db, alex.user, upload) == MORGAN
        assert writer_base.upload_text(db, blake.user, upload) is None


def test_the_tool_answers_a_constant_for_a_profile_build_and_a_file_gone(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    pytest.importorskip("claude_agent_sdk")
    from app.documents import writer_base_tools as tools

    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN)
    profile = Scope(factory, who.user, binding={"mode": "base_profile", "upload_id": None})
    assert tools._read_upload(profile) == (tools.NO_UPLOAD, True)
    bound = Scope(factory, who.user, binding={"mode": "base_upload", "upload_id": str(upload)})
    text, error = tools._read_upload(bound)
    assert not error
    assert '"truncated": false' in text
    stranger = Scope(
        factory, uuid.uuid4(), binding={"mode": "base_upload", "upload_id": str(upload)}
    )
    assert tools._read_upload(stranger) == (tools.UPLOAD_GONE, True)


def test_a_long_upload_is_read_up_to_the_cap(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    who = person(factory, migrated_url, USER_A)
    long = MORGAN + "Built internal tools. " * 3_000
    upload = add_upload(factory, who.user, long, name="long.pdf")
    with factory() as db:
        text = writer_base.upload_text(db, who.user, upload)
        assert text is not None
        assert len(text) == writer_base.UPLOAD_MAX_CHARS
        assert writer_base.readable_upload(db, who.user, upload).characters == len(text)


def test_the_canary_in_the_file_reaches_no_log_no_event_no_job_and_no_redis(
    factory: sessionmaker[Session],
    migrated_url: str,
    caplog: pytest.LogCaptureFixture,
    hooks: list[uuid.UUID],
) -> None:
    planted = marker()
    who = person(factory, migrated_url, USER_A)
    upload = add_upload(factory, who.user, MORGAN + f"\nPlanted line {planted}.\n")
    caplog.set_level(logging.DEBUG)
    _, generation_id = convert(who, upload, "documents_base_upload_valid")
    generation = generation_row(factory, generation_id)
    assert generation.status is GenerationStatus.done
    assert generation.run_id is not None
    stored = everything_stored(factory, generation.run_id)
    assert planted not in stored
    assert "billing team" not in stored
    redis_client = None
    url = os.environ.get("REDIS_URL")
    if url:
        import redis

        redis_client = redis.Redis.from_url(url)

        async def through_redis() -> None:
            real = queues.Queue(url)
            try:
                await generate.enqueue_run(real, generation.run_id)
            finally:
                await real.close()

        asyncio.run(through_redis())
    try:
        assert not find(
            [planted],
            logs=caplog.records,
            values=[job["args"] for job in who.queue.jobs],
            redis=redis_client,
        )
    finally:
        if redis_client is not None:
            redis_client.delete(f"arq:job:{queues.agent_run_job_id(generation.run_id)}")
            redis_client.close()
    assert "billing team" not in caplog.text


def test_another_persons_upload_is_not_found(
    factory: sessionmaker[Session], migrated_url: str
) -> None:
    alex = person(factory, migrated_url, USER_A)
    blake = person(factory, migrated_url, USER_B)
    theirs = add_upload(factory, blake.user, MORGAN)
    with factory() as db, pytest.raises(writer_base.UploadRefusedError) as refused:
        writer_base.build_from_upload(db, alex.user, theirs, at=AT)
    assert refused.value.code == "not_found"
    with factory() as db:
        assert store.base_cvs(db, alex.user)[0].id == alex.base


def _morgan_converts(
    factory: sessionmaker[Session], migrated_url: str, text: str, script: str | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """The person of web/e2e/documents-d24.spec.ts (`invented()`, profile_morgan_export.json,
    whose skills name no PostgreSQL) converts `text`, on the default stream unless `script` is
    given (the page names none): (document, generation)."""
    key = new_key()
    settings = make_settings(migrated_url, credentials_key=key, agents_fake_backend=True)
    keyring = Keyring.from_text(key)
    export = parse_document((FIXTURES / "profile_morgan_export.json").read_bytes())
    with factory() as db:
        user = db.execute(
            sa.text(
                "INSERT INTO users (email, name, password_hash) VALUES (:e, :n, 'h') RETURNING id"
            ),
            {"e": f"e2e-documents-d24-{uuid.uuid4().hex[:8]}@example.org", "n": "Morgan"},
        ).scalar_one()
        profile_service.import_profile(db, user, export.stored_sections(), None, None, AT)
        credentials.save(db, keyring, user, CredentialKind.anthropic_api_key, API_KEY, AT)
        consent.record(db, user, AT)
        db.commit()
    upload = add_upload(factory, user, text)
    with factory() as db:
        built = writer_base.build_from_upload(db, user, upload, at=AT)
        runs = writer_base.start_ready(db, user, at=AT, script=script, settings=settings)
        db.commit()
    assert built.generation is not None
    who = Person(factory, settings, keyring, user, uuid.uuid4(), built.document.id)
    for run_id in runs:
        execute(who, run_id)
    return built.document.id, built.generation.id


def test_the_journeys_person_converts_the_journeys_cv_on_the_default_stream(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    document_id, generation_id = _morgan_converts(factory, migrated_url, MORGAN)
    generation = generation_row(factory, generation_id)
    assert generation.status is GenerationStatus.done, generation.error
    (version,) = versions(factory, document_id)
    assert version.corrections == []  # PostgreSQL is sourced by the file, not by the profile
    assert "with FastAPI and PostgreSQL" in printed(version.content)
    document = document_row(factory, document_id)
    assert document.is_default
    assert [e["employer_key"] for e in document.draft["experience"]] == ["quillmere", "tarnwick"]
    assert hooks == [document_id]


def test_a_name_neither_the_file_nor_the_profile_writes_is_corrected(
    factory: sessionmaker[Session], migrated_url: str, hooks: list[uuid.UUID]
) -> None:
    # The stream keeps the name after the correction pass: code removes it.
    without = MORGAN.replace(" and PostgreSQL", "").replace(", PostgreSQL", "")
    assert "PostgreSQL" not in without
    with_it, _ = _morgan_converts(factory, migrated_url, MORGAN, "documents_base_upload_invented")
    (kept,) = versions(factory, with_it)
    assert "with FastAPI and PostgreSQL" in printed(kept.content)
    document_id, generation_id = _morgan_converts(
        factory, migrated_url, without, "documents_base_upload_invented"
    )
    assert generation_row(factory, generation_id).status is GenerationStatus.done
    (version,) = versions(factory, document_id)
    assert "with FastAPI and PostgreSQL" not in printed(version.content)
    assert any("PostgreSQL" in r["before"] for r in version.corrections)
