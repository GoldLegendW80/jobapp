"""The steps an ingestion run would take over a selection of documents. Pure: no table is read
here, and nothing is written (tasks/ingestion/todo.md, "The run: steps, the chain and the merge").

A run is a chain of steps. First the transcription steps: one per document that still holds
page images and per group of at most 8 of them. Then the extraction steps: one per part of at
most 100,000 characters of a document's text, cut at a paragraph end.

What the caller gives: the documents it has already found readable (the route and the engine
decide that from the rows), the model of the caller's credential, and the identities of the steps
that finished earlier. What it gets back: the ordered steps, each with its identity and whether
it is `reused`, the documents refused because their reader is not switched on, and the documents
left out by the 60 step rule.

Three things are decided here and nowhere else:

- **The cut.** `cut(text)` is the one rule that splits a text into parts. The estimate and the
  run call it, so the parts the dialog counted are the parts the run reads.
- **The identity of a step**: the document's sha256, the kind of step, the part number or the
  image positions, `EXTRACTOR_VERSION` and the model. A step whose identity is in the set of
  finished identities it is given is `reused` and costs nothing, unless the user asked to read
  that document again (`reread`). The query that finds the finished identities is G10's.
- **The limits of a run**: 30 documents, 1,500,000 characters, 60 images, 60 steps. The first
  three refuse the selection (`LimitError`). The fourth leaves out the documents that do not
  fit, in the order of the selection, and the run ends partial.

Transcription is planned only when `TRANSCRIPTION` is on, which G14 turns on with the agent that
does it. Until then a document that still holds images is refused, by name, and never planned.
The characters a transcription will add are not known before it runs: they are counted at 3,000
per image (assumption A10) and split into equal parts, since there is no text to cut yet.
"""

import enum
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from app.agents.definitions import DEFAULT_MODEL
from app.agents.models import CredentialKind

# The limits of one run and of one step ("Limits").
RUN_MAX_DOCUMENTS = 30
RUN_MAX_CHARS = 1_500_000
RUN_MAX_IMAGES = 60
RUN_MAX_STEPS = 60
RUN_MINUTES = 60
STEP_MINUTES = 15
PART_MAX_CHARS = 100_000
GROUP_MAX_IMAGES = 8
# What one transcribed image is counted as, in characters to extract afterwards (A10).
IMAGE_TEXT_CHARS = 3_000

# Part of every step identity: raised when the prompt, the tools or the cut change in a way that
# makes an earlier result stale, so that no finished step is reused across the change.
EXTRACTOR_VERSION = 1

# Decision 5: a subscription runs on the default model, an API key on the second one. The two
# ingestion agents carry the same mapping as `model_by_credential` (G9, G14).
MODEL = DEFAULT_MODEL
MODEL_BY_CREDENTIAL: dict[str, str] = {CredentialKind.anthropic_api_key.value: "claude-sonnet-5-5"}

# On since G14 shipped the transcription agent (module docstring).
TRANSCRIPTION = True

_PARAGRAPH_END = "\n\n"
_LINE_END = "\n"


class StepKind(enum.StrEnum):
    transcribe = "transcribe"
    extract = "extract"


class Refusal(enum.StrEnum):
    """Why a document handed to `build` is not planned."""

    awaiting_transcription = "awaiting_transcription"  # it holds images and the flag is off
    empty = "empty"  # neither text nor images
    over_steps = "over_steps"  # its steps would take the run past RUN_MAX_STEPS


class LimitError(Exception):
    """The selection is refused as a whole. `code` names the limit, `message` is a sentence
    for the user: it holds counts, never a document's name or text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Document:
    """What the plan needs of one document, none of it content.

    `characters` is the text stored now. `images` are the positions of the page images not yet
    transcribed. `parts` are the `(start, end)` offsets `cut` gave for the stored text: without
    them (the caller did not load the text) the characters are split into equal parts."""

    id: uuid.UUID
    sha256: str  # hexadecimal
    characters: int = 0
    images: tuple[int, ...] = ()
    parts: tuple[tuple[int, int], ...] | None = None


@dataclass(frozen=True)
class Identity:
    """What makes two steps the same work ("Idempotency"). Hashable: G10 builds the same value
    from its finished steps and passes a set of them to `build`."""

    sha256: str
    kind: str
    positions: tuple[int, ...]  # the part number, or the positions of the images of the group
    extractor_version: int
    model: str


@dataclass(frozen=True)
class Step:
    kind: StepKind
    document_id: uuid.UUID
    identity: Identity
    reused: bool
    # An extraction step: the part's number and size, and its offsets when the text was cut.
    part: int = 0
    characters: int = 0
    start: int | None = None
    end: int | None = None
    # True when the size is counted in advance at IMAGE_TEXT_CHARS per image, not measured.
    predicted: bool = False
    # A transcription step: the positions of its images.
    images: tuple[int, ...] = ()


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    refused: tuple[tuple[uuid.UUID, Refusal], ...]

    @property
    def planned_documents(self) -> tuple[uuid.UUID, ...]:
        seen: dict[uuid.UUID, None] = {}
        for step in self.steps:
            seen.setdefault(step.document_id, None)
        return tuple(seen)


def model_for(kind: CredentialKind | str | None) -> str:
    """The model a run of this module uses for a kind of credential (decision 5). Without a
    credential it is the default one: only what the identities of a plan are built with."""
    if kind is None:
        return MODEL
    return MODEL_BY_CREDENTIAL.get(str(CredentialKind(kind).value), MODEL)


def models() -> frozenset[str]:
    """Every model a run of this module can use: each one has a price (pricing.py)."""
    return frozenset({MODEL, *MODEL_BY_CREDENTIAL.values()})


def cut(text: str) -> tuple[tuple[int, int], ...]:
    """The parts of a text, as `(start, end)` offsets in characters: each at most
    PART_MAX_CHARS, in order, touching, covering the whole text. A part ends at the last
    paragraph end (an empty line) that fits; a text with none is cut at the last line end that
    fits, and one with neither at PART_MAX_CHARS exactly. An empty text has no part."""
    parts: list[tuple[int, int]] = []
    start, length = 0, len(text)
    while length - start > PART_MAX_CHARS:
        limit = start + PART_MAX_CHARS
        end = limit
        for mark in (_PARAGRAPH_END, _LINE_END):
            found = text.rfind(mark, start, limit)
            if found != -1:
                end = found + len(mark)
                break
        parts.append((start, end))
        start = end
    if length > start:
        parts.append((start, length))
    return tuple(parts)


def equal_parts(characters: int) -> tuple[int, ...]:
    """The sizes of the parts of a text that cannot be cut yet: as few parts as PART_MAX_CHARS
    allows, of equal size (the first ones take the remainder, one character each)."""
    if characters <= 0:
        return ()
    count = -(-characters // PART_MAX_CHARS)
    size, more = divmod(characters, count)
    return tuple(size + 1 if index < more else size for index in range(count))


def groups(images: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    """The images of a document in groups of at most GROUP_MAX_IMAGES, in order of position."""
    ordered = sorted(images)
    return tuple(
        tuple(ordered[at : at + GROUP_MAX_IMAGES])
        for at in range(0, len(ordered), GROUP_MAX_IMAGES)
    )


def check_count(count: int) -> None:
    """A selection of `count` documents is one a run may take: at least one, at most 30. The
    route asks before it reads any row, `build` asks again."""
    if count < 1:
        raise LimitError("no_document", "Select at least one document.")
    if count > RUN_MAX_DOCUMENTS:
        raise LimitError(
            "documents",
            f"A run reads at most {RUN_MAX_DOCUMENTS} documents, and {count} are "
            "selected. Select fewer and start again for the others.",
        )


def check_limits(documents: Sequence[Document]) -> None:
    """Raises `LimitError` for a selection over the documents, the characters or the images of
    a run. Asked by `build`, and by a caller that wants the answer before it loads any text."""
    check_count(len(documents))
    characters = sum(document.characters for document in documents)
    if characters > RUN_MAX_CHARS:
        raise LimitError(
            "characters",
            f"A run reads at most {RUN_MAX_CHARS:,} characters, and the selection holds "
            f"{characters:,}. Select fewer documents and start again for the others.",
        )
    images = sum(len(document.images) for document in documents)
    if images > RUN_MAX_IMAGES:
        raise LimitError(
            "images",
            f"A run reads at most {RUN_MAX_IMAGES} page images, and the selection holds "
            f"{images}. Select fewer documents and start again for the others.",
        )


def _steps_of(
    document: Document,
    model: str,
    finished: Collection[Identity],
    reread: bool,
    extractor_version: int,
) -> tuple[list[Step], list[Step]]:
    """The transcription steps and the extraction steps of one document."""

    def step(kind: StepKind, positions: tuple[int, ...], **fields: object) -> Step:
        identity = Identity(document.sha256, kind.value, positions, extractor_version, model)
        reused = not reread and identity in finished
        return Step(kind, document.id, identity, reused, **fields)  # type: ignore[arg-type]

    transcriptions = [
        step(StepKind.transcribe, group, images=group) for group in groups(document.images)
    ]
    if document.images or document.parts is None:
        # No cut yet: the text a transcription will add is not there, or the caller gave sizes.
        total = document.characters + IMAGE_TEXT_CHARS * len(document.images)
        extractions = [
            step(
                StepKind.extract,
                (part,),
                part=part,
                characters=size,
                predicted=bool(document.images),
            )
            for part, size in enumerate(equal_parts(total))
        ]
    else:
        extractions = [
            step(StepKind.extract, (part,), part=part, characters=end - start, start=start, end=end)
            for part, (start, end) in enumerate(document.parts)
        ]
    return transcriptions, extractions


def build(
    documents: Sequence[Document],
    *,
    model: str,
    finished: Collection[Identity] = frozenset(),
    reread: Collection[uuid.UUID] = frozenset(),
    transcription: bool | None = None,
    extractor_version: int = EXTRACTOR_VERSION,
) -> Plan:
    """The plan of a selection, in the order given (module docstring). Raises `LimitError` for a
    selection over a limit of the run or with no document."""
    check_limits(documents)
    transcribing = TRANSCRIPTION if transcription is None else transcription
    first: list[Step] = []
    then: list[Step] = []
    refused: list[tuple[uuid.UUID, Refusal]] = []
    for document in documents:
        if document.images and not transcribing:
            refused.append((document.id, Refusal.awaiting_transcription))
            continue
        transcriptions, extractions = _steps_of(
            document, model, finished, document.id in reread, extractor_version
        )
        if not transcriptions and not extractions:
            refused.append((document.id, Refusal.empty))
            continue
        if len(first) + len(then) + len(transcriptions) + len(extractions) > RUN_MAX_STEPS:
            refused.append((document.id, Refusal.over_steps))
            continue
        first.extend(transcriptions)
        then.extend(extractions)
    return Plan(steps=(*first, *then), refused=tuple(refused))
