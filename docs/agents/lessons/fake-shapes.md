# Tested only on fake shapes (FAKE)

Cue: the tests run on a fake service, a stand in image, a recorded stream or a sample document,
and the real service, browser, tool version or ordinary content behaves otherwise.

Counts, corpus 2026-10-14 to 10-28: 11 rounds, 9 pull requests, 12 items, 2 in later rounds.
Rank 10. Backs review-checklist.md item 10.

## Edit rows

component: review-checklist.md item 10 ("Make fakes as hostile as the real service")
candidates: the checklist of 2026-10-14
change: the fake pads like Google, answers the real status, a stream parser split at every
position
observed: blocked again with the real thing as the oracle: the real renderer (#265), a real PDF
(#417), legacy iptables (#307), a widget that ignores the click (#310), and documents tested on
the one sample profile (#286, #421, #437, #518)
verdict: no observed effect

## Entry FAKE-1

- **cue**: a check or a layout is tested on the sample content of the repository.
- **lesson**: test with the shapes a person writes: a long unbreakable string, a long or double
  barrelled name, two pages, an emoji with U+FE0F, every field filled; through the real renderer
  under your own image tag (review-checklist.md item 10).
- **evidence**: #518 round 1 ("the check proves visibility for one content shape only, the one in
  `_base/sample.json`"), round 2 ("the canary has no long unbreakable string, so a box or track
  that fits the canary's words hides real ones"); #286 ("a school's rail text overlaps the next
  entry for an ordinary profile"); #437 ("A name breaks inside a word, at the hyphen of a
  double-barrelled name"; "On a document of two pages, the text runs outside the frame"); #421
  (the executive header is "a flex row with no wrap"); #265 ("A single common character in any
  field fails the whole render", then "an emoji written with its presentation selector fails the
  render").
- **confidence**: high: 5 pull requests, all documents templates.
- **supports**: review-checklist.md item 10.

## Entry FAKE-2

- **cue**: a fake or a stand in replaces a service, a tool or a widget in the tests.
- **lesson**: name the real behaviour the fake must reproduce and check it once against the real
  one (version, mode, failure): the iptables backend of the Jetson, a PDF that sets its size in
  `Tm`, a select that ignores the click, a stream split at any byte, a log that grows past a
  buffer.
- **evidence**: #307 ("the 'real iptables (legacy, as the Jetson)' part runs nf_tables"); #417
  (`FPDFText_GetFontSize` "ignores the text matrix and the CTM"); #310 ("A react-select or
  typeahead reads back as `filled` when the option click chose nothing"); #171 ("a valid padded
  attachment fails ... when its closing quote starts a chunk"); the journey notes of 2026-10-27
  (journey b4 passed only while the stack's Caddy log stayed under Node's 1 MiB `execFileSync`
  default).
- **confidence**: medium: 4 pull requests and one journey, each a different fake.
- **supports**: review-checklist.md item 10.

## Single cases (NO_ENTRY)

- Item 15 of 2026-10-14 ("Verify external facts at the source and date them", #112): no case in
  this corpus. Kept here: fetch the live page, write the figures as literals, set `AS_OF` to that
  day, assert more than an ordering.
