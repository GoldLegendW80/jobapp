# Apply final gate freshness

Scope: issue #563, integration PR #604. Base: `73ee000f99468a48f31cac7fcca03e306e9841d4`.

The independent review reproduced 12 expired authorizations incorrectly authorizing browser
Submit or profile Save when their owner, host, run or authorization lock advanced an injected
clock to the exact deadline or later. Six before-deadline controls authorized correctly.
It also reproduced both mail modes sending after an administrator pause began during the
host lock wait.

The fix samples the injected clock after those locks, forwards it to host reservation, and
rechecks expiry before recording either a paced wait or a click. Browser Submit reads the live
pause in the worker's thread through the running event loop after its owner and host locks.
Profile Save rechecks the consumed authorization, section hash and switches after its final
run and authorization locks. Its live pause and deadline checks follow those blocking reads,
immediately before Save or each empty-only autosave write.

Browser and mail also recheck the exact consumed authority, current hash, live switches and
mutable submission guards after their submitting commit, before the external click or send
capability. A known local stop at this point uses the existing explicit no-click transition,
keeps the authorization spent and the committed click audit, returns to fresh review, freezes
no document and settles the mail draft/application/send audit atomically. It does not remove
conservative pacing history. An unavailable final pause read fails closed.

Cap accounting recognizes an admitted item only from a matching spent authorization and its
current submitting decision's click event. General caps keep the same pending-approval count
as before that admission. LinkedIn daily accounting excludes only that one event id, retaining
historical clicks from the same application and clicks from other people. Calls without that
concrete admission proof retain the previous submitting behavior. No public bypass flag,
approval duration, hold, cap, timeout or memory limit was added or raised.

## Verification checkpoint

The root integration gate was still active when this source checkpoint was prepared. No
pytest, browser, database, Docker or build command was started beside that gate.

Pure, actual-function probes run with system Python:

* `/tmp/jobapp-review604-final-clock-probe.py`: original frozen source reproduced all 12
  expired authorizations and six valid controls.
* `/tmp/jobapp-final-clock-fixed-probe.py`: edited source refuses all 12 expired cases and
  retains all six valid controls.
* `/tmp/jobapp-final-clock-mail-fixed-probe.py`: 26 boundary, pacing, timeout and guard-order
  probes pass against the edited source.
* `/tmp/jobapp-final-clock-mail-pause-fixed-probe.py`: six pause flips across owner, host and
  post-commit locks pass. The two post-commit controls retain one click audit but mint no
  capability and make no provider call.

Ruff check/format and `git diff --check` pass for the edited files.

`api/tests/apply/test_final_gate_freshness.py` adds 162 parametrized runtime cases. They include
real PostgreSQL blocker observation with an injected clock, valid/expired boundary controls,
new and elapsed competing host clicks, pause flips, post-commit local stops, current
source/document/rules/settings/fit changes, conservative general and LinkedIn cap accounting,
and the last autosave read. **These tests have not run at this checkpoint.**

## Deferred runtime verification

After the orchestrator releases the heavy-test mutex, run from this worktree:

```sh
rtk proxy caffeinate -i ops/agent-test.sh --name final604 tests/apply/test_final_gate_freshness.py tests/apply/test_a6_gate.py tests/apply/test_a6_recheck.py tests/apply/test_a11_caps.py tests/apply/test_a13_full_auto.py tests/apply/test_a14_email.py tests/apply/test_a14_pacing.py tests/apply/test_a14_host_expiry.py tests/apply/test_a18_profiles.py -m 'not docker' -n 2
rtk proxy caffeinate -i ops/agent-test.sh --name final604all tests/apply tests/mail -m 'not docker' -n 2
```

Then run the repository's API Ruff checks, CI plan and journey registry checks, review any
changed behavior assertions, and re-run the final composed gate on the integrated tree. The
runtime verifier must confirm the new tests fail when the relevant old gate code or broad
application-id exclusion is restored. These commands and mutations are deferred, not passes.
