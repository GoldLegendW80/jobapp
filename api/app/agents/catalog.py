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
    "browser": "A job search sweep, one run per website, two websites at once",
    "apply": "A step of an application being filled in",
    "ingestion": "The reading of a document the person uploaded",
    "documents": "A document generation, or a message in a document's chat",
    "jobs": "The Assess button of a posting's page",
}

# Who starts an agent whose package's sentence above would be wrong for it.
STARTER_OF = {
    "apply_followup": "The Write a draft button of an application the person follows up",
    "posting_reader": "A sweep once its sources ended, and the backlog tick every minute",
    "profile_interpret": "Done on a part of the About you document",
    "profile_titles": "The More ideas button, and the tick after a change to the profile",
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
        "These agents search job websites for postings, and read "
        "each posting found to fill its details.",
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
    "apply_agent": "applying",
    "apply_answer": "applying",
    "apply_check": "applying",
    "apply_form": "applying",
    "apply_followup": "applying",
    "browser_discovery": "finding",
    "document_transcribe": "profile",
    "documents_base": "documents",
    "documents_chat_base": "documents",
    "documents_chat_cv": "documents",
    "documents_chat_letter": "documents",
    "documents_chat_offer_notes": "documents",
    "documents_cv": "documents",
    "documents_design": "documents",
    "documents_interview_practice": "documents",
    "documents_interview_prep": "documents",
    "documents_letter": "documents",
    "documents_offer_read": "documents",
    "documents_panel_manager": "documents",
    "documents_panel_merge": "documents",
    "documents_panel_reader_ceo": "documents",
    "documents_panel_reader_manager": "documents",
    "documents_panel_reader_recruiter": "documents",
    "documents_panel_reader_rival": "documents",
    "documents_panel_reader_teammate": "documents",
    "documents_panel_screen": "documents",
    "documents_proposal": "documents",
    "echo": "test",
    "job_chat": "jobs",
    "jobs_assess": "jobs",
    "posting_reader": "finding",
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

# --- the workflows drawn above each category's table (agents A9) ------------------------------

NODE_KINDS = ("agent", "person", "system", "data")


@dataclass(frozen=True)
class Node:
    """A box of a workflow's diagram: an agent by its registered name, or a step of kind person
    (a click, a message, an upload), system (a sweep, a cron, a tick, a worker job) or data (a
    stored thing another step reads), at a column and a row laid out by hand, left to right in
    the order things happen. `note` says what the drawing alone cannot (an agent nothing starts)."""

    id: str
    kind: str
    label: str
    column: int
    row: int
    note: str = ""


@dataclass(frozen=True)
class Edge:
    """An arrow of a workflow: who starts or feeds whom, in 2 to 5 plain words. An arrow to an
    earlier column is a return (a rebuild, a question answered and queued again)."""

    source: str
    target: str
    label: str


@dataclass(frozen=True)
class Flow:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]


def _agent(name: str, column: int, row: int, note: str = "") -> Node:
    return Node(name, "agent", name, column, row, note)


def _step(kind: str, key: str, label: str, column: int, row: int) -> Node:
    return Node(key, kind, label, column, row)


def _edges(*lines: tuple[str, str, str]) -> tuple[Edge, ...]:
    return tuple(Edge(source, target, label) for source, target, label in lines)


_PANEL = (
    "documents_panel_screen",
    "documents_panel_manager",
    "documents_panel_reader_manager",
    "documents_panel_reader_rival",
    "documents_panel_reader_ceo",
    "documents_panel_reader_recruiter",
    "documents_panel_reader_teammate",
)
_WRITERS = ("documents_cv", "documents_letter", "documents_base")
_CHATS = ("documents_chat_cv", "documents_chat_letter", "documents_chat_base")

# Each edge is a fact of the code that starts or feeds the agent; the comment above a group names
# where. tests/agents/test_admin_agents.py fails when a drawn agent is not registered, or a
# registered one is drawn in no flow. The test category has none.
FLOWS: dict[str, Flow] = {
    "finding": Flow(
        nodes=(
            _step("person", "search", "Save and start search", 0, 0),
            _step("person", "onboarding", "Start, at the end of onboarding", 0, 1),
            _step("system", "schedule", "Scheduled sweeps", 0, 2),
            _step("data", "scope", "Search settings", 0, 3),
            _step("system", "sweep", "Sweep", 1, 1),
            _step("system", "backlog", "Reader backlog tick, every minute", 1, 3),
            _agent("browser_discovery", 2, 0),
            _agent("posting_reader", 2, 2),
            _step("person", "help", "Help in the live view", 3, 0),
            _step("data", "postings", "Job postings", 3, 1),
        ),
        edges=_edges(
            # jobs/sweep_router.py POST /api/jobs/sweeps, opportunities/onboarding_router.py
            # complete, worker.py scheduled_sweeps; sweep.begin freezes the scope.
            ("search", "sweep", "starts a sweep"),
            ("onboarding", "sweep", "starts the first sweep"),
            ("schedule", "sweep", "starts one when due"),
            ("scope", "sweep", "frozen at its start"),
            # sweep.py run_sweep: the websites, then the reader; browser/sources/interactive.py.
            ("sweep", "browser_discovery", "one run per website"),
            ("browser_discovery", "help", "asks for help"),
            ("help", "browser_discovery", "gives the browser back"),
            ("browser_discovery", "postings", "stores verified postings"),
            ("sweep", "posting_reader", "then reads its postings"),
            # worker.py cron:posting_reader, reader.tick.
            ("backlog", "posting_reader", "reads the unread ones"),
            ("posting_reader", "postings", "fills facts and text"),
        ),
    ),
    "applying": Flow(
        nodes=(
            _step("person", "apply", "Apply", 0, 0),
            _step("person", "like", "Like, with Full auto on", 0, 1),
            _step("person", "site", "Complete my profile on a site", 0, 3),
            _step("person", "draft", "Write a draft", 0, 5),
            _step("data", "queued", "Queued application", 1, 0),
            _step("data", "documents", "The posting's CV and letter", 1, 1),
            _step("system", "fill", "Fill job, started by the apply tick", 2, 0),
            _step("system", "decision", "Decision of a Full auto item", 4, 3),
            _agent("apply_agent", 3, 0),
            _agent("apply_answer", 3, 1),
            _agent("apply_form", 3, 2),
            _agent("apply_check", 5, 3),
            _agent("apply_followup", 3, 5),
            _step("person", "question", "Answer its question", 4, 0),
            _step("data", "answers", "Application answers", 4, 1),
            _step("data", "followup", "Follow up draft", 4, 5),
            _step("person", "approve", "Approve, in Review first", 5, 1),
        ),
        edges=_edges(
            # apply/likes.py queues both; worker.py apply_tick starts the fill, which waits for
            # the documents (apply/run.py).
            ("apply", "queued", "queues it"),
            ("like", "queued", "queues it"),
            ("queued", "fill", "the tick starts it"),
            ("documents", "fill", "waits for their PDFs"),
            # worker.py apply_fill: the apply agent, or the adapter path (apply/run.py).
            ("fill", "apply_agent", "One click Apply"),
            ("fill", "apply_answer", "answers what is left"),
            ("fill", "apply_form", "an unknown or changed form"),
            ("apply_agent", "question", "asks the person"),
            ("question", "queued", "queues it again"),
            ("apply_answer", "answers", "writes its answers"),
            ("apply_form", "answers", "writes its answers"),
            ("answers", "approve", "shown for approval"),
            # apply/site_profile.py site_profile_run.
            ("site", "apply_form", "fills the site's profile"),
            # worker.py apply_tick queues the decision; apply/submit.py _decide, model_check.py.
            ("answers", "decision", "a ready Full auto item"),
            ("decision", "apply_check", "checks the free text"),
            # apply/followups_router.py write.
            ("draft", "apply_followup", "writes a follow up"),
            ("apply_followup", "followup", "stores the draft"),
        ),
    ),
    "documents": Flow(
        nodes=(
            _step("person", "fit", "Make it fit", 0, 0),
            _step("person", "click", "Write, Apply or Like", 0, 1),
            _step("person", "base", "New base CV", 0, 3),
            _step("person", "capture", "Retry the design capture", 0, 4),
            _step("person", "chat", "Message in a document's chat", 0, 8),
            _step("person", "offer", "Read an offer letter", 0, 10),
            _step("person", "notes_chat", "Message about the offer notes", 0, 11),
            _step("person", "prepare", "Prepare for the interview", 0, 12),
            _step("person", "practice", "Practice message", 0, 13),
            _agent("documents_cv", 1, 1),
            _agent("documents_letter", 1, 2),
            _agent("documents_base", 1, 3),
            _agent("documents_design", 1, 4),
            *(_agent(name, 1, 7 + index) for index, name in enumerate(_CHATS)),
            _agent("documents_offer_read", 1, 10),
            _agent("documents_interview_prep", 1, 12),
            _agent("documents_proposal", 1, 14, "Not started by the app yet"),
            _step("system", "panel", "Hiring panel job", 2, 0),
            _step("data", "notes", "Offer notes", 2, 10),
            _step("data", "prep", "Interview prep", 2, 12),
            *(_agent(name, 3, index) for index, name in enumerate(_PANEL)),
            _agent("documents_chat_offer_notes", 3, 11),
            _agent("documents_interview_practice", 3, 13),
            _agent("documents_panel_merge", 4, 3),
        ),
        edges=_edges(
            # documents/auto_router.py write_now and make_it_fit, documents/tick.py
            # start_clicks; generate.start_letter_after, after the CV or its panel.
            ("fit", "documents_cv", "asks a shorter CV"),
            ("click", "documents_cv", "writes the CV"),
            ("documents_cv", "documents_letter", "then the letter"),
            # documents/base_router.py create_base_cv; writer_base.py after a conversion;
            # documents/design_router.py capture.
            ("base", "documents_base", "builds or converts it"),
            ("documents_base", "documents_design", "captures an upload's design"),
            ("capture", "documents_design", "captures it again"),
            # generate.py on_final and writer_base.py open the panel; panel.py runs it.
            *((writer, "panel", "sends its draft") for writer in _WRITERS),
            *(("panel", role, "reviews, three at once") for role in _PANEL),
            *((role, "documents_panel_merge", "sends its review") for role in _PANEL),
            ("documents_panel_merge", "panel", "plans a rebuild"),
            *(("panel", writer, "rebuilds the draft") for writer in _WRITERS),
            # documents/chat.py post; documents/offer_router.py read_letter.
            *(("chat", name, "asks for a change") for name in _CHATS),
            ("offer", "documents_offer_read", "reads the letter"),
            ("documents_offer_read", "notes", "writes the notes"),
            ("notes", "documents_chat_offer_notes", "discussed in a chat"),
            ("notes_chat", "documents_chat_offer_notes", "asks about them"),
            # documents/interview_prep_router.py prepare and practice.
            ("prepare", "documents_interview_prep", "writes the prep"),
            ("documents_interview_prep", "prep", "stores the prep"),
            ("prep", "documents_interview_practice", "practised in a chat"),
            ("practice", "documents_interview_practice", "answers the person"),
        ),
    ),
    "profile": Flow(
        nodes=(
            _step("person", "read", "Start reading documents", 0, 0),
            _step("person", "about", "Done on an About you part", 0, 2),
            _step("person", "ideas", "More ideas", 0, 3),
            _step("system", "titles_tick", "Titles tick, after a profile change", 0, 4),
            _step("system", "run", "Ingestion run", 1, 0),
            _agent("document_transcribe", 2, 0),
            _agent("profile_extract", 2, 1),
            _agent("profile_interpret", 2, 2),
            _agent("profile_titles", 2, 3),
            _step("data", "items", "Profile proposals", 3, 1),
            _step("data", "reading", "About you reading", 3, 2),
            _step("data", "titles", "Title suggestions", 3, 3),
            _step("person", "accept", "Accept items", 4, 1),
            _step("person", "use", "Apply the reading", 4, 2),
            _step("person", "add", "Add to job titles", 4, 3),
        ),
        edges=_edges(
            # ingestion/runs_router.py start_run; plan.py puts every transcription first;
            # engine.py merges the items at the run's end.
            ("read", "run", "starts a run"),
            ("run", "document_transcribe", "scanned pages first"),
            ("run", "profile_extract", "then each document"),
            ("document_transcribe", "profile_extract", "its transcribed text"),
            ("profile_extract", "items", "proposes items"),
            ("items", "accept", "reviewed by the person"),
            # profile/about_router.py done and apply.
            ("about", "profile_interpret", "reads that part"),
            ("profile_interpret", "reading", "writes a reading"),
            ("reading", "use", "shown to the person"),
            # profile/titles_router.py more_ideas; worker.py cron:profile_titles.
            ("ideas", "profile_titles", "suggests more titles"),
            ("titles_tick", "profile_titles", "starts the runs due"),
            ("profile_titles", "titles", "stores its suggestions"),
            ("titles", "add", "picked by the person"),
        ),
    ),
    "jobs": Flow(
        nodes=(
            _step("person", "assess", "Assess this posting", 0, 0),
            _step("person", "with_agent", "Assess with agent", 0, 1),
            _step("person", "message", "Message in a job's chat", 0, 2),
            _agent("jobs_assess", 1, 0),
            _agent("triage", 1, 1),
            _agent("job_chat", 1, 2),
            _step("data", "assessment", "Assessment", 2, 0),
            _step("data", "proposals", "Proposals", 2, 1),
            _step("data", "thread", "Chat thread", 2, 2),
            _step("person", "decide", "Accept or Reject", 3, 1),
            _step("data", "job", "The job posting", 4, 1),
        ),
        edges=_edges(
            # jobs/assess_router.py start; agents/router.py runs; agents/chat_router.py.
            ("assess", "jobs_assess", "assesses the posting"),
            ("jobs_assess", "assessment", "stores the assessment"),
            ("with_agent", "triage", "starts a triage run"),
            ("triage", "proposals", "proposes a note or status"),
            ("message", "job_chat", "answers in the chat"),
            ("job_chat", "thread", "writes its answer"),
            # agents/proposals.py accept.
            ("proposals", "decide", "shown to the person"),
            ("decide", "job", "an accepted one changes it"),
        ),
    ),
}

_JOB_LINE = 'then a line "Job id: <the run\'s job id>" when the run is about a job'
_TONE = "<the language asked>, <the tone asked>, <the template's soft lengths>"

# What `prompt()` builds, by the class of the definition (module.qualname).
FIRST_MESSAGES: dict[str, str] = {
    "app.agents.definitions.AgentDefinition": (
        f"The input's message, else the default message below; {_JOB_LINE}."
    ),
    "app.browser.finder.FinderDefinition": (
        "A JSON object search_scope: <the website's start address>, <the titles sought>, <the "
        "places sought>, <the site's filters>, <the person's search criteria>, <the person's own "
        "instructions, marked untrusted>, <a summary of the person's profile>, <whether the "
        "person is signed in there>."
    ),
    "app.apply.form_agent.FormDefinition": 'The fixed message: "{FIRST_MESSAGE}"',
    "app.apply.applier.ApplierDefinition": (
        "A JSON object held in the worker: <the posting: title, company, address, location, "
        "text>, <the person's profile, contact details included>, <the Saved answers>, <the "
        "person's answers to its earlier questions>, <the letter's text>, <the documents on "
        "the browser>."
    ),
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
    "app.documents.writer_interview_prep.PrepWriter": (
        f'"Prepare the person for the interviews of this posting ..." with <the language asked>; '
        f"{_JOB_LINE}."
    ),
    "app.documents.writer_interview_prep.PracticePartner": (
        '"The person wrote in the practice chat of their interview preparation ..." with <the '
        f"language asked>; {_JOB_LINE}."
    ),
    "app.documents.writer_base.BaseWriter": (
        '"Write the person\'s base CV from their profile." or, for an upload, "Convert the '
        "person's uploaded CV into their base CV\", with <the language asked> and <the "
        "template's soft lengths>."
    ),
    "app.documents.panel.PanelDefinition": (
        "Held in the worker while the panel runs, never stored: one line saying what to do, then "
        "data blocks: <the document: its text in reading order, its template and columns, "
        "whether contact details are printed>, <the job brief: the posting, the company, the "
        "offer, the fit>, <for a letter, the CV beside it>, <the reviewer's role at that "
        "company>; for the merge, <the document> and <the reviews>."
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
FIRST_MESSAGES["app.documents.offer_notes.OfferReader"] = (
    'To read: "Read the offer letter the person received for this posting, and write your notes '
    'on it ..."; in a chat: "The person wrote in the chat of their notes on an offer letter '
    "...\"; then <the letter's language>."
)
FIRST_MESSAGES["app.profile.about_agent.InterpretDefinition"] = (
    "One line saying what to do, then one data block: <the kind of the part>, <its heading and "
    "text as the person wrote it when they clicked Done>, <what the profile holds of that kind "
    "now>, <the titles of the fact blocks the part held>."
)
FIRST_MESSAGES["app.apply.followup_agent.FollowUpDefinition"] = (
    f'The fixed message: "{{FIRST_MESSAGE}}"; {_JOB_LINE}. Its tool read_follow_up gives the data.'
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
    from app.browser import finder as _finder  # noqa: F401  (browser_discovery)
    from app.documents import chat as _chat  # noqa: F401  (documents_chat_<kind>)
    from app.documents import offer_notes as _offer  # noqa: F401  (documents_offer_read, chat)
    from app.documents import panel as _panel  # noqa: F401  (documents_panel_<role>, merge)
    from app.documents import writer_base as _base  # noqa: F401
    from app.documents import writer_cv as _cv  # noqa: F401
    from app.documents import writer_design as _design  # noqa: F401
    from app.documents import writer_interview_prep as _interview  # noqa: F401
    from app.documents import writer_letter as _letter  # noqa: F401
    from app.documents import writer_proposal as _proposal  # noqa: F401
    from app.ingestion import agents as _ingestion  # noqa: F401  (profile_extract, transcribe)
    from app.jobs import assess as _assess  # noqa: F401  (jobs_assess)
    from app.jobs import reader as _reader  # noqa: F401  (posting_reader)
    from app.profile import agents as _profile  # noqa: F401  (profile_titles, profile_interpret)

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
# The agent of a sweep (app/browser/finder.py): a worker meets it only once a sweep imports the
# finder, so the agents queue's job timeout, built at the worker's start, never counts it.
SWEEP_AGENTS = ("browser_discovery",)


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


def _in_an_agent_fill() -> Ceiling:
    from app.apply import applier

    # One click Apply's agent (apply A19) runs inside the fill job, which ends it at the code's
    # wall time (applier.WALL_TIME_S, its lease's hour).
    return Ceiling(
        applier.APPLY_AGENT.wall_time_s,
        "It runs inside One click Apply's fill, which ends it at the code's wall time.",
    )


# The agents that run inside another module's job: the bound of that job, from its constants.
_INSIDE: dict[str, Callable[[], Ceiling]] = {
    "apply_agent": _in_an_agent_fill,
    "apply_answer": _in_a_fill,
    "apply_check": _in_a_decision,
    "apply_form": _in_a_fill_form,
    "browser_discovery": _in_a_sweep,
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
    if definition.name in STARTER_OF:
        return STARTER_OF[definition.name]
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
