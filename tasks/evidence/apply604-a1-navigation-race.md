# A1 paced-submit assertion repair

Base: `73ee000f99468a48f31cac7fcca03e306e9841d4`.

The first composed gate attempt timed out in the Ashby A1 journey after a
successful paced submission. The retained Playwright trace shows this sequence
(times are the trace's monotonic milliseconds):

| Event | Time |
| --- | ---: |
| Instant-submit spam refusal assertion finishes | 2432.634 |
| Pre-submit `data-autofill-used` read finishes | 5352.163 |
| Paced JSON application POST starts | 5380.626 |
| Duplicate post-submit form attribute read starts | 5386.006 |
| Confirmation page GET starts, HTTP 200 | 5388.023 |

The duplicate read waits for a form that the confirmation navigation removes.
`tests/ats_forms/a1/forms.js` navigates through `location.assign` after an
accepted response. This is a test assertion race, independent of the fake ATS
server's dwell check. The instant rejection and real server dwell polling had
already completed in the failing attempt.

The repair removes that duplicate DOM read and checks `_systemfield_name` is
`Alex Example` in the captured POST fields instead. The fake resume-autofill
trap changes this field to `Resume autofill candidate`, so the request check
also detects use of that trap. The pre-submit null attribute assertion and
the durable third-submission name assertion remain in place, together with
the webdriver, instant-refusal, server dwell, yes/no, confirmation, file hash
and recorded-submission checks.

The patch changes no application code, fake ATS code, clock, timeout, retry,
dwell threshold or resource limit. Static comparison against the base verifies
only the assertion replacement above. The captured request has the expected
name; the trap's replacement value fails the same name comparison.

Runtime verification of this revised journey is pending the next composed
gate. No browser, database, image build or test suite was started for this
repair while the first gate held the machine.
