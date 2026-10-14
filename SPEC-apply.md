# Spec: apply

Module of [CAPABILITY-MAP.md](CAPABILITY-MAP.md). Depends on `agents`, `documents`, `browser`, `jobs`, `profile`, and on `mail` for applications by e-mail. Umbrella issue: #69; the review queue after a Like is #78, Full auto is #79. Plan: [tasks/apply/todo.md](tasks/apply/todo.md). Intent: `docs/intent/swipe-onboarding-opportunities.md` of the job workspace (decisions of 2026-10-10). The owner's decisions of 2026-10-14 are taken (below); nothing is built.

## Objective

Once a posting has its CV and letter (documents module), the app applies: it opens the application form in the person's browser on the Jetson, uploads the documents, answers the questions from the person's profile, and submits, then records the application, moves the posting to `applied` and keeps a report with screenshots of the form before and after. The person chooses how much they want to see: they apply themselves with the agent's help, they approve each filled form, or, in Full auto, a Like is enough.

User stories:
- As a person, I click **Apply** on a posting, watch the agent fill the form live, check the answers and the screenshot, click **Approve and submit**, and the posting moves to `applied`.
- As a person, I click **Apply myself** and the form opens in my browser with my documents ready to upload; I submit it and the app records it.
- As a person who Likes postings, I find each filled application in the **review queue**, edit an answer, approve or reject it (#78).
- As a person in **Full auto**, a Like leads to a submitted application within limits I set, and I can cancel it during the hold, pause everything with one switch, and read what was sent (#79).
- As a person, when a site needs an account, I sign in or sign up myself in the live view, and the application goes on where it stopped; the next time, I am already signed in.
- As a person, I click **Complete my profile on this site** (Workday, LinkedIn, a company portal), review each section the agent filled from my profile, and approve it before it is saved.
- As a person, I never see an answer the agent made up, a box ticked that commits me to more than the application, or two applications to the same role.

## What the command line teaches (`engine/stage.py`, `engine/vie.py`, `AGENTS.md` "Applying" in the parent folder)

- `stage.py` opens the form in a real Chrome over CDP, rewrites the posting address to the form (Ashby `/application`, Lever `/apply`, Greenhouse's embed page, a VIE offer's external Apply link), uploads the CV to the first resume input and the letter to a cover letter input (or pastes it in the additional information box), then answers yes or no groups, radios, checkboxes, native selects, text inputs and react-select comboboxes from a table of patterns, and prints what it **left** (EEO, salary, anything it cannot map). It never clicks Submit unless told to, and its `submit` reads the page afterwards for a confirmation or the form's errors.
- Lessons kept: headed, persistent profile, human pacing and dwell pass Ashby's invisible reCAPTCHA where headless fails; Ashby yes or no groups are `button[data-option=yes|no]`; Greenhouse selects are react-select comboboxes (type, then click the option); Google Careers' résumé goes through a file chooser and its element ids change on every render (locate by label or role); ElevenLabs' location is a country list; a VIE offer applies on the site's own panel only for a signed in candidate with a complete profile.
- What changes: the table of answers was the owner's own (his name, his dates, his choices); here it is built from each person's profile. `stage.py` ticked arbitration and terms boxes by pattern; here such boxes follow "What the agent may never do". The harness that ran the command line refused to automate the final click; here the click is made by the app's code, never by a model, under the rules of "Who clicks Submit".

## Owner decisions (taken on 2026-10-14)

1. **Full auto: guarded defaults.** The owner's words: "Guarded defaults", as the draft recommended. Full auto is **off until the person turns it on** (Review first is the default, intent of 2026-10-10). Then: only postings at or above the person's fit threshold (decision 2); at most **one application per company in 30 days** (unless a company cap of the person says otherwise); a **hold of 15 minutes** between the decision and the submission, during which a Cancel stops it; **Ashby, Greenhouse and Lever forms first** (e-mail applications once A14 lands and LinkedIn Easy Apply once A16 lands, decision 4), everything else goes to the review queue; and the **first 3 applications in Full auto are rehearsals** (filled and screenshotted, not submitted, confirmed by the person) before the mode goes live.
2. **Daily limit: by fit score only.** The owner's words: "By fit score only". The fit threshold decides which postings Full auto applies to. It is the same family of setting as the documents module's automatic generation (PR #189: `search.documents: {postings, min_fit, daily_limit}`): the same name, `min_fit`, from 0 to 100, set in the same Search options area. When the person turns Full auto on he confirms the threshold, prefilled with `search.documents.min_fit` when that setting is `min_fit`, else 80 (a builders' default, shown and editable). There is **no fixed daily number of applications**. A **safety ceiling of 30 submissions a day** (`safety_ceiling`, in Advanced settings, counted in the time zone of `search.sweep_schedule.timezone`) is a runaway backstop only: when it is reached, the rest of the day's items go to the review queue with the reason `safety_ceiling`, and nothing is lost. The old fixed 10 a day is removed.
3. **Accounts on job sites.** The owner's words: "The agent open playwright, user sign in or log in, we save all the playwright profile per user so next opening will be connected. User can ask to complete his profile on the website with all informations." So: the app never chooses a password and never creates an account; when a site needs one, the agent opens the page in the person's browser session on the Jetson and the person signs in or signs up himself in the live view (the sign in hand off of SPEC-browser); the per person browser profile is persisted, so the next run is already connected; and a new action, **Complete my profile on this site**, lets the person ask the agent to fill the site's candidate profile (Workday, LinkedIn, a company portal) from the app's profile, with the guardrails of a form fill (no invented answers, screenshots, a review before saving where the site allows).
4. **LinkedIn: full use.** The owner's words: "Full use". Searches for jobs and Easy Apply, in Full auto too, from the person's own signed in session. Human pacing, a low request rate and the per company and per day guards stay (LinkedIn's own pace: one Easy Apply every 10 minutes, 15 a day at most). It is a per person switch, off by default; turning it on shows one sentence that the person accepts once: LinkedIn's terms forbid automated use of its site and the account may be limited or closed (the text is in SPEC-browser). Without that acceptance, no LinkedIn work starts.

## Modes, and who clicks Submit

| Mode | Who fills | Who clicks the form's Submit | Authorisation the gate accepts |
|---|---|---|---|
| **Apply myself** (manual) | the person, in the live view; the agent may prefill identity fields and uploads on request | **the person**, on the site's own button in the live view | none: the app never clicks; it records the submission when it sees the site's confirmation, or when the person clicks "I submitted it" |
| **Review first** (default; Apply button and the queue of #78) | the agent | **the app's code** (`apply.gate.submit`), after the person's **Approve and submit** on that exact filled form | `person_approval`: the person's click, bound to the hash of the answers and the document versions it showed, valid 24 hours, used once |
| **Full auto** (#79) | the agent | **the app's code**, after the hold, if every guardrail passes | `full_auto_like`: the person's Like on that posting while Full auto was on, recorded with the settings in force |

- **No model ever clicks Submit, in any mode.** The agent's click tool refuses any element the submit detector marks (SPEC-browser). The one code path that clicks it is the private function of `apply/gate.py`, after the decision below.
- **Dry run:** any application can be run with `dry_run`: the whole flow and every check of the gate, then it stops before the click and records `rehearsed` with the screenshot. Rehearsals of Full auto are dry runs.

## The flow of one application

1. **Start** (the Apply button, a Like, or an approval in the queue): refusals before anything is spent (below); an `applications` row; a browser lease is queued.
2. **Fill:** the lease opens the form address (the ATS strategy rewrites the posting address as `stage.py` does); the documents' versions to send are fixed (the approved version of each, else the latest rendered one, refused when the fact check has an open issue: documents decision 4); the strategy's adapter fills what it knows deterministically; the answer resolver fills the rest from the profile; the agent handles only what neither recognises; everything else is **left**. The posting moves to `staged` (the app filled its form: the mail module's binding rule relies on this).
3. **Screenshot "before"** (full page), and the list of fields with each value and its source.
4. **Decision** by the gate, then **submit**, then **screenshot "after"** and the outcome.
5. **Record:** `applied` through the jobs module's `set_status` (event `via: "apply"`, `applied_on` filled), the document versions frozen with reason `apply` (documents D12 contract), the run report.

In Review first, step 2 runs when the person clicks Apply (they watch) or after a Like (queued). The approval may come hours later: the submit lease re-opens the form, refills it from the **approved answers only** (no model call), checks that each value reads back equal to the approved one, takes the "before" screenshot again, and submits. A difference refuses the submit (`form_changed`) and returns the item to the queue.

## States

`applications.state`: `queued` → `filling` → `needs_person` (captcha, sign in, an account, a required field left) or `ready` (filled, waiting for approval) → `approved` or `held` (Full auto's hold) → `submitting` → `submitted`, or `submit_unknown`, or `refused_by_site` (the form showed errors after the click), or `failed`. Also `rehearsed`, `cancelled`, `blocked` (a guardrail refused it, with its code), `posting_closed`. A `submit_unknown` application is **never submitted again by the app itself**: a second application is worse than a missed one. The person sees "We could not confirm this application. Look for a confirmation mail or on the site." with "It was submitted" and "Try again" (a new approval). A confirmation mail from the ATS still moves the posting to `applied` through the mail module.

## Per ATS strategies

Each strategy is a small adapter: how to reach the form from the posting address, how to recognise the confirmation and the errors, which fields are standard, the submit button, known traps. Recognised by the posting's host first, then by the page (an embedded Greenhouse or Ashby form on a company site).

| Source | Reach the form | Notes | Full auto |
|---|---|---|---|
| **Ashby** | `jobs.ashbyhq.com/<org>/<id>/application`; `?embed=js` when the hosted page answers not found | yes or no button groups, location typeahead, "Autofill from resume" ignored (the app fills), invisible reCAPTCHA at submit: pacing and dwell matter | yes |
| **Greenhouse** | the hosted board page, or the `embed/job_app` page a company site frames | react-select comboboxes, a voluntary EEO block at the end, sometimes a code sent by e-mail to confirm (read through the mail module when connected, else the person types it) | yes |
| **Lever** | `jobs.lever.co/<org>/<id>/apply` | resume parsing fills name, e-mail and phone (checked against the profile and corrected); native selects; "Additional information" carries the letter when no letter input exists; hCaptcha at times | yes |
| **Workday** | the tenant's `myworkdayjobs.com` page, Apply, then "Apply manually" | an account per tenant, which the person creates or signs in to himself (owner decision 3), a multi page wizard, heavy pages (SPEC-browser memory) | no: review only |
| **LinkedIn Easy Apply** | the posting's Easy Apply modal | owner decision 4, only after the person's LinkedIn opt in; the person's own signed in session, human pacing; a multi step modal; the posting's "Apply on company site" link is followed into the strategy of that site instead when present | yes with the opt in (A16), at LinkedIn's own pace |
| **Google Careers** | the role's Apply, signed in with the person's Google account | the résumé through the file chooser, elements found by label and role | no: review only |
| **Business France VIE** | the offer page of mon-vie-via, signed in with a complete candidate profile | the site's own panel: CV, letter, a message; an offer with an external Apply link follows it into that site's strategy | no: review only |
| **E-mail** ("send your CV to ...") | a new mail with the PDFs attached, written from the letter | sent only through the mail module's `mail.gate.send`, which A14 extends with the authorisations of this module; the address must be on the company's own domain in Full auto | yes, once A14 lands |
| **Any other site** (Workable, SmartRecruiters, Teamtailor, Welcome to the Jungle, a custom form) | the posting's Apply link | the generic strategy: the agent reads the snapshot and fills; it names the submit element (`designate_submit`), which the "before" screenshot highlights and the person approves | no: review only |

## Accounts and signing in (owner decision 3)

- The app never creates an account, never chooses a password and never types one. When a form needs an account (a Workday tenant, a company portal, Google Careers, the VIE site, LinkedIn), the adapter or the agent calls the sign in hand off of SPEC-browser: the run pauses with `needs_person: signin`, the person signs in or signs up himself in the live view (a password, "Sign in with Google", an e-mail code), clicks "I'm signed in", and the run continues at the same page. In Full auto no one is watching: the item goes to the review queue as "Needs you: sign in to <site>", and the hand off starts when the person opens it.
- The person's persisted browser profile keeps the session, so the next application on that site is already connected. An expired session is the same hand off again.
- Accepting an ATS account's terms is the person's act, made by him in the live view.

## Complete my profile on a site (owner decision 3)

The person asks the agent to fill the candidate profile that a site keeps (Workday's "My Information" and "My Experience" pages, LinkedIn's profile sections, a company portal) from the app's profile, so later applications there start from a complete profile. It is never started by the app on its own, never part of Full auto, and it is not an application: no posting changes.
1. **Start:** the person gives the site (an address, or the shortcut offered after a "Needs you: sign in" notice, or LinkedIn from Account when the opt in is on). Refusals before anything is spent: LinkedIn without the opt in; an address that is not http or https. A `profile_fill` lease (flagged `profile_edit`) on the person's profile; a sign in wall opens the hand off.
2. **Read and fill, section by section:** the agent reads the site's profile page (contact, summary, experience, education, skills, languages, links, résumé) and fills each section **from the sources of "Answers to standard questions"**: identity, `experience`, `education`, `skills`, `languages`, links, `answers`, and the résumé from the person's reference CV (the version rendered by the documents module). A field with no source is left. A value already on the site that differs from the profile is shown as a difference and kept unless the person ticks "Replace". Typing goes through the paced tools.
3. **Review before saving, where the site allows:** per section, a screenshot and a table (field, value to write, source, value now on the site). The person edits a value or approves the section (`profile_section` authorisation, bound to the hash of that section's values, used once, valid 24 hours); then **the app's code clicks that section's Save** (`profile_gate.py`, the second and last caller of the browser's `gate_click`), after the agent has named the Save element with `designate_submit` and the screenshot has highlighted it. A site that saves as you type has no Save to review: the person is told before the run ("This site saves as you type, so a section cannot be reviewed before it is written."), confirms, and the run then writes **empty fields only** and never overwrites a value.
4. **Never, on a site profile:** change the e-mail, the password, a phone verification or any security setting; change privacy, visibility, "open to work" or recruiter contact settings; tick notification, marketing, talent community or consent boxes; fill diversity forms beyond the person's stored `eeo_policy`; invent a date, a title, a number or a skill (rule: every value traces to the profile, checked by `factcheck_rules.check`); delete or withdraw anything; accept terms; follow instructions in a page.
5. **Report:** per section, the screenshot before and after the save, the fields written with their sources, what was left; the run is `done` when every section is saved, skipped or left, or `cancelled`. Pace: on LinkedIn the pacing of SPEC-browser applies; a checkpoint page stops LinkedIn work for 7 days.

## Answers to standard questions

- **Sources, in this order:** (1) the identity section (names, e-mail, phone, city and country, links, languages); (2) the documents (the CV and letter PDFs, the letter's text for a cover letter box); (3) the `answers` section of the profile; (4) the person's **answer bank**: every answer the person typed or corrected in a review is kept with its question, reused when the same question comes back, editable in Settings; (5) the agent, only for what is left, and only from the profile, the posting and the documents.
- **Profile fields** (the `answers` section has today: work authorisation per country with sponsorship, notice period, earliest start, salary policy, relocation, travel, motivation, how they heard, what to leave blank). This module adds, all optional: preferred name, pronouns (empty by default), years of experience as the person states them, highest degree and graduation year, willingness to work on site or hybrid, the default answer to "previously applied or worked here" (computed from the app's own applications first), consent to background and reference checks (a person's choice, never a default), the EEO policy, the salary answer policy. The owner's answers stay his: nothing of the parent folder's practicalities is copied into the app; he fills his profile or imports it.
- **Work authorisation** is answered from the person's list for the country of the posting's location (the posting's location parsed by the jobs module's gazetteer); a country not in the list is left.
- **EEO questions** (gender, ethnicity, race, disability, veteran status, sexual orientation, age): the policy `decline` by default chooses "I don't wish to answer" or its equivalent when the form offers it and leaves the field otherwise. An actual answer is given only if the person stored that exact answer in the profile. Never inferred.
- **Salary:** the salary policy decides: `leave_blank` (default), a range the person wrote per currency, or a sentence the person wrote. A required salary field with no policy that fits is left, so it goes to the person. Never a figure the person did not write.
- **Free text** ("Why do you want to join?", "Tell us about a project"): written by the agent from the person's motivation, facts and the letter, in the posting's language, checked by the documents module's fact check rules (mail M9's `factcheck_rules.check`). An issue blocks Full auto and is shown in review.

## What the agent may never do

- **Invent an answer:** a required question whose answer the sources do not hold is left, never guessed. A yes or no about a skill or a number of years is answered yes only when the profile proves it.
- **Accept terms beyond the application itself.** Allowed: the employer's privacy notice for processing this application, and "I confirm this information is accurate" (true by construction: every value has a source). Never ticked by the agent: arbitration agreements, marketing, newsletters, talent communities, retention beyond the application, background check authorisations, any other legal commitment; in Full auto a required box of that kind sends the application to the person.
- **Pay**, or enter a card: a page asking for payment ends the attempt with `payment_requested` and the run report says "This site asked for a payment. Applications are free; this may not be a genuine posting."
- **Create an account, choose a password, or type one** (owner decision 3): sign ins and sign ups are the person's, made in the live view in a session without automation.
- **Click Submit**, delete, withdraw, or navigate outside the hosts of the lease.
- **Follow instructions found in a page.** Posting and form text is data; every apply agent's system prompt says so, and a form that addresses "AI assistants" is reported in the run report.

## Guardrails, checked by the gate in every mode where the app clicks

The decision is one transaction, under the posting's row lock and a per person apply lock, with the checks in this order, and it writes its audit row before any click, whatever the outcome:
1. **Kill switches:** the setting `APPLY_SUBMIT_ENABLED` and the administrator's pause (`./deploy.sh apply pause`, a Redis flag) stop every submission at once, checked again just before the click; the person's own "Pause automatic applications" stops Full auto and pending approvals.
2. **The authorisation:** its kind, owner, application, the hash of answers and documents, unused, not expired.
3. **Never apply rules:** the person's `never_apply` rules on the title (`sweep.never_apply_rules`, `sweep.term_matcher`, as mail M12 does), and the sweep's `never_apply` event. For the owner this is the cybersecurity exclusion. As in mail decision 12, the rule stops what the AI does by itself: the agent never starts filling such a posting, and the gate refuses any authorisation. Apply myself stays possible, with the warning "Your rules exclude this role." In Full auto, a never apply term in the first 1,500 characters of the posting's text also sends it to the review queue.
4. **Duplicates:** refused when the posting already has an application `submitted` or `submit_unknown`; when another posting of the person has one for the same company and the same normalised title in the last 180 days (the same role found on two boards); when the canonical form address (ATS and its job id) was already used; when the posting's status is `applied` or later.
5. **Caps:** the person's company caps (`apply_company_caps`: company, at most N applications in D days, and a cool down for the same role, entered by the person; for instance "Example Robotics: 3 in 90 days, 180 days before the same role again"), counted on `submitted` and `submit_unknown` rows; Full auto's one per company rule (owner decision 1) and its safety ceiling of 30 submissions a day (owner decision 2); the pace per ATS host across all people (one submission every 3 minutes, 20 a day, the Jetson's one address; LinkedIn: one every 10 minutes, 15 a day).
6. **Documents and text:** the versions are frozen by `documents.freeze(version, reason="apply")`, which refuses an open issue the person did not acknowledge; in Full auto, no issue at all on the documents or on the agent's free text, and the check by model passed.
7. **Full auto only:** the posting's fit at or above the person's `min_fit`, a supported ATS, no field left, no captcha seen during the fill, the hold elapsed, the rehearsals done.
8. **LinkedIn only:** the person's acceptance of the current LinkedIn sentence is recorded and no checkpoint pause is active.
A refusal in Full auto moves the application to the review queue with the reason, never to the bin.

## Audit trail and run report

- `application_events`: every step with its time and code (created, refused with code, lease, filled counts, left, screenshot, approval, authorisation, decision, click, confirmation seen or not, status changed, cancelled, kill switch), ids and counts only; the answers themselves live in `application_answers` (label, kind, value, source, required) and the images in `application_screenshots` (JPEG, at most 2 MB each, "before" and "after" kept as long as the application; step and error images 30 days).
- The run report on the job page: the timeline, the fields with their sources (profile, document, answer bank, agent, person, left), the two screenshots, the cost in tokens, the duration, and what the person must do if anything.
- Nothing of an answer or a screenshot goes into logs, errors, job arguments or arq results (the canary of mail M1, reused).

## Failures and retries

- Before the click: a timeout, a 5xx page, a browser crash: up to 2 retries, after 10 and 60 minutes, then `failed`. A missing selector of an adapter (`form_changed`): the generic strategy takes over in Review first; Full auto sends it to the queue.
- `needs_person` (a captcha, a sign in or sign up wall, which is the hand off of SPEC-browser, a left field, an e-mail code with no mail connected): the person is told on the job page and in the queue, the live view opens on the tab; after 30 minutes without them the lease ends and the application waits in the queue.
- After the click: a confirmation (the adapter's patterns, `stage.py`'s `SUCCESS` as the base) is `submitted`; the form's errors are `refused_by_site` (the fields named, nothing resubmitted; in Review first the person fixes and approves again); no answer in 60 s is `submit_unknown`.
- A posting that answers "no longer accepting applications" or 404: `posting_closed`, and a proposal to move the posting to `closed`.

## Cost per application

The model is chosen by the credential (`model_by_credential`) and each run carries an estimate and a ceiling of twice that (`token_budget`), priced by ingestion's `pricing.py`. Estimates to be measured in A5 and A8:
- **Ashby, Greenhouse, Lever with a known adapter:** no model for standard fields; one call for the unrecognised questions and the free text (about 15,000 tokens in, 2,000 out) and, in Full auto, one check by model of that text (about 6,000 in, 500 out). About 25,000 tokens per application.
- **The generic strategy:** an agent loop over snapshots (8,000 to 20,000 tokens each), at most 30 turns: 150,000 to 400,000 tokens in (mostly cache reads), 10,000 out.
- **The submit lease after an approval:** no model.
- **Complete my profile on a site:** one model call per section, about 10,000 tokens in and 1,500 out on a site with an adapter (Workday, LinkedIn), so about 50,000 tokens for a five section profile; an unknown site uses the generic agent loop, capped as above. Measured in A18.
Automatic runs share the person's daily cap of automatic runs and the agents runtime's two concurrent runs; a run without a credential falls back to deterministic filling and leaves the rest.

## API

| Method and path | Does |
|---|---|
| POST /api/postings/{id}/applications `{mode: "manual" \| "review", dry_run?}` | start an application (refusals answered at once with their code) |
| GET /api/applications?state= ; GET /api/applications/{id} | the queue and one application with its fields, events, screenshots |
| PUT /api/applications/{id}/answers/{field} | edit an answer before approval (saved to the answer bank) |
| POST /api/applications/{id}/approve `{answers_hash}` ; POST /api/applications/{id}/reject ; POST /api/applications/{id}/cancel | the person's decisions |
| POST /api/applications/{id}/submitted ; POST /api/applications/{id}/retry | "I submitted it", "It was submitted", "Try again" |
| GET /api/applications/{id}/screenshots/{n} | one image, owner only |
| GET/PUT /api/apply/settings ; GET/PUT /api/apply/caps ; GET/PUT /api/apply/answer-bank | mode, Full auto settings (`min_fit`; Advanced: `safety_ceiling`, hold length), pause switch, company caps, saved answers |
| POST /api/site-profiles `{url}` ; GET /api/site-profiles ; GET /api/site-profiles/{id} | start "Complete my profile on this site", list the person's runs, one run with its sections, fields and screenshots |
| PUT /api/site-profiles/{id}/sections/{n}/fields/{field} ; POST /api/site-profiles/{id}/sections/{n}/approve `{values_hash}` ; POST .../skip ; POST /api/site-profiles/{id}/cancel | the person's decisions per section, with "Replace" chosen per field |

## Frontend

Job page: an **Apply** section (Apply, Apply myself, the live view while it runs, the fields table, the "before" screenshot, Approve and submit with a confirmation that names the company and the documents, Cancel, the run report). `/applications`: the review queue (cards with the company, title, the screenshot, what is left; Approve, Edit, Reject; Full auto's held items with a countdown and Cancel). Settings: Applications (Review first or Full auto with its fit threshold `min_fit`, the pause switch, company caps, saved answers; an Advanced section holds the safety ceiling and the hold length). Account, "Your browser": the slot "Complete my profile on a site" and the run page `/site-profiles/{id}` (sections, screenshots, the fields table with the current value on the site, Approve, Skip, Replace). Mobile friendly; the text for people uses no dashes as separators.

## Testing strategy

- Unit: the answer resolver per field kind and source, the EEO and salary policies, the term box classifier, the duplicate and cap rules, the authorisation and its hash, the state machine.
- Integration: a fake ATS server (`tests/ats_forms_stub.py`, invented companies) serving Ashby, Greenhouse, Lever, Workday like, generic and e-mail postings with captcha, error and slow variants, recording what is submitted; the real browser container; the fake model backend. Every task proves that a submission reaches the fake only through the gate, and that person B cannot read or approve A's application.
- Journeys on the Mac stack; on the Jetson the owner's checkpoint.
- **No test ever submits to a real company.**

## Boundaries

- **Always:** the gate before any click by the app; screenshots before and after; the person can watch, take over, cancel and pause.
- **Ask first:** a new ATS in Full auto; raising a cap's default or the safety ceiling's default; any captcha service.
- **Never:** a model clicking Submit or a site profile's Save; the app choosing a password or creating an account; an invented answer or an invented profile entry; a ticked arbitration or marketing box; a payment; a second submission after `submit_unknown`; a submission to a never apply role by the AI.

## Success criteria

1. On the Jetson, the owner clicks Apply on a real Greenhouse or Ashby posting he chose, watches the form fill, approves, and the posting is `applied` with both screenshots and the frozen documents.
2. The review queue holds the applications of his Likes, and an edit there is reused next time.
3. In Full auto, rehearsals, then real submissions, stay within the fit threshold, the one per company rule and the safety ceiling; a captcha or a left field lands in the queue; the pause switch stops everything at once (tests and one manual check).
4. On a site that needs an account, the run waits, the owner signs in himself in the live view, the run continues, and the next day's application there is already signed in.
5. The owner asks the agent to complete his profile on a site, reviews a section, approves it, and finds it saved with only values from his profile.
6. Tests prove: no submission outside the gate, no duplicate, no never apply role, no cross person access.
