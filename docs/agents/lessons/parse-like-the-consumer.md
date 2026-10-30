# A check that reads differently from the consumer (DIFF)

Cue: code checks an address, a redirect, HTML, CSS or a template, and a browser, a print engine
or a tree builder reads the same input later, by its own rules.

Counts, corpus 2026-10-14 to 10-28: 12 rounds, 10 pull requests, 15 items, 4 in later rounds.
Rank 7. Backs review-checklist.md item 7.

## Edit rows

component: review-checklist.md item 13 ("Unicode: decide fold, normalise and strip per field, and
test the lookalikes")
candidates: the checklist of 2026-10-14
change: per field Unicode handling, NFKC before escaping, IDNA 2003 named
observed: the Unicode cases recurred (#179 IDNA 2003, #265 round 2 NFKC, #719 combining marks),
but most items of the family were host and URL parsing (#459, #460, #690, #683, #719, #297) and
CSS or HTML read unlike the browser (#427, #518, #525), which no item named
verdict: no observed effect

## Entry DIFF-1

- **cue**: a host, an address or a redirect target is checked, compared or used as a key.
- **lesson**: parse it as the browser will (WHATWG: percent decoding, IPv4 forms, case, trailing
  dot, IDNA 2008), resolve a `Location` as Chromium does, and key a platform by every spelling of
  its host; test with the browser's own result as the oracle (review-checklist.md item 7).
- **evidence**: #459 round 2 ("a percent encoded host passes the public host check.
  `urlsplit().hostname` keeps `%2e`, but browsers percent decode the host"); #690 ("the site kind
  is decided on the raw `urlsplit(url).hostname`, so a LinkedIn address in another spelling"
  passes); #683 (two LinkedIn addresses of one sweep "are paced separately"); #719 ("Python's
  `urljoin` and Chromium do not resolve a Location the same way"); #297 ("A server side 3xx
  redirect leaves the allow list"); #460 (the check "passes any host that `canonical` accepts");
  #179 ("the IDNA 2003 codec maps one registrable domain onto another").
- **confidence**: high: 7 pull requests.
- **supports**: review-checklist.md item 7 (new, absorbs item 13 of 2026-10-14).

## Entry DIFF-2

- **cue**: a validator walks a template, CSS or HTML that a person's or a model's file supplies,
  and the browser renders or prints it.
- **lesson**: tokenise and build the tree the way the browser does (CSS tokens, custom property
  scope, `@page` and `@page :first` apart, Jinja's blocks of an extending file, the tree builder's
  elements), and compare with a real render for each refusal class. Module knowledge of documents:
  the next design capture task's plan section should carry it.
- **evidence**: #427 round 1 ("the sheet is not tokenized the way CSS reads it"; Jinja "renders
  only the blocks"), round 2 ("the tree is not the one the browser's tree builder keeps"; a custom
  property "substituted everywhere"), round 3 (a `transform` paints over other fields); #518 ("a
  user template may set its own `@page` margin, which wins in print"); #525 ("`@page :first`
  becomes a `Rule` with `selectors=None`, the same as `@page`").
- **confidence**: medium: 3 pull requests, one module.
- **supports**: review-checklist.md item 7.

## Unicode cases (moved from the checklist of 2026-10-14)

casefold merges `ß` and `ss`; NFKC turns full width `＜` back into `<`, so normalise before
escaping, never after; IDNA 2003 maps `straße` to `strasse`; some format characters have glyphs;
an emoji carries U+FE0F; `\d` matches Arabic-Indic digits; stripping every combining mark merges
words that differ (#719). Test e-mail, URL host, name and date fields with these.
