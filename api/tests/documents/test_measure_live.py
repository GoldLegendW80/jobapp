"""The real run of the measure tool (app/documents/measure.py), opt-in: it calls the model with
the credential of the person's environment, so it runs only when asked. The gate and every other
run of this folder skip it. How to ask is in docs/runbooks/measure-document-writers.md:

    MEASURE_LIVE=1 MEASURE_ARGS="--writer cv --runs 1 --pairs alex:brise" \\
        ops/agent-test.sh --name measure tests/documents/test_measure_live.py -q

The throwaway Postgres is the one ops/agent-test.sh started (TEST_DATABASE_URL); the table goes
to the terminal whether or not pytest is run with -s.
"""

from __future__ import annotations

import os
import shlex

import pytest

from app.documents import measure

pytestmark = pytest.mark.skipif(
    os.environ.get("MEASURE_LIVE") != "1",
    reason="set MEASURE_LIVE=1 (and a model credential) to measure the writers on the real model",
)


def test_measure_the_writers_on_the_real_model(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = shlex.split(os.environ.get("MEASURE_ARGS", ""))
    with capsys.disabled():
        code = measure.main(arguments, env={**os.environ, "TEST_DATABASE_URL": database_url})
    assert code == 0
