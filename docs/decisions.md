# Owner decisions

What the owner decided, by module, with the date. A task never decides these again; a change
needs the owner's word (asked with concrete options, see [agents/orchestration.md](agents/orchestration.md)).
The full wording, with the owner's own sentences, is in the "Decisions" section of each
`SPEC-<module>.md` and `tasks/<module>/todo.md`; this page is the index. "Planner default" marks a
choice made by the build that stands until the owner says otherwise.

## Standing rules (all modules)

- No dashes as separators in any outward text: no em dash, no en dash, no " - ". Hyphenated
  words are fine.
- Roles a person never wants are that person's own never apply rules, in their profile. The
  app has no rule of its own about a kind of role (owner, 2026-11-05: the rule against
  cybersecurity roles was the owner's alone, kept in his profile).
- Never send mail by itself: the person clicks Send (Full auto, when built, is the one opt in
  exception for forms and mails, with its guardrails, decided 2026-10-10).
- Nothing is submitted without the person's approval: in One click Apply, the person's click on
  Apply is that approval and the apply agent clicks Submit itself (2026-11-05); in Full auto,
  the Like the person gives while Full auto is on is that approval (2026-10-10); in Review first
  and Full auto no model clicks Submit, the apply gate does.
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
| 2026-10-25 | "Narrow the gate and fix flakes": an `api/app/` change runs the journey groups of its module and of the api files that import it (`ci_plan.py` `API_AREAS`), no longer every group; shared api files, models, worker registrations, the routes of pages every group opens, main.py's middleware and anything the reader cannot follow still run every group. The gate's parallelism and the move to GitHub runners stay as they are (declined). |
| 2026-10-29 | harness2 (google-research/harness2, recursive agent harnessing) for the development of the app first: "its for the dev of the app, not necessaly for the job app itself". Not installed as shipped (Vertex AI, OpenCode or Codex, paid); its harnessing level recursion is adopted by hand: lessons in harness2's EXPERIENCE format in `docs/agents/lessons/`, the review checklist and implementer.md rebuilt from them, a reflector pass after each ship (orchestration.md). The owner chose three steps: the lessons from reviews, trimming the always on text, and a measure of the CV and letter writers' spread (`app.documents.measure`, run by the owner with the owner's own credential); a full harness2 install with a Claude Code executor waits for evidence. Reading rule (owner's answer of the same day, "Module + cross refs"): a build agent reads this file's standing rules, its own module's sections and every section its brief names; the orchestrator names in each brief the sections a task touches; STATUS.md is the orchestrator's. |

## Agents and ingestion

| Date | Decision |
|---|---|
| 2026-10-09 | The owner runs on his Claude subscription; other users bring their own Anthropic API key (Anthropic only), stored encrypted. |
| 2026-10-10 | Model by credential: `claude-opus-5-5` on a subscription, `claude-sonnet-5-5` on an API key, chosen automatically. The start dialog shows a token range (and a cost range for API keys). |
| 2026-10-10 | Ingestion: a copyable laptop analysis prompt; paste or upload any document (20 MB per file); proposals the person accepts. A PDF without text is rendered and transcribed, not refused. Document text is kept until the person deletes it. |
| 2026-10-10 | Explicit consent the first time a run sends documents to Anthropic, recorded with the date and the version of the text. |
| 2026-10-10 | A run pauses at twice its high estimate; an explicit Continue raises the ceiling by one estimate. Applies to every user. |
| 2026-10-10 | Parsers run in their own `parser` service (own uid, no credentials, no network beyond what it needs). Backups keep 7 nightly dumps. The profile accepts a year alone as a date. |
| 2026-10-21 | Exception to "bytes go once the text is stored": the original file of an upload that becomes a base CV is kept until the person deletes it (the document in ingestion, or the last base CV converted from it), so the design capture and the Compare view read it. The CV panel marks the upload at once (`for=base_cv`); every other upload keeps the rule (issue #519, SPEC-ingestion.md, "Text extraction"). |
| 2026-10-21 | Review (#184, #174, #111): "Accept all" takes additions only. A `replace`, and an item that would change a value the profile already holds (typed by the person or kept from an earlier decision), each need their own click. Derived years accepted after their employer was rejected stay as they are, shown stale with "check it" and Edit. Two positions or degrees at one organisation started the same year keep one key (the year), and the review says they may be two entries. Boards a document proposes are still inserted enabled. |
| 2026-11-04 | The Agents tab in Admin (agents A6): "in admin, i want a tab on all the agent. I want you to take an agent as a person, I want the system prompt, the tools, the skills ect.. all what made an agent and be able to vew, and modify it. For example the Agent job search, I want to see the parameters he can take, the prompt, the tools, the model ect.. absolutly everything si it will be easier to me to understand how claude cli is used in the app. so when i save something from the interface, it must be used at the next use of the agent. maybe store these infos in the db" Each agent's page shows everything that makes it, read from the running code plus the saved version. Editable, stored as versions in the database and used from the next run on: description, system prompt, default message (for an agent whose first message reads it), model and model per credential kind, effort, max turns, wall time (never above the code's) and the code's tools on or off. Not editable: the input model, the flags, the tools' code and schemas, FORBIDDEN_TOOLS, the permission mode, the setting sources, and every guard enforced in code (browserd's line, the apply gate, no mail sent by itself, the cybersecurity rule, private runs). Planner defaults: a member gets 404 like every admin route; a wall time may be lowered, never raised. |
| 2026-11-04 | The Agents tab in Admin, what A6 left open (agents A7): After A6 shipped, asked "Should a wall time ever be raised above the code's? Today it can only be lowered" and "Should the tab show which person ran each recent run? Today runs are anonymous on the tab", the owner answered: "Yes possible to raise it and yes show wich person started each run". A saved wall time may now be raised above the code's, up to the agent's own ceiling, never above it: the agents queue's (the longest wall time any agent has in code, today 86,400 s) for an agent run as its own job, the bound of the other module's job for one that runs inside it (the orchestrator, 2026-11-05: other modules' jobs keep their own limits); the worker's job timeout and stale bound are per run (the orchestrator chose per run timeouts over a queue wide day), so a run nobody raised keeps the queue's bounds; this replaces A6's planner default "a wall time may be lowered, never raised". The recent runs name the person each run is for (the name and the e-mail as the Users page shows them, admin only, nothing else of the person); run links stay only for the admin's own runs. |
| 2026-11-05 | The Agents tab in Admin grouped by category, and one table component (agents A8): "there is a lot of agents. group them by categories. verify that you use the same component array than all over the app." The list shows one section per category, in a fixed order (Finding jobs, Applying, Documents, Profile and import, Jobs and chat, Test), each agent's category set by name in the api's catalog; every table of the web app uses `components/ui/table.tsx`. |

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
  a checkpoint (task O7b; the job website search keeps no such pause since 2026-11-04, Interactive
  job search below); Full auto for missions follows the rule of jobs (Ashby, Greenhouse and
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
- 2026-10-27, owner decision on the search lists (#626, #703): the search is four plain word
  lists, Job titles, Unwanted titles, Places and a new Unwanted places (`location_exclude`), in
  the Search tab and the Find jobs setup alike; each list's regular expression switch moves under
  the shared Advanced, off by default, and Advanced opens by itself when a list holds one
  (switches replaced on 2026-10-28: no regular expression in the setup or the Search tab, see
  "Websites and words only" under Interactive job search). An
  unwanted place leaves a posting out wherever the search filters (boards, feeds, browser sources
  and the website searches), through the same matcher as the unwanted titles, and wins over a
  wanted place.
- 2026-11-07, owner decisions on the wizard's first steps (Agentation notes on /welcome): "why
  having two drop files ? make this step simpler by letting the user upload all the files he
  want", and asked, the owner chose "Mark it in the list": step 1 has one place for every file
  (ingestion's drop zone, many at once, and the pasted text); a PDF or Word file added in this
  visit offers "This is my CV", which sends it again marked `for=base_cv` (the server keeps that
  file's bytes) and converts it into the base CV with its look (D24); then one reading of the
  documents into the profile. The CV upload panel stays on the profile's page. Then "claude/codex
  must be the second step (optionnal)": the prompt for Claude Code or Codex is wizard step 2,
  skippable, with where to add the file it writes and its reading; like Websites to search it is
  never sent to the onboarding api and counts as passed once a later step is. Its title,
  "Analyze your computer" (owner: "change Claude Code or Codex chapter title into analyze your
  computer or something like that"). Then "first step
  should be to setup sub or api key": Model access (Account's own panel: a Claude subscription
  token or an Anthropic API key, Save, Verify, Remove) is step 1 of 10, skippable; it is done,
  and the wizard opens past it, when a credential was saved before the wizard opened, and it is
  never sent to the onboarding api. The summary's key form is gone: the summary says which
  credential is saved, or that none is, with Change. On that step: `claude setup-token` gets a
  copy button where it stands in the sentence ("i prefer the old version, just put the copy to
  clip board on the command"), and "I don't want this block, I want automatic verify, loading
  around the token input and turn green or red according the verification": the saved
  credential's block and its Verify button are gone; saving verifies at once (and a credential
  never verified is checked once on opening), the field shows a spinner, then turns green or red
  with the reason; Remove stays next to Save. Same on Account. A subscription token, which the
  server could not check (agents A1), is now checked by one Claude Code run in the worker on
  save (asked, the owner chose "Run Claude Code once" over leaving it to the first agent run):
  a one line prompt, no tools, the smallest model, a tiny part of the subscription; the field
  shows the spinner, then green, or red with the reason.
  Then "remove this, all the files will be analyzed at the end when the user finish all the
  steps": no reading in the steps; the summary says how many documents are read and the token
  estimate, shows the consent box while the person has not agreed to what is sent, and Start
  starts the reading of every file first, then ends the wizard (an unticked box ends it without
  reading). Step 3 is only the analysis of the computer ("remove this from this step, its only
  the analyze the computer"): the prompt and a field to paste its answer. The prompt, version 2:
  "the prompt should be generic and let claude analyze the whole computer to extract all the
  informations for a recruiter profile" and "do not mention jobapp specificly, just ask claude
  or codex to output to the user the full analyze, the user will then copy and paste the
  response into the step": the whole home folder (the never read list still holds), the
  analysis as the answer in the conversation, no file, no app named (SPEC-ingestion); "why you
  naled it dossier ? it should be anwser right ?": the word is gone from the screens and the
  prompt, the answer is titled "Recruiter profile".
  Then the reading moved again: "after the profile and analyse of the computer steps, i want to
  run the full creation of the profile. I want a very cool animation with rive (do it with rive
  cli)": step 3 is Building your profile (the reading of every file, a Rive scene while it runs,
  Next waiting), and the summary no longer reads anything. Asked what happens to the proposals,
  the owner chose "Write the new ones at once": what adds to the profile is written when the run
  ends, as Accept all additions would, and what would replace a value waits in Fill my profile
  (a deviation from "nothing is written before the person reviews it", for additions in the
  wizard only). And "put the analyze the computer step under advenced in your profile step":
  the prompt lives under Advanced in Your profile, its answer pasted with the other files.

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
- 2026-10-30, job alert e-mails (asked through the orchestrator how Indeed's postings should reach
  Find jobs, since Indeed has no API and its security check blocks automation; answer "Gmail job
  alerts"): a narrow exception to "a mailbox read never adds a candidate posting". Only messages
  from an allow list of exact alert sender addresses per provider, only when they pass the
  module's sender authentication (`mail/auth.py`, plus the `dmarc=fail` veto above), parsed by
  deterministic code per provider (never an LLM agent), giving candidate postings that take the
  sweep's commit path (filters, never apply rules, duplicates, scoring); links canonicalised from
  their text (tracking stripped, job id kept) and never fetched; nothing else of the mailbox read
  for it; turned on per provider in Find jobs setup. Indeed and LinkedIn are built; Welcome to the
  Jungle, APEC, HelloWork, France Travail and mon-vie-via stay TODO until a format is established
  (SPEC-mail.md, "Job alert e-mails: a narrow exception").

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
- 2026-11-06 (D27): with three PDF CVs made with Canva attached, the owner wrote: "now i want you deep focus on the templates. There is 3 templates attached to this message. I want to to replicate exactly these tree as editable cv, create the resume in the same style, remove all the templates of the app and upload this one. then, according to the method you used to replicate these templates, I want you to implement this in the app so when a user upload his cv, the app will use the exact same style for generating new cv and cover letters."
  The three, replicated by measurement as `band`, `photo-columns` and `ledger`, replace the twenty
  bundled templates (this revises "switched among 20 templates": a document is switched among the
  three and the person's own). The default is `band`. A document on a removed template moves to
  the nearest of the three (migration 0035: `sidebar`, `split-right`, `portrait`, `banner`,
  `columns-header`, `cards`, `grid` to `photo-columns`; `serif`, `elegant`, `executive`, `nordic` to
  `ledger`; every other to `band`); versions keep theirs. The second half, the design capture of an
  upload with the same method, is task D28. D28 built it: an upload is measured, its fonts chosen by
  name or by measurement, its template written from the numbers and rendered, compared and
  corrected over several passes, the best kept (SPEC-documents.md, "Design capture of an upload").
- 2026-11-06 (D29), the owner wrote: "I don't want to automaticaly generate the cv and cover letter anymore. I want to generate them manually from the job detail page or when i click on apply to the job. For the cv, analyze deeply this project : https://github.com/alexyc9381/hiring-panel-skill you must understand how it works perfectly and implement it during the cv and cover letter build to have perfect cv and cover letter", "use also this project skills when the user upload his cv", and "also when generating the cv and cover letter for the jobs, hiring-panel-skill must know everything about the jobs to be perfectly specialise on the job, the company, the offer ect.."
  Nothing writes a CV, a letter or a base CV without the person's click: the job page's Write, an
  Apply (one click, bulk, a Like, Full auto's apply after a Like) or an upload. This revises
  "Automatic generation is limited by a fit score threshold" above and SPEC-documents.md
  Decisions 14, 15 and 17: no fit threshold for documents, no daily safety ceiling, no backfill;
  the settings card leaves the page, `search.documents` stays readable and unread, Full auto keeps
  its own default threshold of 70, and the consent gets text version 2. A click with no base CV
  builds one from the profile first. Every CV and letter build, and the conversion of an uploaded
  CV, goes through the hiring panel of hiring-panel-skill (MIT, credited in NOTICE): seven
  reviewers on Sonnet that each try to reject it alone, with a job brief of the posting, the
  company and the offer; one merged list of mistakes; a rebuild with the same facts through the
  fact check; the screen again. A failing step never blocks: the first version stays. It also
  revises item 4 of "Product audit: chapters 1 and 2" (2026-11-05): the rescore after a skills
  change no longer says a possible document cost before saving, since no document is written
  without a click.

## Interactive job search (2026-10-24, #626)

- **Owner intent:** Find jobs should let the person choose websites and search parameters, then an actual LLM uses Playwright to browse and search those sites, like the owner's Claude Code workflow. This is broader than enabling the narrow company-site finder or platform consent alone.
- **Approved for implementation, not yet implemented:** the setup, saved criteria/run scope, live run, provenance and measurable acceptance are in [SPEC-jobs](../SPEC-jobs.md#interactive-search-626), with guarded browser interactions in [SPEC-browser](../SPEC-browser.md#interactive-discovery-626). API/feed modes remain explicit; a selected model-driven mode cannot silently substitute them or fixed readers. Existing free service, credential/runtime, browser and never-apply rules stay in force. The guarded browser interactions (B13) were replaced on 2026-11-04 by "Search on the Jetson as on the Mac" below.
- **Confirmed target websites:** LinkedIn, Welcome to the Jungle, Indeed and Free-Work, plus the ability to add custom URLs, accepted by the owner on 2026-10-24. This approves the target set only. The owner subsequently instructed fresh subagents to continue the draft PR, approving implementation of the reviewed contract on the same date.
- **Full-flow confirmation:** the owner also answered the explicit revised-flow question with "Implement this search flow". Continue the remaining executor, setup, guard, findings, cancellation and browser/live-view slices with independent reviews. New draft PR work remains authorized; merge/deploy and real-model/site acceptance remain separate.
- **One agent per website (owner, 2026-10-27, jobs J18):** "An LLM agent for each website that will search intelligently, just like a human, using all the parameters provided to it." Its budget per website: "no limit". This replaces the 2026-10-26 decision "One search per combination" (one native search per title and place pair, at most 6 a website, each on its own lease and frozen scope) and the shared 40 turns and 840 s of its review round. A website is one lease and one finder run a sweep, given all the person's criteria as context; it decides which searches to run, in which order and when the website is covered. It types only one saved wanted title in a search box or one saved wanted place in the place box (or nothing to clear one), and sets only the saved remote stance or the one saved contract type; unwanted titles and places are context only, never typed; browserd refuses any other value. No cap on its turns, pages, tokens or verified postings. What still bounds it: the sweep's 900 s, shared fairly among the websites left (it is told the seconds it has), the lease's maximum, LinkedIn's 5 pages a sweep and one sweep a day, pacing and the 7 day pause. Postings merge once per address; unwanted titles and places and several contract types are applied after reading, before the store; places are typed as written, never mapped to a website's codes; a regular expression that is not a plain word stays unsupported (refined on 2026-10-28: its plain words are typed when it converts safely, "Websites and words only" below). The summary names the searches the agent ran and counts them (`api/app/browser/sources/interactive.py`, `browser/browserd/tools/discovery.py`). Its typed values (only saved titles, places and filters), the website's share of the sweep's 900 s, the lease's maximum, pacing and the 7 day pause were replaced on 2026-11-04 by "Search on the Jetson as on the Mac" below; one agent per website with all the criteria as context and no turn cap stay.
- **No page cap for Free-Work and the other opt in websites (owner, 2026-10-27, jobs J18):** "no limit" replaces the "small page caps" of SPEC-opportunities decision 12 (2026-10-20). Free-Work, Indeed and Welcome to the Jungle keep no page cap of their own; pacing, one sweep a day, the 7 day pause and the website's share of the sweep's time bound them. LinkedIn keeps its own caps: 5 result pages a sweep and one sweep a day (`api/app/browser/platform_policy.py`). For the browser search, pacing, the 7 day pause and the share of the sweep's time were replaced on 2026-11-04 by "Search on the Jetson as on the Mac" below.
- **A ticked website is its permission (owner, 2026-10-27, jobs J17):** ticking a job website in Find jobs and saving is the person's permission to search it. This replaces the #651 rule "selection is not acceptance" and its separate "Review website permissions" cards. Each website's risk sentence (the server's current text) shows once beside its checkbox. Save records the permission of every ticked website through the platform's own consent route (LinkedIn's switch; the platform consents of Free-Work, Indeed and Welcome to the Jungle), at the version of the sentence shown and on the reviewed profile revision, so a change in another tab shows a conflict, never a silent overwrite. Unticking a website and saving takes its permission back; a LinkedIn or Free-Work permission given for its existing reader stays while its box was never ticked in Find jobs. LinkedIn follows the same rule, no exception: it keeps one switch for its search, its fixed reader and Easy Apply, so ticking LinkedIn in Find jobs also allows Easy Apply, and the sentence beside its box says so; each application still needs the person's approval (owner, confirmed 2026-10-27). The risk sentences of Indeed and Welcome to the Jungle keep their current text, with no new text version (owner, 2026-10-27). The profile save never carries a permission and no model accepts anything; the search's checks are unchanged: a website without a recorded consent ends `permission_unavailable`, a consent taken back during a search stops it, and the 7 day pause, daily caps and pacing stay (for the browser search, the 7 day pause and pacing were replaced on 2026-11-04 by "Search on the Jetson as on the Mac" below; the consent rule stays). Find jobs also had **Websites only** (that search ran the ticked websites and no board, feed or fixed reader; the automatic schedule kept searching every source): replaced on 2026-10-28 by "Websites and words only" below, where every search runs the websites alone.
- **Websites and words only (owner, 2026-10-26 and 2026-10-28):** "remove Other search sources, if i want to search on a specific website i will ad it as websites" and "no need to ask the user regular expression for titles to include and exclude, just let the user enter job he want and job he doesn't" (the same for places); on 2026-10-28 the owner asked again to remove from the Find jobs setup "Advanced / Regular expressions" with its four switches and all of "Other search sources" (the API boards, the four feeds and the link "Manage API boards, feeds and search options"). So: the setup is the job websites (the four named ones and the person's own), the four plain lists, the remaining criteria and the instructions, with no switch for regular expressions there, in the Search tab or for the industries. Every search, Find jobs, the first login wizard's Start and the automatic schedule, runs the ticked websites alone; API boards, feeds and fixed readers are no longer read by any search, and the "Websites only" switch of J17 is gone with nothing left to choose. Their saved settings stay in the profile untouched (no deletion, no migration), and the Search tab says they are no longer searched. A search list saved as regular expressions before (the owner's own, such as "forward.deployed" or "solutions? architect") keeps its terms and keeps filtering as the expressions they are (J16); a website search types the plain words of each expression that converts safely ("forward deployed" and "forward-deployed", "solutions architect" and "solution architect", `app/profile/plain_terms.py`), and an expression with no plain words stays in the criteria the agent reads and is counted as left out. A list never empties silently: one that holds terms and gives no word refuses that website's search (`unsupported_criteria`). The search agent interprets every saved word (owner, 2026-10-28). Since 2026-11-04 those plain words are the agent's titles and it may type what it judges right, a translation or a synonym ("Search on the Jetson as on the Mac" below).
- **No daily limit on any website, LinkedIn included (owner, 2026-10-28, jobs J18):** "i don't want limit on search by day because every user will set its own claude token, i don't matter about the cost"; asked which websites, the owner chose "All, LinkedIn too". This removes the one sweep a day of every job website (Free-Work, Indeed, Welcome to the Jungle, LinkedIn, `swept_today` and the `*_daily` refusals) and LinkedIn's 5 result pages a sweep, for its agent and for its fixed reader; it replaces those parts of SPEC-browser decision 2, of SPEC-opportunities decision 12 and of the J18 entries above. Kept: pacing per platform (LinkedIn 10 to 30 seconds a page), the 7 day pause after a security check, the stop on a sign in page, the consent of each website and browserd's B13 guards; Easy Apply's own caps (one every 10 minutes, 15 a day) are applications, not searches, and stay. Older sweeps keep their `linkedin_daily`, `platform_daily` and `page_budget` codes, still shown with a readable sentence (`api/app/opportunities/platforms/guard.py`, `api/app/browser/linkedin_guard.py`, `api/app/browser/platform_policy.py`, `api/app/browser/sources/linkedin.py`). For the browser search, the pacing, the 7 day pause, the stop on a sign in page and the B13 guards kept here were replaced on 2026-11-04 by "Search on the Jetson as on the Mac" below; the consent of each website stays.
- **Every criterion in the search agent's prompt, read as the person meant it (owner, 2026-10-28, jobs J18):** asked whether a Places value "everywhere" should be treated specially, the owner answered "all the parameters should be injected into the prompt of the agent who does the search, you understand ?". The person's saved words are not edited nor validated for a website. The agent of a website gets every criterion (wanted and unwanted titles and places, contract types, remote stance, seniority, industries, company types, pay, recency, the person's instructions) and is told to read them as a person would: "everywhere", "anywhere", "worldwide" or "partout" in Places means leaving the place box empty. Leaving a title or place box empty is always allowed, and a search request may carry no place. What it types is still one saved title or one saved place, now matched with its case and accents aside ("GenAI Engineer" for the saved "genai engineer", "Ile-de-France" for "Île-de-France"; `discovery.saved_as`), which code checks safely; a translation of a saved word stays refused, since no code can check it (declared gap). A search request still carries a saved title. The apply and submit guards are unchanged (`api/app/browser/finder.py` INTERACTIVE_PROMPT, `browser/browserd/tools/discovery.py`). Case and accents aside means: in NFC, the same case folded text, or the same length with each letter the saved one or a letter of the same base; a mark is never added and a compatibility form (a long s, a ligature, a full width letter) is refused (`discovery.same_value`, review of PR 719). The typing rule was replaced on 2026-11-04 by "Search on the Jetson as on the Mac" below: the agent types what it judges right; every criterion in its prompt stays.
- **Indeed's country sites are followed (owner, 2026-10-28, jobs J18, asked through the orchestrator):** Indeed sends `www.indeed.com` to the person's country site (`fr.indeed.com`); the owner chose "Follow its own country sites. A redirect from www.indeed.com to any xx.indeed.com is followed, and the search continues there. Redirects to any other domain stay refused." Only the indeed platform, only the start page's first navigation, only https, only to the start page on a host of exactly two lowercase letters plus `.indeed.com`; browserd's frozen scope then moves to that host for the rest of the session, once, and every other rule holds there unchanged (sign in, apply and destructive addresses, query keys); pacing stays per platform. Replaced on 2026-10-29 by the host families of "Let it use the search boxes" below: a redirect within the platform's family is followed at any time, not only the first one. The families went in turn on 2026-11-04 ("Search on the Jetson as on the Mac" below: any public address).

- **Let it use the search boxes (owner, 2026-10-29, browser B19):** every Find jobs sweep found 0 postings on LinkedIn, Welcome to the Jungle and Indeed with the person signed in to all three, because the app's own guards refused every step (a redirect to Indeed's country site, typing in a search box outside a native GET form, a posting link with a tracking query). Asked how the agent should search the real sites, the owner chose "Let it use the search boxes": signed in, the agent may type the person's saved title or place into the site's own search box (even a script box) and press Search, as a person would; each site's own hosts and its job links are allowed. The address only approach was not chosen. Guards that stay: no Apply, Postuler, Candidater, Easy Apply or any submit, save, message, follow or connect; no sign out; no form other than search; only the person's saved titles and places are ever typed; GET for documents; pacing, daily caps, the 7 day pause and consent unchanged. As built: a platform's hosts are a family (Indeed `indeed.com` and every `<two letters>.indeed.com`, LinkedIn the same under `linkedin.com`, Welcome to the Jungle its domain and `www.`), a redirect within it is followed and one to its sign in host (`secure.indeed.com`) is the sign in wall; a page's fetch or XHR may POST to the family or the platform's search service (Welcome to the Jungle's Algolia hosts); when the app refused the agent's search and nothing was found, the website says "The app refused the search on this website." (`browser/browserd/tools/discovery.py`, `api/app/browser/finder.py`, `api/app/browser/sources/interactive.py`). Replaced on 2026-11-04 by "Search on the Jetson as on the Mac" below, except the guards the line keeps: no apply or submit, no message, invitation or connection request, no sign out and no form other than a search or a consent choice (saving a job, following a company and any public host are now allowed).

- **Job feeds in place of Indeed (owner, 2026-10-30, asked through the orchestrator):** Indeed's Cloudflare check blocks any automated access and Indeed has no official search API, so the owner chose to build three official job APIs: Arbeitnow (jobs in Europe, no key), Himalayas (remote jobs open to France, no key) and Adzuna (jobs in France, the person's own free key stored encrypted in Account; skipped with a clear reason while none is stored). Each is ticked in Find jobs like a website and read before the websites, a few requests a sweep, seconds apart, a 429 stopping that feed for the sweep; what they return is filtered by the saved criteria, deduplicated and scored like every finding, and shown "via" the feed with a link back. This brings back three feeds after "The board and feed engine deleted" below, in a new package (`api/app/jobs/feeds/`); the deleted boards and feeds stay deleted. SPEC-jobs, "Job feeds".

- **The board and feed engine deleted (owner, 2026-10-28, asked through the orchestrator):** to "The code that read the 32 company job boards and 4 feeds is no longer used by any search. What should happen to it?" the owner answered "Delete it", the option reading "A follow-up PR removes the board and feed readers, their tests and settings. Smaller app, faster tests, but bringing them back means rebuilding." So the Ashby, Greenhouse and Lever board readers, the Remote OK, We Work Remotely and Hacker News feeds, the fixed readers no search used (Google Careers, Business France VIE, company career pages and LinkedIn's own reader), the starter boards list, the board check, the `websites_only=False` start, the ATS and feed settings and their fake servers are gone. This replaces "Their saved settings stay in the profile untouched" in the entry above: a stored `boards`, `feeds`, old reader row or `starter_handled` is ignored on read and dropped on the next save, with no migration (no table or column served only the boards). Kept: the job website search, the LinkedIn and Free-Work consent rows, Apply's own adapters, the posting import, the scorer, and old sweep rows read as stored in the history.
- **LinkedIn searched through its public job search, switchable (owner, 2026-10-30, asked through the orchestrator):** "use api to search but keep browser to apply", then "Both, switchable in setup". Find jobs setup shows, under a ticked LinkedIn, "Search with LinkedIn's public job search (no login)" (the default for everyone, existing settings included) or "Search with the browser, signed in" (the finder agent, unchanged); per person, `search.linkedin_search` (`api` or `browser`). Easy Apply always uses the browser. The public job search reads LinkedIn's guest endpoints (search fragments of 10 cards, then the posting page for new postings that pass the saved title rules), at least 8 s between two requests of LinkedIn's host on the shared pacer, at most 30 requests (45 since 2026-10-31, below) and 3 pages a search per sweep, and stops LinkedIn for the sweep on HTTP 429 or 999 (`linkedin_rate_limited`), without recording a checkpoint, with a cooldown of 30 minutes for every sweep after it. The LinkedIn switch, its sentence and the 7 day pause are still checked before every request (kept conservative). LinkedIn's User Agreement forbids automated access, these endpoints included; the owner accepted the risk in the LinkedIn checkbox; the anonymous requests carry no account, so the worst case is a block of the address. Evidence: a proof of concept on 2026-10-30 (HTTP 429 at 4 s, none at 8 s; one page read from the Jetson). Spec: [SPEC-jobs](../SPEC-jobs.md#linkedins-public-job-search-owner-2026-10-30) (`api/app/browser/sources/linkedin_guest.py`).
- **Company job boards are back (owner, 2026-10-30, asked through a question):** the owner reversed "Delete it" above and asked for "Company job boards" built on the ATSs' official public job board APIs, naming jobs.ashbyhq.com. Built in `api/app/jobs/boards/` (SPEC-jobs, Company job boards). What differs from the engine deleted on 2026-10-28: six ATSs instead of three (SmartRecruiters, Workable and Recruitee added; Teamtailor left out, its API needs the employer's key); a new profile key `search.company_boards`, so a stored old `boards` stays dropped and nothing deleted comes back by itself; a sweep reads only a list the person saved (Find jobs shows the 33 boards of the starter list ticked until then), never a list built from the wizard's targets; boards are read before the websites, in at most 300 s; one request per board and sweep, no retry, a pause per ATS and a stop on 429 (the old reader retried three times); the board's posting is stored through the same commits, duplicates and source notes as a website's finding; one `stats.sources` entry per board instead of the `boards_*` counters (kept at zero for old readers); no per board extra exclude list; no board check request when a board is added (the address is parsed, the first sweep says whether it answered). Discovery of more boards: no search engine is usable for free in 2026 (Google's Custom Search JSON API closed to new customers, ending 2027-01-01; Bing's retired; Brave's metered behind a card), so boards are suggested from the person's own postings. The freelance feeds and the fixed browser readers stay deleted.
- **Sources read side by side (owner, 2026-10-31, asked through a question):** the owner chose "Run sources in parallel": the alert e-mails, the job feeds, the company boards and LinkedIn's public job search make no browser call, so they start together instead of one after another; the job websites read in the browser stay one at a time and start once those ended, with the time left of the one 15 minute deadline. Each keeps its own time cap and politeness; they store through the run's one commit path, one commit at a time, so one posting found by two of them at once is one row (SPEC-jobs, Sweep). Planner default: the websites wait for the API sources rather than run beside them (one browser lease, the finder's model calls and the API parsing never compete for the worker's memory). Since 2026-11-04 the websites read in the browser have no time cap ("Search on the Jetson as on the Mac" below); the API sources keep theirs. The job websites read in the browser one at a time were replaced on 2026-11-06 by "Two browsers at once" below.
- **Two browsers at once (owner, 2026-11-06, asked through questions):** the owner asked "when finding a job, i want being able to visualize the browser(s) used (work should be able to be done in parallel), same for applying". Asked how many browsers at once, he chose "2 at once (Recommended)": two browsers at the same time on the Jetson, so two websites searched side by side, two applications filling, or one of each; asked where to build it, "In this branch"; asked when the live views open, "Open on click": a view opens when the person clicks Watch the browser, and browserd keeps counting an open view as the person watching (unchanged). This replaces "the job websites read in the browser stay one at a time" of "Sources read side by side" above and "one Chromium at a time on the whole Jetson" of SPEC-browser ("Memory on the Jetson"): the browser still serves one person at a time (one lease holder), in `BROWSER_SLOTS` slots (default 2, 1 restores one browser), one Chromium of that person with one window per slot, side by side on one display, each slot its own lease (its tools, its tabs, at most 3, its take over, its ask of the person, its live view showing its window only). Kept: nobody else's work and no person's own session (Look, Sign in, Apply myself) shares the browser; the sweep's preemption; the request floor of a sweep and the guards and submit detector of each apply lease, per slot; the shared pacer and each platform's pacing, consent, stops and pause across slots; the one commit path of a sweep (one posting found twice is one row); the submit stays watch only. Planner defaults the owner may change (SPEC-browser, "Two browsers at once"): with two slots every agent's Chromium carries the sweep's switches (software WebGL, `IsolateSandboxedIframes` off), since one Chromium serves both; a lease that joins a running browser gets its own context with the person's cookies and the local storage of every site the browser opened since it started (Playwright's storage state; a site only the second window opens, IndexedDB and session storage are not covered), its cookies written back to the profile at its end; a person's second lease never passes another person who waits in line; a website or a fill without a free slot waits for one (a website only while another of its sweep holds a slot, else `browser_busy` as before); the apply queue runs BROWSER_SLOTS fills at once; the display has one keyboard, so a Take over of one window pauses both assistants until Give back; the sign in hand off, which restarts Chromium, waits for later while two slots run (as when nobody watches); the 2048m container is kept (a second heavy tab estimated at 300 to 600 MiB, the 90 % guard closing the newest tab past about 1840 MiB), to be measured on the Jetson. **Browsers at once per person (owner, 2026-11-07):** "As an admin, i want being able to set the make browser parallel by person": each account has its own number of browsers at once, 1 up to the server's BROWSER_SLOTS, which an admin lowers or raises per person in Admin, Users; the lease store and browserd never give a person more slots than their number, a sweep then runs that many websites side by side and fills wait in line beyond it; BROWSER_SLOTS stays the ceiling for everyone. Planner default the owner may change: every account, existing and new, starts at the server's maximum (keeps "2 at once" of 2026-11-06); a lowered number applies from the person's next lease (a second window already running ends on its own); a member's call to the admin route answers 404 like every admin route (I5), not 403.
- **LinkedIn's public job search gets its own time cap (owner, 2026-10-31, asked through a question):** the owner chose "Fix LinkedIn's time share". Evidence: a sweep of 313 s with 3 saved titles in 7 saved places (21 searches) and two websites read in the browser ticked; LinkedIn ended `time_budget` after 11 searches and 25 requests, 4 new postings, because its share was still the time left divided by the browser's websites plus one (a third), although since the sources read side by side the websites start only after it, and its details after each page took the requests of the later searches. As built: LinkedIn's public job search stops at 420 s from its start (`LINKEDIN_TIME_S`), whatever the number of websites ticked, and never closer to the sweep's deadline than 240 s (`WEBSITES_MIN_S`) when a website read in the browser is ticked (half the time left when less than twice that is left); none is kept when no website is. Why these numbers: beside the alerts (120 s), feeds (180 s) and boards (300 s), 420 s leaves the websites at least 480 s of the 900 s, more than their minimum, and holds 21 first pages even at the slowest pace (21 at 12 s is about 250 s) with their details. Breadth first: the first page of every search, then up to 15 details taken in turn from each search (`DETAILS_PER_ROUND`), then the next page of the searches that go on, the details left at the end. The request cap rises from 30 to 45 a sweep (about 7.5 minutes at 10 s apart, so the time cap mostly stops it first); 30 searches, 3 pages a search, 8 to 12 s between requests and the 30 minute cooldown are unchanged, and so is the terms risk (no account, no cookie). The entry counts the searches planned (`planned`) beside the ones that ran (`searches`), so the summary says "11 of 21 searches ran", and a stop on its time cap reads "LinkedIn is read slowly, about one request every 10 seconds, so its time in this search ran out." (SPEC-jobs, LinkedIn's public job search; `api/app/browser/sources/linkedin_guest.py`).
- **Search on the Jetson as on the Mac (owner, 2026-11-04, browser B20):** the owner asked why Find jobs keeps failing on the Jetson while on his laptop "when I'm on claude code on my laptop and i ask to open playwright cli, find jobs and save them, it's working very well". The orchestrator showed him the laptop (Claude Code with Playwright CLI) beside the Jetson (the app's search agent), row by row: what it may do, rules, who's there, time, browser, how fixes are tested (the table as shown is in [the plan's B20](../tasks/browser/todo.md#b20-search-on-the-jetson-as-claude-code-with-playwright-cli-on-the-mac)). The owner answered, quoting the laptop column line by line: "do this exact same configuration ... also switch to sonnet for the search ... I want the exact same way I'm doing on my mac on my jetson". As built: the website search's agent (`browser_discovery`) has the Playwright CLI set: navigate to any public http or https address, back and forward, snapshot, a screenshot it sees as an image, click and double click, hover, type (Enter with `submit`), press key, scroll, select option, check, tabs (at most 3), wait for, evaluate (a script run on the page, its result read as text), `browser_ask_person` and the two reports; no file upload. No search rule: it types what it judges right (a saved title, a translation, a synonym, as the person would), with every criterion in its prompt as before; no host family or allow list, no address shape, no search box or search word check, no decline only cookie rule, no start page constraint; signed out it runs in a new context with JavaScript on and service workers allowed, signed in in the person's own context with no worker or socket restriction. Who's there: `browser_ask_person` asks the person in one sentence to solve a check, accept something, sign in again or help; with a live view open the lease waits for Take over and Give back (15 minutes at most), with nobody watching it answers at once and the agent goes on another way or ends the website with its reason, so a security check, a sign in wall or a captcha no longer ends a website by itself. The run page shows the person a fixed sentence and the tab's host above the live view, never the agent's words, since a page can steer the model (B12b's rule): a deviation from showing the agent's sentence, which the owner is asked to confirm. Time: no cap on a website (no share of the sweep's 900 s, no page pause, a sweep lease's maximum of 24 hours as a guard against a dead process); the idle timer still ends a lease nobody uses and the run's Cancel stops the search, as Ctrl+C does on the laptop; the hold across people stays with a zero pause, so two people's pages of one host never run at once; the API sources keep their own caps. Both search agents (`browser_finder`, `browser_discovery`) run Sonnet (`claude-sonnet-5-5`) for every credential kind. Software WebGL is on for every sweep lease, and the browser container gets 2048m by default (`BROWSER_MEM_LIMIT` overrides it), the stack's budget 6.5G. What stays is the line: whatever it uses (a click, Enter, a key, a script it runs on the page, a request the page sends), the agent never submits an application or a form that is not a search or a cookie or consent choice (Apply, Easy Apply, Postuler, Candidater, Submit, Send application); never sends anything to a person (a message, a reply, an invitation or connection request, a comment or a post, a mail); never pays or enters payment details, signs out, deletes or changes the account, its profile or its settings; never types a password, a code or any credential (signing in is the person's, in the live view). browserd holds it (`browser/browserd/tools/line.py`): the apply, submit, danger and credential detectors and the line's words for every click, Enter, key and typing, a guard around the agent's own scripts, and a request floor (a document POST that is no search, a POST whose address applies, submits, writes to someone, pays, signs out or changes the account: refused; any GET leaves). Saving a job, following a company, accepting or declining cookies and opening any public page are allowed. Also stay: the consent of each ticked website ("A ticked website is its permission" above), browserd's egress proxy and the host firewall (public addresses only), the three tabs, the one browser queue and the sweep's preemption; apply, Full auto, Easy Apply and LinkedIn's public job search are unchanged. This replaces the B13 guards (the frozen scope and its narrow tools, "Approved for implementation" above), J18's typed saved values and the website's share of the sweep's 900 s ("One agent per website" and "Every criterion in the search agent's prompt" above), B17's signed in restrictions (GET and HEAD only, the worker and socket script, JavaScript off when signed out, the end on a sign in page), B19's guards except the line ("Let it use the search boxes" above), the browser search's page pacing (5 to 15 s, LinkedIn 10 to 30 s) and its 7 day pause after a security check ("No daily limit on any website" above), and the software WebGL "when necessary" choice of 2026-10-14 ("Browser and apply" below). macOS and Google Chrome cannot run on a Jetson (Linux arm64, and Google ships no Chrome for Linux arm64): Debian's Chromium stays, with its own user agent, never spoofed as macOS (B1: no fingerprint that lies). Risks named in the plan's B20: the agent acts freely in the person's signed in accounts (LinkedIn, Indeed, Welcome to the Jungle), friends included; LinkedIn's User Agreement forbids automated access, and with no pause an account restriction is more likely; with no time cap a sweep can hold the one browser long while applies wait; what a script does that code cannot see is held by the request floor alone. Spec: [SPEC-browser](../SPEC-browser.md#interactive-discovery-626).
- **The search reports what fits the profile, and the app keeps it (owner, 2026-11-04, browser B21):** after B20 shipped, the owner ran a search on the VIE website and pasted its agent's summary ("I tried to report both, but the app refused each one: the first with "detail_not_opened", then every retry with "title_not_on_page". The title was on the page each time I reloaded it. I stopped there, and none of the postings were saved."), and asked: "what's the problem ? also the agent must find jobs that fit with my profile, i don't want to find exactly the title job I'v set". What changes: **profile fit**: the agent's first message gets a bounded summary of the person's profile (what the scoring reads and the fixed facts the documents writers read: skills, years of experience, the titles of recent roles, seniority, languages, industries, company types; never a name, contact, address, link or any identity datum, since a page can steer the agent), and the prompt reads the wanted titles as where to start searching, not a filter: a search for each and the related terms the profile suggests, every result read, every posting that could fit opened, each one that fits reported, and when in doubt reported (the app scores every posting); unwanted titles and places and the cybersecurity rule still drop a posting after reading; **verification on any opened page**: a posting is kept when its address is a page the agent opened in this search (the last 200 it read) and its title's words are on that page (case, accents, spacing and punctuation aside), the title stored being the page's own heading or line that holds them, never shorter, so the unwanted titles and the cybersecurity rule read the page's words, else the page's one clear primary heading when it shares a word with the reported title; its employer, location and date are the labelled ones, else the agent's when on the page, else none, never a refusal; an address never opened, or a page that answered with an HTTP error status, is still refused, so no invented posting is stored; **the VIE contract rule**: a website whose every posting has one contract by nature (the VIE website, which `kind` reads as VIE from its address) never drops its postings as of another contract, ticking it being the person's choice of that contract; elsewhere several contract types are checked after reading as before (feeds, company boards, LinkedIn's public job search and the sweep unchanged). This replaces, as far as titles are concerned, the "fit the criteria" reading of "One agent per website" (J18) and of "Every criterion in the search agent's prompt" (2026-10-28) above: every criterion stays in the prompt, read as the person meant it, but the wanted titles no longer limit what is reported. It also replaces the verification of a website's posting on the last page read and by its one heading. Spec: [SPEC-browser](../SPEC-browser.md#interactive-discovery-626), plan: [B21](../tasks/browser/todo.md#b21-the-website-search-reports-what-fits-the-profile-and-the-app-keeps-it).

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
  and remembered (issue #264). Replaced on 2026-11-04 by software WebGL on every sweep lease
  ("Search on the Jetson as on the Mac", Interactive job search above); every other lease keeps
  it off.
- The MVP is one click apply: CV and letter generated automatically per found posting; the person
  clicks Apply; the agent fills Greenhouse or Ashby; the person approves; the gate submits.
- Browser B6 (late on 2026-10-14, built in #318): after a reboot the browser stays off until a deploy has checked its firewall rules
  (no restart policy); a browser image that fails to build deploys the rest without the browser.
- 2026-11-06, two browsers at once ("2 at once (Recommended)", "In this branch", "Open on click"):
  one person's agents use the browser in two slots, two windows of one Chromium, each with its
  own live view, opened when the person clicks Watch the browser; replaces one Chromium at a time
  (Interactive job search above, "Two browsers at once", for the whole entry).
- Planner defaults: EEO answers `decline` and salary `leave_blank` unless the person stores them,
  never inferred; browser memory 1536m without swap (1024m until 2026-10-30, owner approved
  after LinkedIn's search passed the guard; 2048m by default since 2026-11-04, `BROWSER_MEM_LIMIT`,
  B20); Chromium keeps its own sandbox under a custom
  seccomp profile.
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

## Application answers (2026-10-23)

The owner confirmed: keep application edits local; save reusable answers separately in Saved
answers. Editing an application does not add or change an answer-bank entry. This resolves the
conflict between the former source/success wording and the explicit answer-edit API contract
in [SPEC-apply.md](../SPEC-apply.md).

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

## Apply: one click Apply, review checkbox (2026-11-01, issue #738)

- Owner: "I don't understand at all how to apply. It must be way more easy, when I click on apply, it open a browser, navigate to the job offer, fill in the form and click on apply. I want a checkbox in settings to set if I want to review the form filled. By default its no (full auto)".
- Likes keep today's Full auto exactly as it is; only the Apply button of the job page changes.
- Free text the assistant writes is sent without the person seeing it, after the check by model; it stays readable in the run report.
- Built as the mode `direct` with the authorisation `apply_click` (the click is the approval, used once, 7 days at most) and the setting `apply_settings.review_before_send` (off by default). The global guards stay; the hold, the rehearsals, the fit threshold and the one per company rule do not apply to an explicit Apply click. The mode is fixed at the click (SPEC-apply, "Owner decision (taken on 2026-11-01)").
- 2026-11-05 (A19), One click Apply works like Claude Code with Playwright CLI on the owner's Mac. Shown the Mac beside the Jetson, the owner answered: "do it exactly like Mac: Claude Code + Playwright CLI. no screenshot, no guardrails, submit without asking. only asking if the agent do not know what to fill". Asked which protections to keep (no cybersecurity roles; no double application; no payment, account, password or legal terms; the emergency stop), he gave no preference. Asked who presses Submit right after the fill, he chose "The agent clicks it" ("Exactly like your Mac. This changes hard rule 4 in CLAUDE.md, for you and your friends."). Then: "you can add a random pacing to feel more like human like between 3 to 8 seconds." Kept by the orchestrator, since each is a hard rule the owner did not change or adds no step for the person: hard rule 2 (never apply, read before the run, a match ends it `blocked`), the emergency stop (`APPLY_SUBMIT_ENABLED` and the administrator's pause, read before the run, before every act of the agent and again before each act on a submit element), no second sending (`submit_unknown` is never sent again by the app itself, one Apply click gives one run; after a control that may have sent the form, only the person's Try again, or their answer to the question asked there, runs the agent once more, and it looks on the site first: the orchestrator, round 1 of the review), hard rule 3 (an e-mail application stays the person's Send); a password or a one time code box stays the person's (hard rule 7). Built: one agent (`apply_agent`, `api/app/apply/applier.py`) with B20's Playwright CLI tools, the documents' upload and `apply_done`, on a browserd lease of purpose `apply_agent` (any public address, a submit element only with the lease's capability), a pause of 3 to 8 seconds between two of its acts on the page (no pace per host across applications). It replaces, for One click Apply only, the 2026-11-01 build above (adapters, answer resolver, form agent, read back, screenshots, the gate's decision, the check by model); Review first, Full auto, Apply myself and the e-mail strategy are unchanged.
- 2026-11-06 (A20), the apply agent never asks; Review first shows every field it filled, then one Confirm. Shown the application page of a One click Apply on jobs.gem.com, which asked him a question and left "Resume" for him, the owner wrote: "do this in a subagent : when applying, i always have this kind of question, tbh i don't care, when i click on apply in full auto mode, I just want that the agent do all the work to apply and he never ask me a question. when I'm not in full auto mode, I want to see all the input he filled in but don't matter of the website or whatever, then a button to confirm." Reading: "full auto mode" is the Apply button with the review box unticked, his own words of 2026-11-01 ("I want a checkbox in settings to set if I want to review the form filled. By default its no (full auto)"); Likes' Full auto is not this request and stays as it is. Box unticked (`direct`): the agent has no `browser_ask_person` and never creates a question; it fills every required field itself (a neutral option such as "Prefer not to say" for a personal fact, free text from the profile), names each such answer in the run report as chosen by the assistant (label, value, one line why), first looks for a way to apply without an account, and ends `failed` with one plain sentence of what the person can do for a sign in, a password or code box, a captcha or a payment. Box ticked (Review first from the Apply button): the same agent fills any site on a lease of purpose `apply_review` whose acts on a submit element are refused (a wizard's step button aside), names the send with `apply_ready`, and the job page lists every field it filled from its own recorded acts, by step, with one "Confirm and send" and Cancel; on Confirm the gate clicks the named control in the held session, and the agent then only reads the confirmation. Hard rule 4 is unchanged ("in Review first and Full auto no model clicks Submit, the apply gate does"). What stays: Likes' Full auto and Review first after a Like, Apply myself, the e-mail strategy (hard rule 3), the emergency stop, the pacing, hard rule 2 before the run, no second sending; an A19 question left from before still answers and resumes.

## Product audit: chapters 1 and 2 (2026-11-04 and 2026-11-05)

The owner's answers during the product audit, in the session that built them.

- 2026-11-04, Find jobs: "an url is an url, simplify a lot the modal. remove alert emails. company
  job board is exactly like a website so it should be in url. you must precise that the website can
  be generic or a compnay job board with examples". Built as one list of addresses: a known job
  website keeps its permission and risk sentence in its line (jobs J17 unchanged), a job feed or a
  company job board is read through its own API, any other address is a website of the person's
  own. The starter boards are ideas folded under "More company job boards", never saved by
  themselves. Stored alert e-mail settings are left out of the save (kept by the server).
- 2026-11-04, the first login wizard gets a step "Websites to search" with the same list; Start
  with an empty list opens Find jobs. The step is never sent to the onboarding api.
- 2026-11-04, Gmail: "hide" while the server cannot connect it.
- 2026-11-04, deploy: "Use gha to deploy, no gate" for the chapter 1 batch (#746, run 130).
- 2026-11-05, chapter 2: yes to a bottom tab bar on phones (Jobs, Discover, Applications,
  Profile; Runs moves to the user menu as Activity); yes to honest Apply wording (the wizard shows
  the Apply review box; the Applications page no longer promises approval for everything); the
  model key: "Yes but at last possible step" (an optional inline field on the wizard's last step;
  replaced on 2026-11-07 by the first step, Model access, see "the wizard's first steps" under
  Opportunities and Full auto);
  the posting page: "Yes. Also want the salary easily accessible" (Your application right under
  the title, the pay in the header, the Jobs list and the Discover card); the Account page: "Only
  websites we are signin" (only the websites the person is signed in to); Find jobs: "should be a
  sheet from bottom on mobile. All the text an inputs must be fully aligned".
- 2026-11-05, clicks (owner: "count all the Numbers of click ... find the best flow to reduce must
  as possible these clicks") and the profile (owner: "except for the general infos, just put
  informations on a .md should be well rendered and editable. I want an agent to find the best
  job titles according to the profile. When I change something in the cv it should change in the
  profile and vice versa. Everything in profile can be done manually or with a chat"). The
  owner's answers to the seven questions:
  1. The profile's information other than the general infos (identity) is a markdown document
     that a model interprets: "No, I want a markdown that a model interpret, not just a view over
     today's field". The markdown is the person's source; the structured fields the code needs
     (skills terms for the fit, employers and dates, answers) are derived from it by a model.
  2. Fill my profile: locked facts that fill an empty slot join "Accept all additions", and the
     run starts from one button that shows the token range (replaces the 2026-10-10 start dialog
     and SPEC-ingestion's rule that locked facts never join Accept all, for empty slots only).
  3. The job titles agent refreshes by itself after a profile fill or a skills change, at most
     once a day.
  4. Postings are rescored by themselves after a skills change, with the possible document cost
     said before saving.
  5. Apply with no documents writes them and then sends; Apply also appears on Jobs list rows.
  6. Applications: "Approve all ready" with one confirmation naming every company and document,
     and "Remember this answer for next forms".
  7. The CV editor's fixed facts save to the profile, and the base CV's own fixed block goes
     after a one time merge.
- 2026-11-05, the job title ideas (X2), the owner's answers:
  1. A run of about 20,000 to 39,000 tokens: "yes".
  2. What the agent reads: "uploaded cv, the profile based on the computer or just a chat with
     the agent to tell what we want to do is enough. ... he can read everything to help the user
     find best jobs". The agent reads the whole profile, the documents the person uploaded and
     what the person writes to it; any one of them is enough to start.
  3. "security was only for me, for my profile, remove these from permanent memory bc its just
     for me". The rule against cybersecurity roles is the owner's own never apply rule, in his
     profile; it is not a rule of the app. CLAUDE.md's hard rule against it and the app's own
     rule in code (`never_apply.CYBER_RULE`) are removed; every person's own never apply rules
     still hold everywhere they did. This replaces the standing rule of 2026-10-06 and the
     cybersecurity lines of the module sections below it.
  4. "More ideas" adds to the ideas shown: "add to them".
  5. In Find jobs, the line that names the Account page without a link: "yes".
- 2026-11-05, the profile in Markdown narrowed (owner: "sorry, only facts and skills should be in
  plain text or markdown. other fields remins on well structured inputs"). Only Facts and Skills
  are written as plain text or Markdown; the model interprets the Skills text into the skills
  section the fit reads (answer 1 above, for Skills). Experience, education, languages, the
  answers, identity and the search stay structured inputs.
- 2026-11-05, the Answers too (owner: "anwsers tab should be in the plain text/markdown too"):
  Facts, Skills and Answers are written as plain text or Markdown; the model interprets the
  Skills and the Answers texts. A change to a yes/no consent, an EEO answer, a work authorisation
  or a salary figure is listed on its own line and applied only by the person's Apply click.
- 2026-11-07, About you after the classic editor went (owner, asked through questions):
  1. "Plain editor without a key": while no model key is saved, About you shows the skills and
     the saved answers as simple fields to add, change or remove by hand, saved as the skills and
     the answers sections like the former Skills and Answers tabs (no model); the regular
     expressions stay in Advanced matching. With a key About you works as above (text read by
     the model). After a save the part is written again from the profile, by code, when its text
     said what the profile held.
  2. "Bring both back": fact parts move up and down again (arrow buttons, by the keyboard too;
     no drag), and the order is saved as a revision of the facts section, so the fact base keeps
     the order of the parts; the meter of the fact base's size against its 200 KB limit is back,
     amber from 90%, red over it.
- 2026-11-05, the search settings (owner: "search setting should be in the settings of the
  user"): the Search tab leaves the profile for the user's Settings, beside the Applications
  settings (`/settings/search` and `/settings/applications`); the user menu's Settings opens
  Search first, and `/profile?tab=search` leads there.
- 2026-11-06, Apply to a selection (owner: "in the job list and map, i want being able with the
  multi select to apply at all selected"): the Jobs list's and the map's selection get Apply. One
  confirmation names every posting it applies to and every one it leaves out with its reason;
  its click is the approval of exactly those listed, each started as one Apply click.
- 2026-11-06, the job title ideas: "I don't want day limit for more ideas". "More ideas" and the
  messages to the assistant have no daily limit; one run at a time and five clicks a minute stay
  as guards against repeated clicks.
- 2026-11-06, Apply on a Jobs list row and Apply to a selection (owner: "yes confirmation, no
  caps"): a row's Apply opens the confirmation of a selection of one (the posting, its documents
  ready or written first, what the review box makes it do) and starts on "Apply to 1"; the
  posting page's Apply keeps its one click. Apply to a selection leaves the company caps and the
  daily safety ceiling out, as one click Apply does.
- 2026-11-07, the profile in tabs (owner: "review the wole profile page, it very weird, follow
  all the design guidlines", then "I don't want too much scroll, you can put tab"): `/profile` is
  no longer one page. Five tabs, each its own address: CVs (`/profile`), Experience
  (`/profile/experience`), About you (`/profile/about`), Contact (`/profile/contact`), Advanced
  matching (`/profile/matching`). The rail (the person, Ready for, More) stays beside every tab;
  Ready for links to the tab that fixes each use. The old `?tab=` addresses and `#contact`,
  `#experience`, `#about-*`, `#advanced-matching` land on their tab. Leaving a tab with unsaved
  changes asks first, as any other navigation. Replaces "one page, no tabs" of 2026-11-05.
- 2026-11-07, the CV of the welcome wizard (owner: "when i create the profile, I've put my cv but
  then when I'm on my profile, I have an other, why ? it should replicate the one I upload"):
  the first PDF or Word file added in Your profile is the CV, without a click; "This is my CV"
  stays on the other ones to take its place. Its conversion into a base CV with its look is asked
  for as soon as the file is read, whether the documents consent is given or not (the server
  holds it until the consent), and goes on when the person moves to the next step. Before, the
  CV had to be marked by hand and was converted only while the step stayed open, so a person
  who went on got no CV from the file, and "Build my CV from my profile" wrote another one.
- 2026-11-08, reading files into the profile (owner: "I've put a generic cv + my linkedin profile
  but got only 61% completness", "why I have 2 [employer] and no [current employer]", then "yes and a. I
  don't want help to build my profile, I want you correct all the process so it will works
  perfectly for all the users"):
  1. Values that agree merge into one proposal instead of a conflict: the same text but for
     case, spaces or dash style; a value that only fills fields the other leaves empty; lists
     joined; phone numbers compared on their digits.
  2. One entry under two names is one entry: employers with the same dates whose names contain
     one another, schools with the same years and the same distinctive words, a language named
     in English or French. The employer key of 2026-10-21 stays; the matching is added on top.
  3. A years of experience figure a document states wins over the one computed from the dates.
  4. The welcome wizard's Building step also writes a disagreement or a computed value into an
     empty field, taking the value of the person's CV first; nothing the person holds is
     replaced. Fill my profile keeps "nothing picks for the user" for everything else.
  5. Completeness counts "Anywhere" as a place and no contract type as any contract; salary is
     answered by its stance.
  6. The wizard asks the answers (choice "a"): the Pay step also fills the salary answer, the
     Work step asks notice period, earliest start and work authorisation, the Looking step asks
     the motivation.
  7. The reasons a reading refused items are kept with the run and shown in its review.
- 2026-11-08, Advanced matching removed from the profile (owner: "remove Advanced matching from
  the profile"): the profile has four tabs (CVs, Experience, About you, Contact); the Advanced
  matching tab and its Try a posting are gone, and `/profile/matching` lands on the Skills of
  About you. Skill categories and requirements already stored as regular expressions keep
  scoring as stored and the Skills fields keep them on every save; About you says how many stay.
  No screen of the app edits them any more; a profile file imported with some keeps them.
