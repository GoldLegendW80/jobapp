"""The app tools of the document writers (task D7), registered with `app.agents.tools.register`
(worker only: this module imports the Claude Agent SDK). Every tool reads what the run is bound
to (`Scope.binding`, written by the writer's `bind`): none takes an id, so the model cannot point
a run at another document, posting or person.

- `read_profile_section(section)`: one of `facts`, `experience`, `skills`, `answers` of the
  person's profile. `identity` and every other section answer "not available": the writer never
  sees a contact line, and the fixed facts are printed by the template, not written. The agents
  module's `get_profile_section` serves every section, so the writers use this one (the plan's
  assumption 5: no section filter can be set per agent without editing app/agents/tools.py).
  `facts` also carries the person's writing rules of the module (the dash rule and the banned
  phrases), which the writer follows with the blocks.
- `get_base_cv()`: the base CV the generation is written from, with its `fixed` block.
- `get_draft()`: the document's current draft (what a `shorten` generation shortens).
- `submit_cv(...)`: the CV, as D2's content JSON; `submit.handle` checks and corrects it.
- `get_document("cv")` (the letter writer): the draft of the CV of the run's own posting, read by
  the posting the run is bound to, so no id is chosen by the model; "no CV yet" while that CV is
  empty.
- `submit_letter(...)`: the letter, as D2's content JSON, through the same `submit.handle`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import sqlalchemy as sa
from claude_agent_sdk import SdkMcpTool, tool

from app.agents import tools as app_tools
from app.documents import content as c
from app.documents import store, submit
from app.documents.models import Kind
from app.profile.models import Profile

READ_PROFILE_SECTION = "read_profile_section"
GET_BASE_CV = "get_base_cv"
GET_DRAFT = "get_draft"
SUBMIT_CV = "submit_cv"
GET_DOCUMENT = "get_document"
SUBMIT_LETTER = "submit_letter"
SECTIONS = ("facts", "experience", "skills", "answers")
NOT_AVAILABLE = "Not available: read facts, experience, skills or answers."
NO_BASE = "No base CV: write from the profile alone."
GONE = "This document no longer exists."
NOT_AVAILABLE_DOCUMENT = 'Not available: read the "cv" of this posting.'
NO_CV = "No CV for this posting yet: write the letter from the profile and the base CV alone."


def _binding(scope: app_tools.Scope, key: str) -> uuid.UUID | None:
    value = (scope.binding or {}).get(key)
    return uuid.UUID(str(value)) if value else None


def _section(scope: app_tools.Scope, section: str) -> str:
    with scope.sessions() as db:
        stored = db.scalar(sa.select(Profile.sections).where(Profile.user_id == scope.user_id))
        data = dict((stored or {}).get(section) or {})
        if section == "facts":
            rules = store.settings(db, scope.user_id)
            data["writing_settings"] = {
                "no_dash_separators": rules.no_dash_separators,
                "banned_phrases": list(rules.banned_phrases),
            }
    if not data:
        return app_tools.EMPTY_SECTION.format(section=section)
    return json.dumps(data, ensure_ascii=False)


def _read_profile_section(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        READ_PROFILE_SECTION,
        "Read one section of the person's profile: facts (verified facts and the person's "
        "writing rules, the first source of claims), experience (employers and schools), "
        "skills (competencies and years of experience) or answers (standard answers).",
        {
            "type": "object",
            "properties": {"section": {"type": "string", "enum": list(SECTIONS)}},
            "required": ["section"],
            "additionalProperties": False,
        },
    )
    async def read_profile_section(args: dict[str, Any]) -> dict[str, Any]:
        section = args.get("section")
        if section not in SECTIONS:
            return app_tools.text_result(NOT_AVAILABLE, error=True)
        return app_tools.text_result(await asyncio.to_thread(_section, scope, str(section)))

    return read_profile_section


def _document_json(scope: app_tools.Scope, key: str) -> str | None:
    document_id = _binding(scope, key)
    if document_id is None:
        return None
    with scope.sessions() as db:
        found = store.get(db, scope.user_id, document_id)
        if found is None:
            return None
        return json.dumps(found.draft, ensure_ascii=False)


def _get_base_cv(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        GET_BASE_CV,
        "Read the base CV this document is written from: the person's own CV, a source of "
        "claims like the profile, with its fixed facts (employers, schools, languages).",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def get_base_cv(args: dict[str, Any]) -> dict[str, Any]:
        found = await asyncio.to_thread(_document_json, scope, "base_document_id")
        return app_tools.text_result(found if found is not None else NO_BASE)

    return get_base_cv


def _get_draft(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        GET_DRAFT,
        "Read the current draft of the document you write.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def get_draft(args: dict[str, Any]) -> dict[str, Any]:
        found = await asyncio.to_thread(_document_json, scope, "document_id")
        if found is None:
            return app_tools.text_result(GONE, error=True)
        return app_tools.text_result(found)

    return get_draft


def _posting_cv_json(scope: app_tools.Scope) -> str | None:
    """The draft of the bound posting's CV, None while it has no text (not written yet)."""
    posting_id = _binding(scope, "posting_id")
    if posting_id is None:
        return None
    with scope.sessions() as db:
        found = next(
            (
                document
                for document in store.posting_documents(db, scope.user_id, posting_id)
                if document.kind == Kind.cv
            ),
            None,
        )
        if found is None:
            return None
        written = any(text.strip() for _, text in c.text_fields(c.parse("cv", found.draft)))
        return json.dumps(found.draft, ensure_ascii=False) if written else None


def _get_document(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        GET_DOCUMENT,
        "Read the CV already written for this posting, so the letter tells the same story. "
        "The CV is the person's own words, a source of claims like the profile.",
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["cv"]}},
            "required": ["kind"],
            "additionalProperties": False,
        },
    )
    async def get_document(args: dict[str, Any]) -> dict[str, Any]:
        if args.get("kind") != "cv":
            return app_tools.text_result(NOT_AVAILABLE_DOCUMENT, error=True)
        found = await asyncio.to_thread(_posting_cv_json, scope)
        return app_tools.text_result(found if found is not None else NO_CV)

    return get_document


def _context(scope: app_tools.Scope, kind: c.Kind) -> submit.WriterContext | None:
    if scope.binding is None:
        return None
    with scope.sessions() as db:
        return submit.context_for(db, scope.user_id, scope.binding, kind)


def _submit(scope: app_tools.Scope, kind: c.Kind, raw: object) -> tuple[str, bool]:
    if scope.run_id is None:
        return GONE, True
    return submit.handle(_context(scope, kind), scope.run_id, scope.redact(raw))


# The CV as the tool's arguments: D2's schema, unknown fields refused.
CV_SCHEMA = c.CvContent.model_json_schema(by_alias=True)


def _submit_cv(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        SUBMIT_CV,
        "Submit the whole CV once it is written. Fixed facts (name, contact, each employer's "
        "organisation, title and dates, schools, languages) are not fields: an experience entry "
        "names its employer by employer_key only. The answer says what to fix, if anything.",
        CV_SCHEMA,
    )
    async def submit_cv(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(_submit, scope, "cv", args)
        return app_tools.text_result(text, error=error)

    return submit_cv


# The letter as the tool's arguments: D2's schema, unknown fields refused.
LETTER_SCHEMA = c.LetterContent.model_json_schema(by_alias=True)


def _submit_letter(scope: app_tools.Scope) -> SdkMcpTool[Any]:
    @tool(
        SUBMIT_LETTER,
        "Submit the whole letter once it is written: one to five paragraphs, four or five asked "
        "for. The date, the name and the contact lines are printed by the template, not "
        "written. The answer says what to fix, if anything.",
        LETTER_SCHEMA,
    )
    async def submit_letter(args: dict[str, Any]) -> dict[str, Any]:
        text, error = await asyncio.to_thread(_submit, scope, "letter", args)
        return app_tools.text_result(text, error=error)

    return submit_letter


app_tools.register(READ_PROFILE_SECTION, _read_profile_section)
app_tools.register(GET_BASE_CV, _get_base_cv)
app_tools.register(GET_DRAFT, _get_draft)
app_tools.register(SUBMIT_CV, _submit_cv)
app_tools.register(GET_DOCUMENT, _get_document)
app_tools.register(SUBMIT_LETTER, _submit_letter)
