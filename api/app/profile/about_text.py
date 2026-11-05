"""The "About you" document: its parts, the writer that converts a profile into it, and the code
that checks what the model read in a part before anything is shown (SPEC-profile, "About you";
owner, 2026-11-05: "except for the general infos, just put informations on a .md should be well
rendered and editable", then "No, I want a markdown that a model interpret, not just a view over
today's field").

Pure: no database, no network, no model call (about.py stores the document and runs the agent).

**Parts.** The document is Markdown. A line `## Heading` outside a fenced code block starts a
part; what comes before the first one is kept and never read. A part's kind follows its heading
(`kind_of`, case and spaces aside): Experience, Education, Skills and Answers (and their French
names) are the employers, the schools, the skills the fit reads and the prose of the answers;
any other heading is a fact block of that title. A part is identified by its heading's `key`.

**The writer** (`convert`, `body_of`) is deterministic: from the stored sections it writes each
part so that every value can be found again in its text, the way `check` reads it. A dated
entry is a `### Title, Organisation` heading and one line "March 2021 to now · Lyon · CDI"; a
skills category is a line "- name: term, term"; a fact block is a part of its own, its heading
lines of level one or two written as level three (they would start a part). Regular expression
categories and gates (`advanced_regex`) are not written: the code keeps them as stored.

**The check** (`interpret`), first failing reason wins for each value; a value that fails is not
taken, and the stored one stays:
- an employer or a school is checked by Fill my profile's citation checker
  (`ingestion.citations.check_item`) against the lines the model quotes from the part: its
  organisation or school as whole words, its dates with the month next to the year, its numbers;
  then its title, degree and place must be written, word after word, in those lines, and its
  contract named there;
- every other value (a skills term or name, a fact block, an answer) must be written, word after
  word, in the part (`written`: the words of N(value), the citation checker's normalisation,
  appear in that order in N(part)); a number (years, a penalty) must be a number of the part.
An employer keeps its `key` while its organisation is unchanged (a new one takes the slug of its
organisation), so the CV bullets that cite it keep their job.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ingestion import citations
from app.ingestion.items import fold, slug
from app.profile.schemas import (
    LINE_MAX,
    Employer,
    ExperienceSection,
    School,
    SkillsSection,
    format_period,
    validate_section,
)

TEXT_MAX = 300_000  # characters of the whole document
PART_MAX = 20_000  # characters of one part a Done may send to the model
SHOWN_MAX = 3  # names a summary line lists before "and N more"

Kind = Literal["experience", "education", "skills", "answers", "facts"]
HEADINGS: dict[str, Kind] = {
    "experience": "experience",
    "work experience": "experience",
    "expérience": "experience",
    "expériences": "experience",
    "education": "education",
    "formation": "education",
    "études": "education",
    "skills": "skills",
    "compétences": "skills",
    "answers": "answers",
    "réponses": "answers",
}
TITLES: dict[str, str] = {
    "experience": "Experience",
    "education": "Education",
    "skills": "Skills",
    "answers": "Answers",
}
BUCKETS = {
    "have": "What I have",
    "adjacent": "Close to what I have",
    "none": "What I do not have",
}
GATES = "Requirements that lower my fit"
YEARS = "Years of experience"
# The prose answers a part holds, in the order the writer puts them.
ANSWERS = {
    "notice_period": "Notice period",
    "earliest_start": "Earliest start",
    "relocation": "Relocation",
    "travel": "Travel",
    "motivation": "Motivation",
    "heard_about": "How I hear about jobs",
}
AUTHORIZATION = "Work authorisation"
CONTRACT_WORDS = {
    "CDI": ("cdi", "permanent"),
    "CDD": ("cdd", "fixed term"),
    "apprenticeship": ("apprenticeship", "apprentice", "alternance"),
    "freelance": ("freelance", "freelancer", "self employed"),
    "internship": ("internship", "intern"),
    "other": ("other contract",),
}

_PART = re.compile(r"^##[ \t]+(\S.*?)[ \t]*$")
_TOP = re.compile(r"^#{1,2}[ \t]+(?=\S)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_WORD = re.compile(r"[^\W_]+")


# --- parts -------------------------------------------------------------------------------------


def key_of(heading: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", heading).casefold().split())


def kind_of(heading: str) -> Kind:
    return HEADINGS.get(key_of(heading), "facts")


@dataclass(frozen=True)
class Part:
    heading: str
    body: str

    @property
    def key(self) -> str:
        return key_of(self.heading)

    @property
    def kind(self) -> Kind:
        return kind_of(self.heading)

    @property
    def text(self) -> str:
        """The part as the model reads it and its hash covers: heading, then body."""
        return f"## {self.heading}\n\n{self.body}".rstrip()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def split(text: str) -> tuple[str, list[Part]]:
    """The preamble and the parts of a document, in order."""
    preamble: list[str] = []
    parts: list[tuple[str, list[str]]] = []
    fence: str | None = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        opened = _FENCE.match(line)
        if fence is not None:
            if opened and opened.group(1) == fence:
                fence = None
        elif opened:
            fence = opened.group(1)
        else:
            heading = _PART.match(line)
            if heading:
                parts.append((heading.group(1).rstrip("# \t") or heading.group(1), []))
                continue
        (parts[-1][1] if parts else preamble).append(line)
    return "\n".join(preamble).strip("\n"), [
        Part(heading, "\n".join(lines).strip("\n")) for heading, lines in parts
    ]


def join(preamble: str, parts: Iterable[Part]) -> str:
    chunks = [preamble.strip("\n")] if preamble.strip() else []
    chunks += [part.text for part in parts]
    return "\n\n".join(chunks) + "\n"


def demote(body: str) -> str:
    """`body` with its level one and two headings written as level three, outside fences: they
    would start a part of their own."""
    out: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        opened = _FENCE.match(line)
        if fence is not None:
            fence = None if opened and opened.group(1) == fence else fence
        elif opened:
            fence = opened.group(1)
        elif _TOP.match(line):
            line = _TOP.sub("### ", line, count=1)
        out.append(line)
    return "\n".join(out)


# --- the writer ---------------------------------------------------------------------------------


def _when(start: str | None, end: str | None, present: bool) -> str | None:
    if start and end:
        return f"{format_period(start)} to {format_period(end)}"
    if start:
        return f"{format_period(start)} to now" if present else f"From {format_period(start)}"
    return f"Until {format_period(end)}" if end else None


def _line(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


def _contract(value: str | None) -> str | None:
    words = CONTRACT_WORDS.get(value or "")
    return None if words is None else value if value in ("CDI", "CDD") else words[0]


def _write_experience(sections: Mapping[str, Any]) -> str:
    entries = []
    for one in (sections.get("experience") or {}).get("employers") or []:
        line = _line(
            _when(one["start"], one.get("end"), True),
            one.get("location"),
            _contract(one.get("contract")),
        )
        entries.append(f"### {one['title']}, {one['organisation']}\n{line}")
    return "\n\n".join(entries)


def _write_education(sections: Mapping[str, Any]) -> str:
    entries = []
    for one in (sections.get("experience") or {}).get("education") or []:
        lines = [f"### {one['degree']}, {one['school']}"]
        line = _line(_when(one.get("start"), one.get("end"), False), one.get("location"))
        if line:
            lines.append(line)
        if one.get("details"):
            lines.append(f"\n{one['details']}")
        entries.append("\n".join(lines))
    return "\n\n".join(entries)


def _years(value: float) -> str:
    return f"{value:g}"


def _write_skills(sections: Mapping[str, Any]) -> str:
    skills = sections.get("skills") or {}
    chunks = []
    if skills.get("years_experience") is not None:
        chunks.append(f"{YEARS}: {_years(skills['years_experience'])}")
    for bucket, title in BUCKETS.items():
        lines = [
            f"- {one['name']}: {', '.join(one['terms'])}"
            for one in skills.get(bucket) or []
            if not one.get("advanced_regex")
        ]
        if lines:
            chunks.append(f"### {title}\n" + "\n".join(lines))
    gates = [
        f"- {one['name']}, lowers the fit by {one.get('penalty', 25)}: {', '.join(one['terms'])}"
        for one in skills.get("gates") or []
        if not one.get("advanced_regex")
    ]
    if gates:
        chunks.append(f"### {GATES}\n" + "\n".join(gates))
    return "\n\n".join(chunks)


def _write_answers(sections: Mapping[str, Any]) -> str:
    answers = sections.get("answers") or {}
    chunks = []
    for name, title in ANSWERS.items():
        if answers.get(name):
            chunks.append(f"### {title}\n{demote(answers[name])}")
        if name == "earliest_start" and answers.get("work_authorization"):
            lines = [
                f"- {one['country']}: " + one["answer"].replace("\n", "\n  ")
                for one in answers["work_authorization"]
            ]
            chunks.append(f"### {AUTHORIZATION}\n" + "\n".join(lines))
    return "\n\n".join(chunks)


def block_heading(title: str) -> str:
    """The heading of a fact block's part: its title, unless that reads as another kind."""
    return title if kind_of(title) == "facts" else f"Notes: {title}"


def blocks_of(sections: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list((sections.get("facts") or {}).get("blocks") or [])


def body_of(kind: Kind, sections: Mapping[str, Any], owned: Sequence[str] = ()) -> str:
    """The body the writer gives a part of this kind; for facts, the bodies of its blocks."""
    if kind == "experience":
        return _write_experience(sections)
    if kind == "education":
        return _write_education(sections)
    if kind == "skills":
        return _write_skills(sections)
    if kind == "answers":
        return _write_answers(sections)
    titles = {key_of(title) for title in owned}
    bodies = [demote(b["body"]) for b in blocks_of(sections) if key_of(b["title"]) in titles]
    return "\n\n".join(bodies)


@dataclass(frozen=True)
class Converted:
    text: str
    parts: dict[str, dict[str, Any]]  # by part key: what about.py stores


def convert(sections: Mapping[str, Any]) -> Converted:
    """The document of a profile, and the state of each part (in step with the sections)."""
    parts: list[tuple[Part, list[str]]] = []
    for kind in ("experience", "education", "skills"):
        parts.append((Part(TITLES[kind], body_of(kind, sections)), []))
    for block in blocks_of(sections):
        heading = block_heading(block["title"])
        parts.append((Part(heading, demote(block["body"])), [block["title"]]))
    parts.append((Part(TITLES["answers"], body_of("answers", sections)), []))
    state: dict[str, dict[str, Any]] = {}
    for part, owned in parts:
        if part.key not in state:
            state[part.key] = in_step(part, sections, owned)
    return Converted(join("", [part for part, _ in parts]), state)


def in_step(part: Part, sections: Mapping[str, Any], owned: Sequence[str]) -> dict[str, Any]:
    """A part's state once its text and the sections agree."""
    return {
        "applied": part.digest,
        "basis": basis(part.kind, sections, owned),
        "blocks": list(owned),
    }


# --- what a part holds in the sections ------------------------------------------------------------

_PROSE = tuple(ANSWERS)


def held(kind: Kind, sections: Mapping[str, Any], owned: Sequence[str] = ()) -> Any:
    """What the sections hold of a part's kind: what the part replaces, and what its basis
    hashes. Regular expression categories and gates are not a part's."""
    if kind in ("experience", "education"):
        name = "employers" if kind == "experience" else "education"
        return (sections.get("experience") or {}).get(name) or []
    if kind == "skills":
        skills = sections.get("skills") or {}
        return {
            "years_experience": skills.get("years_experience"),
            **{
                bucket: [one for one in skills.get(bucket) or [] if not one.get("advanced_regex")]
                for bucket in (*BUCKETS, "gates")
            },
        }
    if kind == "answers":
        answers = sections.get("answers") or {}
        held_answers: dict[str, Any] = {name: answers.get(name) for name in _PROSE}
        held_answers["work_authorization"] = [
            {"country": one["country"], "answer": one["answer"]}
            for one in answers.get("work_authorization") or []
        ]
        return held_answers
    titles = {key_of(title) for title in owned}
    return [b for b in blocks_of(sections) if key_of(b["title"]) in titles]


def basis(kind: Kind, sections: Mapping[str, Any], owned: Sequence[str] = ()) -> str:
    data = json.dumps(held(kind, sections, owned), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def advanced_count(sections: Mapping[str, Any]) -> int:
    """The regular expression categories and gates, which the document does not hold."""
    skills = sections.get("skills") or {}
    return sum(
        1
        for bucket in (*BUCKETS, "gates")
        for one in skills.get(bucket) or []
        if one.get("advanced_regex")
    )


# --- what the model submits -----------------------------------------------------------------------

_SHORT = 4 * LINE_MAX
_LONG = 2 * PART_MAX


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmployerIn(_In):
    organisation: str = Field(max_length=_SHORT)
    title: str = Field(max_length=_SHORT)
    start: str = Field(max_length=40)
    end: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=_SHORT)
    contract: str | None = Field(default=None, max_length=40)
    excerpts: list[str] = Field(max_length=citations.EXCERPTS_MAX)


class SchoolIn(_In):
    school: str = Field(max_length=_SHORT)
    degree: str = Field(max_length=_SHORT)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=_SHORT)
    details: str | None = Field(default=None, max_length=_LONG)
    excerpts: list[str] = Field(max_length=citations.EXCERPTS_MAX)


class CategoryIn(_In):
    bucket: Literal["have", "adjacent", "none"]
    name: str = Field(max_length=_SHORT)
    terms: list[str] = Field(max_length=300)


class GateIn(_In):
    name: str = Field(max_length=_SHORT)
    terms: list[str] = Field(max_length=300)
    penalty: int | None = Field(default=None, ge=0, le=100)


class BlockIn(_In):
    title: str = Field(max_length=_SHORT)
    # Left out: the whole body of the part, as written (no copy to make, nothing to mistype).
    body: str | None = Field(default=None, max_length=_LONG)


class AuthorizationIn(_In):
    country: str = Field(max_length=_SHORT)
    answer: str = Field(max_length=_LONG)


class AnswersIn(_In):
    notice_period: str | None = Field(default=None, max_length=_LONG)
    earliest_start: str | None = Field(default=None, max_length=_LONG)
    relocation: str | None = Field(default=None, max_length=_LONG)
    travel: str | None = Field(default=None, max_length=_LONG)
    motivation: str | None = Field(default=None, max_length=_LONG)
    heard_about: str | None = Field(default=None, max_length=_LONG)
    work_authorization: list[AuthorizationIn] = Field(default_factory=list, max_length=50)


class Interpretation(_In):
    """What `submit_interpretation` takes: the fields of the part's kind, shape only."""

    employers: list[EmployerIn] | None = Field(default=None, max_length=60)
    education: list[SchoolIn] | None = Field(default=None, max_length=40)
    years_experience: float | None = Field(default=None, ge=0, le=60)
    categories: list[CategoryIn] | None = Field(default=None, max_length=200)
    gates: list[GateIn] | None = Field(default=None, max_length=100)
    blocks: list[BlockIn] | None = Field(default=None, max_length=50)
    answers: AnswersIn | None = None


# What each kind reads of an interpretation; a kind whose first field is missing gave no answer.
READS: dict[Kind, tuple[str, ...]] = {
    "experience": ("employers", "blocks"),
    "education": ("education", "blocks"),
    "skills": ("categories", "gates", "years_experience"),
    "answers": ("answers",),
    "facts": ("blocks",),
}


# --- the check ------------------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return _WORD.findall(citations.normalise(text).text)


class Written:
    """The words of a text, in order, to tell whether a value is written in it."""

    def __init__(self, text: str) -> None:
        self.words = _words(text)
        self.at: dict[str, list[int]] = {}
        for index, word in enumerate(self.words):
            self.at.setdefault(word, []).append(index)
        self.numbers = citations.numbers_of(citations.normalise(text).text)

    def __call__(self, value: str | None) -> bool:
        """Whether the words of `value` appear, in that order, one after the other here. A
        value without a word is not written."""
        wanted = _words(value or "")
        if not wanted:
            return False
        size = len(wanted)
        return any(
            self.words[start : start + size] == wanted for start in self.at.get(wanted[0], [])
        )

    def number(self, value: float) -> bool:
        return _years(value) in self.numbers


EMPLOYER_TYPES = {
    "organisation": "name",
    "title": "text",
    "start": "date",
    "end": "date",
    "location": "text",
    "contract": "enum",
}
SCHOOL_TYPES = {
    "school": "name",
    "degree": "text",
    "start": "date",
    "end": "date",
    "location": "text",
}
_PRESENT = frozenset({"now", "present", "today", "current", "aujourd'hui", "en cours"})


def _cited(
    types: Mapping[str, str],
    value: dict[str, Any],
    document: citations.Document,
    excerpts: Sequence[str],
) -> Written | None:
    """The cited lines as words when the citation checker accepts `value`, else None."""
    found = citations.check_item(types, value, document, excerpts)
    if isinstance(found, citations.Refusal):
        return None
    return Written("\n".join(one.excerpt for one in found.citations))


def _end(value: str | None) -> str | None:
    return None if value is None or value.strip().casefold() in _PRESENT else value.strip()


@dataclass
class Outcome:
    """What a part changes once checked: the sections to write (stored form), the summary, the
    values not taken, and the fact blocks the part holds afterwards."""

    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)


def _named(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 80 else text[:77].rstrip() + "..."


def _many(count: int, one: str, more: str | None = None) -> str:
    return f"{count} {one if count == 1 else more or one + 's'}"


def _listed(names: Sequence[str]) -> str:
    shown = ", ".join(_named(name) for name in names[:SHOWN_MAX])
    rest = len(names) - SHOWN_MAX
    return shown if rest <= 0 else f"{shown} and {rest} more"


def _contract_of(text: str | None) -> str | None:
    """The contract a model named, as the schema writes it ("permanent" is CDI); None for none,
    and "?" for a word that is not a contract."""
    if text is None or not text.strip():
        return None
    if text in CONTRACT_WORDS:
        return text
    wanted = fold(text)
    found = [name for name, words in CONTRACT_WORDS.items() if wanted in (fold(name), *words)]
    return found[0] if found else "?"


def _employers(
    part: Part, stored: list[dict[str, Any]], given: list[EmployerIn], out: Outcome
) -> list[dict[str, Any]]:
    document = citations.prepare(part.text)
    taken: list[dict[str, Any] | None] = []  # None: a new entry, keyed below
    new: list[tuple[int, dict[str, Any]]] = []
    used: set[int] = set()
    for one in given:
        contract = _contract_of(one.contract)
        value = {
            "organisation": one.organisation.strip(),
            "title": one.title.strip(),
            "start": one.start.strip(),
            "end": _end(one.end),
            "location": (one.location or "").strip() or None,
            "contract": None if contract == "?" else contract,
        }
        lines = _cited(EMPLOYER_TYPES, value, document, one.excerpts)
        entry: dict[str, Any] | None = None
        if (
            lines is not None
            and contract != "?"
            and lines(value["title"])
            and (value["location"] is None or lines(value["location"]))
            and (contract is None or any(lines(word) for word in CONTRACT_WORDS[contract]))
        ):
            try:
                entry = Employer.model_validate({"key": "x", **value}).model_dump(mode="json")
            except ValidationError:
                entry = None
        same = slug(value["organisation"])
        match = next(
            (i for i, s in enumerate(stored) if i not in used and slug(s["organisation"]) == same),
            None,
        )
        if match is not None:
            used.add(match)
        if entry is None:
            out.refused.append(
                f"A job at {_named(value['organisation'])}: its dates, title or place are not "
                "written that way in this part."
            )
            if match is not None:  # the stored one stays as it is
                taken.append(stored[match])
            continue
        if match is not None:
            taken.append({**entry, "key": stored[match]["key"]})
        else:
            new.append((len(taken), {**entry, "key": same}))
            taken.append(None)
    keys = {one["key"] for one in taken if one is not None}
    for at, entry in new:
        key, number = entry["key"], 2
        while key in keys:
            key, number = f"{entry['key']}-{number}", number + 1
        keys.add(key)
        taken[at] = {**entry, "key": key}
    return [one for one in taken if one is not None]


def _schools(
    part: Part, stored: list[dict[str, Any]], given: list[SchoolIn], out: Outcome
) -> list[dict[str, Any]]:
    document = citations.prepare(part.text)
    written = Written(part.text)
    taken: list[dict[str, Any]] = []
    for one in given:
        value = {
            "school": one.school.strip(),
            "degree": one.degree.strip(),
            "start": (one.start or "").strip() or None,
            "end": _end(one.end) or None,
            "location": (one.location or "").strip() or None,
        }
        lines = _cited(SCHOOL_TYPES, value, document, one.excerpts)
        details = (one.details or "").strip() or None
        ok = (
            lines is not None
            and lines(value["degree"])
            and (value["location"] is None or lines(value["location"]))
            and (details is None or written(details))
        )
        if ok:
            try:
                taken.append(
                    School.model_validate({**value, "details": details}).model_dump(mode="json")
                )
                continue
            except ValidationError:
                pass
        out.refused.append(
            f"A school, {_named(value['school'])}: its dates, degree or place are not written "
            "that way in this part."
        )
        same = fold(value["school"])
        taken += [s for s in stored if fold(s["school"]) == same and s not in taken]
    return taken


def _terms(written: Written, terms: Sequence[str], what: str, out: Outcome) -> list[str]:
    kept = [term.strip() for term in terms if written(term)]
    dropped = len([term for term in terms if term.strip()]) - len(kept)
    if dropped:
        out.refused.append(f"{_many(dropped, 'term')} of {_named(what)}: not written in this part.")
    return kept


def _skills(
    part: Part, stored: Mapping[str, Any], given: Interpretation, out: Outcome
) -> dict[str, Any]:
    written = Written(part.text)
    advanced = {
        bucket: [
            (i, one) for i, one in enumerate(stored.get(bucket) or []) if one.get("advanced_regex")
        ]
        for bucket in (*BUCKETS, "gates")
    }
    reserved = {fold(one["name"]) for pairs in advanced.values() for _, one in pairs}
    seen = set(reserved)
    result: dict[str, Any] = {"years_experience": stored.get("years_experience")}
    if given.years_experience is not None:
        if written.number(given.years_experience):
            result["years_experience"] = float(given.years_experience)
        else:
            out.refused.append(f"{YEARS}: the number is not written in this part.")
    else:
        result["years_experience"] = None
    plain: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in (*BUCKETS, "gates")}
    for one in given.categories or []:
        terms = _terms(written, one.terms, one.name, out)
        name = one.name.strip() if written(one.name) else (terms[0] if terms else "")
        if not terms or fold(name) in seen:
            if terms:
                out.refused.append(f"{_named(name)}: another category already has this name.")
            continue
        seen.add(fold(name))
        plain[one.bucket].append({"advanced_regex": False, "terms": terms, "name": name})
    stored_gates = {fold(g["name"]): g for g in stored.get("gates") or []}
    for gate in given.gates or []:
        terms = _terms(written, gate.terms, gate.name, out)
        name = gate.name.strip() if written(gate.name) else (terms[0] if terms else "")
        if gate.penalty is not None and not written.number(gate.penalty):
            out.refused.append(f"{_named(name)}: its penalty is not written in this part.")
            continue
        if not terms or fold(name) in seen:
            continue
        seen.add(fold(name))
        before = stored_gates.get(fold(name), {})
        penalty = gate.penalty if gate.penalty is not None else before.get("penalty", 25)
        plain["gates"].append(
            {"advanced_regex": False, "terms": terms, "name": name, "penalty": penalty}
        )
    for bucket, entries in plain.items():
        for index, one in advanced[bucket]:  # regular expressions stay where they were
            entries.insert(min(index, len(entries)), one)
        result[bucket] = entries
    return result


def _answers(
    part: Part, stored: Mapping[str, Any], given: AnswersIn, out: Outcome
) -> dict[str, Any]:
    written = Written(part.text)
    result = dict(stored)
    for name, title in ANSWERS.items():
        value = (getattr(given, name) or "").strip() or None
        if value is None or written(value):
            result[name] = value
        else:
            out.refused.append(f"{title}: not written that way in this part.")
    sponsorship = {
        fold(one["country"]): one.get("needs_sponsorship")
        for one in stored.get("work_authorization") or []
    }
    countries = []
    for one in given.work_authorization:
        if written(one.country) and written(one.answer):
            countries.append(
                {
                    "country": one.country.strip(),
                    "answer": one.answer.strip(),
                    "needs_sponsorship": sponsorship.get(fold(one.country)),
                }
            )
        else:
            out.refused.append(f"{AUTHORIZATION} for {_named(one.country)}: not written there.")
    result["work_authorization"] = countries
    return result


def _blocks(
    part: Part,
    stored: list[dict[str, Any]],
    owned: Sequence[str],
    given: list[BlockIn],
    out: Outcome,
) -> list[dict[str, Any]]:
    written = Written(part.text)
    mine = {key_of(title) for title in owned}
    others = {key_of(b["title"]) for b in stored if key_of(b["title"]) not in mine}
    new: list[dict[str, Any]] = []
    for one in given:
        title = one.title.strip()
        body = part.body.strip() if one.body is None else one.body.strip()
        if not written(title) or not body or (one.body is not None and not written(body)):
            out.refused.append(f"A fact block, {_named(title)}: not written that way in this part.")
            continue
        if key_of(title) in others or key_of(title) in {key_of(b["title"]) for b in new}:
            out.refused.append(f"{_named(title)}: another part already has a block of this title.")
            continue
        new.append({"title": title, "body": body})
    at = next((i for i, b in enumerate(stored) if key_of(b["title"]) in mine), len(stored))
    kept = [b for b in stored if key_of(b["title"]) not in mine]
    at = min(at, len(kept))
    out.blocks = [b["title"] for b in new]
    return [*kept[:at], *new, *kept[at:]]


def _period_words(entry: Mapping[str, Any], present: bool) -> str:
    when = _when(entry.get("start"), entry.get("end"), present)
    return f", {when}" if when else ""


def _diff_lines(
    what: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    identity: Any,
    label: Any,
) -> list[str]:
    old = {identity(one): one for one in before}
    new = {identity(one): one for one in after}
    lines = []
    for verb, names in (
        ("added", [label(new[k]) for k in new if k not in old]),
        ("updated", [label(new[k]) for k in new if k in old and old[k] != new[k]]),
        ("removed", [label(old[k]) for k in old if k not in new]),
    ):
        if names:
            lines.append(f"{_many(len(names), what)} {verb}: {_listed(names)}")
    return lines


def _skills_lines(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    lines = []
    if before.get("years_experience") != after.get("years_experience"):
        new = after.get("years_experience")
        lines.append(f"{YEARS}: {'none' if new is None else _years(new)}")
    for bucket, title in {**BUCKETS, "gates": GATES}.items():
        old = {fold(t) for one in before.get(bucket) or [] for t in one["terms"]}
        new_terms = [t for one in after.get(bucket) or [] for t in one["terms"]]
        new = {fold(t) for t in new_terms}
        added = [t for t in new_terms if fold(t) not in old]
        removed = [
            t for one in before.get(bucket) or [] for t in one["terms"] if fold(t) not in new
        ]
        where = title[0].lower() + title[1:]
        if added:
            lines.append(f"{_many(len(added), 'skill')} added to {where}: {_listed(added)}")
        if removed:
            lines.append(f"{_many(len(removed), 'skill')} removed from {where}: {_listed(removed)}")
    return lines


def _answers_lines(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    changed = [title for name, title in ANSWERS.items() if before.get(name) != after.get(name)]
    if (
        held("answers", {"answers": before})["work_authorization"]
        != held("answers", {"answers": after})["work_authorization"]
    ):
        changed.append(AUTHORIZATION)
    return [f"{_many(len(changed), 'answer')} updated: {', '.join(changed)}"] if changed else []


def interpret(
    part: Part, sections: Mapping[str, Any], owned: Sequence[str], given: Interpretation
) -> Outcome | None:
    """What the part changes, checked; None when the model gave nothing for its kind."""
    kind = part.kind
    if getattr(given, READS[kind][0]) is None:
        return None
    out = Outcome()
    experience = dict(sections.get("experience") or ExperienceSection().model_dump(mode="json"))
    if kind == "experience":
        experience["employers"] = _employers(
            part, experience.get("employers") or [], given.employers or [], out
        )
        out.lines += _diff_lines(
            "job",
            held("experience", sections),
            experience["employers"],
            lambda e: e["key"],
            lambda e: f"{e['title']} at {e['organisation']}{_period_words(e, True)}",
        )
        out.changes["experience"] = experience
    elif kind == "education":
        experience["education"] = _schools(
            part, experience.get("education") or [], given.education or [], out
        )
        out.lines += _diff_lines(
            "school",
            held("education", sections),
            experience["education"],
            lambda s: (fold(s["school"]), s.get("start")),
            lambda s: f"{s['degree']}, {s['school']}{_period_words(s, False)}",
        )
        out.changes["experience"] = experience
    elif kind == "skills":
        stored = sections.get("skills") or SkillsSection().model_dump(mode="json")
        skills = {**stored, **_skills(part, stored, given, out)}
        out.lines += _skills_lines(held("skills", sections), held("skills", {"skills": skills}))
        out.changes["skills"] = skills
    elif kind == "answers" and given.answers is not None:
        stored = sections.get("answers") or {}
        answers = _answers(part, stored, given.answers, out)
        out.lines += _answers_lines(stored, answers)
        out.changes["answers"] = answers
    if kind in ("experience", "education", "facts") and given.blocks is not None:
        stored_blocks = blocks_of(sections)
        blocks = _blocks(part, stored_blocks, owned, given.blocks, out)
        out.lines += _diff_lines(
            "fact block",
            held("facts", sections, owned),
            held("facts", {"facts": {"blocks": blocks}}, out.blocks),
            lambda b: key_of(b["title"]),
            lambda b: b["title"],
        )
        out.changes["facts"] = {"blocks": blocks}
    elif kind in ("experience", "education"):
        out.blocks = list(owned)
    for name, data in list(out.changes.items()):
        try:
            out.changes[name] = validate_section(name, data)
        except ValidationError:
            out.refused.append(f"The {name} could not be saved as read: nothing of it is taken.")
            del out.changes[name]
    return out
