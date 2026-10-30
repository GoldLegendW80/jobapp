# Tests that run on fakes, and tests that must be able to fail

Read this when a trigger line sent you here: your tests run on a fake, a stub, a recorded stream or
a sample (a fake service, a fixture page, a sample PDF), or a new test is the evidence of a claim
you make in the code, the docs or the pull request body. A reviewer reads it for the same diffs.
Moved on 2026-10-29 from checklist items 10 and 16 of
[review-checklist.md](../review-checklist.md). The cases are in
[lessons/fake-shapes.md](../lessons/fake-shapes.md) and
[lessons/blind-tests.md](../lessons/blind-tests.md). The rules on sleeps, wall clocks and planted
privacy markers stay in implementer.md, Tests.

## Checklist item 10, the check

Make fakes and samples as hard as the real thing. (FAKE: 11 rounds, 9 PRs.)

Check: the fake checked once against the real service, tool or version (legacy iptables, a real
PDF, the real renderer); content as people write it (a long unbreakable string, a long name, two
pages, an emoji with U+FE0F); a widget that ignores the click; a stream split at every position;
the real status codes.

## Checklist item 16, the check

A test must be able to fail, and must not depend on the machine. (BLIND: 8 rounds, 8 PRs.)

Check: remove each layer of the fix alone and watch the test go red; assert on what the system
writes (the JSON log with `extra`, every route class); an absence is proved only when the fixture
tries the forbidden act; no wall time bounds.
