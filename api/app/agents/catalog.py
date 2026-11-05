"""Every agent the app has, as the Agents tab of Admin shows it (agents A6).

The api learns its agents by importing the modules that register them (`load`): each one
registers its definition without the Claude Agent SDK, which the api image does not hold. Its
tools are another matter: a tool's description and input schema exist only once the worker
builds it with the SDK. So the worker writes them to `agent_tools` (`publish`, from the reaper of
the agents queue, once per process) and the tab reads that table.

What a definition builds per run (its first message, `AgentDefinition.prompt`) is code, not
data: `FIRST_MESSAGES` says in words what each kind of definition builds and from what, with
the dynamic parts named, never a value of a person's data.
"""

import inspect
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.agents import definitions
from app.agents.models import AgentTool

logger = logging.getLogger("app.agents.catalog")

# Who starts an agent's runs, by the package that registered it (an internal agent is started
# by its module only).
STARTERS = {
    "agents": "A button of the app (POST /api/agents/runs)",
    "browser": "A job search sweep, one website or careers page at a time",
    "apply": "A step of an application being filled in",
    "ingestion": "The reading of a document the person uploaded",
    "documents": "A document generation, or a message in a document's chat",
}


@dataclass(frozen=True)
class Category:
    """A group of the Agents tab's list (agents A8): its key, its heading and one sentence."""

    key: str
    title: str
    sentence: str


# The groups of the Agents tab's list, in the order it shows them (agents A8, the owner's "group
# them by categories").
CATEGORIES: tuple[Category, ...] = (
    Category(
        "finding",
        "Finding jobs",
        "These agents search job websites and company careers pages for postings.",
    ),
    Category(
        "applying",
        "Applying",
        "These agents fill in application forms, answer their open questions and check the "
        "answers before anything is submitted.",
    ),
    Category(
        "documents",
        "Documents",
        "These agents write and change the CVs, cover letters and proposals.",
    ),
    Category(
        "profile",
        "Profile and import",
        "These agents read the documents a person uploads, transcribe their scanned pages and "
        "propose what goes into their profile.",
    ),
    Category(
        "jobs",
        "Jobs and chat",
        "These agents assess a posting against the profile and answer questions about a job "
        "in its chat.",
    ),
    Category(
        "test",
        "Test",
        "These agents exist for the dev run panel and the tests, and are never listed in "
        "production.",
    ),
)

# The category of each agent, by its name: set here, never read from what the agent says of
# itself. tests/agents/test_admin_agents.py fails when a registered agent is missing.
CATEGORY_OF: dict[str, str] = {
    "apply_answer": "applying",
    "apply_check": "applying",
    "apply_form": "applying",
    "browser_discovery": "finding",
    "browser_finder": "finding",
    "document_transcribe": "profile",
    "documents_base": "documents",
    "documents_chat_base": "documents",
    "documents_chat_cv": "documents",
    "documents_chat_letter": "documents",
    "documents_cv": "documents",
    "documents_design": "documents",
    "documents_letter": "documents",
    "documents_proposal": "documents",
    "echo": "test",
    "job_chat": "jobs",
    "profile_extract": "profile",
    "profile_interpret": "profile",
    "profile_titles": "profile",
    "triage": "jobs",
}

# Where an agent missing from CATEGORY_OF goes, by the package that registered it, so the list
# still shows it.
_PACKAGE_CATEGORY = {
    "agents": "jobs",
    "apply": "applying",
    "browser": "finding",
    "documents": "documents",
    "ingestion": "profile",
}

_JOB_LINE = 'then a line "Job id: <the run\'s job id>" when the run is about a job'
_TONE = "<the language asked>, <the tone asked>, <the template's soft lengths>"

# What `prompt()` builds, by the class of the definition (module.qualname).
FIRST_MESSAGES: dict[str, str] = {
    "app.agents.definitions.AgentDefinition": (
        f"The input's message, else the default message below; {_JOB_LINE}."
    ),
    "app.browser.finder.FinderDefinition": (
        "For a website search, a JSON object search_scope: <the website's start address>, "
        "<the titles sought>, <the places sought>, <the site's filters>, <the person's search "
        "criteria>, <the person's own instructions, marked untrusted>, <whether the person is "
        'signed in there>. For a careers page, the fixed message: "{FIRST_MESSAGE}"'
    ),
    "app.apply.form_agent.FormDefinition": 'The fixed message: "{FIRST_MESSAGE}"',
    "app.apply.agents.AnswerDefinition": (
        "One line saying what to do, then four data blocks taken from the fill held in the "
        "worker: <the form's open questions: label, kind, options, required>, <the person's "
        "profile, clipped>, <the posting: title, company, text>, <the cover letter>."
    ),
    "app.apply.model_check.CheckDefinition": (
        "One line saying what to do, then three data blocks: <the answers to check: question "
        "and text>, <the person's profile, clipped>, <the posting: title, company, text>."
    ),
    "app.documents.writer_cv.CvWriter": (
        '"Write the CV of this posting for the person, from their profile and base CV." with '
        f"{_TONE}; in shorten mode, <the fields that overflow the page> to shorten; {_JOB_LINE}."
    ),
    "app.documents.writer_letter.LetterWriter": (
        "\"Write the cover letter for this posting, from the person's profile and base CV, "
        f'and from the CV already written for it when there is one." with {_TONE}; {_JOB_LINE}.'
    ),
    "app.documents.writer_proposal.ProposalWriter": (
        '"Write a short outreach proposal for this posting ..." with <the word limit>, '
        f"<the language asked>, <the tone asked>; {_JOB_LINE}."
    ),
    "app.documents.writer_base.BaseWriter": (
        '"Write the person\'s base CV from their profile." or, for an upload, "Convert the '
        "person's uploaded CV into their base CV\", with <the language asked> and <the "
        "template's soft lengths>."
    ),
    "app.documents.writer_design.DesignWriter": (
        "A fixed message by <the kind of upload, PDF or Word document>: capture the design of "
        "the person's uploaded CV, then write the template."
    ),
    "app.documents.chat.ChatWriter": (
        "The person wrote in the chat of this <document kind>: read the conversation and the "
        f"draft, make the change asked and nothing else; <the language of the document>, "
        f"<the template's soft lengths>; {_JOB_LINE}."
    ),
}
FIRST_MESSAGES["app.profile.about_agent.InterpretDefinition"] = (
    "One line saying what to do, then one data block: <the kind of the part>, <its heading and "
    "text as the person wrote it when they clicked Done>, <what the profile holds of that kind "
    "now>, <the titles of the fact blocks the part held>."
)
CHAT_MESSAGE = (
    "Rebuilt from the thread's messages at every run (app/agents/chat.py): <the conversation so "
    "far, each message quoted in a message tag>, then <the new message>, and <the thread's job "
    "id>."
)


def load() -> dict[str, definitions.AgentDefinition]:
    """Every registered agent, after importing the modules that register them (here, not at the
    top: only the tab needs them all in the api). Each one registers its definition without the
    Claude Agent SDK. tests/agents/test_admin_agents.py fails when a module of app/ registers an
    agent and is not reached from here."""
    from app.apply import agents as _apply  # noqa: F401  (apply_answer, apply_form, apply_check)
    from app.browser import finder as _finder  # noqa: F401  (browser_finder, browser_discovery)
    from app.documents import chat as _chat  # noqa: F401  (documents_chat_<kind>)
    from app.documents import writer_base as _base  # noqa: F401
    from app.documents import writer_cv as _cv  # noqa: F401
    from app.documents import writer_design as _design  # noqa: F401
    from app.documents import writer_letter as _letter  # noqa: F401
    from app.documents import writer_proposal as _proposal  # noqa: F401
    from app.ingestion import agents as _ingestion  # noqa: F401  (profile_extract, transcribe)

    return dict(definitions.AGENTS)


def longest_wall_time_s() -> float:
    """The longest wall time any agent of the app has in code (`load`): the ceiling of a saved
    wall time of an agent that runs as its own run_agent job (`ceiling_of`, agents A7). The
    worker gives arq's timeout of run_agent room for it (worker.RUN_AGENT_MAX_S, compared with
    it by a test) and ends each run's job at that run's own bound."""
    return max(agent.wall_time_s for agent in load().values())


@dataclass(frozen=True)
class Ceiling:
    """The longest a saved wall time may give a run of one agent (agents A7): the bound of the
    job that actually runs it, and one sentence saying where it runs (the page, a refusal)."""

    seconds: float
    where: str


ON_THE_QUEUE = "It runs as its own job on the agents queue, which lets a run take this long."
# The agents of a sweep (app/browser/finder.py): a worker meets them only once a sweep imports
# the finder, so the agents queue's job timeout, built at the worker's start, never counts them.
SWEEP_AGENTS = ("browser_finder", "browser_discovery")


def _in_a_sweep() -> Ceiling:
    from app.browser import leases
    from app.jobs import sweep

    # The sweep's own bound from its start (B20), and its browser lease's most.
    seconds = min(sweep.BROWSER_SEARCH_MAX_S, leases.LEASE_MAX_S["sweep"])
    return Ceiling(float(seconds), "It runs inside a sweep, which ends there at the latest.")


def _in_a_decision() -> Ceiling:
    # apply_decide's job timeout is the agents queue's (worker.agents_queue): the longest wall
    # time of the agents the worker registers when it starts, every agent but a sweep's, plus
    # the margin the runner's end is given, as for a run of its own.
    seconds = max(agent.wall_time_s for agent in load().values() if agent.name not in SWEEP_AGENTS)
    return Ceiling(
        seconds,
        "It runs inside the decision of a Full auto item (apply_decide), whose job ends there.",
    )


def _in_a_fill_form() -> Ceiling:
    from app.apply import form_agent

    # The fill's form deadline is the code's wall time (apply/run.py form_deadline, the
    # session's deadline of form_agent.py): a version cannot move it.
    return Ceiling(
        form_agent.APPLY_FORM.wall_time_s,
        "It runs inside an application fill, whose form deadline is the code's wall time.",
    )


def _in_a_fill() -> Ceiling:
    from app.browser import leases

    # Asked while a fill (or a profile edit) holds its browser lease, which ends at its most.
    return Ceiling(
        float(leases.LEASE_MAX_S["apply_fill"]),
        "It runs inside an application fill, whose browser lease ends there.",
    )


def _in_an_import() -> Ceiling:
    from app.ingestion import plan

    # Its own run_agent job, inside an ingestion run, which cancels it at the run's deadline
    # (ingestion/engine.py RUN_TIME).
    return Ceiling(
        plan.RUN_MINUTES * 60.0,
        "It runs as its own job on the agents queue, inside a profile import that stops it "
        "at its deadline.",
    )


# The agents that run inside another module's job: the bound of that job, from its constants.
_INSIDE: dict[str, Callable[[], Ceiling]] = {
    "apply_answer": _in_a_fill,
    "apply_check": _in_a_decision,
    "apply_form": _in_a_fill_form,
    "browser_discovery": _in_a_sweep,
    "browser_finder": _in_a_sweep,
    "document_transcribe": _in_an_import,
    "profile_extract": _in_an_import,
}


def ceiling_of(definition: definitions.AgentDefinition) -> Ceiling:
    """The ceiling of one agent's saved wall time (Ceiling): for an agent that runs inside
    another module's job, that job's bound; for every other one, its own run_agent job's, the
    longest the agents queue lets a run take (`longest_wall_time_s`).
    tests/agents/test_admin_agents.py compares each with the job a fresh worker builds."""
    inside = _INSIDE.get(definition.name)
    if inside is not None:
        return inside()
    return Ceiling(longest_wall_time_s(), ON_THE_QUEUE)


def package_of(definition: definitions.AgentDefinition) -> str:
    parts = definitions.module_of(definition.name).split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "app" else ""


def category_of(definition: definitions.AgentDefinition) -> str:
    """The key of the agent's category in CATEGORIES (`CATEGORY_OF`). One missing from it, which
    a test refuses, goes with the test agents when it is one, else with its package's agents; one
    of a package the map does not know gets "other", which the page shows under Other."""
    found = CATEGORY_OF.get(definition.name)
    if found is not None:
        return found
    if definition.test_only:
        return "test"
    return _PACKAGE_CATEGORY.get(package_of(definition), "other")


def starter(definition: definitions.AgentDefinition) -> str:
    if definition.test_only:
        return "The dev run panel and the tests, never in production"
    if definition.chat:
        return "A message in a chat thread"
    if not definition.internal:
        return STARTERS["agents"]
    return STARTERS.get(package_of(definition), "Its own module, in code")


def first_message(definition: definitions.AgentDefinition) -> str:
    """What the run's first message is built from, in words (`FIRST_MESSAGES`)."""
    if definition.chat:
        return CHAT_MESSAGE
    cls = type(definition)
    text = FIRST_MESSAGES.get(f"{cls.__module__}.{cls.__qualname__}")
    if text is None:
        return f"Built per run by {cls.__qualname__}.prompt in {cls.__module__}."
    fixed = getattr(inspect.getmodule(cls), "FIRST_MESSAGE", "")
    return text.replace("{FIRST_MESSAGE}", str(fixed))


def tools_described(db: Session, names: tuple[str, ...]) -> dict[str, AgentTool]:
    if not names:
        return {}
    rows = db.scalars(sa.select(AgentTool).where(AgentTool.name.in_(names)))
    return {row.name: row for row in rows}


# --- the worker's side -------------------------------------------------------------------------

# The tool names this process has published, and the lock that keeps two ticks from publishing at
# once.
_published: frozenset[str] = frozenset()
_publishing = threading.Lock()


def _schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    # A Python type given as the schema: the SDK turns it into a JSON schema itself.
    return {"type": "object", "description": getattr(value, "__name__", str(value))}


def publish(sessions: sessionmaker[Session], now: datetime) -> frozenset[str]:
    """Write every app tool this worker process knows, as the model sees it, to `agent_tools`, in
    one transaction. Worker only: it builds each tool with the SDK, for no run. A tool that
    cannot be built that way is skipped alone. Nothing is imported to find more tools: a module
    that registers its own (the finder's, which `load` imports since agents A7) is published once
    it is there, and a row is never deleted here, so a tool another process described stays. Answers
    the names written."""
    from app.agents import tools as app_tools

    rows: list[dict[str, Any]] = []
    scope = app_tools.Scope(sessions=sessions, user_id=uuid.UUID(int=0))
    for name, factory in sorted(app_tools.FACTORIES.items()):
        try:
            made = factory(scope)
        except Exception as exc:
            logger.warning(
                "agent tool not described", extra={"tool": name, "error": type(exc).__name__}
            )
            continue
        rows.append(
            {
                "name": name,
                "module": str(getattr(factory, "__module__", "")),
                "description": str(made.description),
                "input_schema": _schema(made.input_schema),
                "published_at": now,
            }
        )
    if rows:
        statement = insert(AgentTool).values(rows)
        with sessions() as db:
            db.execute(
                statement.on_conflict_do_update(
                    index_elements=[AgentTool.name],
                    set_={
                        "module": statement.excluded.module,
                        "description": statement.excluded.description,
                        "input_schema": statement.excluded.input_schema,
                        "published_at": statement.excluded.published_at,
                    },
                )
            )
            db.commit()
    return frozenset(row["name"] for row in rows)


def publish_once(sessions: sessionmaker[Session], now: datetime) -> None:
    """`publish`, when this process knows a tool it has not published yet: called by every tick
    of the agents queue's reaper, so a worker started before its migration describes its tools a
    minute later, and so do the tools a module registers later. Never raises: a failure is
    logged by its type and the next tick tries again."""
    global _published
    from app.agents import tools as app_tools

    if not _publishing.acquire(blocking=False):
        return
    try:
        if frozenset(app_tools.FACTORIES) <= _published:
            return
        _published = _published | publish(sessions, now)
    except Exception as exc:
        logger.warning("agent tools not published", extra={"error": type(exc).__name__})
    finally:
        _publishing.release()
