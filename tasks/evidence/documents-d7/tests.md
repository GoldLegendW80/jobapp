# D7: what was run (2026-10-14)

Throwaway services: `postgres:16.15-alpine` and `redis:7.4.11-alpine` on free ports, as
`ops/local-check.sh` starts them; removed afterwards. The writer runs on the fake backend only
(`AGENTS_FAKE_BACKEND`, the recorded streams `api/app/agents/fake_streams/documents_cv_*.json`);
no model is called and no key is set or read.

```
uv --native-tls run pytest -q tests/documents tests/agents -m "not docker" -n 6
2050 passed, 19 skipped in 86.67s

uv --native-tls run pytest -q tests/ingestion -m "not docker" -n 6
6370 passed, 6 skipped in 127.98s

uv --native-tls run ruff check app tests
All checks passed!

uv --native-tls run ruff format --check app tests
425 files already formatted
```

No Playwright journey: the plan gives D7 none (no route starts a writer yet). The generation is
driven by the service in `api/tests/documents/test_writer_cv.py`, on the fake backend.
