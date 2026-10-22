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
| 2026-10-20 | This Mac (the main Mac's orchestrator) merges approved pull requests only through `ops/ship.sh` (gate, merge, deploy), the owner's answer of 2026-10-20 after the classifier refused direct merges. The second laptop's rule of the same day stands unchanged: it merges approved pull requests directly into main, then the gate checks a commit of main (#354). |
| 2026-10-20 | The board's Status columns are the pipeline steps: Needs you, Paused, Blocked, Spec, Ready, Building, In review, Fix & re-check, Approved, Gating, Merged, Live, Dropped, Epic, kept by the board sync. Every open card is Ready or Done; a blocked card shows as Ready with what it waits for. |
| 2026-10-20 | Everything the owner must do himself lives in one checklist issue, #506. |
| 2026-10-21 | Distributed sign in flood (#152): a rate limit in front of the api, a Caddy build with a free, open source rate limit module (arm64 image, tests). Everything stays free. |

## Agents and ingestion

| Date | Decision |
|---|---|
| 2026-10-09 | The owner runs on his Claude subscription; other users bring their own Anthropic API key (Anthropic only), stored encrypted. |
| 2026-10-10 | Model by credential: `claude-opus-5-5` on a subscription, `claude-sonnet-5-5` on an API key, chosen automatically. The start dialog shows a token range (and a cost range for API keys). |
| 2026-10-10 | Ingestion: a copyable laptop analysis prompt; paste or upload any document (20 MB per file); proposals the person accepts. A PDF without text is rendered and transcribed, not refused. Document text is kept until the person deletes it. |
| 2026-10-10 | Explicit consent the first time a run sends documents to Anthropic, recorded with the date and the version of the text. |
| 2026-10-10 | A run pauses at twice its high estimate; an explicit Continue raises the ceiling by one estimate. Applies to every user. |
| 2026-10-10 | Parsers run in their own `parser` service (own uid, no credentials, no network beyond what it needs). Backups keep 7 nightly dumps. The profile accepts a year alone as a date. |

### Ingestion and profile fill (owner decisions of 2026-10-21, through the orchestrator)

- #408: `EXTRACTOR_VERSION` stays 1, no re-read; the merge drops the reused list removals that
  `tools._removes_a_list` now refuses (no token cost).
- #186: a secret found only in a transcribed page is extracted anyway (the image was already sent
  under the same consent); no refusal in the run under way. A confirmation given for the parsed
  pages of a mixed PDF still covers a secret found later in its transcribed pages (no reset).
- #184: hand typed values that differ from a document need their own click: excluded from Accept
  all (`bulk_exclusion`), shown as typed by the person. Derived years accepted after their
  employer was rejected are kept, shown stale with "check it" and Edit.
- #174 and #111: two positions or degrees at one organisation starting the same year keep the year
  key, with the list line "these may be two entries".
- #111: boards proposed by a document keep today's behaviour (inserted enabled, Accept all can add
  them). Accept all takes additions only: every `replace` needs its own click (`_takes_away`
  extended).
- #145: LinkedIn `Profile.csv`: every column is kept, none dropped at read time.
- #117: the documents a person may have waiting at once are capped (a 429 or 409 rule with a plain
  message); no fair order needed.
- #115: consent text version 2 adds "The proposals it writes are stored in your account until you
  accept or reject them."; everyone consents again once. The dialog stays as is (no "pauses at
  about 2 x high" line).
- #114: the laptop analysis prompt stays as is (no new prohibition). `/profile/fill`: when the
  person has documents it opens on Documents and keeps the prompt step visible and one click away;
  with no document, the prompt step as today.

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
- 2026-10-20, owner answers on opportunities: a Pass is stored only for now (learning from Passes is
  a separate later task); seniority, industries and company types feed the score as a bonus, never a
  filter; freelance platforms whose terms forbid automated reading (Malt, Free-Work, Codeur, Upwork,
  Freelancer.com, Comet, Crème de la Crème) are read with Playwright in the app's browser only for a
  person who opted in per platform, with the risk sentence, a recorded acceptance and a 7 day pause on
  a checkpoint (task O7b); Full auto for missions follows the rule of jobs (Ashby, Greenhouse and
  Lever forms only).
- 2026-10-20, owner answers on the first login wizard (PR #480, O5): only members are sent to the
  wizard (admins never redirected). The step is stored in the profile (the preferences section's
  onboarding part, written only through the onboarding routes, never by a profile PUT or an import,
  never proposable by ingestion, hidden from the model), so another device resumes at the same step,
  skipped steps included. A starter list of boards comes from the person's targets: from the roles
  and industries typed in the wizard, public Ashby, Greenhouse and Lever boards plus Google Careers
  and VIE sources; one click accepts all, each removable.
- 2026-10-20, owner answers on the starter list (PR #496, O5b): the catalog keeps Elastic, Datadog,
  GitLab and Palantir (the board's role filter still drops cybersecurity titles). The starter list
  also appears in Search options, for accounts that skipped the wizard or want more boards later (a
  follow-up issue after the merge). Google Careers and VIE are always proposed to everyone, the
  person unticks what he does not want, not only VIE when VIE is a chosen contract type.
- 2026-10-21, orchestrator decision (not the owner's), implemented by the fix round of PR #505:
  feeds (O7) follow the boards' never apply rule: what a feed brings is stored as skipped.
- 2026-10-21, owner decisions on the rename (#73): the freeze starts once every lane adding job
  names is merged (jobs, B8, B9, B10, O7, O7b, O8, A12, A13, O2). `posting_id` and the other
  `posting` names are renamed too (to `opportunity_id` and so on) in O11b's migration. Old API
  paths answer 308 for 30 days or two releases, whichever is later. Existing accounts with a
  profile never see the wizard, only a "Finish setting up" card for those with steps left.

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
- 2026-10-20 (#357): the pause is LIFTED. Mail is built after the job search tickets
  (opportunities O7 to O10 and apply A16), two tasks at a time, each through review and the gate.
- 2026-10-21, M3 parsing (#190): `dkim=pass` with an explicit `dmarc=fail` is not trusted (veto).
  Forged imported mail faking Gmail's authentication lines: stage B also looks for a mark real
  imports carry, besides never auto applying on mail older than the connection. Aliases: always the
  person's real Gmail account; only the connected address counts as the person, no alias handling
  (such threads fail closed). `in_reply_to` is appended to `references_header` by M7 (no
  migration). The quote depth limit stays 100. The French market ATS sending domains (welcomekit.co,
  join.com, flatchr.io, taleez.com, digitalrecruiters.com, jobaffinity.fr, beetween.com, breezy.hr,
  applytojob.com, pinpointhq.com, comeet.com, homerun.co, eightfold.ai, avature.net, csod.com,
  oraclecloud.com) are added before M5 ships, corrected from the real mailbox at stage B.
- 2026-10-21 (#190): mail goes through an MCP server rather than the Gmail API directly. This
  changes SPEC-mail; a spec revision is being written and its questions go to the owner before any
  mail code changes.
- 2026-10-21, M4 (#187): the consent tick is bound to the person and 10 minutes and cleared at sign
  out. On hold until the MCP redesign: CSRF on the OAuth start, the localhost redirect, the callback
  without a session.
- 2026-10-21, M2 public pages (#182): the contact address on `/privacy` and `/terms` is the owner's
  Gmail address, set as a build setting on the Jetson, never committed. The 13 sentences are
  approved with two changes: name Anthropic as the model provider, and state the lifetime of third
  parties' text and that deletion also removes the Gmail side copy. The home page sentence "the
  documents you prepare for them" is dropped. The `/privacy` search terms come from the same
  constants as the sync's query, with a comparing test.
- 2026-10-21, M9 fact check (#183): "3 years" against 3.5 years is flagged softly with its own code
  (`duration_below_profile`), never silently allowed. Fact check participants are the posting's
  company and its address domains only, never display names.
- 2026-10-21, M1 (#175): the canary fixtures move to `api/tests/conftest.py` in the first mail
  task; the sync's batches stay under 150 `messages.get` a minute.
- 2026-10-21 (#17): the points of the mail spec review are answered in the plan; closed.
- 2026-10-21 (#251): applications by e-mail stay behind mail M12, M18 and M23, as planned.

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
- 2026-10-21 (#358): a posting that leaves `applied` before the approved version's render lands is
  frozen by the next trigger (Try again, Mark as sent, apply's send, a return to applied): one
  `SPEC-documents.md` sentence plus a test pinning it.

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
- 2026-10-20 (on #429): "block chrome://flags, and every chrome:// settings page, in ALL sessions
  of the app's browser, the person's own sign-in sessions included, not only agent sessions."
  Built as the image's managed policy: every chrome:// page blocked but chrome://policy,
  chrome://sandbox, the new tab page and the browser's own UI; chrome://policy's test page off; no extension installed; saved flags and saved test policies
  dropped before every start.

## Likes (2026-10-21)

- The owner decided “no limit”: Likes have no per-click, session or daily cap. Application
  guardrails remain in force, including fit checks, company caps, pacing, holds and the safety
  ceiling.

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

- 2026-10-21 (#528): a skipped (passed) posting is refused with `passed` at start, at the
  decision and at both submission rechecks, in Review first and Full auto. Apply myself only
  warns; the other duplicate refusals still apply. Undo the Pass before asking the app to apply.

- 2026-10-21 (#560): before an apply click, a 429 is a passing fault retried after 600 then
  3,600 seconds, then failed. A 403 is not retried: it waits as Needs you with the page in the
  live view. A step detected on that page still wins over the status.
