# Measure the document writers

How to run the CV writer and the cover letter writer several times on invented people and
postings, and read how much room their prompts have. A tool for the owner's laptop, not part of
the app: `api/app/documents/measure.py`.

## What it measures, and why

The study of harness2 (step 3, owner decision 2026-10-29) asks one question before anyone builds
an adapter: is there anything left to tune in `api/app/documents/prompts/cv.md` and `letter.md`?
The writers already submit through a deterministic checker (`documents.checks.run`) and a
correction pass, so the headroom may be small. This tool measures it.

For each fixture pair (a profile with its base CV, and a posting, all invented, in
`api/tests/documents/fixtures`) and each repetition it builds a fresh person in a throwaway
database (`e2e-measure-*@example.org`), starts the app's own generation and runs it through the
app's own agent runtime with the real Claude Agent SDK: the same prompt, tools, limits, checks and
correction pass as a click in the app. Then it scores two outputs of each run with
`documents.checks.run`:

- **first**: the writer's first valid submit (a submit refused for its structure is not kept),
  after the code's dash fix and before the correction pass: the closest the tool gets to what the
  prompt alone produced.
- **final**: what is stored, after the correction pass and the removal by code.

| Figure | Meaning |
|---|---|
| `first_claims`, `first_separators`, `first_notes` | issues of the first output by level: a claim the profile and base CV do not state, a dash used as a separator, a soft length or an empty field. Counted after the code's dash fix, so `first_separators` stays 0 unless a dash could not be fixed |
| `final_claims`, `final_separators`, `final_notes` | the same for what is stored. A claim left here is shown to the person |
| `fixed_by_writer`, `fixed_by_code` | fields the writer changed in the correction pass, and corrections made by code (the dash fix of the stored submit, a claim cut, a fallback); a claim the code kept as written is not counted |
| `rounds` | submits of the run (`submit_cv` or `submit_letter` calls, those refused for their structure included): 1 is a clean first submit, 2 or more used the correction pass or followed a refused submit |
| `turns`, `tool_calls` | the run's own counts |
| `tokens`, `cost_usd`, `seconds` | input plus output tokens (cache included), the SDK's cost at API prices (billed to nobody on a subscription), run time |

A table of mean, min, max and spread (max minus min) of each figure is printed per pair and
writer, then once over all pairs. A run that did not finish is counted under its code (for
example `invalid_output`, `no_result`, `token_limit`) and left out of the figures. The JSON file
holds every sample: its figures, the issue counts by code, and the first and final texts (invented
data).

Declared open points: a letter written alone (`--writer letter`) finds no CV of the posting, a
state of its own in the app; `--writer both` writes the CV first and the letter from it, as the
app does. The person's writing rule is the dash rule on and no banned phrase. Only the checker's
issues are measured, not tailoring quality or style. The shortening pass after a render overflow
is not run: the tool queues no render (`NullQueue`).

## Safety

- The model credential is read from the environment of the command and nowhere else:
  `CLAUDE_CODE_OAUTH_TOKEN` (a Claude subscription token, free) or `ANTHROPIC_API_KEY` (billed).
  Without either the command stops at once with a sentence naming them. With both set, the
  subscription token wins. It is never printed, logged or written to the JSON; the app's runtime
  reads its credential from the database, so it is saved there encrypted with a key made in
  memory, in the throwaway database that is dropped when the command ends.
- The database is one the command makes itself on a Postgres on this machine (host `127.0.0.1`,
  `localhost` or `[::1]` only, with no `host`, `hostaddr`, `port` or `service` in the query of the URL),
  migrates, and drops at the end. It does not take its server from `DATABASE_URL`, only from
  `--database-url`, `MEASURE_DATABASE_URL` or `TEST_DATABASE_URL`. After connecting, it refuses to
  go on unless the connection ended on a loopback address or a Unix socket, which also catches
  `PGHOSTADDR`, `PGSERVICE` and a service file. A Postgres reached through an ssh tunnel or a port
  forward on `localhost` looks local and cannot be told apart: close every tunnel to the Jetson's
  database (or to any other machine) before you run the command, and unset `MEASURE_DATABASE_URL`
  if an earlier run left it in your shell, because it wins over the throwaway database that
  `ops/agent-test.sh` provides.
- Only the fixtures are read for data. The JSON file must be outside the repository.
- You type the token yourself: the commands below read it with `read -rs`, so it is neither
  echoed nor in the shell history.

## Run it on the Mac

The throwaway Postgres and Redis are the ones `ops/agent-test.sh` starts and removes. The opt-in
test `api/tests/documents/test_measure_live.py` runs the tool inside them; every other run of the
test folder skips it, and CI and the gate always do (they set `CI`).

A first, cheap run: one CV writer run on one pair.

```bash
cd ~/path/to/jobapp                          # a checkout of main, or a worktree
read -rs CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN   # paste the token, press Enter
MEASURE_LIVE=1 MEASURE_ARGS="--writer cv --runs 1 --pairs alex:brise" \
  ops/agent-test.sh --name measure tests/documents/test_measure_live.py -q
unset CLAUDE_CODE_OAUTH_TOKEN
```

The token comes from `claude setup-token` (run it yourself, in your own terminal). The table is
printed to the terminal and the JSON goes to `~/jobapp-measure/measure-<date>-<time>.json`.
Without `MEASURE_ARGS`, the test runs exactly this cheap first run, never the full measure.

The full measure, the default of the tool (5 pairs, 3 runs each, CV and letter):

```bash
read -rs CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN
MEASURE_LIVE=1 MEASURE_ARGS="--runs 3 --writer both --max-pairs 5 --json $HOME/jobapp-measure/full.json" \
  ops/agent-test.sh --name measure tests/documents/test_measure_live.py -q
unset CLAUDE_CODE_OAUTH_TOKEN
```

Options (`MEASURE_ARGS`, or the command line of `python -m app.documents.measure`):

| Option | Default | |
|---|---|---|
| `--runs N` | 3 | runs per pair and writer, 1 to 10 |
| `--writer cv\|letter\|both` | both | `both`: the CV, then the letter from it |
| `--pairs a:b,c:d` | the first 5 of 25 | `profile:posting` from the fixtures |
| `--max-pairs N` | 5 | how many of the default pairs |
| `--json PATH` | `~/jobapp-measure/measure-<time>.json` | outside the repository |
| `--database-url URL` | `MEASURE_DATABASE_URL`, then `TEST_DATABASE_URL` | a Postgres on this machine |

Fixture profiles with a base CV: alex, blake, casey, robin, sacha. Postings: brise (French),
brume, halo, lumen, polaris. The default pairs are alex:brise, blake:brume, casey:halo,
robin:lumen, sacha:polaris (blake has an empty profile, a hard case on purpose).

Without `ops/agent-test.sh`, on any Postgres of this machine you can throw away:

```bash
cd api
read -rs CLAUDE_CODE_OAUTH_TOKEN && export CLAUDE_CODE_OAUTH_TOKEN
MEASURE_DATABASE_URL=postgresql+psycopg://<user>:<password>@127.0.0.1:<port>/<any database> \
  uv run python -m app.documents.measure --runs 1 --writer cv --pairs alex:brise
unset CLAUDE_CODE_OAUTH_TOKEN
```

It creates a database named `measure_<id>` on that server and drops it at the end; the server's
other databases are not touched. Ctrl+C, a `kill` (SIGTERM) and a closed terminal (SIGHUP) drop it
too. `kill -9` (SIGKILL) and a power cut cannot, and nor can a second signal that comes while the
first one is dropping it. Under `ops/agent-test.sh` the throwaway container, and with it the
database, is removed when the script ends. On a server of your own, find a leftover and drop it:

```bash
psql -h 127.0.0.1 -p <port> -U <user> <any database> \
  -c "SELECT datname FROM pg_database WHERE datname LIKE 'measure\_%'"
psql -h 127.0.0.1 -p <port> -U <user> <any database> -c 'DROP DATABASE "measure_<id>" WITH (FORCE)'
```

## What it costs

The table is the app's own estimate (`api/app/documents/estimate.py`, computed on 2026-10-29), not
a measurement: the `tokens` and `cost_usd` of the first real run replace it. For the five default
pairs, the estimate of one pair (its CV and letter), at the prices of `app/ingestion/pricing.py`
read on 2026-10-13:

| | Tokens | Opus 5.5 | Sonnet 5.5 |
|---|---|---|---|
| Typical (3 turns each) | 61,000 to 64,200 | $0.22 to $0.23 | $0.11 to $0.12 |
| Worst (10 turns, 4 submits each) | 403,100 to 417,200 | $2.11 to $2.17 | $1.06 to $1.09 |

The writers use Opus on a subscription and Sonnet on an API key. The full measure is 15 pair runs
(5 pairs, 3 runs): about 0.95 million tokens when runs are typical, up to 6.2 million at the worst,
so $3.4 to $32 at Opus list prices. On the subscription nothing is billed, but every run counts
against the plan's weekly usage limit: start with the one run above, read its
`tokens` and `seconds`, and size the full measure from them. A run stops at its token ceiling
(twice the worst estimate: 806,300 to 834,500 tokens a pair, at most 12.4 million for the full
measure), at 10 turns and at 300 seconds (the app's settings), so a stuck run cannot spend
without bound.

## What the numbers mean for prompt tuning

A reading guide, a judgement and not a measured threshold. Three runs per pair is small: trust the
`all pairs` block more than one pair. The spread of a pair is the variation between its replicates;
the spread of the `all pairs` block also holds the differences between pairs.

- `first_claims` near 0 with a spread near 0: the prompt already produces claims the sources
  state. The correction pass has nothing to do and there is nothing to tune on facts.
- `first_claims` of 1 or more, or a spread of 2 or more: the writer invents or varies, and the
  prompt is the lever. Compare which codes: the JSON holds them per sample (`first_codes`).
- `final_claims` above 0 anywhere: a claim reaches the person after the correction pass and the
  removal by code. This is the strongest argument for tuning.
- `rounds` near 2: every generation pays a correction pass. The saving of a better prompt is the
  difference in `tokens` and `seconds` between the runs with 1 round and those with 2.
- A large spread of `tokens` or `turns` with the same pair: the procedure varies between
  replicates (re-reading sections, extra tool calls). Harness2 reads exactly that variance as its
  signal.
- Any run under `not finished`: a writer that fails is a bug or a prompt problem, and costs a
  full run.
- All of these near zero over all pairs: do not build the adapter. Stop at this tool, and run it
  again after a prompt change by hand to see whether it moved the numbers.

Reading it is the owner's: the tool prints figures and decides nothing.

## Tests

`api/tests/documents/test_measure.py` runs the tool on the recorded streams of
`api/app/agents/fake_streams/` (no model call, no network): the table and the JSON from scripted
outputs, the refusal without a credential, the credential found in no output, log, file or table,
and only fixture data read. `ops/agent-test.sh --name measure tests/documents/test_measure.py -n 2`.
