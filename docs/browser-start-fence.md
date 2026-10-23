# Browser startup ownership, Q1a

Partial Refs #291 and #447. This slice adds private manager evidence and an
installation fence. It supplies no completed shutdown acknowledgement.

The manager reserves a server-created lease id and its original frozen Request
before starting the factory. Its owned task holds any returned browser until
installation succeeds or cleanup finishes. The factory's successful return
records the existing prepare/bind_uid path; the Request alone proves no binding.

Caller cancellation revokes the generation before yielding. A factory that
suppresses cancellation cannot install its returned browser after revocation.
Initial startup and handoff swaps use this fence before start and installation.
Cancellation between installation and delivery also schedules owned cleanup.
The admission lock is released before awaiting start or physical cleanup.

Admission stays busy while owned startup or browser cleanup remains pending.
A failed close retains the exact browser and failure evidence, even after
ordinary release returns. That release still reports its existing boolean,
current and last_end values. Those values do not prove completed shutdown.

The private registry retains at most 32 records, evicting oldest terminal records.
It drops consumed task references and successfully closed browser references.
No live or failed-cleanup record is evicted to admit a new lease.

The event-barrier regressions are in `api/tests/browser/test_start_fence.py`.
They use a real Manager with fake factories and browsers. Native Chromium,
strict close/process evidence, restart recovery and acknowledgement remain for
separate work; eviction or daemon restart does not reconstruct lease ownership.
