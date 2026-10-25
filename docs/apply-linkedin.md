# LinkedIn Easy Apply

Easy Apply requires the person's enabled LinkedIn switch, acceptance of the current terms
sentence, and no active checkpoint pause. The fill checks before leasing a browser. The
application gate checks at the decision and again before the final click. Other people's
consent and pauses never authorize a run. Without consent, the posting offers the ordinary
external link "Open LinkedIn and apply myself" to the validated job URL in the person's own
browser. It starts no application or browser lease. The in-app Apply myself remains guarded
by B10 and is offered after acceptance. An already discovered company-site address follows
its normal adapter. No browser is opened to discover an alternative without consent.

The known adapter fills at most six modal pages. It resolves answers through the existing
profile answer resolver, keeps unknown answers left, records ordered Next and Review names
in the existing hash-covered step rows, and designates the final Submit application control.
Optional Follow-company marketing checkboxes are explicitly declined, including on replay.
The final Review page may contain no editable fields. Every earlier step is refilled and read
back; labelled application-summary values, when present, must match the approved values.
A changed or unreadable summary returns the application for review.
An unanswered required question on an early page stops before Next, including when Next is
disabled by validation. The partial report records its field shapes and incomplete state.
After the person supplies the last required answer, the shared deterministic-wizard path
queues a fresh fill, validates the same earlier fields, continues the remaining pages, and
requires a final designation and approval. It never treats that partial report as ready to send.

The private browserd endpoint `/leases/{id}/linkedin/open` opens the modal. It accepts only a
digits-only job id from application code, authenticates the service and current lease token,
and supports only automated application fill, Full auto, and submit leases. It is absent from
the model tool table. Before and after normal human pacing it checks the exact known job URL,
one exact Easy Apply button, native `type=button`, no form owner or form ancestor, no dialog
or iframe, no nested interactive control, and the same button at the click point. It never
clicks the final Submit control. Ordinary model clicks keep the existing submit detector.
Only the application gate has the capability for the final submission.

This distinction follows [LinkedIn's public Help](https://www.linkedin.com/help/linkedin/answer/a512388),
checked on 2026-10-22: the opener leads to popup screens, then Review and a separate final
submission. No real posting was visited during implementation.

A company-site link is parsed and validated without clicking it. The restricted LinkedIn
lease ends first. The application is queued with the new address and canonical key, and the
next fill rechecks posting, documents, duplicates, and site guards before requesting its own
lease. No host is appended to the LinkedIn lease. In Full auto the followed site keeps Full
auto only when it is the posting's own company's form: the ATS organisation of an Ashby,
Greenhouse or Lever address, or the registrable domain of a company's own site, has the key of
the posting's company (`linkedin.own_company`, whole name only). Any other site, a job board or
a shared host goes to Review first (`company_site_unverified`), so the person approves what is
sent. A checkpoint or unusual-activity page
commits the person's seven-day LinkedIn pause before content returns to the adapter, and
held LinkedIn work becomes Review first. Sign-in walls use the existing owned handoff.

Full auto retains its three rehearsals, fit threshold, company caps, safety ceiling and hold.
LinkedIn shares one host pace across people: 600 seconds between submissions and at most 15
in 24 hours. A waiting interval preserves approval; the daily Full auto limit returns work
to review. Revoking consent or changing the terms version after a hold stops the click.
Production configuration cannot lower the 600-second floor or raise the 15-per-day ceiling.
`TEST_APPLY_LINKEDIN_MIN_INTERVAL_S` may shorten only the interval on a nonproduction stack
with both `TEST_FAST_TIMERS` and an explicit fake forms base; it does not raise the daily cap.

The fake lives at `tests/ats_forms/a16/modal.html`, served under
`/linkedin/jobs/view/<digits>/`. Its query variants are `plain`, `unknown`, `company`, `signin`,
`checkpoint`, and `changed`. The browser's explicit exact-origin fixture mapping is
`BROWSER_LINKEDIN_STUB_BASE_URL`; production rejects it. Submissions are counted in the fake's
existing `/_stub/submissions` ledger. The tests and `apply-a16.spec.ts` use only invented
people and local fixtures. Passing fixture tests establishes local behavior, not success on
the live site.
