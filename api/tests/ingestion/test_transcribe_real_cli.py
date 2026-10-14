"""The transcription path through the real Claude Agent SDK and its bundled Claude Code CLI
(ingestion G14, assumptions A6 and the image path of G0), opt-in only: set
JOBAPP_REAL_CLI_STUB=1. Skipped otherwise, and never in CI (it starts the CLI once per run).

No model is called and no credential is used: the CLI is pointed (MODEL_API_BASE_URL) at
tests/anthropic_stub.py, started in this process on a loopback port, with a made up key of the
right shape. The stub plays a model that views each image and stores a constant sentence for
it, and keeps what the tool results of each request held (sizes and hashes, `/_seen`). The
runner is the product's (`runner.execute` with `SdkBackend`, the fake backend off), the agents
are the product's, on a real Postgres.

What it holds, on the pinned SDK and CLI:
- the JPEG stored for a page arrives at the API as an `image` block of its tool result, byte
  for byte, with the pixel size it was stored with (so the CLI neither recompressed it nor
  replaced it by a thumbnail), one image per result, for a page close to the 300,000 bytes a
  stored image may weigh;
- the document is assembled from what the model stored, through the real tool calls;
- a `read_document` result of 30,000 French characters, and one of 40,000 UTF-8 bytes of
  Chinese, arrive whole and inline (A6): not cut, not replaced by a file reference;
- MAX_MCP_OUTPUT_TOKENS is absent from the environment the CLI inherits.

    cd api && JOBAPP_REAL_CLI_STUB=1 TEST_DATABASE_URL=... uv run --native-tls pytest \
        tests/ingestion/test_transcribe_real_cli.py -p no:cacheprovider -s
"""

import hashlib
import importlib.util
import io
import json
import os
import random
import threading
import uuid
from collections.abc import Iterator
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.agents.backend import SdkBackend
from app.agents.crypto import Keyring
from app.agents.models import RunStatus
from app.config import Settings
from app.ingestion import agents, tools
from app.ingestion.models import IMAGE_MAX_BYTES, IngestionImage, TextStatus
from tests.agents.g6_helpers import run_context
from tests.agents.run_helpers import add_user, execute, run_row, save_credential
from tests.conftest import make_settings
from tests.ingestion import extract_helpers
from tests.ingestion.extract_helpers import add_document, add_step, key, keyring
from tests.ingestion.transcribe_helpers import (
    HEIGHT,
    WIDTH,
    add_scan,
    add_transcribe_step,
    document_of,
    jpeg,
)

__all__ = ["key", "keyring"]

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBAPP_REAL_CLI_STUB") != "1",
    reason="set JOBAPP_REAL_CLI_STUB=1 to run the real Claude Code CLI against a local stub",
)

STUB_PATH = Path(__file__).resolve().parents[3] / "tests" / "anthropic_stub.py"
# A made up key of the right shape that the stub accepts: never sent anywhere but to it.
STUB_KEY = "sk-ant-api03-" + "e2egood" + "Ab1_" * 20
PAGE = (1109, 1568)  # an A4 page with its long side at G8's 1568 pixels
TRANSCRIBE = replace(agents.TRANSCRIBE, on_final=None)
EXTRACT = replace(agents.EXTRACT, on_final=None)
FRENCH = "Développeuse à Besançon, côté données : façade, œuvre, garçon, où, déjà. "
CHINESE = "数据工程师在里昂工作了三年，负责仓库的数据管道。"


def load_stub() -> Any:
    spec = importlib.util.spec_from_file_location("anthropic_stub", STUB_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stub() -> Iterator[tuple[Any, str]]:
    module = load_stub()
    module.DELAY_S, module.CHUNKS = 0.0, 1  # the final line at once
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield module, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def settings(migrated_url: str, key: str, tmp_path: Path, stub: tuple[Any, str]) -> Settings:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    worker.skip_sdk_version_probe()
    return make_settings(
        migrated_url,
        credentials_key=key,
        agents_scratch_dir=str(scratch),
        model_api_base_url=stub[1],
        agents_fake_backend=False,
    )


def new_user(factory: sessionmaker[Session], keyring: Keyring, name: str) -> uuid.UUID:
    user = add_user(factory, f"e2e-{name}@example.org")
    save_credential(factory, keyring, user, STUB_KEY)
    return user


def dense_page() -> bytes:
    """A page of noise at the size of a rendered A4 scan, as heavy as a stored image may be:
    the heaviest JPEG under IMAGE_MAX_BYTES among a few qualities."""
    noise = random.Random(14).randbytes(PAGE[0] * PAGE[1])  # noqa: S311, a picture, not a secret
    picture = Image.frombytes("L", PAGE, noise).convert("RGB")
    for quality in (40, 30, 22, 16, 11, 7, 4, 2, 1):
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=quality)
        if out.tell() <= IMAGE_MAX_BYTES:
            return out.getvalue()
    raise AssertionError("no quality fits a stored image")


def image_blocks(module: Any) -> list[list[dict[str, Any]]]:
    """Per tool result that held an image, its blocks as the stub saw them."""
    return [
        entry["blocks"]
        for entry in module.SEEN
        if any(block["type"] == "image" for block in entry["blocks"])
    ]


def test_a_stored_page_arrives_as_one_image_block_with_its_bytes_and_its_pixel_size(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    stub: tuple[Any, str],
) -> None:
    module = stub[0]
    user = new_user(factory, keyring, "g14-image")
    heavy = dense_page()
    assert len(heavy) > IMAGE_MAX_BYTES // 2
    document = add_scan(factory, user, [None, "A page read as text.", None])
    with factory() as db, db.begin():
        db.execute(
            sa.update(IngestionImage)
            .where(IngestionImage.document_id == document, IngestionImage.position == 0)
            .values(jpeg=heavy, width=PAGE[0], height=PAGE[1])
        )
    step = add_transcribe_step(factory, user, document, [0, 2])
    context = run_context(settings, factory, keyring, TRANSCRIBE, backends=SdkBackend)
    status = execute(context, step.agent_run_id)
    assert status is RunStatus.done, run_row(factory, step.agent_run_id).error

    expected = [(heavy, PAGE), (jpeg(2), (WIDTH, HEIGHT))]
    seen = image_blocks(module)
    assert len(seen) == len(expected)  # one result per image
    for blocks, (data, (width, height)) in zip(seen, expected, strict=True):
        assert [block for block in blocks if block["type"] == "image"] == [
            {
                "type": "image",
                "media_type": "image/jpeg",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "width": width,
                "height": height,
            }
        ]
        assert not any(block.get("cut") for block in blocks)
    assert "MAX_MCP_OUTPUT_TOKENS" not in os.environ
    found = document_of(factory, document)
    assert found.text == "\f".join(
        [module.transcription_of(0), "A page read as text.", module.transcription_of(2)]
    )
    assert (found.text_status, found.transcribed_pages) == (TextStatus.done, [0, 2])
    row = run_row(factory, step.agent_run_id)
    assert row.output is not None
    assert (row.output["tool_calls"], row.output["tool_errors"]) == (4, 0)
    assert row.input_tokens == 5 * 120  # five requests, each as the stub reported it
    print(
        f"\nG14: {len(seen)} images forwarded, the heavy one {len(heavy)} bytes at "
        f"{PAGE[0]} by {PAGE[1]}; run recorded {row.input_tokens} input tokens"
    )


@pytest.mark.parametrize(("language", "sentence"), [("french", FRENCH), ("chinese", CHINESE)])
def test_a_full_read_document_result_arrives_inline_a6(
    settings: Settings,
    factory: sessionmaker[Session],
    keyring: Keyring,
    stub: tuple[Any, str],
    language: str,
    sentence: str,
) -> None:
    module = stub[0]
    user = new_user(factory, keyring, f"g14-{language}")
    text = sentence * (45_000 // len(sentence) + 1)
    document = add_document(factory, user, text)
    step = add_step(factory, user, document, start=0, end=len(text))
    answer = extract_helpers.raw_answer(
        extract_helpers.tools_for(factory, step), "read_document", offset=0
    )
    piece = json.loads(answer)["untrusted_text"]
    if language == "french":
        assert len(piece) == tools.READ_MAX_CHARS
    else:  # three bytes a character: the limit in bytes is the one that binds
        assert tools.READ_MAX_BYTES - 3 < len(piece.encode()) <= tools.READ_MAX_BYTES
    context = run_context(settings, factory, keyring, EXTRACT, backends=SdkBackend)
    status = execute(context, step.agent_run_id)
    assert status is RunStatus.done, run_row(factory, step.agent_run_id).error
    texts = [
        block
        for entry in module.SEEN
        for block in entry["blocks"]
        if block["type"] == "text" and block["chars"] > 1_000
    ]
    assert texts == [
        {
            "type": "text",
            "chars": len(answer),
            "utf8_bytes": len(answer.encode()),
            "sha256": hashlib.sha256(answer.encode()).hexdigest(),
            "cut": False,
        }
    ]
    print(f"\nA6 {language}: {len(piece)} characters, {len(answer.encode())} bytes, inline")
