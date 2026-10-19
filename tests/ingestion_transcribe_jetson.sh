#!/usr/bin/env bash
# Ingestion G14, assumption A7: one real transcription of a dense A4 scan on the Jetson, for the
# operator of the box, with the owner's own saved model credential (the one of A5).
#
#   tests/ingestion_transcribe_jetson.sh                         dry run: every check, no model call
#   REAL_RUN=1 LABEL=first tests/ingestion_transcribe_jetson.sh  one real transcription, never retried
#
# The page is generated here, on the operator's machine: an A4 page of small print (about 8 point
# at the size G8 renders a scan, 1568 pixels on its long side) holding a made up text that this
# script knows to the character: names, dates, numbers, addresses of example.org, French accents.
# Nothing of it is about a real person and nothing of the owner's documents is read.
#
# What it does, in order:
# 1. Before (read only): every Compose service healthy, the deployed commit, the api knows the
#    agent document_transcribe, the owner is an admin account with a credential of the
#    subscription kind (role and kind only, never the secret) who accepted the current consent
#    text, with no ingestion run and no agent run under way.
# 2. The page: generated locally, checked against the limits of a stored image (300,000 bytes,
#    2,000 pixels a side). A dry run stops here: nothing was written on the Jetson.
# 3. Start, inside the api container, with the app's own functions: the page is stored as a
#    document of the owner that waits for its transcription (named g14-dense-a4<label>.jpg), the
#    run is quoted and created as POST /api/ingestion/runs does (estimate_router.quote,
#    engine.create), the extraction step it predicts is removed before anything runs (a made up
#    page must propose nothing for the owner's profile), then engine.advance starts the
#    transcription step under its budget and the agent run is offered to the agents queue.
# 4. Wait: the agent run row is polled to its end, at most the step's 15 minutes plus a minute.
# 5. Measure, inside the api container: the text the model stored is compared with the known
#    text. Character error rate and word error rate are edit distances over the length of the
#    known text, after runs of spaces are folded. With them: the model, the tokens, the tool
#    calls and errors, the duration. Numbers and names only, never a text.
# 6. After: the document is done and its image deleted, the ingestion run is closed, and no
#    item was stored for it. The line to copy under "Measurements" of the ingestion plan (the
#    paragraph headed G14) is printed.
#
# What it leaves: the document g14-dense-a4<label>.jpg in the owner's list and one closed run
# with no proposal. Nothing is deleted by hand in production: delete the document from the app.
#
# Where it writes. A dry run writes only under a temporary directory that is removed at the
# end. A real run writes tasks/evidence/ingestion-g14/run<label>.log and metrics<label>.json and
# refuses to start, before any connection to the Jetson, when one of them exists: a new real
# run needs a new LABEL.
#
# If the page is not read faithfully (the plan: assumption A7), the fix is two tiles per page,
# a task of its own. This script only measures.
#
# Settings as deploy.sh: DEPLOY_HOST (default jetson), DEPLOY_DIR (default ~/jobapp).
# OWNER (required: the e-mail address of the owner's account), LABEL, WAIT_S, POLL_S.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
OWNER="${OWNER:-}"
RAW_LABEL="${LABEL:-}"
LABEL="${LABEL:+-$LABEL}"
REAL="${REAL_RUN:-0}"
EVIDENCE="${EVIDENCE:-$ROOT/tasks/evidence/ingestion-g14}"
# Only an account with this role and a credential of this kind gets a run (as A5).
OWNER_ROLE="admin"
OWNER_CREDENTIAL="claude_subscription"
# The wall time of a transcription step (agents.TRANSCRIBE_WALL_TIME_S) plus a minute.
WAIT_S="${WAIT_S:-960}"
POLL_S="${POLL_S:-5}"
# The limits of a stored page image (app/ingestion/models.py).
IMAGE_MAX_BYTES=300000
IMAGE_MAX_SIDE=2000
WORK="$(mktemp -d)"

# shellcheck disable=SC2329 # called by the EXIT trap
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die() {
  printf 'ingestion_transcribe_jetson.sh: %s\n' "$*" >&2
  exit 1
}

[[ -n "$OWNER" ]] || die "OWNER is not set: run it again with OWNER=<the e-mail address of the owner's account on the Jetson>"
[[ "$OWNER" =~ ^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$ ]] || die "OWNER is not an email address"
[[ "$RAW_LABEL" =~ ^[A-Za-z0-9_-]*$ ]] || die "LABEL holds letters, digits, _ and - only"
[[ "$WAIT_S" =~ ^[0-9]+$ && "$POLL_S" =~ ^[1-9][0-9]*$ ]] || die "WAIT_S and POLL_S are whole seconds"
command -v jq >/dev/null || die "jq is needed"
command -v uv >/dev/null || die "uv is needed (the page is drawn with the api's own Pillow)"

# Replaces the owner's address in all that is printed through it, remote errors and bash -x traces included.
masked() {
  LC_ALL=C awk -v owner="$OWNER" -v shown="(the owner's address)" '
    BEGIN { o = tolower(owner); n = length(o) }
    {
      line = $0
      out = ""
      while (n && (i = index(tolower(line), o))) {
        out = out substr(line, 1, i - 1) shown
        line = substr(line, i + n)
      }
      print out line
      fflush()
    }'
}

if [[ "$REAL" == "1" ]]; then OUT="$EVIDENCE"; else OUT="$WORK"; fi
LOG="$OUT/run$LABEL.log"
METRICS="$OUT/metrics$LABEL.json"
if [[ "$REAL" == "1" ]]; then
  existing=""
  for path in "$LOG" "$METRICS"; do
    if [[ -e "$path" ]]; then existing="$existing ${path#"$ROOT"/}"; fi
  done
  [[ -z "$existing" ]] || die "refused, nothing was started: evidence of an earlier run exists and would be overwritten:$existing. Give this run a new LABEL."
  mkdir -p "$OUT"
fi
exec > >(masked | tee "$LOG") 2>&1

remote() { # a command in the stack's directory on the Jetson
  ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && $1"
}

in_api() { # in_api <args...>: the Python on stdin inside the api container, its input in $INPUT
  # The program and its input travel together: the first line is the length of the program.
  local program="$WORK/program.py"
  cat >"$program"
  {
    wc -c <"$program" | tr -d ' '
    cat "$program" "${INPUT:-/dev/null}"
  } | ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && docker compose -f compose.yml exec -T api python -c $(printf '%q' 'import sys; n = int(sys.stdin.buffer.readline()); exec(compile(sys.stdin.buffer.read(n).decode(), "operator", "exec"))') $*"
}

draw_page() { # the page and its known text, as JSON on stdout: {jpeg, width, height, text}
  uv --native-tls run --project api python - <<'PAGE_PY'
"""A dense A4 page of small print with a text known to the character. Made up: no real person."""
import base64
import io
import json
import random

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1109, 1568  # A4 with its long side at the 1568 pixels G8 renders a scan to
MARGIN = 64
FONT_PX = 15  # about 8 point at this size of page
LINE_PX = 19
IMAGE_MAX_BYTES = 300_000
NAMES = ["Camille Durand", "Noël Lefèvre", "Zoé Maçon", "Hélène Brière", "Loïc Guérin"]
ROLES = ["Data Engineer", "Développeuse back end", "Chef de projet", "Analyste", "Product Designer"]
FIRMS = ["Example Works", "Atelier Exemple", "Société Fictive", "Compagnie Modèle", "Maison Témoin"]
TOWNS = ["Lyon", "Besançon", "Orléans", "Nîmes", "Saint-Étienne", "Angoulême"]
MONTHS = ["January", "février", "March", "avril", "June", "août", "October", "décembre"]
VERBS = ["Built", "A conçu", "Led", "A migré", "Measured", "A réduit", "Wrote", "A formé"]
THINGS = [
    "the ingestion pipeline for {n} regional warehouses",
    "un entrepôt de données de {n} tables",
    "a review process used by {n} engineers",
    "les coûts d'hébergement de {n} %",
    "{n} tableaux de bord pour la direction",
    "an alerting service that handles {n} events a day",
]


def lines(seed: int = 14) -> list[str]:
    draw = random.Random(seed)  # noqa: S311, a made up page, not a secret
    out: list[str] = []
    while len(out) < (HEIGHT - 2 * MARGIN) // LINE_PX:
        name, firm = draw.choice(NAMES), draw.choice(FIRMS)
        start = draw.randint(2009, 2021)
        mail = name.split()[0].lower().encode("ascii", "ignore").decode()
        out.append(
            f"{firm}, {draw.choice(ROLES)}, {draw.choice(MONTHS)} {start} to "
            f"{draw.choice(MONTHS)} {start + draw.randint(1, 4)}, {draw.choice(TOWNS)}"
        )
        out.append(f"{name}, {mail}{draw.randint(10, 99)}@example.org, +33 5 55 01 {draw.randint(10, 99)} {draw.randint(10, 99)}")
        for _ in range(draw.randint(2, 4)):
            thing = draw.choice(THINGS).format(n=draw.randint(3, 480))
            out.append(f"{draw.choice(VERBS)} {thing}, ref. {draw.randint(1000, 9999)}-{draw.choice('ABCDEFGH')}.")
        out.append("")
    return out[: (HEIGHT - 2 * MARGIN) // LINE_PX]


def page() -> dict[str, object]:
    font = ImageFont.load_default(size=FONT_PX)
    picture = Image.new("RGB", (WIDTH, HEIGHT), "white")
    pen = ImageDraw.Draw(picture)
    written = lines()
    for index, line in enumerate(written):
        if pen.textlength(line, font=font) > WIDTH - 2 * MARGIN:
            raise SystemExit("a line is wider than the page: shorten the vocabulary")
        pen.text((MARGIN, MARGIN + index * LINE_PX), line, fill="black", font=font)
    for quality in (90, 80, 70, 60, 50):
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=quality)
        if out.tell() <= IMAGE_MAX_BYTES:
            break
    else:
        raise SystemExit("the page does not fit a stored image")
    return {
        "jpeg": base64.b64encode(out.getvalue()).decode("ascii"),
        "width": WIDTH,
        "height": HEIGHT,
        "quality": quality,
        "text": "\n".join(written).strip(),
    }


if __name__ == "__main__":
    print(json.dumps(page()))
PAGE_PY
}

before() { # one line of JSON about the owner and the stack: names, kinds and counts only
  INPUT="" in_api "$OWNER" <<'BEFORE_PY'
import json
import sys

import sqlalchemy as sa

from app.agents import credentials, definitions
from app.agents import runs as agent_runs
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.identity.models import User
from app.ingestion import engine, estimate_router

settings = get_settings()
database = build_engine(settings)
out: dict[str, object] = {"agent": engine.TRANSCRIBE_NAME in definitions.AGENTS}
try:
    with build_sessionmaker(database)() as db:
        user = db.execute(sa.select(User).where(User.email == sys.argv[1])).scalar_one_or_none()
        out["user"] = user is not None
        if user is not None:
            credential = credentials.get(db, user.id)
            out["role"] = str(getattr(user.role, "value", user.role))
            out["credential"] = None if credential is None else str(credential.kind.value)
            out["consented"] = estimate_router.has_consented(db, user.id)
            out["ingestion_run"] = engine.active_run_id(db, user.id) is not None
            out["agent_runs"] = agent_runs.count_active(db, user.id)
finally:
    database.dispose()
print(json.dumps(out))
BEFORE_PY
}

start() { # stores the page and starts its transcription: one line of JSON, ids and codes only
  INPUT="$WORK/page.json" in_api "$OWNER" "g14-dense-a4$LABEL.jpg" <<'SEED_PY'
"""The operator's stand-in for an upload of one page image and POST /api/ingestion/runs: the
app's own functions for the quote, the run, the budget and the queue. The one difference is
told in `seed`: the extraction step of the run is removed before anything runs."""
import asyncio
import base64
import hashlib
import json
import sys
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa


def seed(db: Any, settings: Any, user_id: uuid.UUID, page: dict[str, Any], name: str, now: datetime) -> dict[str, Any]:
    """One document that waits for its transcription with its page image, one ingestion run
    over it holding the transcription step alone, started. The caller commits."""
    from app.ingestion import documents, engine, estimate_router, plan
    from app.ingestion.models import IngestionDocument, IngestionImage, TextSource, TextStatus
    from app.ingestion.runs_models import IngestionRunStep

    picture = base64.b64decode(page["jpeg"])
    document = IngestionDocument(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        mime="image/jpeg",
        size=len(picture),
        sha256=hashlib.sha256(picture).digest(),
        text="",
        text_source=TextSource.transcribed,
        text_status=TextStatus.awaiting_transcription,
        pages=1,
        pages_text=0,
        pages_image=1,
        parser_version=documents.PARSER_VERSION,
    )
    db.add(document)
    db.flush()
    db.add(
        IngestionImage(
            document_id=document.id,
            user_id=user_id,
            position=0,
            jpeg=picture,
            width=int(page["width"]),
            height=int(page["height"]),
        )
    )
    db.flush()
    quote = estimate_router.quote(db, user_id, [str(document.id)], [])
    run = engine.create(db, user_id, quote, now)
    db.flush()
    # A made up page proposes nothing for a real profile: the run keeps its transcription step
    # and loses the extraction step predicted for the text, which never ran.
    db.execute(
        sa.delete(IngestionRunStep).where(
            IngestionRunStep.ingestion_run_id == run.id,
            IngestionRunStep.kind == plan.StepKind.extract,
        )
    )
    db.flush()
    db.expire_all()
    moved = engine.advance(db, run.id, now, settings)
    return {
        "document_id": str(document.id),
        "run_id": str(run.id),
        "agent_run_id": None if moved.enqueue is None else str(moved.enqueue),
        "run_status": str(moved.status.value),
    }


def main() -> None:
    from app import queue as queues
    from app.config import get_settings
    from app.db import build_engine, build_sessionmaker
    from app.identity import service as identity_service
    from app.identity.models import User
    from app.ingestion import engine

    page = json.loads(sys.stdin.buffer.read().decode())
    settings = get_settings()
    database = build_engine(settings)
    sessions = build_sessionmaker(database)
    out: dict[str, Any] = {}

    async def offer(agent_run_id: uuid.UUID) -> bool:
        queue = queues.Queue(settings.redis_url.get_secret_value())
        try:
            await queue.enqueue(
                "run_agent",
                str(agent_run_id),
                queue=queues.AGENTS,
                job_id=queues.agent_run_job_id(agent_run_id),
            )
            return True
        except queues.QueueUnavailableError:
            return False
        finally:
            await queue.close()

    try:
        with sessions() as db, db.begin():
            user_id = db.scalar(sa.select(User.id).where(User.email == sys.argv[1]))
            if user_id is None:
                raise LookupError
            out.update(seed(db, settings, user_id, page, sys.argv[2], identity_service.utcnow()))
        if out["agent_run_id"] is not None:
            agent_run_id = uuid.UUID(out["agent_run_id"])
            out["enqueued"] = asyncio.run(offer(agent_run_id))
            if not out["enqueued"]:
                with sessions() as db, db.begin():
                    engine.queue_failed(
                        db, uuid.UUID(out["run_id"]), agent_run_id, identity_service.utcnow()
                    )
    except Exception as exc:  # the class name only: a message could quote a statement
        out["error_class"] = type(exc).__name__
    finally:
        database.dispose()
    print(json.dumps(out))


if __name__ == "__main__":
    main()
SEED_PY
}

measure() { # measure <document-id> <agent-run-id>: one JSON object, numbers and names only
  INPUT="$WORK/page.json" in_api "$1" "$2" <<'MEASURE_PY'
"""How faithfully the page was read, and what it cost. Never prints a text."""
import json
import sys
import uuid
from typing import Any


def folded(text: str) -> str:
    """Runs of spaces folded, the ends of lines trimmed, empty lines dropped."""
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def distance(known: Any, got: Any) -> int:
    """The edit distance between two sequences (insertions, deletions, substitutions)."""
    row = list(range(len(got) + 1))
    for i, one in enumerate(known, 1):
        last, row[0] = row[0], i
        for j, other in enumerate(got, 1):
            last, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, last + (one != other))
    return row[-1]


def rates(known: str, got: str) -> dict[str, Any]:
    known, got = folded(known), folded(got)
    words, got_words = known.split(), got.split()
    characters, word_edits = distance(known, got), distance(words, got_words)
    return {
        "known_characters": len(known),
        "transcribed_characters": len(got),
        "character_edits": characters,
        "character_error_rate": round(characters / max(len(known), 1), 5),
        "known_words": len(words),
        "word_edits": word_edits,
        "word_error_rate": round(word_edits / max(len(words), 1), 5),
        "lines_exact": sum(1 for line in known.splitlines() if line in set(got.splitlines())),
        "lines": len(known.splitlines()),
    }


def main() -> None:
    import sqlalchemy as sa

    from app.agents.models import AgentRun
    from app.config import get_settings
    from app.db import build_engine, build_sessionmaker
    from app.ingestion.models import IngestionDocument, IngestionImage
    from app.ingestion.runs_models import IngestionItem, IngestionRun, IngestionRunStep

    page = json.loads(sys.stdin.buffer.read().decode())
    document_id, agent_run_id = uuid.UUID(sys.argv[1]), uuid.UUID(sys.argv[2])
    database = build_engine(get_settings())
    out: dict[str, Any] = {}
    try:
        with build_sessionmaker(database)() as db:
            run = db.get_one(AgentRun, agent_run_id)
            document = db.execute(
                sa.select(
                    IngestionDocument.text,
                    IngestionDocument.text_status,
                    IngestionDocument.transcribed_pages,
                ).where(IngestionDocument.id == document_id)
            ).one()
            step = db.execute(
                sa.select(IngestionRunStep).where(IngestionRunStep.agent_run_id == agent_run_id)
            ).scalar_one()
            chain = db.get_one(IngestionRun, step.ingestion_run_id)
            started = getattr(run, "started_at", None) or run.created_at
            out.update(
                agent_status=str(run.status.value),
                agent_error=run.error,  # a constant sentence of app/agents/failures.py
                model=run.model,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                counts=run.output if isinstance(run.output, dict) else None,
                duration_s=None
                if run.finished_at is None
                else round((run.finished_at - started).total_seconds(), 1),
                token_budget=step.token_budget,
                step_status=str(step.status.value),
                run_status=str(chain.status.value),
                run_tokens_spent=chain.tokens_spent,
                document_status=str(document.text_status.value),
                transcribed_pages=document.transcribed_pages,
                images_left=db.scalar(
                    sa.select(sa.func.count())
                    .select_from(IngestionImage)
                    .where(IngestionImage.document_id == document_id)
                ),
                items_stored=db.scalar(
                    sa.select(sa.func.count())
                    .select_from(IngestionItem)
                    .where(IngestionItem.document_id == document_id)
                ),
                page={key: page[key] for key in ("width", "height", "quality")},
            )
            out.update(rates(page["text"], document.text or ""))
    except Exception as exc:  # the class name only
        out["error_class"] = type(exc).__name__
    finally:
        database.dispose()
    print(json.dumps(out))


if __name__ == "__main__":
    main()
MEASURE_PY
}

status_of() { # the status of the agent run $1, read through the app's own model
  INPUT="" in_api "$1" <<'STATUS_PY'
import sys
import uuid

from app.agents.models import AgentRun
from app.config import get_settings
from app.db import build_engine, build_sessionmaker

database = build_engine(get_settings())
try:
    with build_sessionmaker(database)() as db:
        print(db.get_one(AgentRun, uuid.UUID(sys.argv[1])).status.value)
finally:
    database.dispose()
STATUS_PY
}

step "Before"
health="$(remote "docker compose -f compose.yml ps --format '{{.Service}}:{{.Health}}'" | sort | tr '\n' ' ')"
info "services: $health"
if grep -Eq ':(unhealthy|starting)' <<<"$health"; then die "a service is not healthy"; fi
info "deployed commit: $(remote 'git rev-parse --short HEAD 2>/dev/null || cat .deployed-commit 2>/dev/null || echo unknown' | tr -d '\r')"
state="$(before | tail -n 1)"
jq -e . >/dev/null <<<"$state" || die "the api container did not answer"
[[ "$(jq -r .agent <<<"$state")" == true ]] || die "the deployed api does not know document_transcribe: deploy G14 first"
[[ "$(jq -r .user <<<"$state")" == true ]] || die "the owner's account is not on $HOST"
role="$(jq -r .role <<<"$state")"
credential="$(jq -r .credential <<<"$state")"
info "owner: role $role, credential of kind $credential"
[[ "$role" == "$OWNER_ROLE" && "$credential" == "$OWNER_CREDENTIAL" ]] ||
  die "refused, nothing was started: runs are started only for an account with the role $OWNER_ROLE and a credential of kind $OWNER_CREDENTIAL"
[[ "$(jq -r .consented <<<"$state")" == true ]] ||
  die "refused, nothing was started: the owner's account has not accepted the current consent text of the ingestion (do it in the app)"
[[ "$(jq -r .ingestion_run <<<"$state")" == false && "$(jq -r .agent_runs <<<"$state")" == 0 ]] ||
  die "refused, nothing was started: a run of the owner's account is under way"

step "The page"
draw_page >"$WORK/page.json"
bytes="$(jq -r .jpeg "$WORK/page.json" | base64 --decode | wc -c | tr -d ' ')"
width="$(jq -r .width "$WORK/page.json")"
height="$(jq -r .height "$WORK/page.json")"
info "$width by $height pixels, $bytes bytes at quality $(jq -r .quality "$WORK/page.json"), $(jq -r '.text | length' "$WORK/page.json") known characters on $(jq -r '.text | split("\n") | length' "$WORK/page.json") lines"
((bytes <= IMAGE_MAX_BYTES && width <= IMAGE_MAX_SIDE && height <= IMAGE_MAX_SIDE)) ||
  die "the page is over the limits of a stored image"

if [[ "$REAL" != "1" ]]; then
  step "Dry run"
  info "every check passed. Nothing was stored on the Jetson and no model was called."
  info "REAL_RUN=1 LABEL=<name> $0 makes the one real transcription."
  exit 0
fi

step "Start"
started="$(start | tail -n 1)"
jq -e . >/dev/null <<<"$started" || die "the api container did not answer; check the owner's documents before another run"
info "$(jq -c 'del(.error_class)' <<<"$started")"
[[ "$(jq -r '.error_class // empty' <<<"$started")" == "" ]] || die "the start failed ($(jq -r .error_class <<<"$started")): nothing is retried"
document="$(jq -r .document_id <<<"$started")"
agent_run="$(jq -r '.agent_run_id // empty' <<<"$started")"
[[ -n "$agent_run" ]] || die "no step was started (run status $(jq -r .run_status <<<"$started")): nothing is retried"
[[ "$(jq -r .enqueued <<<"$started")" == true ]] || die "the agents queue refused the run: nothing is retried"

step "Wait"
waited=0
status=queued
while [[ "$status" == queued || "$status" == running ]]; do
  ((waited < WAIT_S)) || die "the run is still $status after ${WAIT_S}s: look at it in the app, nothing is retried"
  sleep "$POLL_S"
  waited=$((waited + POLL_S))
  status="$(status_of "$agent_run" | tail -n 1 | tr -d '\r')"
  info "t+${waited}s status=${status:-unread}"
done
# The engine closes the run in the hook that follows the end of the agent run.
sleep "$POLL_S"

step "Measure"
measure "$document" "$agent_run" | tail -n 1 | jq . >"$METRICS"
jq . "$METRICS"
[[ "$(jq -r '.error_class // empty' "$METRICS")" == "" ]] || die "the measure failed ($(jq -r .error_class "$METRICS"))"

step "After"
[[ "$(jq -r .agent_status "$METRICS")" == "done" ]] || die "the agent run ended $(jq -r .agent_status "$METRICS"): $(jq -r .agent_error "$METRICS")"
[[ "$(jq -r .document_status "$METRICS")" == "done" && "$(jq -r .images_left "$METRICS")" == 0 ]] ||
  die "the document is not done or its image is still stored"
[[ "$(jq -r .items_stored "$METRICS")" == 0 ]] || die "items were stored for a made up page: reject them in the app"
info "run status: $(jq -r .run_status "$METRICS") (the chain closes at the latest on the next tick of the worker)"
step "The line for Measurements (the ingestion plan, paragraph G14)"
jq -r --arg label "${RAW_LABEL:-unlabelled}" --arg day "$(date +%Y-%m-%d)" \
  '"Jetson, \($day), run \($label): a dense A4 page (\(.page.width) by \(.page.height) px, \(.known_characters) known characters on \(.lines) lines) transcribed by \(.model): character error rate \(.character_error_rate) (\(.character_edits) edits), word error rate \(.word_error_rate) (\(.word_edits) of \(.known_words) words), \(.lines_exact) of \(.lines) lines exact; \(.input_tokens) input and \(.output_tokens) output tokens under a budget of \(.token_budget); \(.duration_s) s."' \
  "$METRICS"
