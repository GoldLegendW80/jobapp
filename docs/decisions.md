# Owner decisions

What the owner decided, by module, with the date. A task never decides these again; a change
needs the owner's word (asked with concrete options, see [agents/orchestration.md](agents/orchestration.md)).
The full wording, with the owner's own sentences, is in the "Decisions" section of each
`SPEC-<module>.md` and `tasks/<module>/todo.md`; this page is the index. "Planner default" marks a
choice made by the build that stands until the owner says otherwise.

## Standing rules (all modules)

- No dashes as separators in any outward text: no em dash, no en dash, no " - ". Hyphenated
  words are fine.
- Never apply to a cybersecurity role, whatever the fit score.
- Never send mail by itself: the person clicks Send (Full auto, when built, is the one opt in
  exception for forms and mails, with its guardrails, decided 2026-10-10).
- Nothing is submitted without the person's approval: in Full auto, the Like the person gives
  while Full auto is on is that approval (2026-10-10); no model ever clicks Submit; the one click
  path is the apply gate.
- Tests use only `e2e-*@example.org` accounts and never touch real user data.
- The owner types sudo, passwords and 2FA codes himself. Agents never read `.env` files or
  production credentials.
- Everything stays free: no paid service, domain or registry.
- The repository is public (2026-10-15): no personal data, secrets or private URLs in commits.

## Platform and workflow

| Date | Decision |
|---|---|
| 2026-10-08 | Self hosted on a Jetson Orin Nano (arm64) at home. React + Tailwind + shadcn, FastAPI + Postgres + Redis, Docker Compose. Invite only accounts for the owner and a few friends. |
| 2026-10-08 | Built by agents: an orchestrator plus one subagent per task, many tests, explicit edge cases, real Playwright journeys; keep going module after module. |
| 2026-10-09 | GitHub flow: one branch and one pull request per task, a Claude review subagent on every pull request, only the orchestrator merges, only `main` deploys. |
| 2026-10-10 | Public URL through Tailscale Funnel (a stable name, free), replacing the Cloudflare quick tunnel of the first plan. |
| 2026-10-10 | A kanban on GitHub Projects (project 1, "Jobapp roadmap"), updated on every task state change. |
| 2026-10-13 | No CI runners any more ("it takes too much time"). Gate = review plus one batch check on the Mac (`ops/local-check.sh`) on the tree the merged batch produces; deploy = the Jetson's own deploy workflow, dispatched by `ops/deploy-checked.sh` for a checked tree only. |
| 2026-10-13 | "Keep Opus": Opus builds M tasks and does every first review; Sonnet builds small work and does re-checks. Three to four agents at a time ("Sonnet for small work"). |
| 2026-10-14 | Faster, more accurate delivery: pull requests under about 1500 lines, a self review checklist, reviewers push mechanical fixups, `ops/ship.sh` (gate, merge, deploy, Jetson check in one command), `ops/agent-test.sh` so the gate can run beside agents. |
| 2026-10-14 | Priority: documents, browser and apply first. The MVP is ONE CLICK APPLY (see Apply); Full auto comes after it. |
| 2026-10-15 | The repository is public, at the owner's explicit request. |

## Agents and ingestion

| Date | Decision |
|---|---|
| 2026-10-09 | The owner runs on his Claude subscription; other users bring their own Anthropic API key (Anthropic only), stored encrypted. |
| 2026-10-10 | Model by credential: `claude-opus-5-5` on a subscription, `claude-sonnet-5-5` on an API key, chosen automatically. The start dialog shows a token range (and a cost range for API keys). |
| 2026-10-10 | Ingestion: a copyable laptop analysis prompt; paste or upload any document (20 MB per file); proposals the person accepts. A PDF without text is rendered and transcribed, not refused. Document text is kept until the person deletes it. |
| 2026-10-10 | Explicit consent the first time a run sends documents to Anthropic, recorded with the date and the version of the text. |
| 2026-10-10 | A run pauses at twice its high estimate; an explicit Continue raises the ceiling by one estimate. Applies to every user. |
| 2026-10-10 | Parsers run in their own `parser` service (own uid, no credentials, no network beyond what it needs). Backups keep 7 nightly dumps. The profile accepts a year alone as a date. |

## Opportunities and Full auto (intent of 2026-10-10)

- Swipe mode: Like starts the CV, the letter and the application; Pass hides. Review first by
  default, Full auto as an option. A first login onboarding wizard; simple by default.
- In Full auto a Like is the click for forms and e-mails, for the users who turn it on; the
  guardrails stay (no cybersecurity, caps, fact check).
- Freelance platforms are configurable sources, like job boards.
- Job is renamed Opportunity everywhere (table, API, code, specs). Decided, not built yet: it
  comes after ingestion and mail.
- 2026-10-20: the full rename runs LAST, in a freeze window, once find, apply and swipe are
  merged (option 1 of #353). Spec and plan: [SPEC-opportunities.md](../SPEC-opportunities.md),
  [tasks/opportunities/todo.md](../tasks/opportunities/todo.md).

## Mail (2026-10-13; paused 2026-10-14 after M4)

- Gmail only, per user, through Google OAuth; one send gate (`mail.gate.send`) that today accepts
  only the signed in person's explicit click on that exact draft.
- A status change concluded from a mail may apply by itself above a confidence threshold, always
  undoable, only when Gmail authenticated the sender and the sender is tied to the posting (for
  ATS mails: the system's domain is authenticated and the posting is hosted on that system).
- No sending limit as a product rule (the gate keeps the place for one, default none).
- The cybersecurity rule stops only what the AI drafts or sends by itself, not a reply the person
  wrote and sent.
- Consent first, before the assistant reads any mail. Planner default: 20,000 messages or 300 MB
  of mail text per user.
- 2026-10-14: mail M5 onward is PAUSED; documents, browser and apply go first.

## Documents (2026-10-14)

- A CV reference in the profile: upload during onboarding or later. Without one, the app writes a
  CV per posting from the profile with a default template; with one, it is converted into an
  editable base CV whose template every new CV uses. A list of base CVs, one the default; CVs can
  also be built in the app.
- Every document can be seen, edited by hand, changed through a chat, and switched among 20
  templates.
- Automatic generation is limited by a fit score threshold (Advanced settings, default 70), not by
  a count; a safety ceiling of 100 a day is a backstop only.
- "Correct, never block": the writer is careful, the fact check corrects unsupported claims itself
  and records each correction; nothing refuses a render, a download or a send.
- Import the command line's CVs and letters; those of applied postings stay byte for byte.
- Filling the profile from an uploaded CV is one click, never automatic.
- The renderer is the builders' choice: a headless Chromium service (measured in D1).

## Browser and apply (2026-10-14)

- Full auto: "Guarded defaults". Off until turned on; then only postings at or above the person's
  fit threshold, one application per company per 30 days, a hold of at least 15 minutes with
  Cancel before each submit, Ashby, Greenhouse and Lever first, the first 3 runs are rehearsals.
- Daily limit "by fit score only", plus a safety ceiling of 30 submissions a day.
- Accounts on job sites: the person signs in or signs up himself in the live view; the browser
  profile is saved per person; on request the agent completes the person's profile on that site.
- LinkedIn: "Full use" (searches and Easy Apply, in Full auto too), chosen knowing the account
  restriction risk; a per person switch, off by default.
- Software WebGL "when necessary": off by default, switched on per site when a page needs it,
  and remembered (issue #264).
- The MVP is one click apply: CV and letter generated automatically per found posting; the person
  clicks Apply; the agent fills Greenhouse or Ashby; the person approves; the gate submits.
- Browser B6 (late on 2026-10-14, built in #318): after a reboot the browser stays off until a deploy has checked its firewall rules
  (no restart policy); a browser image that fails to build deploys the rest without the browser.
- Planner defaults: EEO answers `decline` and salary `leave_blank` unless the person stores them,
  never inferred; browser memory 1024m without swap; Chromium keeps its own sandbox under a
  custom seccomp profile.
- 2026-10-15: the filter of form questions the answer agent may answer is a positive topic
  anchor (the job, the company, the team, motivation, experience); the person answers everything
  else in the approval screen.

## The short path to Full auto (2026-10-19)

The owner's answers of 2026-10-19 (issue #405). The plans and specs of browser and apply follow
them ([tasks/apply/todo.md](../tasks/apply/todo.md), [tasks/browser/todo.md](../tasks/browser/todo.md)).

1. Full auto ships first for Ashby, Greenhouse and Lever, before browser B12 and the sign in half
   of apply A10 (A10b). Until then a sign in wall sends the item to the review queue as "Needs
   you".
2. The check by model is apply's own: one model call per item (A13c, about 6,500 tokens), since
   mail M20 is paused. Until A13c lands, an item with free text written by the agent goes to the
   review queue.
3. A rehearsal the person answers Yes to lands in the review queue, ready for a one click Approve.
4. The generic form agent takes over by itself on `form_changed` in both modes (Review first and
   Full auto), within the run's token budget and every guardrail: the gate, the caps, the safety
   ceiling and the check by model before a submit. This answers the question of #343.
5. Planner default, not asked, which the owner may change: an approval or a Full auto Like that
   only the pace per host holds back stays valid, at most 7 days after it was given, and the gate
   checks everything again before the click; a Full auto Like stays valid at most 7 days in any
   case, and only while Full auto stays on.
