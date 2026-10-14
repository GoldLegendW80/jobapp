"""The recorded streams of the journeys against their fixtures (tests/ingestion/
journey_streams.py): every stream named after a hash has its fixture, the files are the ones
written from their source, an extraction cites only words of its document (for an image, of
what its transcription stream stores), and the large one keeps the run of the journey between
two steps."""

from __future__ import annotations

import hashlib
import json
import re
import uuid

import pytest

from app.agents import fake_backend
from app.ingestion import estimate, plan
from tests.ingestion import journey_streams
from tests.ingestion.journey_streams import (
    FIXTURES,
    LARGE_FIXTURE,
    LARGE_TOKENS,
    PLAIN,
    STREAMS,
    TRANSCRIPTIONS,
    path_of,
    transcription_path_of,
)

NAMED = re.compile(r"^ingest_[a-z]+_([0-9a-f]{12})_p\d+$")


def _step(fixture: str) -> plan.Step:
    characters = len((FIXTURES / fixture).read_text(encoding="utf-8"))
    identity = plan.Identity("0" * 64, "extract", (), plan.EXTRACTOR_VERSION, "")
    return plan.Step(
        plan.StepKind.extract, uuid.uuid4(), identity, reused=False, characters=characters
    )


def test_every_stream_named_after_a_hash_has_its_fixture_file() -> None:
    fixtures = {
        hashlib.sha256(path.read_bytes()).hexdigest()[:12]: path.name
        for path in FIXTURES.iterdir()
        if path.is_file()
    }
    named = {
        path.name: found.group(1)
        for path in fake_backend.STREAMS.glob("ingest_*.json")
        if (found := NAMED.match(path.stem)) is not None
    }
    assert named, "the journey's streams are missing"
    assert {name: digest for name, digest in named.items() if digest not in fixtures} == {}
    assert set(named) == {path_of(name).name for name in STREAMS} | {
        transcription_path_of(name).name for name in TRANSCRIPTIONS
    }
    # An image has no text of its own: its extraction has a transcription before it.
    images = {name for name in STREAMS if not name.endswith(".txt")}
    assert images == set(TRANSCRIPTIONS)
    # The plain fixture replays the default stream: no file is named after it.
    assert journey_streams.digest_of(PLAIN) not in named


@pytest.mark.parametrize("name", sorted(STREAMS))
def test_the_journey_streams_are_the_ones_written_from_their_source(name: str) -> None:
    assert json.loads(path_of(name).read_text(encoding="utf-8")) == STREAMS[name]
    assert path_of(name).read_text(encoding="utf-8") == journey_streams.written(name)


@pytest.mark.parametrize("name", sorted(TRANSCRIPTIONS))
def test_the_transcription_streams_are_the_ones_written_from_their_source(name: str) -> None:
    path = transcription_path_of(name)
    assert json.loads(path.read_text(encoding="utf-8")) == TRANSCRIPTIONS[name]
    assert path.read_text(encoding="utf-8") == journey_streams.transcription_written(name)
    assert all(journey_streams.transcribed(name))


@pytest.mark.parametrize("name", sorted(STREAMS))
def test_a_journey_stream_cites_only_words_of_its_fixture(name: str) -> None:
    if name in TRANSCRIPTIONS:  # the document's text is what its transcription stream stored
        text = "\f".join(journey_streams.transcribed(name))
    else:
        text = (FIXTURES / name).read_text(encoding="utf-8")
    excerpts = [
        citation["excerpt"]
        for step in STREAMS[name]["steps"]
        if step.get("message", {}).get("type") == "assistant"
        for block in step["message"]["message"]["content"]
        if block["type"] == "tool_use"
        for item in block["input"].get("items", [])
        for citation in item["citations"]
    ]
    assert excerpts
    assert [excerpt for excerpt in excerpts if excerpt not in text] == []


def test_the_large_stream_pauses_the_run_between_two_steps_and_one_continue_finishes_it() -> None:
    steps = [_step(LARGE_FIXTURE), _step(PLAIN)]
    figures = estimate.run(steps)
    messages = [
        step["message"]
        for step in STREAMS[LARGE_FIXTURE]["steps"]
        if step.get("message", {}).get("type") in ("assistant", "result")
    ]
    *before, last, result = messages
    assert sum(result["usage"].values()) == LARGE_TOKENS
    # The large usage is in the last message, which asks for nothing more: a step whose usage
    # passed its budget before a tool call would end TOKEN_LIMIT, and its second attempt after
    # Continue would need the same tokens again.
    assert all(block["type"] != "tool_use" for block in last["message"]["content"])
    spent_before = sum(sum(one["message"]["usage"].values()) for one in before)
    first = estimate.budget(steps[0], figures.ceiling)
    assert first is not None
    assert spent_before < first
    # At or over the ceiling `C = 2 * H`: the run pauses before the second step.
    assert figures.ceiling == 2 * figures.high
    assert figures.ceiling <= LARGE_TOKENS
    assert estimate.budget(steps[1], figures.ceiling - LARGE_TOKENS) is None
    # One Continue adds `H`: what remains covers the low estimate of the second step, with
    # room for a stored text one character shorter or longer than the file.
    assert estimate.budget(steps[1], figures.ceiling + figures.high - LARGE_TOKENS - 1_000)
