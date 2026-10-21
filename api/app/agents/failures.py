"""Why a run failed: the messages the runner stores on a run (they reach the user), and what a
caller that chains runs may conclude from one. No SDK import: the api reads this module too,
and `app.agents.runner`, which only the worker can import, takes its messages from here
(`runner.NO_CREDENTIAL`, `runner.is_provider_failure` and the others are these very objects).

A message is either a constant or a template filled with a number or a name from Claude Code
(a turn count, a duration, an HTTP status, a result subtype, an exit code): never with a text
of the model, of a tool or of a document.

Three kinds of failure, for a caller that chains runs (the ingestion engine):
- the **provider's** (`is_provider_failure`): the credential or Anthropic. The user fixes the
  credential, then resumes;
- the **box's** (`is_box_failure`): this worker could not run the agent at all. Decided by the
  orchestrator on 2026-10-11 (review of PR #95): a third kind, neither the provider's nor the
  run's own. The next run would fail the same way, so a chain pauses after the first one in a
  state it can resume from, instead of failing every remaining step for a reason that has
  nothing to do with its document;
- the run's **own**, everything else: its content, its limits, its budget. Trying the same run
  again would fail the same way, and the chain goes on with the next.
"""

import re

from app.agents import runs

NO_CREDENTIAL = "No model credential is saved. Add one on the Account page."
UNREADABLE = "The saved credential cannot be read with this server's key. Paste it again."
# What was decrypted is off the shape of every credential the app accepts (prefix, characters,
# length: credentials.shape_error): never used, because the scrub and the log filter rely on it.
UNUSABLE = "The saved credential is not a whole one. Paste it again."
UNKNOWN_AGENT = "This agent no longer exists."
THREAD_GONE = "This conversation no longer exists."
# The run was bound to something that is gone when it starts (AgentDefinition.bind returned
# None). The wording is the one the ingestion plan asks for: its steps are bound to a document.
BINDING_GONE = "This document no longer exists."
TIME_LIMIT = "The run took longer than its limit of {minutes} and was stopped."
TURN_LIMIT = "The run reached its limit of {turns} turns."
# The run used up the token budget its caller gave it (`token_budget` in the run's input). A
# constant, so that a caller can tell this end from every other by comparing the stored error.
# Nothing is wrong with the run or with the credential: the same work can be started again with
# a new budget.
TOKEN_LIMIT = "The run reached its token budget and was stopped."  # noqa: S105, a message
NO_RESULT = "The agent stopped without a result."
REFUSED = "Anthropic refused the credential: it is wrong, revoked or has no access."
BUSY = "Anthropic is busy or rate limiting this credential. Try again later."
# The credential is valid and has nothing left to pay with. Claude Code marks the assistant
# message `billing_error`; the HTTP status does not say it: the API documents 402 for
# `billing_error` (platform.claude.com/docs/en/api/errors, read on 2026-10-13) and a credit
# balance that is too low was measured as a 400 (review of PR #95), which is a content status.
# So the runner reads the mark, not the status.
NO_CREDIT = "This credential has no credit left at Anthropic. Add credit, then try again."
API_ERROR = "The model API failed (HTTP {status})."
FAILED = "The agent failed ({reason})."
NO_CLI = "Claude Code is not installed in the worker."
NO_START = "Claude Code could not start."
PROCESS = "The Claude Code process failed (exit code {code})."
INTERNAL = "The run failed on an internal error."

# A request the API refuses for what it holds (too large, malformed, unprocessable): sending the
# same content again fails the same way, whatever the credential.
CONTENT_STATUSES = frozenset({400, 413, 422})
_API_ERROR = re.compile(re.escape(API_ERROR).replace(re.escape("{status}"), r"(\d{3})"))
_PROVIDER = frozenset(
    {NO_CREDENTIAL, UNREADABLE, UNUSABLE, REFUSED, BUSY, NO_CREDIT, runs.QUEUE_DOWN}
)
# The worker could not run the agent: Claude Code is missing, did not start or died, the runner
# itself failed, or this worker does not know the agent (an older worker during a deploy).
_BOX = frozenset({NO_CLI, NO_START, INTERNAL, UNKNOWN_AGENT})
_PROCESS = re.compile(re.escape(PROCESS).replace(re.escape("{code}"), r"(-?\d+|unknown)"))


def is_provider_failure(error: str | None) -> bool:
    """Whether a run's stored `error` says the credential or the provider failed, not the run's
    own content: no credential, one the server cannot read, one Anthropic refused, one without
    credit (`NO_CREDIT`, whatever its HTTP status), Anthropic busy or rate limiting, the agent
    queue down, or the model API failing with any status other than
    400, 413 and 422. A caller that chains runs (the ingestion engine) stops and lets the user
    fix the credential and resume. False for a run that did not fail (`error` is None) and for
    any text this module did not write."""
    if error is None:
        return False
    if error in _PROVIDER:
        return True
    found = _API_ERROR.fullmatch(error)
    return found is not None and int(found.group(1)) not in CONTENT_STATUSES


def is_box_failure(error: str | None) -> bool:
    """Whether a run's stored `error` says this worker could not run the agent, whatever the
    run was about: Claude Code not installed, not starting, or its process failing; an internal
    error of the runner; an agent this worker does not know. Never true together with
    `is_provider_failure`. A failure that is neither is the run's own (module docstring). False
    for a run that did not fail and for any text this module did not write."""
    if error is None:
        return False
    return error in _BOX or _PROCESS.fullmatch(error) is not None
