"""Section models of the profile, the request and response bodies of its API, and completeness.

Every section is one Pydantic v2 model. The API refuses anything else (422): unknown fields,
wrong types (strict mode, no coercion of "3" into 3), values out of range, oversized lists and
strings. Every string is NFC-normalised and stripped; single-line fields refuse every control
character, multi-line fields (Markdown, motivation) allow only newline and tab, and both refuse
lone surrogates (which Postgres cannot store). An optional field left empty is stored as null.
What is stored is `model.model_dump(mode="json")`, so a later read returns exactly the
normalised value.

Sections and what they replace in the engine:
- identity: name, headline, contact, city and country, links, languages (`assemble.py`).
  The photo is not here: it lives in its own columns (task P3).
- experience: employers (each with a stable slug `key`, unique within the section; months as
  YYYY-MM, `end` null means present, `end` never before `start`) and education
  (`assemble.py` EMPLOYERS, EDUCATION).
- facts: the fact base as named Markdown blocks (titles unique, so agents can cite a block),
  at most FACTS_MAX_BYTES of UTF-8 in all (`profile/facts.md`).
- skills: the scoring model (`profile/skills.json`): years_experience 0 to 60, categories
  have, adjacent, none (names unique across the three), gates with a penalty 0 to 100.
- search: title include, title exclude and location terms, contract types, remote stance,
  min_fit, posting age window, boards, never-apply rules (`engine/boards.json`, hard rule 11).
- answers: standard answers of application forms (`facts.md` PRACTICALITIES).

Terms. A term is a plain word or phrase, matched later as a whole word, case-insensitive.
Every term list carries `advanced_regex`; when true each term is a Python regular expression,
compiled on save (an invalid one is a 422 that names the position). Python `re` has no match
timeout, so a pattern that backtracks catastrophically (ReDoS) could stall a sweep. The defence
here is size and shape, not proof: a pattern is at most REGEX_MAX characters and nested
quantifiers such as `(a+)+` or `(\\w+\\s?)*` are refused. Overlapping alternations such as
`(a|a)*` are not detected, and the jobs module must still match against bounded text.
Within one list duplicate terms (ignoring case) are dropped, the first kept.

Completeness. `completeness(sections)` gives, per section, a percentage (whole number, the share
of its required fields that are filled) and the list of the required fields still missing,
plus an overall percentage (the mean of the six). A section never saved counts as empty.
Required fields, chosen as what search and CV generation cannot run without:
- identity: full_name, headline, email, phone, city, country, languages (at least one)
- experience: employers (at least one), education (at least one)
- facts: blocks (at least one)
- skills: years_experience, have (at least one category)
- search: title_include (at least one term), location (at least one term), contract_types
  (at least one), remote_stance, boards (at least one enabled)
- answers: work_authorization (at least one country), notice_period, earliest_start,
  salary_policy, motivation
Fields with a default (min_fit, days) always count as filled, so they are not listed.
"""

import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

# --- limits -------------------------------------------------------------------------------------

LINE_MAX = 200  # names, titles, short answers
TEXT_MAX = 2_000  # one paragraph answers
LONG_MAX = 10_000  # motivation in the user's own words
TERM_MAX = 200  # a plain term
REGEX_MAX = 500  # an advanced term: longer patterns are refused (ReDoS surface)
URL_MAX = 500
TERMS_MAX = 300  # terms in one list
FACTS_MAX_BYTES = 200_000  # titles and bodies of all fact blocks, UTF-8
FACT_BLOCKS_MAX = 200
MONTH_MIN_YEAR = 1900
MONTH_MAX_YEAR = 2100

# --- strings ------------------------------------------------------------------------------------

# C0 and C1 control characters, DEL, and lone surrogates. Multi-line text allows \t and \n.
_CONTROL_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]")
_CONTROL_TEXT = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")


def _clean(value: object, multiline: bool = False, empty_is_none: bool = False) -> object:
    """NFC, unified newlines, stripped; control characters refused. Non-strings pass through
    untouched so strict validation reports the wrong type."""
    if not isinstance(value, str):
        return value
    if re.search(r"[\ud800-\udfff]", value):
        raise ValueError("invalid unicode: lone surrogate")
    text = unicodedata.normalize("NFC", value)
    if multiline:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.strip()
    pattern = _CONTROL_TEXT if multiline else _CONTROL_LINE
    if pattern.search(text):
        raise ValueError("control characters are not allowed")
    if empty_is_none and not text:
        return None
    return text


def Text(  # noqa: N802, a type factory
    max_length: int, multiline: bool = False, pattern: str | None = None
) -> Any:
    """A required string: cleaned, 1 to max_length characters. The constraints come before the
    cleaning validator so pydantic keeps its string messages ("at most N characters")."""
    return Annotated[
        str,
        StringConstraints(min_length=1, max_length=max_length, pattern=pattern),
        BeforeValidator(partial(_clean, multiline=multiline)),
    ]


def OptText(max_length: int, multiline: bool = False) -> Any:  # noqa: N802, a type factory
    """An optional string: cleaned, empty becomes null."""
    return Annotated[
        Annotated[str, StringConstraints(max_length=max_length)] | None,
        BeforeValidator(partial(_clean, multiline=multiline, empty_is_none=True)),
    ]


Line = Text(LINE_MAX)
OptLine = OptText(LINE_MAX)
OptParagraph = OptText(TEXT_MAX, multiline=True)


def _url(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"https?://[^\s/?#]+\.[^\s/?#]+(?:[/?#]\S*)?", value, re.IGNORECASE):
        raise ValueError("must be a full http:// or https:// address")
    return value


OptUrl = Annotated[OptText(URL_MAX), AfterValidator(_url)]

_MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")


def _month(value: str) -> str:
    match = _MONTH.fullmatch(value)
    if not match or not MONTH_MIN_YEAR <= int(match.group(1)) <= MONTH_MAX_YEAR:
        raise ValueError(
            f"must be a month written YYYY-MM between {MONTH_MIN_YEAR} and {MONTH_MAX_YEAR}"
        )
    return value


Month = Annotated[Text(20), AfterValidator(_month)]
OptMonth = Annotated[OptText(20), AfterValidator(lambda v: None if v is None else _month(v))]

SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
Slug = Text(40, pattern=SLUG_PATTERN)
# Board org as the ATS spells it in its URL: letters, digits, dot, dash, underscore.
ORG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
OrgSlug = Text(100, pattern=ORG_PATTERN)


def _email(value: str | None) -> str | None:
    if value is None:
        return None
    from app.identity.service import InvalidEmailError, normalize_email

    try:
        return normalize_email(value)
    except InvalidEmailError:
        raise ValueError("must be a valid email address") from None


OptEmail = Annotated[OptText(320), AfterValidator(_email)]
OptPhone = Annotated[
    OptText(40),
    AfterValidator(lambda v: _pattern(v, r"\+?[0-9][0-9 ().-]{4,38}", "a phone number")),
]


def _pattern(value: str | None, pattern: str, what: str) -> str | None:
    if value is not None and not re.fullmatch(pattern, value):
        raise ValueError(f"must be {what}")
    return value


def _unique(items: list[Any], key: Callable[[Any], str], what: str) -> list[Any]:
    seen: set[str] = set()
    for item in items:
        k = key(item)
        if k in seen:
            raise ValueError(f"duplicate {what}: each must be unique")
        seen.add(k)
    return items


# --- terms and regular expressions --------------------------------------------------------------

_REPEAT = re.compile(r"[+*]|\{\d*,?\d*\}")


def has_nested_quantifier(pattern: str) -> bool:
    """True when a repeated group (`+`, `*` or `{m,n}` after its closing parenthesis) contains a
    repetition itself, at any depth: (a+)+, (\\w+\\s?)*, (?:x{2,})+, ((ab)+)+. Escapes and
    character classes are skipped, so (c\\+\\+)+ and ([+*])+ pass. Call on a pattern that
    compiles."""
    stack: list[bool] = []  # per open group: does it contain a repetition so far
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if char == "[":  # skip the class; a `]` right after `[` or `[^` is a literal
            i += 1
            if pattern[i : i + 1] == "^":
                i += 1
            if pattern[i : i + 1] == "]":
                i += 1
            while i < len(pattern) and pattern[i] != "]":
                i += 2 if pattern[i] == "\\" else 1
            i += 1
            continue
        if char == "(":
            stack.append(False)
            i += 1
            continue
        if char == ")" and stack:
            inner = stack.pop()
            quantifier = _REPEAT.match(pattern, i + 1)
            if quantifier and inner:
                return True
            if stack:
                stack[-1] = stack[-1] or inner or bool(quantifier)
            i += 1 + (len(quantifier.group()) if quantifier else 0)
            continue
        if stack and _REPEAT.match(pattern, i):
            stack[-1] = True
        i += 1
    return False


def check_regex(pattern: str) -> str:
    """Compile an advanced term as the matcher will (case-insensitive). Raises ValueError with
    the error position when it does not compile, is too long, or nests quantifiers."""
    if len(pattern) > REGEX_MAX:
        raise ValueError(f"regular expression longer than {REGEX_MAX} characters")
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        where = f" at position {exc.pos}" if exc.pos is not None else ""
        raise ValueError(f"invalid regular expression{where}: {exc.msg}") from None
    if has_nested_quantifier(pattern):
        raise ValueError(
            "nested quantifiers such as (a+)+ are not allowed: they can make matching hang"
        )
    return pattern


Term = Text(REGEX_MAX)


def _dedupe(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    kept = []
    for term in terms:
        if term.casefold() not in seen:
            seen.add(term.casefold())
            kept.append(term)
    return kept


class _Section(BaseModel):
    # Unknown fields are refused and nothing is coerced: a client bug is a clear 422.
    model_config = ConfigDict(extra="forbid", strict=True)


class _Terms(_Section):
    """A list of terms with the advanced toggle; subclasses add a name or a penalty."""

    advanced_regex: bool = False
    terms: Annotated[list[Term], Field(max_length=TERMS_MAX)] = Field(default_factory=list)

    @field_validator("terms")
    @classmethod
    def _check_terms(cls, value: list[str], info: ValidationInfo) -> list[str]:
        terms = _dedupe(value)
        advanced = info.data.get("advanced_regex") is True
        for index, term in enumerate(terms):
            if advanced:
                try:
                    check_regex(term)
                except ValueError as exc:
                    raise ValueError(f"term {index + 1}: {exc}") from None
            elif len(term) > TERM_MAX:
                raise ValueError(f"term {index + 1}: a plain term is at most {TERM_MAX} characters")
        return terms


class TermList(_Terms):
    pass


# --- identity -----------------------------------------------------------------------------------


class Language(_Section):
    name: Line
    level: OptLine = None


class Link(_Section):
    label: Line
    url: Annotated[Text(URL_MAX), AfterValidator(_url)]


class IdentitySection(_Section):
    full_name: OptLine = None
    headline: OptLine = None
    email: OptEmail = None
    phone: OptPhone = None
    city: OptLine = None
    country: OptLine = None
    linkedin_url: OptUrl = None
    website: OptUrl = None
    links: Annotated[list[Link], Field(max_length=10)] = Field(default_factory=list)
    languages: Annotated[list[Language], Field(max_length=20)] = Field(default_factory=list)

    @field_validator("languages")
    @classmethod
    def _unique_languages(cls, value: list[Language]) -> list[Language]:
        return _unique(value, lambda lang: lang.name.casefold(), "language")


# --- experience ---------------------------------------------------------------------------------

EmployerContract = Literal["CDI", "CDD", "apprenticeship", "freelance", "internship", "other"]


class _Dated(_Section):
    start: Any = None
    end: Any = None

    @model_validator(mode="after")
    def _end_after_start(self) -> "_Dated":
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("end must not be before start")
        return self


class Employer(_Dated):
    key: Slug
    organisation: Line
    title: Line
    start: Month
    end: OptMonth = None  # null: present
    location: OptLine = None
    contract: EmployerContract | None = None


class School(_Dated):
    school: Line
    degree: Line
    start: OptMonth = None
    end: OptMonth = None
    location: OptLine = None
    details: OptParagraph = None


class ExperienceSection(_Section):
    employers: Annotated[list[Employer], Field(max_length=30)] = Field(default_factory=list)
    education: Annotated[list[School], Field(max_length=20)] = Field(default_factory=list)

    @field_validator("employers")
    @classmethod
    def _unique_keys(cls, value: list[Employer]) -> list[Employer]:
        return _unique(value, lambda employer: employer.key, "employer key")


# --- facts --------------------------------------------------------------------------------------


class FactBlock(_Section):
    title: Line
    body: Text(FACTS_MAX_BYTES, multiline=True)


class FactsSection(_Section):
    blocks: Annotated[list[FactBlock], Field(max_length=FACT_BLOCKS_MAX)] = Field(
        default_factory=list
    )

    @field_validator("blocks")
    @classmethod
    def _blocks(cls, value: list[FactBlock]) -> list[FactBlock]:
        _unique(value, lambda block: block.title.casefold(), "block title")
        size = sum(len(b.title.encode()) + len(b.body.encode()) for b in value)
        if size > FACTS_MAX_BYTES:
            raise ValueError(f"the fact base is {size} bytes, at most {FACTS_MAX_BYTES} allowed")
        return value


# --- skills -------------------------------------------------------------------------------------


class Category(_Terms):
    name: Line


class Gate(_Terms):
    name: Line
    penalty: Annotated[StrictInt, Field(ge=0, le=100)] = 25


class SkillsSection(_Section):
    years_experience: Annotated[float, Field(ge=0, le=60, allow_inf_nan=False)] | None = None
    have: Annotated[list[Category], Field(max_length=100)] = Field(default_factory=list)
    adjacent: Annotated[list[Category], Field(max_length=100)] = Field(default_factory=list)
    none: Annotated[list[Category], Field(max_length=100)] = Field(default_factory=list)
    gates: Annotated[list[Gate], Field(max_length=50)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> "SkillsSection":
        _unique([*self.have, *self.adjacent, *self.none], lambda c: c.name.casefold(), "category")
        _unique(self.gates, lambda g: g.name.casefold(), "gate")
        return self


# --- search -------------------------------------------------------------------------------------

ContractType = Literal["CDI", "CDD", "VIE", "Stage", "Alternance", "Freelance"]
RemoteStance = Literal["remote", "hybrid", "office", "any"]
Ats = Literal["ashby", "greenhouse", "lever"]


class Board(_Section):
    company: Line
    ats: Ats
    org: OrgSlug
    enabled: bool = True
    extra_exclude: TermList = Field(default_factory=TermList)


class SearchSection(_Section):
    title_include: TermList = Field(default_factory=TermList)
    title_exclude: TermList = Field(default_factory=TermList)
    location: TermList = Field(default_factory=TermList)
    contract_types: Annotated[list[ContractType], Field(max_length=6)] = Field(default_factory=list)
    remote_stance: RemoteStance | None = None
    min_fit: Annotated[StrictInt, Field(ge=0, le=100)] = 70
    days: Annotated[StrictInt, Field(ge=1, le=365)] = 30
    boards: Annotated[list[Board], Field(max_length=300)] = Field(default_factory=list)
    never_apply: Annotated[list[Text(500)], Field(max_length=50)] = Field(default_factory=list)

    @field_validator("contract_types")
    @classmethod
    def _unique_contracts(cls, value: list[str]) -> list[str]:
        return _unique(value, str, "contract type")

    @field_validator("boards")
    @classmethod
    def _unique_boards(cls, value: list[Board]) -> list[Board]:
        return _unique(value, lambda b: f"{b.ats}:{b.org.lower()}", "board (same ATS and org)")


# --- answers ------------------------------------------------------------------------------------


class WorkAuthorization(_Section):
    country: Line
    answer: Text(TEXT_MAX, multiline=True)
    needs_sponsorship: bool | None = None


class AnswersSection(_Section):
    work_authorization: Annotated[list[WorkAuthorization], Field(max_length=50)] = Field(
        default_factory=list
    )
    notice_period: OptParagraph = None
    earliest_start: OptLine = None
    salary_policy: OptParagraph = None
    relocation: OptParagraph = None
    travel: OptParagraph = None
    motivation: OptText(LONG_MAX, multiline=True) = None
    heard_about: OptParagraph = None
    leave_blank: Annotated[list[Line], Field(max_length=50)] = Field(default_factory=list)

    @field_validator("work_authorization")
    @classmethod
    def _unique_countries(cls, value: list[WorkAuthorization]) -> list[WorkAuthorization]:
        return _unique(value, lambda w: w.country.casefold(), "country")


# --- registry -----------------------------------------------------------------------------------

SECTION_MODELS: dict[str, type[_Section]] = {
    "identity": IdentitySection,
    "experience": ExperienceSection,
    "facts": FactsSection,
    "skills": SkillsSection,
    "search": SearchSection,
    "answers": AnswersSection,
}
SECTIONS = tuple(SECTION_MODELS)
SectionName = Literal["identity", "experience", "facts", "skills", "search", "answers"]


def validate_section(section: str, data: Any) -> dict[str, Any]:
    """The stored form of `data` for this section. Raises pydantic.ValidationError."""
    model = SECTION_MODELS[section].model_validate(data)
    return model.model_dump(mode="json")


# --- completeness -------------------------------------------------------------------------------


def _filled(value: Any) -> bool:
    return value not in (None, "", [], {})


REQUIRED: dict[str, dict[str, Callable[[Any], bool]]] = {
    "identity": {
        name: (lambda n: lambda s: _filled(getattr(s, n)))(name)
        for name in ("full_name", "headline", "email", "phone", "city", "country", "languages")
    },
    "experience": {
        "employers": lambda s: bool(s.employers),
        "education": lambda s: bool(s.education),
    },
    "facts": {"blocks": lambda s: bool(s.blocks)},
    "skills": {
        "years_experience": lambda s: s.years_experience is not None,
        "have": lambda s: bool(s.have),
    },
    "search": {
        "title_include": lambda s: bool(s.title_include.terms),
        "location": lambda s: bool(s.location.terms),
        "contract_types": lambda s: bool(s.contract_types),
        "remote_stance": lambda s: s.remote_stance is not None,
        "boards": lambda s: any(board.enabled for board in s.boards),
    },
    "answers": {
        "work_authorization": lambda s: bool(s.work_authorization),
        "notice_period": lambda s: _filled(s.notice_period),
        "earliest_start": lambda s: _filled(s.earliest_start),
        "salary_policy": lambda s: _filled(s.salary_policy),
        "motivation": lambda s: _filled(s.motivation),
    },
}


class SectionCompleteness(BaseModel):
    percent: int
    missing: list[str]


class Completeness(BaseModel):
    overall: int
    sections: dict[str, SectionCompleteness]


def completeness(sections: dict[str, Any]) -> Completeness:
    """Per section: share of required fields filled (0 to 100) and the ones missing."""
    result = {}
    for name, model in SECTION_MODELS.items():
        stored = sections.get(name)
        # Stored data was validated on save; a section never saved counts as empty.
        instance = model.model_validate(stored) if stored is not None else model()
        rules = REQUIRED[name]
        missing = [field for field, ok in rules.items() if not ok(instance)]
        percent = round(100 * (len(rules) - len(missing)) / len(rules))
        result[name] = SectionCompleteness(percent=percent, missing=missing)
    overall = round(sum(c.percent for c in result.values()) / len(result))
    return Completeness(overall=overall, sections=result)


# --- API bodies ---------------------------------------------------------------------------------

REVISION_MAX = 2**31 - 1  # Postgres integer


class SaveSectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: Annotated[StrictInt, Field(ge=0, le=REVISION_MAX)]
    data: dict[str, Any]


class ProfileOut(BaseModel):
    revision: int
    updated_at: datetime
    # One entry per section; null for a section never saved.
    sections: dict[str, dict[str, Any] | None]
    completeness: Completeness


class ConflictOut(BaseModel):
    detail: str
    revision: int
    sections: dict[str, dict[str, Any] | None]


class RevisionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    revision: int
    section: str
    created_at: datetime


class RevisionPage(BaseModel):
    items: list[RevisionSummary]
    # Pass as `before` to get the next (older) page; null when this page is the last.
    next_before: int | None


class RevisionOut(RevisionSummary):
    data: dict[str, Any]
