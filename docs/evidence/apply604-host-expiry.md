# Mail host-lock expiry evidence

Issue #563, PR #604. Tested on 2026-10-22 using invented accounts, fake Gmail and
throwaway PostgreSQL 16 and Redis services. No real account or provider was used.

The final application mail send checked its approval before acquiring the shared
host pacing lock. Another sender could hold that lock until the approval expired
and release it without a click, after which the waiting sender recorded a click
and called its provider using the earlier time.

The final reservation now samples the injected clock after its lock wait for the
pacing calculation. The mail gate then repeats `submit.recheck` with the current
time before the existing atomic refusal cleanup or click transaction. The initial
guard order is retained. A simultaneous approval expiry and host pace returns
`authorisation_expired`, resets the draft and its active send binding atomically,
and returns the application to review without freezing its documents. A valid
paced application retains its original approval and mode.

`test_a14_host_expiry.py` observes the sender in PostgreSQL's
`pg_blocking_pids` before releasing the real host advisory lock and advancing the
injected clock. Its 18 cases cover Review first's 24-hour and Full auto's seven-day
deadlines at one second before, exactly at and one second after expiry. Each has
three host outcomes: no click, another person's current click, or a click whose
pacing interval has exactly elapsed. The valid unpaced controls send once, mint
with the current time and prove the host lock remains held at the click event.
Refused controls mint no capability and call no provider. The source authorization
timestamps stay unchanged.

Validation:

- Before the source fix, the original 12-case deadline table had 10 failures and
  two passes. Four expired approvals sent, four simultaneous expiry and pace cases
  returned `paced`, and two valid controls minted with stale times.
- After the source fix, the original table and existing A14 pacing tests passed:
  26 tests in 21.88 seconds.
- The complete mail and A14 suites passed: 1,358 tests in 88.59 seconds.
- After adding the elapsed-pacing controls, the final 18-case table, A14 pacing
  and existing A11 caps suite passed: 164 tests in 46.37 seconds.
- API Ruff check passed; Ruff format check passed for all 746 Python files.
- CI plan tests passed, 81 tests. The e2e group registry passed.

Existing callers of `caps.reserve` keep their current behavior unless they supply
the optional injected clock. Pacing limits, approval deadlines and capability
lifetimes were not changed. All test services were removed by `ops/agent-test.sh`.
