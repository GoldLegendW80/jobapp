"""Why a run failed: the messages the runner stores on a run (they reach the user), and what a
caller that chains runs may conclude from one. No SDK import: the api reads this module too,
and `app.agents.runner`, which only the worker can import, takes its messages from here
(`runner.NO_CREDENTIAL`, `runner.is_provider_failure` and the others are these very objects).

A message is either a constant or a template filled with a number or a name from Claude Code
(a turn count, a duration, an HTTP status, a result subtype, an exit code): never with a text
of the model, of a tool or of a document.
"""

import re

from app.agents import runs

NO_CREDENTIAL = "No model credential is saved. Add one on the Account page."
UNREADABLE = "The saved credential cannot be read with this server's key. Paste it again."
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
_PROVIDER = frozenset({NO_CREDENTIAL, UNREADABLE, REFUSED, BUSY, runs.QUEUE_DOWN})


def is_provider_failure(error: str | None) -> bool:
    """Whether a run's stored `error` says the credential or the provider failed, not the run's
    own content: no credential, one the server cannot read, one Anthropic refused, Anthropic busy
    or rate limiting, the agent queue down, or the model API failing with any status other than
    400, 413 and 422. A caller that chains runs (the ingestion engine) stops and lets the user
    fix the credential and resume; every other failure is the run's own and trying again would
    fail the same way. False for a run that did not fail (`error` is None) and for any text this
    module did not write."""
    if error is None:
        return False
    if error in _PROVIDER:
        return True
    found = _API_ERROR.fullmatch(error)
    return found is not None and int(found.group(1)) not in CONTENT_STATUSES
