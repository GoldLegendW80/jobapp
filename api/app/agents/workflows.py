"""The workflows of Activity (agents A10): one per thing a person starts, finer than the Agents
tab's categories, each drawn as the part of its category's flow (`catalog.FLOWS`) it runs, so the
facts of who starts or feeds whom stay in one place. A workflow run groups the agent runs of one
thing: an agent run records its workflow, its workflow run and its trigger when its starter
gives them (`runs.WorkflowRef`); an older run is grouped by the id its input holds when that is
certain, else it shows alone.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.agents.catalog import FLOWS, Flow, Node


@dataclass(frozen=True)
class Workflow:
    key: str
    title: str
    sentence: str
    # The category whose flow it draws a part of, and the boxes of that part.
    category: str = ""
    nodes: tuple[str, ...] = ()


_PANEL = (
    "documents_panel_screen",
    "documents_panel_manager",
    "documents_panel_reader_manager",
    "documents_panel_reader_rival",
    "documents_panel_reader_ceo",
    "documents_panel_reader_recruiter",
    "documents_panel_reader_teammate",
    "documents_panel_merge",
)

# In the order the side list of Activity shows them.
WORKFLOWS: tuple[Workflow, ...] = (
    Workflow(
        "job_search",
        "Job search",
        "A sweep: each job website searched by an agent, then each new posting read.",
        "finding",
        (
            "search",
            "onboarding",
            "schedule",
            "scope",
            "sweep",
            "browser_discovery",
            "posting_reader",
            "help",
            "postings",
        ),
    ),
    Workflow(
        "posting_reading",
        "Posting reading",
        "The postings not read yet, read a few at a time by the reader's tick.",
        "finding",
        ("backlog", "posting_reader", "postings"),
    ),
    Workflow(
        "posting_documents",
        "Documents for a posting",
        "A CV and a letter written for one posting, each screened by the hiring panel.",
        "documents",
        ("fit", "click", "documents_cv", "documents_letter", "documents_base", "panel", *_PANEL),
    ),
    Workflow(
        "base_cv",
        "Base CV",
        "A base CV built from the profile or converted from an upload, and its design captured.",
        "documents",
        ("base", "capture", "documents_base", "documents_design", "panel", *_PANEL),
    ),
    Workflow(
        "document_chat",
        "Document chat",
        "An answer in the chat of a CV, a letter or a base CV.",
        "documents",
        ("chat", "documents_chat_cv", "documents_chat_letter", "documents_chat_base"),
    ),
    Workflow(
        "offer_letter",
        "Offer letter",
        "An offer letter read into notes, and the chat about them.",
        "documents",
        ("offer", "documents_offer_read", "notes", "notes_chat", "documents_chat_offer_notes"),
    ),
    Workflow(
        "interview_prep",
        "Interview preparation",
        "An interview prepared from the posting and its documents, and the practice.",
        "documents",
        ("prepare", "documents_interview_prep", "prep", "practice", "documents_interview_practice"),
    ),
    Workflow(
        "fill_profile",
        "Fill my profile",
        "The documents you upload read, their scanned pages transcribed, and what goes into "
        "your profile proposed.",
        "profile",
        ("read", "run", "document_transcribe", "profile_extract", "items", "accept"),
    ),
    Workflow(
        "about_you",
        "About you",
        "A part of the About you document read, and its reading proposed.",
        "profile",
        ("about", "profile_interpret", "reading", "use"),
    ),
    Workflow(
        "job_titles",
        "Job title ideas",
        "Job titles suggested from your profile.",
        "profile",
        ("ideas", "titles_tick", "profile_titles", "titles", "add"),
    ),
    Workflow(
        "application",
        "Application",
        "An application filled in, its open questions answered and checked before it is sent.",
        "applying",
        (
            "apply",
            "like",
            "queued",
            "documents",
            "fill",
            "apply_agent",
            "apply_answer",
            "apply_form",
            "question",
            "answers",
            "approve",
            "decision",
            "apply_check",
        ),
    ),
    Workflow(
        "site_profile",
        "Profile on a job website",
        "Your profile filled in on a job website.",
        "applying",
        ("site", "apply_form"),
    ),
    Workflow(
        "follow_up",
        "Follow up",
        "A follow up written for an application.",
        "applying",
        ("draft", "apply_followup", "followup"),
    ),
    Workflow(
        "assess",
        "Assess",
        "A posting assessed against your profile, or triaged with a note or a status proposed.",
        "jobs",
        (
            "assess",
            "with_agent",
            "jobs_assess",
            "triage",
            "assessment",
            "proposals",
            "decide",
            "job",
        ),
    ),
    Workflow(
        "job_chat",
        "Job chat",
        "An answer in a job's chat.",
        "jobs",
        ("message", "job_chat", "thread"),
    ),
    Workflow("other", "Other runs", "Runs no workflow draws: test agents, and older runs."),
)
BY_KEY: dict[str, Workflow] = {workflow.key: workflow for workflow in WORKFLOWS}

# The workflow of an agent's run when its starter recorded none (an older run).
AGENT_WORKFLOW: dict[str, str] = {
    "browser_discovery": "job_search",
    "posting_reader": "posting_reading",
    "documents_cv": "posting_documents",
    "documents_letter": "posting_documents",
    "documents_base": "base_cv",
    "documents_design": "base_cv",
    **{name: "posting_documents" for name in _PANEL},
    "documents_chat_cv": "document_chat",
    "documents_chat_letter": "document_chat",
    "documents_chat_base": "document_chat",
    "documents_offer_read": "offer_letter",
    "documents_chat_offer_notes": "offer_letter",
    "documents_interview_prep": "interview_prep",
    "documents_interview_practice": "interview_prep",
    "document_transcribe": "fill_profile",
    "profile_extract": "fill_profile",
    "profile_interpret": "about_you",
    "profile_titles": "job_titles",
    "apply_agent": "application",
    "apply_answer": "application",
    "apply_check": "application",
    "apply_form": "application",
    "apply_followup": "follow_up",
    "jobs_assess": "assess",
    "triage": "assess",
    "job_chat": "job_chat",
}


@dataclass(frozen=True)
class Trigger:
    words: str
    # Its box in the workflow's graph.
    node: str | None = None


# How a run was triggered, by the key its starter records (`runs.WorkflowRef.trigger`).
TRIGGERS: dict[str, Trigger] = {
    "search": Trigger("Your click on Save and start search", "search"),
    "onboarding": Trigger("The end of onboarding", "onboarding"),
    "schedule": Trigger("The schedule", "schedule"),
    "backlog": Trigger("The reader's tick", "backlog"),
    "write": Trigger("Your click on Write now", "click"),
    "apply": Trigger("Your click on Apply", "apply"),
    "like": Trigger("A Like, with Full auto on", "like"),
    "fit": Trigger("Your click on Make it fit", "fit"),
    "new_base": Trigger("Your click on New CV or Upload your CV", "base"),
    "capture": Trigger("Your click on Try the design again", "capture"),
    "chat": Trigger("Your message in the chat", "chat"),
    "offer": Trigger("Your click on Read an offer", "offer"),
    "notes_chat": Trigger("Your message about the offer notes", "notes_chat"),
    "prepare": Trigger("Your click on Prepare", "prepare"),
    "practice": Trigger("Your click on Start practice", "practice"),
    "read": Trigger("Your click on Start reading", "read"),
    "onboarding_read": Trigger("Your click on Build my profile", "read"),
    "about": Trigger("Done on a part of About you", "about"),
    "ideas": Trigger("Your click on More ideas", "ideas"),
    "titles_tick": Trigger("The titles tick, after a profile change", "titles_tick"),
    "site": Trigger("Your click on Complete my profile on this site", "site"),
    "draft": Trigger("Your click on Write a draft", "draft"),
    "assess": Trigger("Your click on Assess", "assess"),
    "with_agent": Trigger("Your click on Assess with agent", "with_agent"),
    "message": Trigger("Your message in the chat", "message"),
}

# The trigger of an older run, by its agent, where only one thing starts it.
AGENT_TRIGGER: dict[str, str] = {
    "documents_chat_cv": "chat",
    "documents_chat_letter": "chat",
    "documents_chat_base": "chat",
    "documents_offer_read": "offer",
    "documents_chat_offer_notes": "notes_chat",
    "documents_interview_prep": "prepare",
    "documents_interview_practice": "practice",
    "document_transcribe": "read",
    "profile_extract": "read",
    "profile_interpret": "about",
    "apply_followup": "draft",
    "jobs_assess": "assess",
    "triage": "with_agent",
    "job_chat": "message",
}
NOT_RECORDED = Trigger("Not recorded for this run")

# The id an older run's input holds that names the one thing it was part of, by workflow.
_GROUPED_BY = {
    "fill_profile": "ingestion_run_id",
    "application": "application_id",
    "posting_documents": "generation_id",
}


def group_of(
    agent: str,
    run_id: str,
    workflow: str | None,
    workflow_run_id: str | None,
    trigger: str | None,
    input_data: Mapping[str, Any],
) -> tuple[str, str, Trigger]:
    """The workflow, the workflow run's id and the trigger of one agent run: as its starter
    recorded them, else from the id its input holds, else the run alone."""
    key = workflow if workflow in BY_KEY else AGENT_WORKFLOW.get(agent, "other")
    group = workflow_run_id
    if group is None and workflow is None:
        held = input_data.get(_GROUPED_BY.get(key, ""))
        group = held if isinstance(held, str) and held else None
    said = trigger or (AGENT_TRIGGER.get(agent) if workflow is None else None)
    return key, group or run_id, TRIGGERS.get(said or "", NOT_RECORDED)


def flow_of(workflow: Workflow) -> Flow | None:
    """The part of its category's flow a workflow draws: its boxes, the arrows between them,
    the columns and rows nothing of it uses taken out."""
    whole = FLOWS.get(workflow.category)
    if whole is None:
        return None
    kept = [node for node in whole.nodes if node.id in workflow.nodes]
    columns = sorted({node.column for node in kept})
    rows = sorted({node.row for node in kept})
    nodes = tuple(
        Node(n.id, n.kind, n.label, columns.index(n.column), rows.index(n.row), n.note, n.group)
        for n in kept
    )
    edges = tuple(
        edge
        for edge in whole.edges
        if edge.source in workflow.nodes and edge.target in workflow.nodes
    )
    return Flow(nodes=nodes, edges=edges)
