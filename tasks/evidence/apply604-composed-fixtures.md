# Apply composed fixture checks

Issue #563, PR #604. Base `1b3642e3`. All profiles and accounts are invented.
No production settings, model stream, source judge or approval rule changed.

## Fake stack settings

The CI environment writer generated settings inside a temporary directory. Docker Compose
resolved `compose.yml`, `compose.mac.yml`, `tests/ats-stub.compose.yml` and
`tests/ats-forms-stub.compose.yml` with that directory as the project directory. The resolved
API and worker environments were passed to the real `Settings` class. No service was started,
and no real environment file was read or printed.

Before the correction, the API rejected `TEST_APPLY_LINKEDIN_MIN_INTERVAL_S=60` because it did
not have `TEST_FAST_TIMERS`; the worker validated. After adding the flag only to the fake forms
API override, both validated. Both resolved environments still refuse `APP_ENV=production`.
The production defaults remain 600 seconds between LinkedIn applications, 15 per day and a
900 second Full auto hold.

`test_fake_forms_stack_has_all_required_timer_guards` exercises both fake API and worker
environment overlays against `Settings` and checks the production refusal and unchanged
production limits. It failed for the API before the override correction.

## Cross-user model-check fixture

`test_b_s_check_never_reads_a_s_profile_or_posting` replaced the invented motivation with a
canary achievement sentence. Its fill helper emits the complete `WHY_TEXT` intent sentence,
which that replacement no longer sourced. The source judge correctly returned `needs_person`,
so the test stopped before its privacy assertions.

The fixture now appends the exact recorded `WHY_TEXT` to its canary motivation. Both profile
canaries and both posting canaries remain random. All assertions about B's prompt, A's absent
data, absent contact data and A's empty run history remain unchanged.

## Verification

- RED: the new composed settings cases and the original cross-user test: 2 failed, 1 passed.
  The failures were the API settings prerequisite and the cross-user fixture stopping before
  the model check.
- GREEN: `caffeinate -i bash ops/agent-test.sh --name apply604-composed-green
  tests/apply/test_composed_fixture_settings.py tests/apply/test_a13c_check.py
  tests/apply/test_a13b_follow_ups.py tests/test_config.py tests/jobs/test_fast_timers.py
  -m 'not docker' -n 2 -q`: 158 passed in 24.65 seconds.
- Real Docker Compose configuration probe: API and worker settings valid, both production
  refusals retained, production limits 600 seconds / 15 per day / 900 seconds retained.
- Ruff check passed; Ruff format check: 748 files already formatted.
- CI plan: 81 tests passed. Journey registry: 29 CI specs, 4 excluded with a reason and 46
  local group specs. Required pre-commit hooks and the secret scan passed.

The test wrapper removed its disposable Postgres and Redis services on exit. The settings
probe removed its temporary synthetic environment directory. No images were built or changed.
