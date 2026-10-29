# Untrusted input, personal data and secrets, consumers that read it differently

Read this when a trigger line sent you here: your diff logs, stores, sends or shows personal data,
a secret or model input; reads text, files, numbers or addresses from outside (a page, an upload,
a header, a URL, third party HTML or CSS); adds a secret file; or adds a guard that page code could
try to pass. A reviewer reads it for the same diffs. Moved on 2026-10-29 from checklist items 2,
6 and 7 of [review-checklist.md](../review-checklist.md). The cases are in
[lessons/trust-boundaries.md](../lessons/trust-boundaries.md),
[lessons/hostile-input.md](../lessons/hostile-input.md) and
[lessons/parse-like-the-consumer.md](../lessons/parse-like-the-consumer.md). A rule over words
that people write also needs [heuristic-code.md](heuristic-code.md).

## Checklist item 2, the check

Trace every sink of personal data, secrets and model input, and every way page code can pass a
guard. (TRUST: 22 rounds, 22 PRs.)

Check: a random canary through each sink the diff adds: the JSON log with its `extra` fields,
access log headers (`Referer`, `Location`), the public Actions log, every table after a delete,
the model's context (only the fields its task needs). Composite keys (id, user_id) for a
reference to another user's row. A new secret file joins the deny list in the same pull request.
Browser guards: try a service worker, WebRTC, a sandboxed iframe, a redirect and a steered model
in a real Chromium.

## Checklist item 6, the check

Bound every untrusted input, and let one bad item fail alone. (HOST: 12 rounds, 9 PRs.)

Check: one hostile fixture per parser at the largest accepted size (nested tags, 16,000 quotes in
a header, `&#` and 4,301 digits, `NaN`, 1,000 nested `minmax(`), counted by work in the real
container; no CPU bound parse on the event loop; a per user share of shared slots; one poisoned
posting, comment or item beside a good one, and the loop goes on.

## Checklist item 7, the check

Read addresses, HTML and CSS the way their consumer does. (DIFF: 12 rounds, 10 PRs.)

Check: hosts after WHATWG decoding (percent encoding, IPv4 forms, case, IDNA 2008), a `Location`
resolved as Chromium does, every spelling of one platform's host; CSS tokenised as CSS reads it
and the tree the browser builds; normalise (NFKC) before escaping, never after.
