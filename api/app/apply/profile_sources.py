"""Profile-only factual resolution for site sections. No answer bank, posting or page facts."""

import json
import re
from typing import Any

from app.apply import answers, profile_refusals
from app.apply.ats import base
from app.mail.factcheck_rules import Sources, check

# Entire labels only: unknown questions are left rather than guessed.
EXPERIENCE = {
    "employer": "organisation",
    "company": "organisation",
    "organisation": "organisation",
    "organization": "organisation",
    "entreprise": "organisation",
    "job title": "title",
    "title": "title",
    "poste": "title",
    "intitule du poste": "title",
    "start date": "start",
    "date de debut": "start",
    "end date": "end",
    "date de fin": "end",
    "location": "location",
    "lieu": "location",
}
EDUCATION = {
    "school": "school",
    "university": "school",
    "ecole": "school",
    "universite": "school",
    "degree": "degree",
    "diplome": "degree",
    "start date": "start",
    "date de debut": "start",
    "end date": "end",
    "date de fin": "end",
    "details": "details",
    "location": "location",
}


def texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, dict):
        return tuple(s for item in value.values() for s in texts(item))
    if isinstance(value, list):
        return tuple(s for item in value for s in texts(item))
    return (str(value),) if isinstance(value, (int, float)) else ()


def factual(value: str, context: answers.Context) -> bool:
    sections = context.sections
    exp = sections.get("experience", {})
    employers = exp.get("employers", [])
    schools = exp.get("education", [])
    return not check(
        value,
        Sources(
            profile=texts(dict(sections)),
            answers=texts(sections.get("answers", {})),
            employers=tuple(e["organisation"] for e in employers if e.get("organisation")),
            schools=tuple(e["school"] for e in schools if e.get("school")),
            titles=tuple(e["title"] for e in employers if e.get("title")),
            known_terms=texts(sections.get("skills", {})),
        ),
    )


def resolve(
    item: base.Field, context: answers.Context, section: str
) -> answers.Answer | answers.Left:
    if code := profile_refusals.refusal(item.label, item.kind):
        return answers.Left(code, item.options, item.required)
    answer = answers.resolve(item.form_field(), context)
    if isinstance(answer, answers.Answer):
        return (
            answer
            if not isinstance(answer.value, str) or factual(answer.value, context)
            else answers.Left("factcheck")
        )
    key = answers.option_key(item.label)
    section_key = answers.option_key(section)
    value: Any = None
    source = ""
    for names, entries, fields, prefix in (
        (
            ("experience", "employment", "emploi", "professional experience"),
            context.sections.get("experience", {}).get("employers", []),
            EXPERIENCE,
            "experience.employers",
        ),
        (
            ("education", "formation", "studies"),
            context.sections.get("experience", {}).get("education", []),
            EDUCATION,
            "experience.education",
        ),
    ):
        if any(section_key.startswith(name) for name in names) and key in fields:
            rank = re.search(r"\b([1-9][0-9]?)$", section_key)
            index = int(rank[1]) - 1 if rank else (0 if len(entries) == 1 else -1)
            if 0 <= index < len(entries):
                value = entries[index].get(fields[key])
                source = f"{prefix}.{index}.{fields[key]}"
    if key in (
        "headline",
        "professional headline",
        "titre professionnel",
        "summary",
        "resume professionnel",
    ):
        value = context.sections.get("identity", {}).get("headline")
        source = "identity.headline"
    if key in ("skills", "competences"):
        # Categories are the person's explicit terms, not the search's matching rules.
        categories = context.sections.get("skills", {}).get("categories", [])
        terms = [term for category in categories for term in category.get("terms", [])]
        value, source = ", ".join(dict.fromkeys(terms)), "skills.categories"
    if key in ("languages", "langues"):
        langs = context.sections.get("identity", {}).get("languages", [])
        value = ", ".join(
            language["name"] + (f" ({language['level']})" if language.get("level") else "")
            for language in langs
        )
        source = "identity.languages"
    if value in (None, "") or item.kind not in ("text", "select", "combobox"):
        return answers.Left("no_source", item.options, item.required)
    value = str(value)
    if not factual(value, context):
        return answers.Left("factcheck", item.options, item.required)
    if item.options and value not in item.options:
        return answers.Left("option_missing", item.options, item.required)
    return answers.Answer(value, source)


def value_hash(section: Any, fields: list[Any]) -> bytes:
    import hashlib

    data = [
        section.section,
        section.label,
        section.save_name,
        [
            [
                f.field_key,
                f.kind,
                f.value,
                f.current_value,
                f.source.value,
                f.source_path,
                f.replace,
                f.refusal,
                f.options,
            ]
            for f in sorted(fields, key=lambda row: row.field_key)
        ],
    ]
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).digest()
