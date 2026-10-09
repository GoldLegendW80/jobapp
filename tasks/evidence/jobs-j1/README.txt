Jobs J1: schema and scoring port. Evidence, 2026-10-09.

No HTTP surface in J1 (routes come with J2), so there is no Playwright check and no screenshot.

Golden scoring test (tests/jobs/test_scoring_golden.py, against ../engine/jobs.py score_row run on
../profile/skills.json, and the port run on the skills section the P2 importer converts):
  golden scoring: 395 postings scored identically, 38 of 433 rows have no posting.txt
Fit, competencies and gaps are identical, in the same order, on all 395. The 38 rows without a
posting.txt cannot be scored by the engine either (it scores the file named in the jd column).

Full suite: uv run --native-tls pytest -q --cov
  1069 passed in 124.29s; coverage 99% overall; app/jobs: models 100%, scoring 100%, slug 100%,
  service 99% (the re-raise of an IntegrityError that is not the one-running-sweep index).

Scoring cost: a 1 MB posting scores in under 200 ms with a 40-category model (only the first
64,000 characters are scored; the longest real posting is 12,829 bytes).
