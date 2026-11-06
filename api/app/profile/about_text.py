"""The "About you" document: its parts, the writer that converts a profile into it, and the code
that checks a reading before anything is shown (SPEC-profile, "About you"; owner, 2026-11-05:
"a markdown that a model interpret", narrowed the same day: "only facts and skills should be in
plain text or markdown. other fields remins on well structured inputs").

Pure: no database, no network, no model call (about.py stores the document and runs the agent).

**Parts.** The document is Markdown. A line `## Heading` outside a fenced code block starts a
part; what comes before the first one is kept and never read. A part headed Skills (or
Compétences, case and spaces aside) holds the skills the fit reads; any other part is one fact
block of that title. A part is identified by its heading's `key`.

**The writer** (`convert`, `body_of`) is deterministic: from the stored sections it writes the
skills part so that every value can be found again in its text, "Years of experience: 5" then a
line "- name: term, term" per category under What I have, Close to what I have, What I do not
have and Requirements that lower my fit ("- name, lowers the fit by 25: terms"), and each fact
block as a part of its own, its level one and two headings written as level three (they would
start a part). Regular expression categories and gates (`advanced_regex`) are not written: the
code keeps them as stored. No other section is written: experience, education, languages,
answers, identity and the search stay forms.

**Reading a part.** A fact part needs no model: it is the block, its heading the title and its
text the body, as written (`read_facts`). The skills part is read by the agent; code checks
every value against the text (`interpret`), first failing reason wins, and a value that fails is
not taken (the stored one stays): every term and name must be written, word after word, in the
part (`Written`: the words of the citation checker's normalisation, `ingestion.citations`, in that
order); the years and a gate's penalty must be numbers of the part (`citations.numbers_of`). A
category whose name the text lacks takes its first term as its name.
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
from app.ingestion.items import fold
from app.profile.schemas import LINE_MAX, SkillsSection, validate_section

TEXT_MAX = 300_000  # characters of the whole document
PART_MAX = 20_000  # characters of one part a Done may send to the model
SHOWN_MAX = 3  # names a summary line lists before "and N more"

Kind = Literal["skills", "answers", "facts"]
HEADINGS: dict[str, Kind] = {
    "skills": "skills",
    "compétences": "skills",
    "answers": "answers",
    "réponses": "answers",
}
SKILLS_TITLE = "Skills"
ANSWERS_TITLE = "Answers"
NOTES = "Notes: "  # before the title of a block that would read as another kind
BUCKETS = {
    "have": "What I have",
    "adjacent": "Close to what I have",
    "none": "What I do not have",
}
GATES = "Requirements that lower my fit"
YEARS = "Years of experience"

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


def block_heading(title: str) -> str:
    """The heading of a fact block's part: its title, unless that reads as another kind."""
    return title if kind_of(title) == "facts" else f"{NOTES}{title}"


def block_title(heading: str) -> str:
    """The title of the block a fact part is: its heading, without the prefix `block_heading`
    gave a title that reads as another kind."""
    rest = heading[len(NOTES) :] if heading.startswith(NOTES) else heading
    return rest if kind_of(rest) != "facts" else heading


def blocks_of(sections: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list((sections.get("facts") or {}).get("blocks") or [])


def body_of(kind: Kind, sections: Mapping[str, Any], owned: Sequence[str] = ()) -> str:
    """The body the writer gives a part of this kind; for facts, the bodies of its blocks."""
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
    """The document of a profile (the skills part, a part per fact block, then the answers), and
    the state of each part (in step with the sections)."""
    parts: list[tuple[Part, list[str]]] = [(Part(SKILLS_TITLE, body_of("skills", sections)), [])]
    for block in blocks_of(sections):
        heading = block_heading(block["title"])
        parts.append((Part(heading, demote(block["body"])), [block["title"]]))
    parts.append((Part(ANSWERS_TITLE, body_of("answers", sections)), []))
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


def held(kind: Kind, sections: Mapping[str, Any], owned: Sequence[str] = ()) -> Any:
    """What the sections hold of a part's kind: what the part replaces, and what its basis
    hashes. Regular expression categories and gates are not a part's."""
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
        return validate_section("answers", sections.get("answers") or {})
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


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryIn(_In):
    bucket: Literal["have", "adjacent", "none"]
    name: str = Field(max_length=_SHORT)
    terms: list[str] = Field(max_length=300)


class GateIn(_In):
    name: str = Field(max_length=_SHORT)
    terms: list[str] = Field(max_length=300)
    penalty: int | None = Field(default=None, ge=0, le=100)


# --- the answers part -----------------------------------------------------------------------------
#
# The answers are written one `###` heading each, short sentences the person can change. A prose
# answer must be written word after word in the part. A value that was a switch (a yes or no, the
# equal opportunity choice and answers, a work authorisation's sponsorship, the salary stance and
# ranges) comes with the line that states it (`quote`): the line must be written in the part and
# name its topic, a yes or no is that line's only yes or no word, a figure a number of it. A value
# the text does not state that way is not changed, and the reading says so. Each such change is a
# line of its own in the summary, old value and new.

PROSE = {
    "notice_period": "Notice period",
    "earliest_start": "Earliest start",
    "salary_policy": "Salary sentence",
    "relocation": "Relocation",
    "travel": "Travel",
    "motivation": "Motivation",
    "heard_about": "How I hear about jobs",
    "preferred_name": "Preferred name",
    "pronouns": "Pronouns",
    "years_experience_stated": "Years of experience, as I say it",
    "highest_degree": "Highest degree",
}
# A yes or no: its heading, its topic word (the quote must name it), its two sentences and what
# the reading says when the text does not state it.
SWITCHES = {
    "willing_onsite": (
        "Working on site", "site", "Yes, I can work on site.", "No, I cannot work on site.",
        "whether you can work on site",
    ),
    "willing_hybrid": (
        "Hybrid work", "hybrid", "Yes, I can work hybrid.", "No, I cannot work hybrid.",
        "whether you can work hybrid",
    ),
    "previously_applied_default": (
        "Applied before", "applied", "Yes, by default I say I applied before.",
        "No, by default I say I have not applied before.", "whether you applied before",
    ),
    "background_check_consent": (
        "Background checks", "background", "Yes, I consent to background checks.",
        "No, I do not consent to background checks.",
        "whether you consent to background checks",
    ),
    "reference_check_consent": (
        "Reference checks", "reference", "Yes, I consent to reference checks.",
        "No, I do not consent to reference checks.", "whether you consent to reference checks",
    ),
}  # fmt: skip
AUTHORIZATION = "Work authorisation"
SALARY = "Salary on forms"
STANCES = {
    "leave_blank": ("blank", "On forms, I leave the salary blank."),
    "range": ("range", "On forms, I give the salary range below."),
    "sentence": ("sentence", "On forms, I write the salary sentence."),
}
RANGES = "Salary ranges"
PERIODS = {
    "year": ("year", "yearly", "annual"),
    "month": ("month", "monthly"),
    "day": ("day", "daily"),
}
EEO = "Equal opportunity questions"
POLICIES = {
    "decline": ("decline", "I decline the equal opportunity questions."),
    "leave": ("blank", "I leave the equal opportunity questions blank."),
    "stored": ("below", "I answer the equal opportunity questions with the answers below."),
}
TOPICS = {
    "gender": "Gender",
    "ethnicity": "Ethnicity",
    "race": "Race",
    "disability": "Disability",
    "veteran": "Veteran status",
    "sexual_orientation": "Sexual orientation",
    "age": "Age",
}
LEAVE_BLANK = "Questions I leave blank"
YES = frozenset({"yes", "oui"})
NO = frozenset({"no", "non"})


def _yes_no(value: bool | None) -> str:
    return "not set" if value is None else "yes" if value else "no"


def _range_words(one: Mapping[str, Any]) -> str:
    per = f"a {one.get('period') or 'year'}"
    if one.get("maximum") is None:
        return f"{one['currency']} {one['minimum']} or more {per}"
    return f"{one['currency']} {one['minimum']} to {one['maximum']} {per}"


def _write_answers(sections: Mapping[str, Any]) -> str:
    answers = sections.get("answers") or {}
    chunks: list[str] = []

    def add(title: str, body: str) -> None:
        chunks.append(f"### {title}\n{body}")

    if answers.get("work_authorization"):
        lines = []
        for one in answers["work_authorization"]:
            line = f"- {one['country']}: " + demote(one["answer"]).replace("\n", "\n  ")
            sponsor = one.get("needs_sponsorship")
            if sponsor is not None:
                line += f"\n  Sponsorship needed: {'yes' if sponsor else 'no'}."
            lines.append(line)
        add(AUTHORIZATION, "\n".join(lines))
    for name, title in PROSE.items():
        if name == "highest_degree":
            continue
        if answers.get(name):
            add(title, demote(answers[name]))
    if answers.get("highest_degree") or answers.get("highest_degree_year"):
        lines = [answers.get("highest_degree") or ""]
        if answers.get("highest_degree_year"):
            lines.append(f"Year: {answers['highest_degree_year']}")
        add(PROSE["highest_degree"], "\n\n".join(line for line in lines if line))
    if answers.get("salary_stance"):
        add(SALARY, STANCES[answers["salary_stance"]][1])
    if answers.get("salary_ranges"):
        add(RANGES, "\n".join(f"- {_range_words(one)}" for one in answers["salary_ranges"]))
    for name, (title, _, yes, no, _) in SWITCHES.items():
        if answers.get(name) is not None:
            add(title, yes if answers[name] else no)
    policy = answers.get("eeo_policy") or "decline"
    eeo = [POLICIES[policy][1]]
    if answers.get("eeo_answers"):
        eeo.append(
            "\n".join(
                f"- {TOPICS[one['topic']]}: {one['answer']}" for one in answers["eeo_answers"]
            )
        )
    add(EEO, "\n\n".join(eeo))
    if answers.get("leave_blank"):
        add(LEAVE_BLANK, "\n".join(f"- {line}" for line in answers["leave_blank"]))
    return "\n\n".join(chunks)


class SaidIn(_In):
    """A yes or no, with the line of the part that states it."""

    value: bool
    quote: str = Field(max_length=_SHORT)


class AuthorizationIn(_In):
    country: str = Field(max_length=_SHORT)
    answer: str = Field(max_length=2 * PART_MAX)
    needs_sponsorship: SaidIn | None = None


class RangeIn(_In):
    currency: str = Field(max_length=8)
    minimum: int = Field(ge=0, le=100_000_000)
    maximum: int | None = Field(default=None, ge=0, le=100_000_000)
    period: Literal["year", "month", "day"] = "year"
    quote: str = Field(max_length=_SHORT)


class EeoIn(_In):
    topic: Literal[
        "gender", "ethnicity", "race", "disability", "veteran", "sexual_orientation", "age"
    ]
    answer: str = Field(max_length=_SHORT)


class ChoiceIn(_In):
    value: str = Field(max_length=40)
    quote: str = Field(max_length=_SHORT)


class AnswersIn(_In):
    notice_period: str | None = Field(default=None, max_length=2 * PART_MAX)
    earliest_start: str | None = Field(default=None, max_length=2 * PART_MAX)
    salary_policy: str | None = Field(default=None, max_length=2 * PART_MAX)
    relocation: str | None = Field(default=None, max_length=2 * PART_MAX)
    travel: str | None = Field(default=None, max_length=2 * PART_MAX)
    motivation: str | None = Field(default=None, max_length=2 * PART_MAX)
    heard_about: str | None = Field(default=None, max_length=2 * PART_MAX)
    preferred_name: str | None = Field(default=None, max_length=_SHORT)
    pronouns: str | None = Field(default=None, max_length=_SHORT)
    years_experience_stated: str | None = Field(default=None, max_length=_SHORT)
    highest_degree: str | None = Field(default=None, max_length=_SHORT)
    highest_degree_year: int | None = Field(default=None, ge=1950, le=2100)
    work_authorization: list[AuthorizationIn] = Field(default_factory=list, max_length=50)
    salary_stance: ChoiceIn | None = None
    salary_ranges: list[RangeIn] = Field(default_factory=list, max_length=10)
    eeo_policy: ChoiceIn | None = None
    eeo_answers: list[EeoIn] = Field(default_factory=list, max_length=7)
    leave_blank: list[str] = Field(default_factory=list, max_length=50)
    willing_onsite: SaidIn | None = None
    willing_hybrid: SaidIn | None = None
    previously_applied_default: SaidIn | None = None
    background_check_consent: SaidIn | None = None
    reference_check_consent: SaidIn | None = None


def _states(written: Written, said: SaidIn, topic: str) -> bool | None:
    """The yes or no a quote states about its topic, or None when it is not clear: the quote
    must be written in the part, name the topic and hold one kind of yes or no word only."""
    if not written(said.quote):
        return None
    words = set(_words(said.quote))
    if not Written(said.quote)(topic):
        return None
    yes, no = bool(words & YES), bool(words & NO)
    return None if yes == no else yes


def _answers(
    part: Part, stored: Mapping[str, Any], given: AnswersIn, out: Outcome
) -> dict[str, Any]:
    written = Written(part.text)
    result = dict(stored)
    for name, title in PROSE.items():
        value = (getattr(given, name) or "").strip() or None
        if value is None or written(value):
            result[name] = value
        else:
            out.refused.append(f"{title}: not written that way in this part.")
    year = given.highest_degree_year
    if year is None or written.number(year):
        result["highest_degree_year"] = year
    else:
        out.refused.append(f"{PROSE['highest_degree']}: its year is not written in this part.")

    for name, (_, topic, _, _, whether) in SWITCHES.items():
        said = getattr(given, name)
        if said is None:
            if stored.get(name) is not None:
                out.refused.append(f"Not changed: the text does not say {whether}.")
            continue
        value = _states(written, said, topic)
        if value is None or value != said.value:
            out.refused.append(
                f"Not changed: the text does not say clearly {whether}. Start the line with Yes "
                "or No."
            )
            continue
        result[name] = value

    sponsorship = {
        fold(one["country"]): one.get("needs_sponsorship")
        for one in stored.get("work_authorization") or []
    }
    countries = []
    for one in given.work_authorization:
        if not (written(one.country) and written(one.answer)):
            out.refused.append(f"{AUTHORIZATION} for {_named(one.country)}: not written there.")
            countries += [
                s
                for s in stored.get("work_authorization") or []
                if fold(s["country"]) == fold(one.country)
            ]
            continue
        need = sponsorship.get(fold(one.country))
        if one.needs_sponsorship is not None:
            value = _states(written, one.needs_sponsorship, "sponsorship")
            if value is None or value != one.needs_sponsorship.value:
                out.refused.append(
                    f"Not changed: the text does not say clearly whether {_named(one.country)} "
                    "needs a sponsorship."
                )
            else:
                need = value
        countries.append(
            {
                "country": one.country.strip(),
                "answer": one.answer.strip(),
                "needs_sponsorship": need,
            }
        )
    result["work_authorization"] = countries

    for name, choices, what in (
        ("salary_stance", STANCES, "how you answer the salary on forms"),
        ("eeo_policy", POLICIES, "how you answer the equal opportunity questions"),
    ):
        choice: ChoiceIn | None = getattr(given, name)
        if choice is None:
            if stored.get(name) is not None:
                out.refused.append(f"Not changed: the text does not say {what}.")
            continue
        word = choices.get(choice.value, ("", ""))[0]
        if word and written(choice.quote) and Written(choice.quote)(word):
            result[name] = choice.value
        else:
            out.refused.append(f"Not changed: the text does not say clearly {what}.")

    ranges = []
    for one in given.salary_ranges:
        quote = Written(one.quote)
        ok = (
            written(one.quote)
            and quote(one.currency)
            and quote.number(one.minimum)
            and (one.maximum is None or quote.number(one.maximum))
            and any(quote(word) for word in PERIODS[one.period])
        )
        if ok:
            ranges.append(
                {
                    "currency": one.currency.strip().upper(),
                    "minimum": one.minimum,
                    "maximum": one.maximum,
                    "period": one.period,
                }
            )
        else:
            out.refused.append(
                f"Not changed: a salary range in {_named(one.currency)} is not written clearly."
            )
            ranges += [
                r
                for r in stored.get("salary_ranges") or []
                if r["currency"] == one.currency.strip().upper()
            ]
    result["salary_ranges"] = ranges

    eeo = []
    for one in given.eeo_answers:
        if written(TOPICS[one.topic]) and written(one.answer):
            eeo.append({"topic": one.topic, "answer": one.answer.strip()})
        else:
            out.refused.append(
                f"Not changed: your {TOPICS[one.topic].lower()} answer is not written there."
            )
            eeo += [e for e in stored.get("eeo_answers") or [] if e["topic"] == one.topic]
    result["eeo_answers"] = eeo
    blank = [line.strip() for line in given.leave_blank if written(line)]
    if len(blank) < len([line for line in given.leave_blank if line.strip()]):
        out.refused.append(f"{LEAVE_BLANK}: a line not written that way in this part.")
    result["leave_blank"] = blank
    return result


def _change(title: str, old: str, new: str) -> str:
    """One changed value, old and new, read without ambiguity even when a value holds "to"
    ("Salary range: was EUR 50000 to 60000 a year, now none")."""
    return f"{title}: was {old}, now {new}"


def _answers_lines(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Prose in one line; every switch, sponsorship, salary and equal opportunity change on its
    own line, old value and new."""
    lines = []
    prose = [title for name, title in PROSE.items() if before.get(name) != after.get(name)]
    if before.get("highest_degree_year") != after.get("highest_degree_year"):
        prose.append("Year of the highest degree")
    if prose:
        lines.append(f"{_many(len(prose), 'answer')} updated: {', '.join(prose)}")
    for name, (title, *_rest) in SWITCHES.items():
        if before.get(name) != after.get(name):
            lines.append(_change(title, _yes_no(before.get(name)), _yes_no(after.get(name))))
    old = {fold(one["country"]): one for one in before.get("work_authorization") or []}
    new = {fold(one["country"]): one for one in after.get("work_authorization") or []}
    for key, one in new.items():
        was = old.get(key)
        if was is None:
            lines.append(f"{AUTHORIZATION} for {one['country']}: added")
        elif was.get("answer") != one["answer"]:
            lines.append(f"{AUTHORIZATION} for {one['country']}: answer updated")
        if was is not None and was.get("needs_sponsorship") != one.get("needs_sponsorship"):
            lines.append(
                _change(
                    f"Sponsorship for {one['country']}",
                    _yes_no(was.get("needs_sponsorship")),
                    _yes_no(one.get("needs_sponsorship")),
                )
            )
    for key, one in old.items():
        if key not in new:
            lines.append(f"{AUTHORIZATION} for {one['country']}: removed")
    for name, title in (("salary_stance", SALARY), ("eeo_policy", EEO)):
        if before.get(name) != after.get(name):
            lines.append(
                _change(title, before.get(name) or "not set", after.get(name) or "not set")
            )
    old_ranges = {one["currency"]: one for one in before.get("salary_ranges") or []}
    new_ranges = {one["currency"]: one for one in after.get("salary_ranges") or []}
    for currency in [*new_ranges, *[c for c in old_ranges if c not in new_ranges]]:
        was, now = old_ranges.get(currency), new_ranges.get(currency)
        if was != now:
            lines.append(
                _change(
                    "Salary range",
                    _range_words(was) if was else "none",
                    _range_words(now) if now else "none",
                )
            )
    old_eeo = {one["topic"]: one["answer"] for one in before.get("eeo_answers") or []}
    new_eeo = {one["topic"]: one["answer"] for one in after.get("eeo_answers") or []}
    for topic in TOPICS:
        if old_eeo.get(topic) != new_eeo.get(topic):
            lines.append(
                _change(
                    f"{TOPICS[topic]} answer",
                    old_eeo.get(topic) or "none",
                    new_eeo.get(topic) or "none",
                )
            )
    if (before.get("leave_blank") or []) != (after.get("leave_blank") or []):
        lines.append(f"{LEAVE_BLANK}: updated")
    return lines


class Interpretation(_In):
    """What `submit_interpretation` takes: the skills, or the answers, the part states, shape
    only."""

    years_experience: float | None = Field(default=None, ge=0, le=60)
    categories: list[CategoryIn] | None = Field(default=None, max_length=200)
    gates: list[GateIn] | None = Field(default=None, max_length=100)
    answers: AnswersIn | None = None


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
    seen = {fold(one["name"]) for pairs in advanced.values() for _, one in pairs}
    result: dict[str, Any] = {"years_experience": None}
    if given.years_experience is not None:
        if written.number(given.years_experience):
            result["years_experience"] = float(given.years_experience)
        else:
            result["years_experience"] = stored.get("years_experience")
            out.refused.append(f"{YEARS}: the number is not written in this part.")
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


def _diff_lines(what: str, before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    old = {key_of(one["title"]): one for one in before}
    new = {key_of(one["title"]): one for one in after}
    lines = []
    for verb, names in (
        ("added", [new[k]["title"] for k in new if k not in old]),
        ("updated", [new[k]["title"] for k in new if k in old and old[k] != new[k]]),
        ("removed", [old[k]["title"] for k in old if k not in new]),
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


def _validated(out: Outcome) -> Outcome:
    for name, data in list(out.changes.items()):
        try:
            out.changes[name] = validate_section(name, data)
        except ValidationError:
            out.refused.append(f"The {name} could not be saved as read: nothing of it is taken.")
            del out.changes[name]
    return out


def interpret(part: Part, sections: Mapping[str, Any], given: Interpretation) -> Outcome | None:
    """What the skills or the answers part changes, checked; None when the model gave nothing
    of the part's kind."""
    out = Outcome()
    if part.kind == "answers":
        if given.answers is None:
            return None
        stored_answers = held("answers", sections)
        answers = _answers(part, stored_answers, given.answers, out)
        out.lines += _answers_lines(stored_answers, answers)
        out.changes["answers"] = answers
        return _validated(out)
    if given.categories is None:
        return None
    stored = sections.get("skills") or SkillsSection().model_dump(mode="json")
    skills = {**stored, **_skills(part, stored, given, out)}
    out.lines += _skills_lines(held("skills", sections), held("skills", {"skills": skills}))
    out.changes["skills"] = skills
    return _validated(out)


def read_facts(part: Part, sections: Mapping[str, Any], owned: Sequence[str]) -> Outcome:
    """A fact part read by code: the block of its heading and its text as written (an empty part
    is no block), in the place of the blocks the part held; a title another block holds is
    refused."""
    out = Outcome()
    stored = blocks_of(sections)
    mine = {key_of(title) for title in owned}
    title, body = block_title(part.heading).strip(), part.body.strip()
    new = [{"title": title, "body": body}] if body else []
    if new and any(
        key_of(b["title"]) == key_of(title) for b in stored if key_of(b["title"]) not in mine
    ):
        out.refused.append(f"{_named(title)}: another part already has a block of this title.")
        out.blocks = list(owned)
        return out
    at = next((i for i, b in enumerate(stored) if key_of(b["title"]) in mine), len(stored))
    kept = [b for b in stored if key_of(b["title"]) not in mine]
    at = min(at, len(kept))
    blocks = [*kept[:at], *new, *kept[at:]]
    out.blocks = [b["title"] for b in new]
    out.lines = _diff_lines("fact block", held("facts", sections, owned), new)
    out.changes["facts"] = {"blocks": blocks}
    return _validated(out)
