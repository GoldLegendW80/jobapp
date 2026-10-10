# Probe: a documentation only pull request (issue #91)

This file exists to be the only change of a pull request against `platform/91-ci-fast`, so that
the plan of `.github/scripts/ci_plan.py` is exercised on the runners for the case "documentation
alone": every job starts, computes the plan `none`, runs nothing but pre-commit (in `checks`),
and verifies that every step of a unit was skipped.

The pull request is closed without merging once its run has been read.
