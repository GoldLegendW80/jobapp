# Native known-fallback captures

The complete six-test file passed in 11.7 minutes on one uninterrupted stack with one worker,
zero retries, the unchanged 1 GiB browser cap and 90% guard. Tested fixture commit
`5e9cd38a53156ca6f22ea05d72d8690f83235aca` is integrated as `7782c22b`; application code
matches frozen `0070e6202cfe983ab2b6136f29e154c87c16a67c`.

Root inspected all twenty captures: approved document versions and source labels, the question
left for the person, held countdown/Cancel controls, and the submitted timeline. The external
ATS and model are local fakes using invented people. This native checkpoint precedes the
final combined gate; its exact result is recorded on PR #604.

| Stage | Desktop light | Desktop dark | Mobile light | Mobile dark |
| --- | --- | --- | --- | --- |
| Hosted Review | [desktop light](hosted-review-desktop-light.png) | [desktop dark](hosted-review-desktop-dark.png) | [mobile light](hosted-review-mobile-light.png) | [mobile dark](hosted-review-mobile-dark.png) |
| Hosted Applied | [desktop light](hosted-applied-desktop-light.png) | [desktop dark](hosted-applied-desktop-dark.png) | [mobile light](hosted-applied-mobile-light.png) | [mobile dark](hosted-applied-mobile-dark.png) |
| Full auto held | [desktop light](hosted-held-desktop-light.png) | [desktop dark](hosted-held-desktop-dark.png) | [mobile light](hosted-held-mobile-light.png) | [mobile dark](hosted-held-mobile-dark.png) |
| Framed recovery | [desktop light](framed-review-desktop-light.png) | [desktop dark](framed-review-desktop-dark.png) | [mobile light](framed-review-mobile-light.png) | [mobile dark](framed-review-mobile-dark.png) |
| No credential | [desktop light](no-credential-review-desktop-light.png) | [desktop dark](no-credential-review-desktop-dark.png) | [mobile light](no-credential-review-mobile-light.png) | [mobile dark](no-credential-review-mobile-dark.png) |
