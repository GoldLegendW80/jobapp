#!/usr/bin/env bash
# Agents A5: real agent runs on the Jetson, for the operator of the box. One triage run and one
# chat message on ONE job of the owner's account, with the owner's own saved model credential,
# framed by the proof that the owner's jobs and profile are untouched and by a memory sampler.
#
#   tests/agents_real_run_jetson.sh <job-id>                 dry run: every check, no model call
#   REAL_RUN=1 LABEL=second tests/agents_real_run_jetson.sh <job-id>     one triage run, then one chat message
#   REAL_RUN=1 RUNS=chat LABEL=third MESSAGE='...' tests/agents_real_run_jetson.sh <job-id>
#
# Follow up, issue #39: the start of a run below calls underscore prefixed functions of the
# routers (runs_router._create, chat_router._open and _send). A public operator entry point shared
# with the routers, or an api test that runs the embedded Python against the test database, will
# keep this script from drifting away from the endpoints.
#
# What it does, in order:
# 1. Before: every Compose service healthy, the deployed commit, the worker's code has the chat
#    lock hook (issue #27), the owner is an admin account with a credential of the subscription
#    kind (role, kind and verified flag only, never the secret) and no run queued or running; the
#    job is the owner's, has a posting text, and neither its title, its company nor its posting
#    mentions security (hard rule: never a cybersecurity role). Snapshot of the owner's data: jobs
#    (count, per status, md5 over every column of every row), profile (revision and md5), job
#    events (count and highest id), files and sweeps (count only), and the agent tables.
# 2. For each run: a sampler on the Jetson writes, every second, the worker's and the api's
#    cgroup memory, the host's available memory and the Claude Code processes (count, RSS).
#    The run is started with no web session: inside the api container, the functions the HTTP
#    endpoints call (runs_router._create, chat_router._open and _send, then Queue.enqueue on the
#    agents queue), so the credential check, the per user advisory lock, the thread lock and the
#    pending answer check are the app's own. Then the run row is polled to its end, at most the
#    agent's wall time plus a minute. Never retried.
# 3. Metrics from the database: status, model, tokens, cost, turns, duration, event kinds, tool
#    calls, denials, proposals. Never a prompt, a posting, a profile, an answer or a proposal text.
# 4. Checks: scratch directory gone, no transcript in the worker, the logs of the run hold no
#    message text, no credential variable name and no token shaped value (matches are counted,
#    never printed, and the text looked for is never printed either).
# 5. After: the same snapshot. The owner's jobs and profile must be identical; only the agent
#    tables may differ, by exactly the runs made here, and no proposal may have been decided
#    meanwhile. Nothing is accepted, rejected or deleted.
#
# Where it writes. A dry run writes only under a temporary directory that is removed at the end:
# nothing under tasks/evidence/ and nothing on the Jetson. A real run writes its evidence to
# tasks/evidence/agents-a5/: run.log, metrics.json, memory-<run>.tsv (LABEL adds a suffix), and
# refuses to start, before any connection to the Jetson, when one of those files already exists:
# the committed files are the record of the runs of 2026-10-10, so a new real run needs a new LABEL.
#
# Limits. At most two runs per call, each triage or chat, and never a second run after a failed
# one; nothing limits the number of calls. The same kind twice (RUNS="chat chat") keeps both
# sets of samples and metrics: the second is suffixed with its index (memory-chat-2.tsv).
# OWNER may name another account only if it is an admin with a credential of the subscription
# kind: the script never starts a run with the Anthropic API key of a member.
#
# Settings as deploy.sh: DEPLOY_HOST (default jetson), DEPLOY_DIR (default ~/jobapp).
# OWNER (default hthautin@gmail.com), RUNS (default "triage chat"), MESSAGE, LABEL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST="${DEPLOY_HOST:-jetson}"
RDIR="${DEPLOY_DIR:-jobapp}"
RDIR="${RDIR#\~/}"
OWNER="${OWNER:-hthautin@gmail.com}"
RUNS="${RUNS:-triage chat}"
MESSAGE="${MESSAGE:-What are the two strongest points of my profile for this role, and the one gap I should address in the letter?}"
RAW_LABEL="${LABEL:-}"
LABEL="${LABEL:+-$LABEL}"
REAL="${REAL_RUN:-0}"
JOB="${1:-}"
EVIDENCE="${EVIDENCE:-$ROOT/tasks/evidence/agents-a5}"
# Only an account with this role (identity Role) and a credential of this kind (agents
# CredentialKind, the owner's personal option by A1) gets a run.
OWNER_ROLE="admin"
OWNER_CREDENTIAL="claude_subscription"
# Never a cybersecurity role: the job is refused when its title, company or posting matches.
FORBIDDEN='(cyber|security|sécurité|securite|pentest|threat)'
# The wall time of every agent (definitions.DEFAULT_WALL_TIME_S) plus a minute.
WAIT_S="${WAIT_S:-660}"
# Seconds of idle baseline before a run, and between two reads of the run row.
IDLE_S="${IDLE_S:-5}"
POLL_S="${POLL_S:-3}"
# The shortest message text worth looking for in the logs: shorter matches too much by chance.
NEEDLE_MIN=8
WORK="$(mktemp -d)"
SAMPLER=""

# shellcheck disable=SC2329 # called by the EXIT trap
cleanup() {
  if [[ -n "$SAMPLER" ]]; then kill "$SAMPLER" 2>/dev/null || true; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die() {
  printf 'agents_real_run_jetson.sh: %s\n' "$*" >&2
  exit 1
}

[[ "$JOB" =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || die "usage: [REAL_RUN=1] $0 <job-id>"
[[ "$OWNER" =~ ^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$ ]] || die "OWNER is not an email address"
[[ "$RAW_LABEL" =~ ^[A-Za-z0-9_-]*$ ]] || die "LABEL holds letters, digits, _ and - only"
[[ "$IDLE_S" =~ ^[0-9]+$ && "$POLL_S" =~ ^[0-9]+$ ]] || die "IDLE_S and POLL_S are whole seconds"
# One key per run, the name of its files: the kind, suffixed by its index when the kind repeats.
KEYS=()
seen=""
for kind in $RUNS; do
  [[ "$kind" == triage || "$kind" == chat ]] || die "RUNS holds triage and chat only"
  n=1
  for earlier in $seen; do
    if [[ "$earlier" == "$kind" ]]; then n=$((n + 1)); fi
  done
  seen="$seen $kind"
  if ((n == 1)); then KEYS+=("$kind"); else KEYS+=("$kind-$n"); fi
  if [[ "$kind" == chat && -z "${MESSAGE//[[:space:]]/}" ]]; then die "MESSAGE is empty"; fi
done
((${#KEYS[@]} >= 1)) || die "RUNS is empty"
((${#KEYS[@]} <= 2)) || die "at most two runs per call"
command -v jq >/dev/null || die "jq is needed"

# Where this call writes: a dry run only under the temporary directory, a real run in the evidence
# folder, and only files that do not exist yet. Checked here, before any connection to the Jetson.
if [[ "$REAL" == "1" ]]; then OUT="$EVIDENCE"; else OUT="$WORK"; fi
LOG="$OUT/run$LABEL.log"
METRICS="$OUT/metrics$LABEL.json"
samples_of() { printf '%s/memory%s-%s.tsv' "$OUT" "$LABEL" "$1"; }
if [[ "$REAL" == "1" ]]; then
  existing=""
  for path in "$LOG" "$METRICS"; do
    if [[ -e "$path" ]]; then existing="$existing ${path#"$ROOT"/}"; fi
  done
  for key in "${KEYS[@]}"; do
    path="$(samples_of "$key")"
    if [[ -e "$path" ]]; then existing="$existing ${path#"$ROOT"/}"; fi
  done
  [[ -z "$existing" ]] || die "refused, nothing was started: evidence of an earlier run exists and would be overwritten:$existing. Give this run a new LABEL."
fi

remote() { # a command in the stack's directory on the Jetson
  ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && $1"
}

sql() { # the SQL on stdin in the Jetson's database, unaligned, fields separated by |
  # shellcheck disable=SC2016 # $POSTGRES_* expand in the db container
  ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && docker compose -f compose.yml exec -T db sh -c" \
    "'psql -X -q -v ON_ERROR_STOP=1 -tA -F \"|\" -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"'"
}

fixed() { # what must not change: the owner's jobs and profile, one field per line
  sql <<SQL | tr '|' '\n'
with me as (select id from users where email = '$OWNER'),
     j as (select * from jobs where user_id = (select id from me))
select 'users=' || (select count(*) from users),
       'owner_role=' || coalesce((select role::text from users where email = '$OWNER'), 'none'),
       'jobs=' || (select count(*) from j),
       'per_status=' || coalesce((select string_agg(status::text || ':' || n, ',' order by status)
                                  from (select status, count(*) n from j group by status) s), 'none'),
       'fit_sum=' || coalesce((select sum(fit) from j), 0),
       'max_updated_at=' || coalesce((select max(updated_at)::text from j), 'none'),
       'rows_md5=' || coalesce((select md5(string_agg(md5(j::text), ',' order by id)) from j), 'none'),
       'job_events=' || (select count(*) || ',max_id:' || coalesce(max(id), 0) from job_events
                         where user_id = (select id from me)),
       'files=' || (select count(*) from job_files f join j on j.id = f.job_id),
       'sweeps=' || (select count(*) from sweeps where user_id = (select id from me)),
       'profile_revision=' || coalesce((select revision::text from profiles where user_id = (select id from me)), 'none'),
       'profile_md5=' || coalesce((select md5(sections::text || updated_at::text) from profiles
                                   where user_id = (select id from me)), 'none'),
       'credential=' || coalesce((select kind::text || ',updated_at:' || updated_at from model_credentials
                                  where user_id = (select id from me)), 'none');
SQL
}

agents() { # what the runs add: the agent tables (every user, then the owner), one field per line
  sql <<SQL | tr '|' '\n'
with me as (select id from users where email = '$OWNER')
select 'agent_runs=' || (select count(*) from agent_runs),
       'agent_runs_owner=' || (select count(*) from agent_runs where user_id = (select id from me)),
       'agent_runs_active=' || (select count(*) from agent_runs where status in ('queued', 'running')),
       'agent_events=' || (select count(*) from agent_events),
       'proposals=' || (select count(*) from proposals),
       'proposals_owner=' || coalesce((select string_agg(status::text || ':' || n, ',' order by status)
                                       from (select status, count(*) n from proposals
                                             where user_id = (select id from me) group by status) s), 'none'),
       'proposals_decided=' || (select count(*) from proposals where decided_at is not null or status <> 'pending'),
       'agent_threads=' || (select count(*) from agent_threads),
       'agent_messages=' || (select count(*) from agent_messages),
       'credential_verified=' || coalesce((select (verified_at is not null)::text from model_credentials
                                           where user_id = (select id from me)), 'none');
SQL
}

md5_of() { # the md5 of the text $1, on macOS and Linux
  if command -v md5sum >/dev/null; then printf '%s' "$1" | md5sum | cut -d' ' -f1; else printf '%s' "$1" | md5 -q; fi
}

field() { # field <name> <file>
  sed -n "s/^$1=//p" "$2"
}

sampler() { # every second on the Jetson, to the file $1: epoch, worker bytes, api bytes, host
  # available MiB, Claude Code processes, the sum and the highest of their RSS in KiB
  ssh -o BatchMode=yes "$HOST" "bash -s -- $(printf '%q' "$RDIR")" >"$1" <<'REMOTE' &
cd "$1" || exit 1
w="/sys/fs/cgroup/system.slice/docker-$(docker compose -f compose.yml ps -q worker).scope/memory.current"
a="/sys/fs/cgroup/system.slice/docker-$(docker compose -f compose.yml ps -q api).scope/memory.current"
while :; do
  claude="$(ps -eo rss=,comm=,args= | awk '/_bundled\/claude|^ *[0-9]+ claude / {n++; s += $1; if ($1 > m) m = $1} END {print n + 0 "\t" s + 0 "\t" m + 0}')"
  printf '%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "$(cat "$w")" "$(cat "$a")" \
    "$(awk '/^MemAvailable/ {print int($2 / 1024)}' /proc/meminfo)" "$claude" || exit 0
  sleep 1
done
REMOTE
  SAMPLER=$!
}

stop_sampler() {
  [[ -n "$SAMPLER" ]] || return 0
  kill "$SAMPLER" 2>/dev/null || true
  wait "$SAMPLER" 2>/dev/null || true
  SAMPLER=""
}

memory_json() { # the summary of the samples file $1 as JSON
  awk -F'\t' 'NF == 7 {
      n++; w = $2 / 1048576; a = $3 / 1048576
      if (n == 1 || w < wmin) wmin = w
      if (w > wmax) wmax = w
      if (a > amax) amax = a
      if (n == 1 || $4 < hmin) hmin = $4
      if ($4 > hmax) hmax = $4
      if ($5 > cmax) cmax = $5
      if ($6 > csum) csum = $6
      if ($7 > cone) cone = $7
      if (n == 1) first = w
    } END {
      printf "{\"samples\": %d, \"interval_s\": 1, \"worker_idle_mib\": %.1f, \"worker_min_mib\": %.1f, \"worker_peak_mib\": %.1f, \"worker_limit_mib\": 1536, \"api_peak_mib\": %.1f, \"host_available_min_mib\": %d, \"host_available_max_mib\": %d, \"claude_processes_max\": %d, \"claude_rss_sum_peak_mib\": %.1f, \"claude_rss_one_peak_mib\": %.1f}\n",
        n, first, wmin, wmax, amax, hmin, hmax, cmax, csum / 1024, cone / 1024
    }' "$1"
}

start() { # start <triage|chat>: prints one line of JSON {http, run_id, ...}; no session, no HTTP
  ssh -o BatchMode=yes "$HOST" "cd $(printf '%q' "$RDIR") && docker compose -f compose.yml exec -T api python - $1 $OWNER $JOB $(printf '%s' "$MESSAGE" | base64 | tr -d '\n')" <<'PY'
"""The operator's stand-in for POST /api/agents/runs and POST /api/agents/threads/{id}/messages:
the same functions, in the same order, with the same transactions and the same enqueue."""
import asyncio
import base64
import json
import sys
import uuid

import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app import queue as queues
from app.agents import chat_router, definitions, runs, runs_router
from app.agents.schemas import MessageIn
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.identity import service as identity_service
from app.identity.models import User
from app.main import credentials_keyring

action, email, job_id = sys.argv[1], sys.argv[2], uuid.UUID(sys.argv[3])
text = base64.b64decode(sys.argv[4]).decode()
settings = get_settings()
engine = build_engine(settings)
sessions = build_sessionmaker(engine)
out: dict[str, object] = {}


def create() -> uuid.UUID | None:
    with sessions() as db:
        user_id = db.scalar(sa.select(User.id).where(User.email == email))
    if user_id is None:
        out.update(http=404, detail="no such user")
        return None
    if credentials_keyring(settings) is None:
        out.update(http=503, detail=runs_router.NOT_CONFIGURED)
        return None
    now = identity_service.utcnow
    if action == "triage":
        agent = definitions.get("triage", settings.app_env)
        data = agent.validate_input({})
        started = runs_router._create(sessions, user_id, agent, job_id, data, now())
        return started.id
    opened = chat_router._open(sessions, user_id, job_id, now())
    if opened is None:
        out.update(http=404, detail=chat_router.JOB_NOT_FOUND)
        return None
    thread, created = opened
    out.update(thread_id=str(thread.id), thread_created=created)
    body = MessageIn(text=text)
    sent = chat_router._send(sessions, user_id, thread.id, body.text.strip(), now())
    if isinstance(sent, JSONResponse):
        out.update(http=sent.status_code, **json.loads(sent.body))
        return None
    out.update(message_id=sent.message.id)
    return sent.run.id


async def enqueue(run_id: uuid.UUID) -> None:
    queue = queues.Queue(settings.redis_url.get_secret_value())
    try:
        queued = await queue.enqueue(
            "run_agent", str(run_id), queue=queues.AGENTS, job_id=queues.agent_run_job_id(run_id)
        )
        out.update(http=202, run_id=str(run_id), enqueued=queued)
    except queues.QueueUnavailableError:
        runs_router._fail_queued(sessions, run_id, identity_service.utcnow())
        out.update(http=503, run_id=str(run_id), detail=runs.QUEUE_DOWN)
    finally:
        await queue.close()


try:
    made = create()
    if made is not None:
        asyncio.run(enqueue(made))
except HTTPException as exc:
    out.update(http=exc.status_code, detail=str(exc.detail))
except Exception as exc:  # the class name only: a message could quote the input or a statement
    out.update(http=500, error_class=type(exc).__name__)
finally:
    engine.dispose()
print(json.dumps(out))
PY
}

run_metrics() { # one JSON object for the run $1: numbers, names and ids only, never a text
  sql <<SQL
with r as (select * from agent_runs where id = '$1'),
     e as (select * from agent_events where run_id = '$1'),
     u as (select data from e where kind = 'usage' order by seq desc limit 1)
select jsonb_build_object(
  'run_id', r.id, 'agent', r.agent, 'job_id', r.job_id, 'status', r.status, 'error', r.error,
  'model', r.model, 'created_at', r.created_at, 'started_at', r.started_at, 'finished_at', r.finished_at,
  'queued_s', round(extract(epoch from r.started_at - r.created_at)::numeric, 3),
  'duration_s', round(extract(epoch from r.finished_at - r.started_at)::numeric, 3),
  'input_tokens_total', r.input_tokens, 'output_tokens', r.output_tokens, 'cost_usd', r.cost_usd,
  'usage', (select data from u),
  'output_text_chars', length(r.output ->> 'text'),
  'events', (select count(*) from e),
  'event_kinds', (select jsonb_object_agg(kind, n) from (select kind, count(*) n from e group by kind) k),
  'event_seconds_distinct', (select count(distinct date_trunc('second', at)) from e),
  'tool_calls', coalesce((select jsonb_object_agg(name, n) from (select data ->> 'name' as name, count(*) n
                          from e where kind = 'tool_call' group by 1) t), '{}'::jsonb),
  'tool_results_error', (select count(*) from e where kind = 'tool_result' and (data ->> 'is_error')::boolean),
  'tools_denied', (select count(*) from e where kind = 'tool_denied'),
  'thinking_events_with_content', (select count(*) from e where kind = 'thinking' and data <> '{}'::jsonb),
  'init_tools', (select data -> 'tools' from e where kind = 'init' limit 1),
  'proposals', coalesce((select jsonb_agg(jsonb_build_object('kind', kind, 'target', target, 'status', status,
                         'decided', decided_at is not null, 'payload_chars', length(payload::text)) order by created_at)
                         from proposals where run_id = '$1'), '[]'::jsonb),
  'messages', coalesce((select jsonb_agg(jsonb_build_object('role', role, 'chars', length(content ->> 'text'),
                        'md5', md5(content ->> 'text'),
                        'prompt_escapes', (length(content ->> 'text') - length(replace(replace(replace(
                           content ->> 'text', '&amp;', ''), '&lt;', ''), '<message', ''))) > 0) order by id)
                        from agent_messages where run_id = '$1'), '[]'::jsonb)
) from r;
SQL
}

one_run() { # one_run <triage|chat> <key>
  local kind="$1" key="$2" samples started run status events waited=0 since rc=0 http
  samples="$(samples_of "$key")"
  step "$kind run"
  since="$(remote 'date -u +%Y-%m-%dT%H:%M:%SZ')"
  sampler "$samples"
  sleep "$IDLE_S" # the idle baseline
  if [[ "$REAL" != "1" ]]; then
    sleep "$POLL_S"
    stop_sampler
    info "dry run: nothing started (REAL_RUN=1 starts it)"
    info "memory: $(memory_json "$samples")"
    checks 00000000-0000-0000-0000-000000000000 "$since"
    return 0
  fi
  # The remote Python's stderr stays out of the evidence: a traceback could quote the message.
  started="$(start "$kind" 2>"$WORK/start.stderr" | tail -n 1)" || rc=$?
  info "start: exit $rc, $(wc -l <"$WORK/start.stderr" | tr -d ' ') lines on its stderr (not printed)"
  http="$(jq -r '.http // empty' <<<"$started" 2>/dev/null || true)"
  if [[ -z "$http" ]]; then
    # No answer is not a refusal: the connection may have dropped after the run was queued.
    stop_sampler
    run="$(sql <<<"select id from agent_runs where job_id = '$JOB' and created_at >= '$since' order by created_at desc limit 1" 2>/dev/null || true)"
    info "FAIL: unknown, check the run row: the start gave no answer (exit $rc), so a run may be queued or running. Run id: ${run:-not known}. Do not start another one before reading it."
    return 1
  fi
  info "started through the app's own service functions: $(jq -c 'del(.detail)' <<<"$started")"
  if [[ "$http" != "202" ]]; then
    stop_sampler
    info "FAIL: refused: $(jq -r '.detail // .error_class // "no detail"' <<<"$started")"
    return 1
  fi
  run="$(jq -r .run_id <<<"$started")"
  while :; do
    status="" events=""
    IFS='|' read -r status events < <(sql <<<"select status, (select count(*) from agent_events where run_id = '$run') from agent_runs where id = '$run'")
    info "t+${waited}s status=${status:-unread} events=${events:-} scratch_dirs=$(remote 'docker compose -f compose.yml exec -T worker sh -c "ls -A /tmp | wc -l"' | tr -d ' \r')"
    [[ -z "$status" || "$status" == queued || "$status" == running ]] || break
    if ((waited >= WAIT_S)); then
      info "FAIL: still $status after ${WAIT_S}s; left as it is (the worker's own limits end it)"
      break
    fi
    sleep "$POLL_S"
    waited=$((waited + POLL_S))
  done
  sleep "$POLL_S"
  stop_sampler
  run_metrics "$run" >"$WORK/$key.run.json"
  memory_json "$samples" >"$WORK/$key.memory.json"
  jq -n --slurpfile run "$WORK/$key.run.json" --slurpfile memory "$WORK/$key.memory.json" \
    '$run[0] + {memory: $memory[0]}' >"$WORK/$key.json"
  jq . "$WORK/$key.json" | sed 's/^/   /'
  if [[ "$kind" == chat ]]; then
    info "message sent: ${#MESSAGE} characters, md5 $(md5_of "$MESSAGE") (the stored user message must show the same)"
  fi
  checks "$run" "$since"
  DONE=$((DONE + 1))
  [[ "$status" == "done" ]]
}

count_in_logs() { # how many log lines hold the text $1, whatever its case; the text is never printed
  grep -c -i -F -e "$1" "$WORK/logs" || true
}

checks() { # checks <run-id> <since>: what the run left behind, matches counted and never printed
  local run="$1" since="$2" fragment left transcripts hits needle
  left="$(remote 'docker compose -f compose.yml exec -T worker sh -c "ls -A /tmp | wc -l"' | tr -d ' \r')"
  info "entries left in the worker's /tmp (scratch directories live there): $left"
  transcripts="$(remote 'docker compose -f compose.yml exec -T worker sh -c "find / /tmp -xdev \\( -name \"*.jsonl\" -o -name .claude -o -name .claude.json \\) -not -path \"/proc/*\" -not -path \"*/site-packages/*\" 2>/dev/null | wc -l"' | tr -d ' \r')"
  info "session transcripts or Claude Code config left in the worker (*.jsonl, .claude, .claude.json): $transcripts"
  [[ "$left" == "0" && "$transcripts" == "0" ]] || CHECKS_OK=0
  remote "docker compose -f compose.yml logs --no-color --no-log-prefix --since $since worker api" >"$WORK/logs" 2>/dev/null
  info "log lines of worker and api since $since: $(wc -l <"$WORK/logs" | tr -d ' '), naming the run: $(grep -c -F "$run" "$WORK/logs" || true)"
  # Per logger: the count, the two fixed event names of the runner and the field names. Any other
  # message is counted and not printed, since a message can embed an error text.
  grep -F "$run" "$WORK/logs" | jq -R -r 'fromjson?
      | (.msg | tostring) as $m
      | "\(.logger): \(if $m == "agent run started" or $m == "agent run finished" then $m else "(message not printed)" end) [\(keys_unsorted | join(","))]"' |
    sort | uniq -c | sed 's/^/     /'
  # The message text: 20 characters from its middle, or all of it when it is short. Never printed.
  if [[ -n "${FRAGMENT:-}" ]]; then
    fragment="$FRAGMENT"
  elif ((${#MESSAGE} >= 33)); then
    fragment="${MESSAGE:13:20}"
  else
    fragment="$MESSAGE"
  fi
  if ((${#fragment} >= NEEDLE_MIN)); then
    hits="$(count_in_logs "$fragment")"
    info "log lines holding a fragment of the message (${#fragment} characters, not printed): $hits"
    [[ "$hits" == "0" ]] || CHECKS_OK=0
  else
    info "log lines holding the message: not checked, the message is shorter than $NEEDLE_MIN characters"
  fi
  for needle in "CLAUDE_CODE_OAUTH" "ANTHROPIC_API" "CREDENTIALS_KEY" "Job id:"; do
    hits="$(count_in_logs "$needle")"
    info "log lines holding \"$needle\": $hits"
    [[ "$hits" == "0" ]] || CHECKS_OK=0
  done
  hits="$(count_in_logs "sk-ant-")"
  info "log lines holding a token shaped value (prefix sk-ant-): $hits"
  [[ "$hits" == "0" ]] || CHECKS_OK=0
  rm -f "$WORK/logs"
  info "worker OOM kills (memory.events): $(oom_kills)"
}

oom_kills() {
  # shellcheck disable=SC2016 # expands on the Jetson
  remote 'cat "/sys/fs/cgroup/system.slice/docker-$(docker compose -f compose.yml ps -q worker).scope/memory.events"' |
    awk '$1 == "oom_kill" {print $2}'
}

if [[ "$REAL" == "1" ]]; then
  mkdir -p "$EVIDENCE"
  exec > >(tee "$LOG") 2>&1
else
  exec 2>&1 # a dry run keeps no log: the terminal is its only output
fi
printf 'tests/agents_real_run_jetson.sh, %s, %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$([[ "$REAL" == "1" ]] && echo "real runs: $RUNS" || echo "dry run, nothing written outside a temporary directory")"

step "before"
health="$(remote "docker compose -f compose.yml ps --format '{{.Service}}:{{.Health}}'" | sort | tr '\n' ' ')"
info "services: ${health% }"
for service in api worker db redis caddy; do
  [[ " $health" == *" $service:healthy "* ]] || die "$service is not healthy"
done
info "deployed commit: $(remote 'cat .deployed-commit' | cut -c1-12)"
hook="$(remote 'docker compose -f compose.yml exec -T worker grep -c lock_first /app/app/agents/runner.py' | tr -d '\r')"
info "worker code: chat lock hook of issue #27 present (lock_first, $hook lines)"
[[ "$hook" -ge 1 ]] || die "the worker's runner.py has no chat lock hook: deploy main first"
info "host: $(remote 'free -m' | awk '/^Mem:/ {print "total " $2 " MiB, used " $3 ", available " $7} /^Swap:/ {print ", swap used " $3 " MiB"}' | tr -d '\n')"
info "containers: $(remote "docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'" | grep -E 'worker|api' | tr '\n' ';')"
info "worker OOM kills (memory.events): $(oom_kills)"
fixed >"$WORK/fixed.before"
agents >"$WORK/agents.before"
sed 's/^/   /' "$WORK/fixed.before" "$WORK/agents.before"
role="$(field owner_role "$WORK/fixed.before")"
credential="$(field credential "$WORK/fixed.before")"
[[ "$credential" != none ]] || die "$OWNER has no model credential"
# Never a run with another user's Anthropic API key: the account is an admin and the credential
# its own subscription, whatever OWNER says.
[[ "$role" == "$OWNER_ROLE" && "${credential%%,*}" == "$OWNER_CREDENTIAL" ]] ||
  die "refused, nothing was started: $OWNER has the role ${role:-none} and a credential of kind ${credential%%,*}; runs are started only for an account with the role $OWNER_ROLE and a credential of kind $OWNER_CREDENTIAL"
[[ "$(field agent_runs_active "$WORK/agents.before")" == 0 ]] || die "a run is queued or running: wait for it"
job="$(sql <<SQL
select status, fit, length(coalesce(posting_text, '')),
       (title || ' ' || company || ' ' || coalesce(posting_text, '') || ' ' || coalesce(description, '')) ~* '$FORBIDDEN',
       (select count(*) from agent_runs where job_id = j.id), (select count(*) from proposals where target_id = j.id),
       (select count(*) from agent_threads where job_id = j.id)
from jobs j where id = '$JOB' and user_id = (select id from users where email = '$OWNER');
SQL
)"
[[ -n "$job" ]] || die "no job $JOB for $OWNER"
IFS='|' read -r jstatus fit posting forbidden jruns jproposals jthreads <<<"$job"
info "job $JOB (status $jstatus, fit $fit, posting $posting characters)"
info "on this job before: runs=$jruns proposals=$jproposals threads=$jthreads"
[[ "$forbidden" == f ]] || die "this job mentions security: never a cybersecurity role"
[[ "$posting" -ge 500 ]] || die "this job has no posting text stored"

DONE=0
CHECKS_OK=1
status=0
index=0
for kind in $RUNS; do
  one_run "$kind" "${KEYS[$index]}" || {
    status=1
    break # never a second run after a failed one: read its error first
  }
  index=$((index + 1))
done

step "after: the owner's jobs and profile unchanged, only agent rows added"
fixed >"$WORK/fixed.after"
agents >"$WORK/agents.after"
if diff -u "$WORK/fixed.before" "$WORK/fixed.after"; then
  info "unchanged before and after ($(wc -l <"$WORK/fixed.after" | tr -d ' ') fields): jobs (md5 over every column of every row), profile (md5 of its sections and date), credential row (kind and date), owner role; job events (count and highest id only); files and sweeps (count only)"
else
  printf '   FAIL: the owner'"'"'s data changed\n'
  status=1
fi
info "agent tables, before then after:"
paste -d' ' "$WORK/agents.before" "$WORK/agents.after" | awk '{split($2, a, "="); print "     " $1 " -> " a[2]}'
added=$(($(field agent_runs "$WORK/agents.after") - $(field agent_runs "$WORK/agents.before")))
info "runs added: $added (started here: $DONE)"
[[ "$added" == "$DONE" ]] || status=1
decided=$(($(field proposals_decided "$WORK/agents.after") - $(field proposals_decided "$WORK/agents.before")))
info "proposals decided (accepted, rejected or other) during this call: $decided (count only)"
if [[ "$decided" != "0" ]]; then
  info "FAIL: a proposal was decided while the script ran"
  status=1
fi
info "host: $(remote 'free -m' | awk '/^Mem:/ {print "available " $7 " MiB"}')"
((CHECKS_OK == 1)) || status=1
if [[ "$REAL" == "1" ]]; then
  files=()
  for key in "${KEYS[@]}"; do
    if [[ -f "$WORK/$key.json" ]]; then files+=("$WORK/$key.json"); fi
  done
  if ((${#files[@]})); then
    jq -n --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg host "$HOST" --arg commit "$(remote 'cat .deployed-commit')" \
      --arg job "$JOB" '{at: $at, host: $host, deployed_commit: $commit, job_id: $job, runs: [inputs]}' "${files[@]}" >"$METRICS"
    info "metrics: ${METRICS#"$ROOT"/}"
  fi
fi
if ((status == 0)); then info "PASS"; else info "FAIL"; fi
exit "$status"
