"""The worker imports D7's writers where it builds the documents and agents queues (D6's hook
point): the import is what sets `render_job.on_overflow` in the process that renders."""

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import queue as queues
from app import worker
from app.config import Settings
from app.documents import render_client, render_job, store

HOOK = """
from app.documents import render_job

def on_overflow(document_id, version_id):
    on_overflow.calls.append((document_id, version_id))

on_overflow.calls = []
render_job.on_overflow = on_overflow
"""


@pytest.fixture
def writers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A stand in for app/documents/writers.py, under a name of its own, that sets the hook."""
    name = f"fake_writers_{uuid.uuid4().hex}"
    (tmp_path / f"{name}.py").write_text(HOOK, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(render_job, "on_overflow", render_job.on_overflow)
    yield name
    sys.modules.pop(name, None)


def test_the_writers_module_is_imported_and_sets_the_overflow_hook(writers: str) -> None:
    before = render_job.on_overflow
    worker.register_document_writers(writers)
    assert writers in sys.modules
    assert render_job.on_overflow is not before
    render_job.on_overflow(uuid.UUID(int=1), uuid.UUID(int=2))
    assert render_job.on_overflow.calls == [(uuid.UUID(int=1), uuid.UUID(int=2))]  # type: ignore[attr-defined]


def test_a_missing_writers_module_is_skipped() -> None:
    before = render_job.on_overflow
    worker.register_document_writers(f"fake_writers_absent_{uuid.uuid4().hex}")
    assert render_job.on_overflow is before


def test_a_writers_module_that_fails_to_import_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"fake_writers_broken_{uuid.uuid4().hex}"
    (tmp_path / f"{name}.py").write_text("raise RuntimeError('broken writer')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(RuntimeError, match="broken writer"):
        worker.register_document_writers(name)


def test_the_default_module_is_d7s() -> None:
    assert worker.DOCUMENT_WRITERS == "app.documents.writers"


@pytest.mark.parametrize("build", ["documents_queue", "register_ingestion_agents"])
def test_both_queue_builders_register_the_writers(
    build: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(worker, "register_document_writers", lambda: calls.append(build))
    built = getattr(worker, build)()
    assert calls == [build]
    if build == "documents_queue":
        assert built.name == queues.DOCUMENTS


def test_the_documents_queue_of_the_worker_registers_them(
    writers: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end in this process: building the queues, as `serve` does, sets the hook."""
    monkeypatch.setattr(worker, "DOCUMENT_WRITERS", writers)
    before = render_job.on_overflow
    names = [spec.name for spec in worker.queues()]
    assert queues.DOCUMENTS in names
    assert render_job.on_overflow is not before


def test_a_render_reads_its_settings_after_the_secrets_left_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker drops its secrets from the environment before it serves, and a render reads
    its settings with `get_settings()` (render_client, store): they must already be loaded, or
    the first render of a started worker fails with ConfigError (found by the D6 journey)."""
    environ = dict(os.environ)
    environ.update(
        DATABASE_URL="postgresql+psycopg://jobapp:pw-canary@db:5432/jobapp",
        APP_ENV="test",
        DOCUMENTS_RENDER_TIMEOUT_S="17",
        DOCUMENTS_BASE_CVS_MAX="4",
    )
    monkeypatch.setattr(os, "environ", environ)
    seen: dict[str, object] = {}

    async def serve(settings: Settings, specs: object) -> bool:
        seen["left"] = "DATABASE_URL" not in os.environ
        seen["timeout"] = render_client._setting("documents_render_timeout_s")
        seen["bases"] = store._limit(None, "documents_base_cvs_max")
        return False

    monkeypatch.setattr(worker, "serve", serve)
    monkeypatch.setattr(worker, "configure_logging", lambda level: None)
    assert worker.main([]) == 0
    assert seen == {"left": True, "timeout": 17, "bases": 4}
