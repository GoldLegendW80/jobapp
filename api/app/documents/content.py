"""The content of a document: structured fields (SPEC-documents.md, "Content", Decision 6).

A CV, a base CV (a CV with its `fixed` block), a letter and an outreach proposal (issue #485:
a subject and a short body for a posting whose only way to apply is an e-mail address, text only,
never rendered) are Pydantic models with the hard
limits of the spec: beyond them the api and the submit tools refuse. The soft limits (the
counters shown under a field) belong to each template and live in `rules.SoftLimits`.

Fixed facts (name, contact, photo, each employer's organisation, title and dates, the schools,
the languages) are never fields of a posting document: an experience entry names its employer
by `employer_key` only, and the template prints the rest from the profile. A base CV carries
them in `fixed`, used where the profile is still empty (`profile_view`).

Strings are cleaned the way the profile cleans them (NFC, unified newlines, stripped, control
characters refused) and runs of spaces or tabs become one space. That is normalisation, not a
correction: it is never recorded. Nothing here does I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.profile.schemas import OptPeriod, Slug

SCHEMA = 1

Kind = Literal["cv", "letter", "base", "proposal"]
KINDS: tuple[Kind, ...] = ("cv", "letter", "base", "proposal")

# Hard limits, refused beyond (the spec's "Limits per field").
HEADLINE_MAX = 200
SUMMARY_MAX = 600
BULLET_MAX = 300  # a bullet or a highlight
PARAGRAPH_MAX = 2_000
BULLETS_MAX = 8  # per employer
EMPLOYERS_MAX = 12  # experience entries of a CV
CONTENT_MAX_BYTES = 20 * 1024  # the canonical JSON of the whole content
# Counts: the writer is asked for 3 highlights, 8 competencies, 4 skills groups and 4 or 5
# paragraphs; fewer render (an empty section is not printed), more are refused.
HIGHLIGHTS_MAX = 3
COMPETENCIES_MAX = 8
SKILLS_MAX = 4
PARAGRAPHS_MAX = 5
# A proposal: the writer is asked for at most PROPOSAL_WORDS words, more than PROPOSAL_WORDS_MAX
# are refused ("about 150 words"), and the body's characters are capped as a paragraph's.
PROPOSAL_WORDS = 150
PROPOSAL_WORDS_MAX = 180
PROPOSAL_BODY_MAX = 1_500
# The render minimums: what a template needs to print the document at all. Only a letter has
# one: a paragraph. The corrections never leave less (`corrections.remove_remaining`).
RENDER_MIN: dict[str, dict[str, int]] = {
    "cv": {"highlights": 0, "competencies": 0, "skills": 0},
    "base": {"highlights": 0, "competencies": 0, "skills": 0},
    "letter": {"paragraphs": 1},
    "proposal": {},
}
# Limits the spec leaves to the plan: one line, one term of a list, the notes.
LINE_MAX = 200
TERM_MAX = 100
SKILL_ITEMS_MAX = 30
NOTES_MAX = 2_000
FIXED_EMPLOYERS_MAX = 30  # as the profile's experience
FIXED_SCHOOLS_MAX = 20
FIXED_LANGUAGES_MAX = 20
# A raw submit larger than this is refused before it is even validated.
RAW_MAX_BYTES = 4 * CONTENT_MAX_BYTES


# --- strings ------------------------------------------------------------------------------------

_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")
_CONTROL_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_TEXT = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SPACE_RUN = re.compile(r"[ \t]+")  # one class under one quantifier: linear


def _tidy(value: object, multiline: bool = False) -> object:
    """NFC, unified newlines, tabs and runs of spaces as one space, each line stripped; control
    characters refused. Non-strings pass through so strict validation names the wrong type."""
    if not isinstance(value, str):
        return value
    if _LONE_SURROGATE.search(value):
        raise PydanticCustomError("lone_surrogate", "invalid unicode: lone surrogate")
    text = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACE_RUN.sub(" ", text)
    if (_CONTROL_TEXT if multiline else _CONTROL_LINE).search(text):
        raise PydanticCustomError("control_character", "control characters are not allowed")
    if multiline:
        text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


def _text(max_length: int, multiline: bool = False) -> Any:
    """A string that may be empty (an empty field is not printed)."""
    return Annotated[
        str,
        StringConstraints(max_length=max_length),
        BeforeValidator(partial(_tidy, multiline=multiline)),
    ]


def _item(max_length: int, multiline: bool = False) -> Any:
    """An entry of a list: never empty (an empty bullet is no bullet)."""
    return Annotated[
        str,
        StringConstraints(min_length=1, max_length=max_length),
        BeforeValidator(partial(_tidy, multiline=multiline)),
    ]


# --- models -------------------------------------------------------------------------------------


class _Model(BaseModel):
    # Unknown fields are refused and nothing is coerced, as in the profile.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Content(_Model):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, populate_by_name=True, serialize_by_alias=True
    )

    # `schema` in JSON; the attribute has another name because BaseModel has a `schema` method.
    schema_version: Literal[1] = Field(default=SCHEMA, alias="schema")

    @model_validator(mode="after")
    def _size(self) -> _Content:
        size = len(canonical_json(self))
        if size > CONTENT_MAX_BYTES:
            raise PydanticCustomError(
                "content_too_large",
                "the content is {size} bytes, at most {limit} allowed",
                {"size": size, "limit": CONTENT_MAX_BYTES},
            )
        return self


def _unique_keys(keys: list[str]) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise PydanticCustomError(
                "duplicate_employer", "the employer {key} appears twice", {"key": key}
            )
        seen.add(key)


class SkillGroup(_Model):
    label: _item(TERM_MAX)
    items: Annotated[list[_item(TERM_MAX)], Field(max_length=SKILL_ITEMS_MAX)] = Field(
        default_factory=list
    )


class Role(_Model):
    """One employer of a CV: the key of a fixed employer and the bullets written for it."""

    employer_key: Slug
    bullets: Annotated[list[_item(BULLET_MAX)], Field(max_length=BULLETS_MAX)] = Field(
        default_factory=list
    )


class CvContent(_Content):
    target: _text(LINE_MAX) = ""  # shown in the app, not printed
    headline: _text(HEADLINE_MAX) = ""
    summary: _text(SUMMARY_MAX, multiline=True) = ""
    highlights: Annotated[list[_item(BULLET_MAX)], Field(max_length=HIGHLIGHTS_MAX)] = Field(
        default_factory=list
    )
    competencies: Annotated[list[_item(TERM_MAX)], Field(max_length=COMPETENCIES_MAX)] = Field(
        default_factory=list
    )
    experience: Annotated[list[Role], Field(max_length=EMPLOYERS_MAX)] = Field(default_factory=list)
    skills: Annotated[list[SkillGroup], Field(max_length=SKILLS_MAX)] = Field(default_factory=list)
    notes: _text(NOTES_MAX, multiline=True) = ""  # for the person, not printed

    @field_validator("experience")
    @classmethod
    def _one_entry_per_employer(cls, value: list[Role]) -> list[Role]:
        _unique_keys([role.employer_key for role in value])
        return value


class FixedEmployer(_Model):
    key: Slug
    organisation: _item(LINE_MAX)
    title: _text(LINE_MAX) = ""
    start: OptPeriod = None
    end: OptPeriod = None  # null: present, or not stated
    location: _text(LINE_MAX) = ""


class FixedSchool(_Model):
    school: _item(LINE_MAX)
    degree: _text(LINE_MAX) = ""
    start: OptPeriod = None
    end: OptPeriod = None
    location: _text(LINE_MAX) = ""


class FixedLanguage(_Model):
    name: _item(LINE_MAX)
    level: _text(LINE_MAX) = ""


class Fixed(_Model):
    """The fixed facts as a base CV states them (an upload may state only some of them)."""

    employers: Annotated[list[FixedEmployer], Field(max_length=FIXED_EMPLOYERS_MAX)] = Field(
        default_factory=list
    )
    schools: Annotated[list[FixedSchool], Field(max_length=FIXED_SCHOOLS_MAX)] = Field(
        default_factory=list
    )
    languages: Annotated[list[FixedLanguage], Field(max_length=FIXED_LANGUAGES_MAX)] = Field(
        default_factory=list
    )

    @field_validator("employers")
    @classmethod
    def _unique(cls, value: list[FixedEmployer]) -> list[FixedEmployer]:
        _unique_keys([employer.key for employer in value])
        return value


class BaseCvContent(CvContent):
    fixed: Fixed = Field(default_factory=Fixed)


class LetterContent(_Content):
    target: _text(LINE_MAX) = ""
    headline: _text(HEADLINE_MAX) = ""
    recipient: _text(LINE_MAX) = ""  # by default "Hiring team, " and the posting's company
    subject: _text(LINE_MAX) = ""
    salutation: _text(LINE_MAX) = ""
    paragraphs: Annotated[
        list[_item(PARAGRAPH_MAX, multiline=True)], Field(max_length=PARAGRAPHS_MAX)
    ] = Field(default_factory=list)
    closing: _text(LINE_MAX) = ""


# An address as the proposal's `to` holds it: one `@`, no space. Which address of a posting is
# chosen is the caller's rule (apply's, for O9a); this only keeps the field one address.
_ADDRESS = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}")


def word_count(text: str) -> int:
    return len(text.split())


class ProposalContent(_Content):
    """An outreach proposal: what the person sends from their own mail program. `to` is the
    address it goes to, set by the app from the posting (never by the writer) and not printed
    prose: the fact check reads the subject and the body only."""

    target: _text(LINE_MAX) = ""  # shown in the app, not sent
    to: _text(LINE_MAX) = ""
    subject: _text(LINE_MAX) = ""
    body: _text(PROPOSAL_BODY_MAX, multiline=True) = ""

    @field_validator("to")
    @classmethod
    def _one_address(cls, value: str) -> str:
        if value and not _ADDRESS.fullmatch(value):
            raise PydanticCustomError("bad_address", "not one e-mail address")
        return value

    @field_validator("body")
    @classmethod
    def _short(cls, value: str) -> str:
        words = word_count(value)
        if words > PROPOSAL_WORDS_MAX:
            raise PydanticCustomError(
                "too_many_words",
                "the body has {words} words, at most {limit} allowed",
                {"words": words, "limit": PROPOSAL_WORDS_MAX},
            )
        return value


Content = CvContent | LetterContent | ProposalContent  # BaseCvContent is a CvContent

MODELS: dict[str, type[_Content]] = {
    "cv": CvContent,
    "letter": LetterContent,
    "base": BaseCvContent,
    "proposal": ProposalContent,
}


# --- parsing ------------------------------------------------------------------------------------

# Pydantic's error types, as the short codes of a fixed list that a writer is answered with.
_CODES = {
    "missing": "required",
    "extra_forbidden": "unknown_field",
    "string_too_long": "too_long",
    "string_too_short": "empty",
    "too_long": "too_many",
    "too_short": "too_few",
    "literal_error": "unknown_value",
    "string_pattern_mismatch": "bad_format",
    "value_error": "bad_format",
}
_OWN_CODES = frozenset(
    (
        "duplicate_employer",
        "content_too_large",
        "control_character",
        "lone_surrogate",
        "bad_address",
        "too_many_words",
    )
)


@dataclass(frozen=True, slots=True)
class FieldError:
    """One rule a submit breaks: the field path (`experience.1.bullets.0`), a code of a fixed
    list, and pydantic's message, which never quotes the value."""

    path: str
    code: str
    message: str


class ContentInvalidError(ValueError):
    def __init__(self, errors: tuple[FieldError, ...]) -> None:
        super().__init__(f"{len(errors)} field error(s)")
        self.errors = errors


def _code(error_type: str) -> str:
    if error_type in _OWN_CODES:
        return error_type
    if error_type.endswith("_type"):
        return "wrong_type"
    return _CODES.get(error_type, "invalid")


def parse(kind: Kind, data: object) -> Content:
    """The validated content of `kind`, or `ContentInvalidError` with every field error in order."""
    model = MODELS[kind]
    try:
        raw = len(json.dumps(data, ensure_ascii=False, default=str).encode())
    except (TypeError, ValueError):
        raw = 0  # not JSON: validation names the field
    if raw > RAW_MAX_BYTES:
        raise ContentInvalidError(
            (FieldError("", "content_too_large", f"at most {CONTENT_MAX_BYTES} bytes allowed"),)
        )
    try:
        return model.model_validate(data)  # type: ignore[return-value]
    except ValidationError as exc:
        errors = tuple(
            FieldError(
                ".".join(str(part) for part in error["loc"]), _code(error["type"]), error["msg"]
            )
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise ContentInvalidError(errors) from None


# --- canonical form -----------------------------------------------------------------------------


def dump(content: BaseModel) -> dict[str, Any]:
    """The content as JSON data, `schema` included."""
    return content.model_dump(mode="json", by_alias=True)


def canonical_json(content: BaseModel) -> bytes:
    """Sorted keys, no spaces, UTF-8: the same content always gives the same bytes."""
    return json.dumps(
        dump(content), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def content_hash(kind: Kind, content: BaseModel) -> bytes:
    """SHA-256 of the kind and the canonical JSON (32 bytes): a CV and a letter never share a
    hash, and a render reuses a version whose hash is the draft's."""
    return hashlib.sha256(kind.encode() + b"\n" + canonical_json(content)).digest()


# --- the printed text fields --------------------------------------------------------------------


def text_fields(content: BaseModel) -> list[tuple[str, str]]:
    """Every printed text field as `(path, text)`, in the order a reader meets them. `target`,
    `notes`, a proposal's `to` and the fixed facts are not written prose of the document and are
    left out."""
    if isinstance(content, ProposalContent):
        return [("subject", content.subject), ("body", content.body)]
    if isinstance(content, LetterContent):
        out = [
            ("headline", content.headline),
            ("recipient", content.recipient),
            ("subject", content.subject),
            ("salutation", content.salutation),
        ]
        out += [(f"paragraphs.{i}", text) for i, text in enumerate(content.paragraphs)]
        out.append(("closing", content.closing))
        return out
    if not isinstance(content, CvContent):
        raise TypeError("not the content of a document")
    out = [("headline", content.headline), ("summary", content.summary)]
    out += [(f"highlights.{i}", text) for i, text in enumerate(content.highlights)]
    out += [(f"competencies.{i}", text) for i, text in enumerate(content.competencies)]
    for i, role in enumerate(content.experience):
        out += [(f"experience.{i}.bullets.{j}", text) for j, text in enumerate(role.bullets)]
    for i, group in enumerate(content.skills):
        out.append((f"skills.{i}.label", group.label))
        out += [(f"skills.{i}.items.{j}", text) for j, text in enumerate(group.items)]
    return out


def kind_of(content: BaseModel) -> Kind:
    if isinstance(content, BaseCvContent):
        return "base"
    if isinstance(content, LetterContent):
        return "letter"
    if isinstance(content, ProposalContent):
        return "proposal"
    return "cv"
