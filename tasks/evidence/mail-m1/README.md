# Evidence, mail M1 (issue #123)

Run on 2026-10-14 on the Apple M4 Mac, from the branch `mail/m1-fake-gmail`. There is no CI: these runs are the evidence.

| Command | Result |
|---|---|
| `uv run --native-tls pytest -q -n 4 tests/mail tests/test_app.py tests/test_config.py tests/identity/test_body_limit_api.py` (in `api/`) | 160 passed |
| `uv run --native-tls pytest -q -n 4 tests/agents/test_api.py tests/test_logging_secrets.py` | 171 passed |
| `uv run --native-tls pytest -q -n 4 tests/mail --junitxml=...` | 93 passed |
| `uv run --native-tls ruff check app tests` and `ruff format` | clean |
| `python3 .github/scripts/e2e_groups_test.py`, `python3 .github/scripts/e2e_groups.py` | 26 tests OK; 24 specs run in CI, 4 excluded with a reason, 1 run by a group of the local check only |
| `pnpm --dir web run typecheck`, `lint`, `build` | clean |
| `LOCAL_CHECK_MIN_FREE_GB=5 bash tests/local_check_test.sh`, the same with `/bin/bash` | All 21 checks passed, twice |
| `bash tests/env_check_test.sh` | All 10 checks passed |
| `playwright test -c e2e/mail-all.config.ts --workers=1` against the stack `jobapp-m1` through Caddy with `gmailstub` | 4 passed (2.1 s) |

`LOCAL_CHECK_MIN_FREE_GB=5`: the disk had 11.85 GiB free that hour, under the 12 GiB at which the script under test refuses to start, so its own tests failed on their first case for that reason alone. With 13 GiB free, an hour before, the same tests ran to the comparison of the journey groups.

The stack: `.env` from `.github/scripts/ci_env.sh <port>` plus `AGENTS_FAKE_BACKEND=true`, the files `compose.yml`, `tests/ats-stub.compose.yml`, `tests/gmail-stub.compose.yml`, `compose.mac.yml` and an override naming the images `jobapp-m1-caddy`, `jobapp-m1-api`, `jobapp-m1-worker`, two loopback ports picked free. Taken down with `down -v --remove-orphans`, the three images removed.

The canary: `tests/mail/test_canary.py` plants a random marker in a log line and in Redis values of every type (a throwaway `redis:7.4.11-alpine`) and finds it; `tests/mail/test_google.py` plants seven random tokens (refresh token, access token, client secret, Google's error text, a subject, a search term, a header) and finds none in the log records, the errors, their tracebacks or their pickles.

`01-consent-page-*.png`: the fake's consent page, desktop and mobile, light and dark.
