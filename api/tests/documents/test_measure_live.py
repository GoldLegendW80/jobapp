"""The real run of the measure tool (app/documents/measure.py), opt-in: it calls the model with
the credential of the person's environment, so it runs only when asked. Every other run of this
folder skips it, and CI and the gate always do (they set CI, even with MEASURE_LIVE inherited
from the shell). How to ask is in docs/runbooks/measure-document-writers.md:

    MEASURE_LIVE=1 MEASURE_ARGS="--writer cv --runs 1 --pairs alex:brise" \\
        ops/agent-test.sh --name measure tests/documents/test_measure_live.py -q

Without MEASURE_ARGS the run is that cheap first one, never the full measure.

The throwaway Postgres is the one ops/agent-test.sh started (TEST_DATABASE_URL); the table goes
to the terminal whether or not pytest is run with -s.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Mapping

import pytest

from app.documents import measure

# The cheap first run of the runbook: one CV writer run on one pair.
DEFAULT_ARGS = "--writer cv --runs 1 --pairs alex:brise"


def live_requested(env: Mapping[str, str]) -> bool:
    """Never under CI or the gate (ops/local-check.sh exports CI=true), even with MEASURE_LIVE
    inherited from the shell that runs it."""
    return env.get("MEASURE_LIVE") == "1" and not env.get("CI")


pytestmark = [
    pytest.mark.skipif(
        not live_requested(os.environ),
        reason="set MEASURE_LIVE=1 and a model credential to measure the writers for real",
    ),
    # The project turns warnings into errors; the real SDK's subprocess may leave an unclosed
    # transport behind, which must not fail a measure that has printed its table.
    pytest.mark.filterwarnings("ignore"),
]


def test_measure_the_writers_on_the_real_model(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = shlex.split(os.environ.get("MEASURE_ARGS", DEFAULT_ARGS))
    with capsys.disabled():
        code = measure.main(arguments, env={**os.environ, "TEST_DATABASE_URL": database_url})
    assert code == 0
