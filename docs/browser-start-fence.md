# Browser startup ownership

Part of #291 item 7, browserd's own half: a start the api abandons can still
leave a lease in browserd, since its control server does not cancel a handler
when the api goes away. #447 (O7b) will read platforms under sweep leases of
this manager; this slice does not activate its reader. It adds an installation
fence and supplies no completed shutdown acknowledgement.

The manager reserves a server-created lease id and its original frozen Request
before starting the factory. Its owned task holds any returned browser until
installation succeeds or cleanup finishes.

Caller cancellation revokes the generation before yielding. A factory that
suppresses cancellation cannot install its returned browser after revocation.
Initial startup and handoff swaps use this fence before start and installation.
Cancellation between installation and delivery also schedules owned cleanup.
The admission lock is released before awaiting start or physical cleanup.

Admission stays busy, and the profile is not removed, while owned startup or
browser cleanup remains pending. A close that raises or is interrupted is still
an end: Chromium's end reaps every process of the profile's uid even then, and
every start reaps again first and refuses while one survives (SPEC-browser,
"Chromium lifecycle"). So the record drops its browser once the close returns
or raises, and admission opens when the owning task ends. Release still reports
its existing boolean, current and last_end values; those values do not prove
completed shutdown.

The private registry retains at most 32 records, evicting oldest terminal records.
It drops task references once consumed and browser references once their close
ended. No live record is evicted to admit a new lease.

The event-barrier regressions are in `api/tests/browser/test_start_fence.py`.
They use a real Manager with fake factories and browsers. Native Chromium,
strict close/process evidence, restart recovery and acknowledgement remain for
separate work; eviction or daemon restart does not reconstruct lease ownership.
