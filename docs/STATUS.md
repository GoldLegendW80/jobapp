# Status

The state of the build, for whoever picks it up next (a person or a new orchestrator session).
Updated by the orchestrator after every shipped batch; the live truth is always
`gh pr list --state open`, `gh issue list --state open` and the board (project 1 of
GoldLegendW80, "Jobapp roadmap"). How work flows: [agents/orchestration.md](agents/orchestration.md).
What the owner decided: [decisions.md](decisions.md).

Last updated: 2026-10-15.

## In production

`main` at `da518af` (gate `LOCAL CHECK: PASS`, tree `15a5cf7`), deployed by `ops/ship.sh` on
2026-10-15: 8 services running and healthy, alembic at `0018` (head), about 2.3 GiB of 7.6 used,
no error line. The `browser` service is built but not started yet: it waits for the owner's step
below.

Built and merged, by module (task ids of `tasks/<module>/todo.md`):

| Module | Done |
|---|---|
| platform | Compose stack, deploy, Tailscale Funnel URL, backups (7 days), the batch gate `ops/local-check.sh`, `ops/deploy-checked.sh`, `ops/ship.sh`, `ops/agent-test.sh`, request body limits, flaky test fixes (W1, W2, W3, first slice of W3b) |
| identity | invite only accounts, sessions, throttling, administration |
| profile | profile editing, export and import, year only dates (P1 to P7) |
| jobs | postings, board sweeps (Ashby, Greenhouse, Lever), scoring, map, edit, J8 lock guard |
| agents | encrypted credentials, runtime and live stream, triage, chat, real runs (A1 to A5) |
| ingestion | G0 to G16 (documents to profile proposals, parser service, transcription, review, deletion); Checkpoint G (#61) still open |
| mail | M1 fake Gmail and Google client, M2 public pages, M3 parsing, M4 Gmail connection, M9 fact check rules; the rest is paused by the owner |
| documents | D1 to D12, D14, D19: renderer, checks, templates (first 8), storage, render queue, CV and letter writers, the CV tab, the documents of a posting |
| browser | B1 to B6: measurement, the browser container, leases, the tool layer, the gate click, the host firewall and the owner's reboot and build rules |
| apply | A1 to A7: fake ATS forms, tables and refusals, answers from the profile, Greenhouse and Ashby adapters, the fill run, the gate and submission, the Apply section of a posting |

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

## Open pull requests

| PR | What | State | Next |
|---|---|---|---|
| #323 | Apply A5 follow ups: topic anchor on free text, the `apply_answer` agent, its journey (#317 items 1 to 3) | REQUEST CHANGES on `6aafd82`: the agent sees the whole `answers` section (EEO, salary and more) and `judge` keeps personal statements; questions about leaving a job are still sent to the agent | a fresh fix agent with the review's items, then a Sonnet re-check |
| #325 | This harness: CLAUDE.md, agent docs, `.claude/` config, `ops/card.sh`, `ops/dev-setup.sh` | in review | first review (docs and scripts: Sonnet is enough) |

## Next steps, in order

1. **Finish the open pull requests** above, then ship them (`ops/ship.sh --plan --approved`,
   then `ops/ship.sh <prs>`).
2. **Follow ups on the MVP path**, highest first:
   - #311 Browser B6b: prove the egress refusals and Chromium's sandbox from inside the browser
     container (Mac stack and Jetson), right after the owner's firewall step;
   - #317 the rest of the A5 follow ups (item 4), #296 A2, #282 A1 stub bounds;
   - #314 D12, #306 D8, #304 D14, #299 D9, #290 D6 and D7, #291 B3;
   - #264 software WebGL only when a site needs it.
3. **Platform follow ups:** #293 gate under 7 minutes (rest of W3b), #289 and #287 (ship.sh and
   registries), #146 local check, #104 flaky tests.
4. **Apply after the MVP:** A8 Lever and the generic strategy (#245), A9 Apply myself (#246).
5. **Full auto:** A10 failures, retries and "needs you" (#247), A11 company caps, pacing and the
   Applications settings (#248), A12 the review queue after a Like (#249), A13 Full auto (#250).
6. Later, unchanged order: documents D13, D15 to D18, D20 to D26; browser B7 to B12; apply A14 to
   A18; ingestion Checkpoint G (#61) and the older module follow ups; mail M5 onward when the
   owner lifts the pause.
