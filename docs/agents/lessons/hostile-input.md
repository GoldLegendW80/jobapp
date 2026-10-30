# Hostile input without a bound (HOST)

Cue: third party text, files or numbers (mail, postings, feeds, job boards, uploaded documents,
CSS a model writes) are parsed, or a shared slot serves several people.

Counts, corpus 2026-10-14 to 10-28: 12 rounds, 9 pull requests, 15 items, 5 in later rounds.
Rank 6. Backs review-checklist.md item 6.

## Edit rows

component: review-checklist.md item 6 ("Give every input a size cap and a time and memory budget,
and measure the worst shape")
candidates: the checklist of 2026-10-14
change: a hostile fixture per parser at the largest accepted size, timed in the real container
observed: cost blocked again (#179 three rounds, #505, #427 round 2), and a shape the item does
not name blocked five times: one bad item that fails the whole batch (#438, #460, #498, #505
round 2, #687)
verdict: confounded (12 findings in 5 pull requests on 2026-10-14; 15 items in 9 of 242 since)

## Entry HOST-1

- **cue**: a parser, a scan or a regular expression runs over text a third party controls.
- **lesson**: bound size, time, depth and per user share, measure the worst shape at the largest
  accepted size by counting work (not with a clock), and keep CPU bound parsing off the event loop
  (review-checklist.md item 6).
- **evidence**: #179 round 1 ("address headers cost up to 52 s of CPU in the worker for 480 kB of
  headers"), rounds 2 and 3 (8.9 s, then 16.6 s for 500 kB); #505 ("Turning HTML into plain text
  takes quadratic time, and it runs on the sweep's event loop"); #427 round 2 (`minmax(` nested
  about 1,000 deep "recurses ... until Python raises `RecursionError`"); #302 ("No cap on viewers
  per lease, so a tab loop exhausts the shared 1024m browser container"); #312 ("One person's
  backlog starves everyone else's fills").
- **confidence**: high: 5 pull requests.
- **supports**: review-checklist.md item 6.

## Entry HOST-2

- **cue**: a loop handles many items from outside (postings of a board, comments of a feed,
  applications of a tick, sources of a sweep).
- **lesson**: one item's exception, overflow or odd value fails that item alone, recorded; the
  loop goes on. Test it with one poisoned item beside a good one (review-checklist.md item 6).
- **evidence**: #498 ("A hostile or broken board stalls the whole sweep": `NaN`, `Infinity` and
  `1e400` reach `int()`); #505 round 2 ("One hostile character reference fails a whole feed, every
  sweep": `html.unescape("&#" + "1" * 4301)` raises `ValueError`); #438 ("Only `SourceError` is
  caught, so any other exception escapes `Runner` and `run_sweep` marks the whole sweep failed");
  #460 round 2 ("an IPv4 address still stops the rest of the sweep"); #687 ("One item's exception
  aborts the whole tick").
- **confidence**: high: 5 pull requests, one shape.
- **supports**: review-checklist.md item 6, last clause (new).
