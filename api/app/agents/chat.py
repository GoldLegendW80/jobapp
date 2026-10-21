"""Chat threads (agents A4, SPEC-agents.md, Data model and API): a conversation per job and user,
its messages, and the prompt of each answer.

Continuity (tasks/agents/todo.md, Chat continuity): no SDK session is ever resumed. Each user
message starts a fresh run of the chat agent; the worker rebuilds that run's prompt from the
thread's messages in Postgres (`prompt_for_run`), and the run's final text becomes the assistant
message (`record_reply`, in the same transaction as the run's end). Nothing about a conversation
stays on disk.

History budget: the prompt holds the newest earlier messages that fit HISTORY_BUDGET_TOKENS
(about 30k tokens, estimated as characters / 4 plus a small overhead per message), oldest first,
then the new message. Older messages are left out and replaced by one deterministic note saying
how many. Decision (A4): a deterministic note rather than a stored summary. A summary needs a
second model call per long thread (cost on the user's own credential, latency, one more way for
an answer to fail) and is itself a model output that can drift from what was said; the note is
free, predictable and testable. The facts that matter do not live in the history anyway: the
agent reads the posting and the profile through its tools at every answer.

One answer at a time per thread: a new message is refused (ThreadBusyError, 409) while the run of
the previous one is queued or running. The thread row is locked for the check and the insert, so
two sends at once cannot both pass.

Lock order (issue #27): the thread row first, then the user's run lock (the advisory lock of
runs.create_run), then the run row. Every path that touches a thread and its run in one
transaction follows it: `send` (thread, then create_run), `delete_thread` (thread, then
runs.request_cancel) and the worker ending a chat run (`lock_thread_of_run` before runs.finish,
see Recorder in runner.py, then `record_reply`). The thread goes first because it is the lock
that decides whether an answer is pending: `delete_thread` cannot know which run to cancel
before it holds it (a send could slip a new run in between). Paths that only touch runs (the
cancel endpoint, claim, the reapers) take no thread lock and fit any order. Nothing hard deletes
a job or a user today; a path that did would cascade to runs and threads in trigger order, so it
would have to lock the threads itself first.

Bounded waits (issue #161): a send and a delete wait LOCK_WAIT_S at most for each lock of their
transaction (`bounded_lock_waits`), then answer 409 with nothing written. The worker ending a
chat run waits its own bound for the thread; when that passes, it stores no answer and fails the
run at once, in a transaction that locks the run's row alone and so fits the order above
(runner.py, `_record_end`).

Quoting (issue #27): in the prompt, every message sits inside <message> tags, with "&" and "<"
written as "&amp;" and "&lt;" (`quoted`), so no message can close its own block, fake another
turn or end the conversation. Only the prompt is escaped: the database, the api and the UI keep
the raw text.
"""

import contextlib
import math
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.agents import definitions, runs
from app.agents.models import (
    THREAD_TITLE_MAX,
    AgentMessage,
    AgentRun,
    AgentThread,
    MessageRole,
    RunStatus,
)
from app.jobs import service as jobs_service
from app.jobs.models import Job

HISTORY_BUDGET_TOKENS = 30_000
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4
# The newest messages read to build a prompt: far more than any budget keeps.
HISTORY_READ_MAX = 1000
# How long a send or a delete waits for each lock of its transaction, the thread's row first
# (seconds, Postgres `lock_timeout`). Another send, a delete or the worker storing an answer
# holds that row for a moment; a holder that keeps it longer is stuck.
LOCK_WAIT_S = 5.0

AGENT = definitions.JOB_CHAT


class ThreadBusyError(Exception):
    """The thread's previous message is still being answered."""

    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(str(run_id))
        self.run_id = run_id


class ThreadLockedError(Exception):
    """A lock wait inside `bounded_lock_waits` outlasted its bound; the transaction is rolled
    back."""


@contextlib.contextmanager
def bounded_lock_waits(db: Session, wait_s: float) -> Iterator[None]:
    """Make each lock wait of `db`'s transaction, from here to its end, last `wait_s` at most.
    The bound is the transaction's own (`SET LOCAL lock_timeout`): it is gone with the
    transaction, and the pooled connection goes back to waiting as it did. A wait refused inside
    the block (SQLSTATE 55P03) rolls the transaction back and raises ThreadLockedError; every
    other error goes through as it is."""
    db.execute(
        sa.text("SELECT set_config('lock_timeout', :wait, true)"),
        {"wait": f"{max(1, round(wait_s * 1000))}ms"},
    )
    try:
        yield
    except DBAPIError as exc:
        if not jobs_service.is_lock_timeout(exc):
            raise
        db.rollback()
        raise ThreadLockedError from exc


def default_title(job: Job) -> str:
    title = f"{job.title} at {job.company}".strip()
    return title[:THREAD_TITLE_MAX] or "Chat"


# --- api side ------------------------------------------------------------------------------------


def threads_of(user_id: uuid.UUID) -> sa.Select[tuple[AgentThread]]:
    return sa.select(AgentThread).where(AgentThread.user_id == user_id)


def get_thread(
    db: Session, user_id: uuid.UUID, thread_id: uuid.UUID, *, lock: bool = False
) -> AgentThread | None:
    query = threads_of(user_id).where(AgentThread.id == thread_id)
    if lock:
        query = query.with_for_update()
    return db.execute(query.execution_options(populate_existing=True)).scalar_one_or_none()


def list_threads(
    db: Session, user_id: uuid.UUID, job_id: uuid.UUID | None, limit: int
) -> Sequence[AgentThread]:
    query = threads_of(user_id)
    if job_id is not None:
        query = query.where(AgentThread.job_id == job_id)
    return db.scalars(
        query.order_by(AgentThread.updated_at.desc(), AgentThread.id).limit(limit)
    ).all()


def thread_for_job(
    db: Session, user_id: uuid.UUID, job: Job, now: datetime
) -> tuple[AgentThread, bool]:
    """The user's thread on this job, created when there is none. Returns (thread, created).
    Two calls at once create one thread (the partial unique index, ON CONFLICT DO NOTHING)."""
    created_id = db.execute(
        insert(AgentThread)
        .values(
            user_id=user_id,
            job_id=job.id,
            title=default_title(job),
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["user_id", "job_id"],
            index_where=sa.text("job_id IS NOT NULL"),
        )
        .returning(AgentThread.id)
    ).scalar_one_or_none()
    thread = db.execute(
        threads_of(user_id)
        .where(AgentThread.job_id == job.id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    return thread, created_id is not None


def messages_of(
    db: Session, thread_id: uuid.UUID, limit: int
) -> tuple[list[tuple[AgentMessage, AgentRun | None]], int]:
    """The thread's newest `limit` messages, oldest first, each with its run (a user message's
    run is its answer's progress), and the thread's message count."""
    rows = db.execute(
        sa.select(AgentMessage, AgentRun)
        .outerjoin(AgentRun, AgentRun.id == AgentMessage.run_id)
        .where(AgentMessage.thread_id == thread_id)
        .order_by(AgentMessage.id.desc())
        .limit(limit)
    ).all()
    total = db.scalar(
        sa.select(sa.func.count())
        .select_from(AgentMessage)
        .where(AgentMessage.thread_id == thread_id)
    )
    return [(message, run) for message, run in reversed(rows)], total or 0


def active_run(db: Session, thread_id: uuid.UUID) -> AgentRun | None:
    """The run still answering in this thread (queued or running), if any."""
    return db.execute(
        sa.select(AgentRun)
        .join(AgentMessage, AgentMessage.run_id == AgentRun.id)
        .where(
            AgentMessage.thread_id == thread_id,
            AgentMessage.role == MessageRole.user,
            AgentRun.status.in_((RunStatus.queued, RunStatus.running)),
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def send(
    db: Session, user_id: uuid.UUID, thread: AgentThread, text: str, now: datetime
) -> tuple[AgentMessage, AgentRun]:
    """Store the user's message and queue the run that answers it. The thread must have been
    read with `lock=True` in this transaction. Raises ThreadBusyError while a previous answer is
    pending, runs.ActiveRunsError over the user's limit. The caller commits, then enqueues."""
    busy = active_run(db, thread.id)
    if busy is not None:
        raise ThreadBusyError(busy.id)
    run = runs.create_run(
        db, user_id, AGENT.name, thread.job_id, {"thread_id": str(thread.id)}, AGENT.model, now
    )
    message = AgentMessage(
        thread_id=thread.id, role=MessageRole.user, content={"text": text}, run_id=run.id, at=now
    )
    db.add(message)
    thread.updated_at = now
    db.flush()
    return message, run


def delete_thread(db: Session, user_id: uuid.UUID, thread_id: uuid.UUID, now: datetime) -> bool:
    """Delete the thread and its messages; a reply still pending is cancelled first. The runs
    stay in the run log (as when a job is deleted). The caller commits.

    Lock order (module docstring): the thread, then the run. The worker ending that run takes
    them in the same order, so the two wait for each other in turn and never deadlock."""
    thread = get_thread(db, user_id, thread_id, lock=True)
    if thread is None:
        return False
    busy = active_run(db, thread.id)
    if busy is not None:
        runs.request_cancel(db, user_id, busy.id, now)
    db.execute(sa.delete(AgentThread).where(AgentThread.id == thread.id))
    return True


# --- the prompt (worker side) --------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    role: MessageRole
    text: str


@dataclass(frozen=True)
class History:
    turns: list[Turn]  # oldest first
    omitted: int  # earlier messages left out


def quoted(text: str) -> str:
    """A message as it stands inside the prompt's <message> tags: "&" becomes "&amp;" and "<"
    becomes "&lt;". Without a raw "<" the text holds no tag at all, so it cannot close
    </message> or </conversation>, nor open a turn of its own. ">" is left alone (it means
    nothing without "<").

    "&" is escaped too so that the quoting loses nothing: a user who types "&lt;" literally is
    quoted as "&amp;lt;" and the model can tell it from a typed "<". The price, accepted, is
    that text which already holds entities is escaped once more, and that a plain "&" reads
    "&amp;" in the prompt (the chat agent's system prompt says how quoted text is written).
    Never apply it twice, and never store or show its result."""
    return text.replace("&", "&amp;").replace("<", "&lt;")


def estimate_tokens(text: str) -> int:
    """A cheap, deterministic upper-leaning estimate: about four characters per token."""
    return math.ceil(len(text) / CHARS_PER_TOKEN) + MESSAGE_OVERHEAD_TOKENS


def fit_history(turns: Sequence[Turn], budget: int) -> History:
    """The newest turns whose estimated tokens fit `budget`, oldest first. Stops at the first
    turn that does not fit (so the kept turns are always one unbroken tail). A turn costs what
    it takes in the prompt, that is, once quoted."""
    kept: list[Turn] = []
    used = 0
    for turn in reversed(turns):
        cost = estimate_tokens(quoted(turn.text))
        if used + cost > budget:
            break
        kept.append(turn)
        used += cost
    kept.reverse()
    return History(turns=kept, omitted=len(turns) - len(kept))


def omitted_note(count: int) -> str:
    plural = "message is" if count == 1 else "messages are"
    return (
        f"[{count} earlier {plural} left out to keep this conversation within its history "
        "budget. If the user refers to something you cannot see here, say so and ask.]"
    )


def render_prompt(job_id: uuid.UUID | None, history: History, current: str) -> str:
    """The run's first and only user message: the job, the conversation so far, the new
    message. Everything inside <conversation> and <message> is data written earlier, quoted
    (`quoted`) so that it cannot hold a tag of this layout."""
    parts = []
    if job_id is not None:
        parts.append(f"Job id: {job_id}")
    if history.turns or history.omitted:
        lines = []
        if history.omitted:
            lines.append(omitted_note(history.omitted))
        lines.extend(
            f'<message role="{turn.role.value}">\n{quoted(turn.text)}\n</message>'
            for turn in history.turns
        )
        parts.append(
            "The conversation so far, oldest first:\n<conversation>\n"
            + "\n".join(lines)
            + "\n</conversation>"
        )
    else:
        parts.append("This is the first message of the conversation.")
    parts.append(f'The user\'s new message:\n<message role="user">\n{quoted(current)}\n</message>')
    return "\n\n".join(parts)


@dataclass(frozen=True)
class ChatPrompt:
    text: str = ""
    included: int = 0
    omitted: int = 0
    # The new message alone (the fake backend's {message}).
    current: str = ""

    def __repr__(self) -> str:  # never print the conversation
        return f"ChatPrompt(included={self.included}, omitted={self.omitted})"


def _turn(message: AgentMessage) -> Turn:
    content: dict[str, Any] = message.content or {}
    return Turn(role=message.role, text=str(content.get("text", "")))


def prompt_for_run(
    db: Session, run: AgentRun, budget: int = HISTORY_BUDGET_TOKENS
) -> ChatPrompt | None:
    """The prompt of a chat run, rebuilt from the database: the user message this run answers
    and the thread's messages before it. None when that message or its thread is gone, or the
    thread is not the run's user's."""
    message = db.execute(
        sa.select(AgentMessage)
        .join(AgentThread, AgentThread.id == AgentMessage.thread_id)
        .where(
            AgentMessage.run_id == run.id,
            AgentMessage.role == MessageRole.user,
            AgentThread.user_id == run.user_id,
        )
    ).scalar_one_or_none()
    if message is None:
        return None
    thread = db.get(AgentThread, message.thread_id)
    if thread is None:
        return None
    earlier = db.scalars(
        sa.select(AgentMessage)
        .where(AgentMessage.thread_id == thread.id, AgentMessage.id < message.id)
        .order_by(AgentMessage.id.desc())
        .limit(HISTORY_READ_MAX)
    ).all()
    current = _turn(message).text
    turns = [_turn(row) for row in reversed(earlier)]
    history = fit_history(turns, max(0, budget - estimate_tokens(quoted(current))))
    if len(earlier) == HISTORY_READ_MAX:
        # Messages past HISTORY_READ_MAX were never read: they are left out too.
        total = db.scalar(
            sa.select(sa.func.count())
            .select_from(AgentMessage)
            .where(AgentMessage.thread_id == thread.id, AgentMessage.id < message.id)
        )
        history = History(turns=history.turns, omitted=(total or 0) - len(history.turns))
    return ChatPrompt(
        text=render_prompt(thread.job_id, history, current),
        included=len(history.turns),
        omitted=history.omitted,
        current=current,
    )


def lock_thread_of_run(db: Session, run_id: uuid.UUID) -> uuid.UUID | None:
    """Lock the thread of the message this run answers, until the transaction ends. None when
    that message or its thread is gone (deleted meanwhile).

    Lock order (module docstring): the worker calls this BEFORE runs.finish touches the run row,
    because `delete_thread` holds the thread while it cancels the run. Locking the run first
    here is the deadlock of issue #27."""
    thread_id = db.scalar(
        sa.select(AgentMessage.thread_id).where(
            AgentMessage.run_id == run_id, AgentMessage.role == MessageRole.user
        )
    )
    if thread_id is None:
        return None
    return db.scalar(sa.select(AgentThread.id).where(AgentThread.id == thread_id).with_for_update())


def record_reply(db: Session, run_id: uuid.UUID, text: str, now: datetime) -> bool:
    """Store a finished run's answer in the thread of the message it answered. False (and
    nothing stored) when that thread was deleted meanwhile. The caller commits.

    A caller that updates the run in the same transaction (the worker) must have called
    `lock_thread_of_run` before that update; the lock taken here is then already held."""
    thread_id = lock_thread_of_run(db, run_id)
    if thread_id is None:
        return False
    db.add(
        AgentMessage(
            thread_id=thread_id,
            role=MessageRole.assistant,
            content={"text": text},
            run_id=run_id,
            at=now,
        )
    )
    db.execute(
        sa.update(AgentThread)
        .where(AgentThread.id == thread_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return True
