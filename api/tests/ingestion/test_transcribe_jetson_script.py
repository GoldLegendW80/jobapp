"""The Python that tests/ingestion_transcribe_jetson.sh runs (ingestion G14, assumption A7),
held against the test database so that the script cannot drift away from the app: the page it
draws, the document and the run it starts with the app's own functions, and its error rates.
The script itself is never run here: it talks to the Jetson and makes one real model call."""

import base64
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from app.ingestion import engine
from app.ingestion.models import IMAGE_MAX_BYTES, IMAGE_MAX_SIDE, TextStatus
from app.ingestion.runs_models import IngestionItem, StepStatus
from app.ingestion.runs_models import RunStatus as ChainStatus
from tests.agents.run_helpers import add_user, now, run_row, save_credential
from tests.ingestion.engine_helpers import advance, run_of, steps_of
from tests.ingestion.extract_helpers import key, keyring
from tests.ingestion.test_transcribe_step import run_agent, settings
from tests.ingestion.transcribe_helpers import document_of, images_of
from tests.ingestion.transcribe_streams import DEFAULT_LINE

__all__ = ["key", "keyring", "settings"]

SCRIPT = Path(__file__).resolve().parents[3] / "tests" / "ingestion_transcribe_jetson.sh"


def program(name: str) -> dict[str, Any]:
    """The names a Python block of the script defines, without running its `main`."""
    found = re.search(rf"<<'{name}'\n(.*?)\n{name}\n", SCRIPT.read_text(encoding="utf-8"), re.S)
    assert found is not None
    names: dict[str, Any] = {"__name__": name.lower()}
    exec(compile(found.group(1), name, "exec"), names)  # noqa: S102, this repository's own script
    return names


def test_the_script_is_valid_bash_and_makes_no_model_call_by_default() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    checked = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, check=False)
    assert checked.returncode == 0, checked.stderr.decode()[-500:]
    assert 'REAL="${REAL_RUN:-0}"' in text
    assert text.index('if [[ "$REAL" != "1" ]]; then') < text.index('step "Start"')
    for word in ("ANTHROPIC_API_KEY", "sk-ant-", "CREDENTIALS_KEY"):
        assert word not in text  # no credential is read, named or passed by the script


def test_the_page_is_a_dense_a4_within_the_limits_of_a_stored_image() -> None:
    page = program("PAGE_PY")["page"]
    first, second = page(), page()
    assert first == second  # the known text is the same at every run
    picture = base64.b64decode(first["jpeg"])
    assert picture[:2] == b"\xff\xd8"
    assert len(picture) <= IMAGE_MAX_BYTES
    assert (first["width"], first["height"]) == (1109, 1568)
    assert max(first["width"], first["height"]) <= IMAGE_MAX_SIDE
    assert len(first["text"]) > 2_500
    assert len(first["text"].splitlines()) > 60
    assert "@example.org" in first["text"]


def test_the_error_rates_are_edit_distances_over_the_known_text() -> None:
    names = program("MEASURE_PY")
    rates, distance = names["rates"], names["distance"]
    assert distance("kitten", "sitting") == 3
    assert distance(["a", "b"], ["a", "c", "b"]) == 1
    same = rates(
        "Example Works,  Lyon\n\nBuilt 12 tables.", "Example Works, Lyon\nBuilt 12 tables."
    )
    assert (same["character_edits"], same["word_edits"]) == (0, 0)
    assert (same["lines_exact"], same["lines"]) == (2, 2)
    off = rates("Built 12 tables.", "Built 72 tables.")
    assert (off["character_edits"], off["word_edits"], off["lines_exact"]) == (1, 1, 0)
    assert off["character_error_rate"] == round(1 / 16, 5)
    assert rates("abc", "")["character_error_rate"] == 1.0


def test_the_start_of_the_script_runs_one_transcription_step_and_proposes_nothing(
    settings: Settings, factory: sessionmaker[Session], keyring: Keyring
) -> None:
    user = add_user(factory, "e2e-operator@example.org")
    save_credential(factory, keyring, user)
    seed = program("SEED_PY")["seed"]
    page = program("PAGE_PY")["page"]()
    with factory() as db, db.begin():
        started = seed(db, settings, user, page, "g14-dense-a4-test.jpg", now())
    run_id = uuid.UUID(started["run_id"])
    document = uuid.UUID(started["document_id"])
    assert started["run_status"] == "running"
    assert started["agent_run_id"] is not None
    steps = steps_of(factory, run_id)
    assert [(step.kind, step.status) for step in steps] == [("transcribe", StepStatus.running)]
    queued = run_row(factory, uuid.UUID(started["agent_run_id"]))
    assert queued.agent == engine.TRANSCRIBE_NAME
    assert images_of(factory, document) == {0: None}
    assert run_agent(settings, factory, keyring, queued.id)[0] is RunStatus.done
    advance(factory, run_id, settings)
    assert run_of(factory, run_id).status is ChainStatus.done  # no extraction was planned
    assert len(steps_of(factory, run_id)) == 1
    found = document_of(factory, document)
    assert (found.text, found.text_status) == (DEFAULT_LINE, TextStatus.done)
    with factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(IngestionItem)) == 0


@pytest.mark.parametrize("name", ["BEFORE_PY", "STATUS_PY"])
def test_the_read_only_blocks_compile(name: str) -> None:
    found = re.search(rf"<<'{name}'\n(.*?)\n{name}\n", SCRIPT.read_text(encoding="utf-8"), re.S)
    assert found is not None
    compile(found.group(1), name, "exec")
