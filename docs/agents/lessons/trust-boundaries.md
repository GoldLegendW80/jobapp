# Privacy, secrets and trust boundaries (TRUST)

Cue: personal data, a secret, an exception's text or a model's input reaches a new sink, or code
that a page controls (a script, a frame, a redirect, a steered model) can act past a guard.

Counts, corpus 2026-10-14 to 10-28: 22 rounds, 22 pull requests, 22 items, 4 in later rounds.
Open branch: 3 (B17 rounds 1 and 2). Rank 2. Backs review-checklist.md item 2.

## Edit rows

component: review-checklist.md item 4 ("Trace every sink of a secret, personal datum or foreign
user id")
candidates: the checklist of 2026-10-14
change: grep each sink (logs, proxy headers, public pages, stored payloads, cross user keys),
canary test
observed: the listed sinks still blocked (#185 `Referer` in the access log, #266 cross user
references, #426 a cut address in a tracked log), and sinks it does not name blocked as often:
the model's context (#323, #518), a public Actions log (#334), the deny list for new secret files
(#681), the page's own code (#267, B17)
verdict: no observed effect

component: CLAUDE.md hard rule 8 and the deny list of `.claude/settings.json`
candidates: the harness of 2026-10-15 onward
change: no personal data in the repository; secret files denied by name
observed: #176 (a contact address in a public page constant) and #681 ("this PR
introduces three secret files ... and none is in the deny lists") were caught by the reviewer,
not by the builder
verdict: no observed effect at build time; the review held

## Entry TRUST-1

- **cue**: a change adds a place where data goes (a log line, a model prompt, a public page, a CI
  log, a new secret file) or a place where it should stop (a delete, a boundary of a 500).
- **lesson**: in review-checklist.md item 2, list every sink the change adds and plant a random
  canary through each: the JSON log with its `extra` fields, access log headers, the Actions log,
  every table after a delete, and the model's context, which gets only the fields its task needs.
  A new secret file name joins the deny list in the same pull request.
- **evidence**: #185 ("The callback's `code` and `state` can reach Caddy's access log through the
  `Referer` header"); #188 ("Values the user turned down, drawn from the deleted document, survive
  in full in accepted proposals"); #323 ("The model gets the whole `answers` section, including
  EEO answers, salary, work authorization"); #518 ("the person's real profile photo is sent to the
  model"); #334 (a failed deploy prints every service's logs "into the Actions log of this PUBLIC
  repository"); #620 (the private 500 boundary keeps the exception text); #681 (secret files
  missing from the deny lists); #426 (`psql` prints a cut address that the mask cannot match).
- **confidence**: high: 12 pull requests (also #176, #266, #307, #661), one shape: a sink nobody
  listed.
- **supports**: review-checklist.md item 2 (sinks extended to model input, CI logs, deny list).

## Entry TRUST-2

- **cue**: a guard in the browser service or the apply flow is meant to hold against what a page,
  a frame or a steered model does.
- **lesson**: test the guard in a real Chromium against the page's own escape routes: a service
  worker, WebRTC, `WebSocketStream`, a sandboxed `srcdoc` iframe, a server side redirect, a
  framed form inside a company page, a text that steers the model (review-checklist.md item 2).
- **evidence**: #267 ("WebRTC UDP bypasses the proxy and reaches private addresses"); #310 ("A
  framed form's fill types into the company page around it"); #429 round 2 (the allow entry
  `chrome://policy` "matches every path under it"); #465 ("The banner names whatever site the
  model was steered to"); B17 round 1 (a service worker sends a POST the route never sees), round 2
  (`WebSocketStream` not removed; "an inline sandboxed iframe ... is blind to both the init script
  and the route"); #302 (screen bytes reach a viewer after its lease ends); #438 and #459 (a
  posting or apply address on another host accepted, "no public host check").
- **confidence**: medium to high: 7 pull requests and one open part, each found by a probe.
- **supports**: review-checklist.md item 2, last sentence.

## Single cases (NO_ENTRY)

- #265 round 2: NFKC run over the escaped page "recreates markup from profile text". One case;
  the rule (normalise before escaping) is in review-checklist.md item 7.
- #679 round 2: a cut that runs after redaction removes the redaction marker itself. One case.
- #312 round 3: `judge` trusts a source string the model writes. One case.
