# Status

The state of the build, for whoever picks it up next (a person or a new orchestrator session).
Updated by the orchestrator after every shipped batch; the live truth is always
`gh pr list --state open`, `gh issue list --state open` and the board (project 1 of
GoldLegendW80, "Jobapp roadmap"). How work flows: [agents/orchestration.md](agents/orchestration.md).
What the owner decided (latest: 2026-10-21): [decisions.md](decisions.md).

Last updated: 2026-10-15.

## In production

`main` at `d2b6d94` (the merge of #326, tree `d8601e9`), deployed on 2026-10-15 by the deploy
workflow: run 38718620535, dispatched at 10:12 UTC, success at 10:14 UTC. Before it the same day:
#325 (`af94c78`, run 38715024235, 09:43 UTC) and #323 (`63deb82`, run 38712414158). Alembic head
on `main`: `0018`, unchanged by #323, #325 and #326. The
Jetson figures here date from the check of `ops/ship.sh` on 2026-10-15 before #323 (`main` at
`da518af`): 8 services running and healthy, alembic at `0018`, about 2.3 GiB of 7.6 used, no error
line; later checks are not recorded here. The `browser` service is built but not started yet: it
waits for the owner's step below.

Built and merged, by module (task ids of `tasks/<module>/todo.md`):

| Module | Done |
|---|---|
| platform | Compose stack, deploy, Tailscale Funnel URL, backups (7 days), the batch gate `ops/local-check.sh`, `ops/deploy-checked.sh`, `ops/ship.sh`, `ops/agent-test.sh`, request body limits, flaky test fixes (W1, W2, W3, first slice of W3b), the harness versioned (#325 for #324: CLAUDE.md, `docs/agents/`, `.claude/` config, `ops/card.sh`, `ops/dev-setup.sh`), the harness permissions (#326: `git -C` forms, `gh api` reads, `.env` shell denies) |
| identity | invite only accounts, sessions, throttling, administration |
| profile | profile editing, export and import, year only dates (P1 to P7) |
| jobs | postings, board sweeps (Ashby, Greenhouse, Lever), scoring, map, edit, J8 lock guard |
| agents | encrypted credentials, runtime and live stream, triage, chat, real runs (A1 to A5) |
| ingestion | G0 to G16 (documents to profile proposals, parser service, transcription, review, deletion); Checkpoint G (#61) still open |
| mail | M1 fake Gmail and Google client, M2 public pages, M3 parsing, M4 Gmail connection, M9 fact check rules; the rest is paused by the owner |
| documents | D1 to D12, D14, D19: renderer, checks, templates (first 8), storage, render queue, CV and letter writers, the CV tab, the documents of a posting |
| browser | B1 to B6: measurement, the browser container, leases, the tool layer, the gate click, the host firewall and the owner's reboot and build rules |
| apply | A1 to A7: fake ATS forms, tables and refusals, answers from the profile, Greenhouse and Ashby adapters, the fill run, the gate and submission, the Apply section of a posting; the A5 follow ups of #323: the topic anchor on A3's free text route, the `apply_answer` agent and its journey |

The MVP (one click apply, see [decisions.md](decisions.md)) is code complete: its full journey
(documents, fill on fake Greenhouse and Ashby, the person answers, approve, gate, submit) passes
in the gate. It goes live for real postings once the browser runs on the Jetson.

## The owner's pending action

The browser starts only after its host firewall is installed on the Jetson, which needs sudo, so
it is the owner's to run (once):

```bash
ssh jetson
cd ~/jobapp && sudo bash ops/browser-egress.sh install jobapp_egress
bash ops/browser-egress.sh host-check
```

Then a deploy (the next `ops/ship.sh`, or `ops/deploy-checked.sh` on the current `main`) applies
and checks the rules and starts the browser. Older owner steps for paused work stay in their
issues (mail: #63 Google console, #187 the OAuth redirect setting on the Jetson).

## Which machine ships

Until #334 merges, the last step of `ops/ship.sh` checks the Jetson over ssh, so the owner's main
Mac ships. #334 moves that check into the deploy workflow, after which any laptop whose gh account
can write to the repository will ship. The gate's PASS records live in `.local-check/` of the
machine that ran the gate (CONTRIBUTING.md, step 6), so that machine deploys the tree it checked.
A second machine says on the issue when it starts a builder, so the other does not start a
duplicate.

## Open pull requests

| PR | What | State | Next |
|---|---|---|---|
| #329 | This file after the ships of #323, #325 and #326 (closes #327) | round 3 reduces it to `docs/STATUS.md`; the APPROVE of 10:14 UTC was on `7bd3244`, an older head | review of the new head |
| #330 | Apply: the A2 follow ups (closes #296), built from the owner's second laptop | APPROVE on its head `d920775` at 10:38 UTC | ship |
| #331 | Apply A8, first slice: the Lever adapter (refs #245), built from the owner's second laptop | REQUEST CHANGES on `f11e518` at 10:22 UTC; round 2 pushed, head `411933c` (read at 10:40 UTC) | review of `411933c` |
| #333 | Platform: `ops/dev-setup.sh` accepts pnpm 10 over a 9.x pin (closes #328) | APPROVE on its head `a2abfc1` at 10:39 UTC (round 2) | ship |
| #334 | Platform: deploy and operate the Jetson from any laptop through GitHub, no ssh (the owner's decision of 2026-10-15): the Jetson check moves into the deploy workflow and a new `ops.yml` runs the operations; from the owner's main Mac | REQUEST CHANGES on its head `8fd6eab` at 10:20 UTC, its first review | fix round |

A8b, the generic strategy (#332), is being built from the owner's second laptop on branch
`apply/a8b-generic`, stacked on #331.

## Next steps, in order

1. **Ship #330 and #333, then review the rest** (#329, #331 and #334 on their new heads): a
   fresh reviewer for each, then `ops/ship.sh --plan --approved` and `ops/ship.sh <prs>` on the
   machine that ships (above).
2. **Follow ups on the MVP path**, highest first:
   - #311 Browser B6b: prove the egress refusals and Chromium's sandbox from inside the browser
     container (Mac stack and Jetson), right after the owner's firewall step;
   - #317 item 4 (the rest of the A5 follow ups), #282 A1 stub bounds;
   - #314 D12, #306 D8, #304 D14, #299 D9, #290 D6 and D7, #291 B3;
   - #264 software WebGL only when a site needs it.
3. **Platform follow ups:** #293 gate under 7 minutes (rest of W3b), #289 and #287 (ship.sh and
   registries), #146 local check, #104 flaky tests.
4. **Apply after the MVP:** the rest of A8 (#245): #331 is the first slice (Lever); the generic
   strategy is #332 (being built, above). Then A9 Apply myself (#246).
5. **Full auto:** A10 failures, retries and "needs you" (#247), A11 company caps, pacing and the
   Applications settings (#248), A12 the review queue after a Like (#249), A13 Full auto (#250).
6. Later, unchanged order: documents D13, D15 to D18, D20 to D26; browser B7 to B12; apply A14 to
   A18; ingestion Checkpoint G (#61) and the older module follow ups; mail M5 onward when the
   owner lifts the pause.
