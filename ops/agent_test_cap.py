"""The worker cap of ops/agent-test.sh, loaded there with `-p agent_test_cap` (issue #280).

The script refuses `-n` above 2 in the arguments it sees; this reads what pytest resolved, so the
other spellings are refused too: clustered short options (`-vn3`), PYTEST_ADDOPTS, `-o addopts=`,
an ini file. By pytest_configure, xdist's own pytest_cmdline_main has turned `auto` and `logical`
into numbers and written one `popen` per worker into `tx`, so counting `tx` covers `--tx` as well.
"""

import os

import pytest

MAX_JOBS = int(os.environ.get("AGENT_TEST_MAX_JOBS", "2"))


def _workers(tx: list[str]) -> int:
    count = 0
    for spec in tx:
        times, star, _ = spec.partition("*")
        count += int(times) if star and times.isdigit() else 1
    return count


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    jobs = getattr(config.option, "numprocesses", None) or 0
    # A value xdist did not resolve is refused rather than guessed.
    if not isinstance(jobs, int):
        raise pytest.UsageError(
            f"ops/agent-test.sh: -n {jobs}: give a number, at most -n {MAX_JOBS}"
        )
    asked = max(jobs, _workers(getattr(config.option, "tx", None) or []))
    if asked > MAX_JOBS:
        raise pytest.UsageError(
            f"ops/agent-test.sh: {asked} pytest workers asked for, "
            f"at most -n {MAX_JOBS} beside the merge gate"
        )
