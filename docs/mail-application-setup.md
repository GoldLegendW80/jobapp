# Application email setup

Email applications use the same application approval, document versions, Full auto hold,
company caps, pause switches and rehearsal rules as forms. The complete message and its new
recipient are shown in the approval. Addresses come only from the posting. Multiple addresses
require the person to choose one. Full auto accepts an address only when its domain belongs to
the posting's own company website, never a public mailbox or an ATS host.

The worker prepares drafts and performs the Full auto text check. Only the API process sends.
Its owned dispatcher checks due items every 15 seconds, at most five per pass. The mail gate
commits a content-free audit before sending, builds MIME itself, rechecks live guards, and
mints a one-minute capability bound to the exact MIME bytes. The Gmail provider consumes that
capability in Redis before its single Gmail call. A timeout or a Gmail 5xx stays unknown until
Sent reconciliation or the person's decision. A background task never sends it again.

The regular deployment starts the base `gmailmcp` service in read-only mode after checking its
separate host firewall. OAuth refresh tokens remain encrypted in the API database. The provider
receives one access token per call, verifies its connected address, and stores no credentials.
Its memory limit is 128 MiB. The original declared service limits total 5248 MiB. With the
provider they total 5376 MiB, above the 5120 MiB platform budget. No existing limit was reduced.
Physical Jetson memory acceptance remains an operator measurement before deployment.

The sending overlay is adopted automatically by `deploy.sh` when `.env.mailsend` exists.
It loads that file into API and provider alone. API and worker receive `.env.redis`, whose
default Redis credential differs from the provider's file credential. The provider's ACL permits
only `SET` on `mailmcp:send:*`; it cannot use Redis's default user without that user's password.
The signing key is forbidden in worker settings and never belongs in the general `.env`.

Run these commands yourself on the Jetson, in the checkout used for deployment. They need root
to install host firewall rules and assign the provider's credential file to service uid 10001.
They create new random credentials without displaying them and refuse to overwrite existing
files. The setup does not connect to Google or send a message.

New files receive their intended permissions regardless of the operator's umask: plaintext
credentials use mode 600 and the hashed Redis ACL uses mode 444. A new `.secrets` directory
uses mode 755 so the separate runtime users can traverse it. An existing `.secrets` must be a
real directory with traversal permission for all users and no group or other write permission.
Setup preserves its permissions and refuses an incompatible directory before creating credentials.
Review any existing contents and permissions yourself before retrying.

```sh
sudo python3 ops/mail-setup.py --directory "$PWD" --owner-uid "$(id -u)"
sudo env JOBAPP_EGRESS_KIND=mail bash ops/browser-egress.sh install jobapp_gmail_egress
```

Then use the normal checked deployment command. It selects `compose.mail.yml`, verifies the
firewall before starting the provider, and recreates API, worker and Redis with their matching
credentials. No source edit is needed. The Gmail Account card must show the intended account
with sending permission. An absent connection, missing send scope or missing send configuration
leaves the application with “Needs you” and a concrete reason.

First deploy the read-only provider after installing its firewall, before creating sending
credentials. Verify the normal Account connect/read behavior. Then run the credential setup and
the checked deployment together; the prior Redis process must be recreated with its ACL before
API and worker use the new Redis credential. Provider firewall installation without credential
setup enables mailbox reads while keeping sending unavailable. After a reboot the provider stays down until a deployment checks the
firewall again, as the browser does. Confirm the provider's memory on the Jetson before enabling
Full auto email. The human mail checkpoint uses a second address of the owner's own, never a
company address. No test uses a real Gmail account.

Synthetic evidence lives in `tests/apply/test_a14_email.py`, `tests/mail/test_mcp_send.py` and
`tests/mail/test_send_key.py`. The Gmail fake stores the actual MIME bytes so attachment tests
compare both PDFs byte for byte. The broader mail inbox and sync pipeline are separate work;
the application dispatcher reconciles its own unknown send records through the read provider.

The normal apply journey group starts both Gmail and ATS fixtures, claims and retries their
loopback ports, uses the synthetic `tests/mail-send.compose.yml` ACL/key settings, and enables
the `browser,mail` Compose profiles. `apply-a14.spec.ts` fails when the Gmail fixture is absent;
it passed through Caddy, the API/worker, MCP and fake Gmail, including the exact-message review,
recipient confirmation, one send with both PDFs and a clean browser console. The existing mail
journey group starts the same provider read-only, without the synthetic send overlay.

On the isolated Docker Desktop arm64 fixture stack, a single maximum 12582912-byte raw message
passed through the installed MCP 2.3.0 HTTP transport and fake Gmail under a 134217728-byte
provider cgroup. Baseline memory.current was 79577088 bytes; memory.peak reached the cap;
the initial probe recorded 215 memory.events max events, zero oom and zero oom_kill. A repeat
on the final serialized provider also sent successfully, with 292 max events, zero oom and
zero oom_kill, and memory.current 103272448 bytes afterward. The provider survived with
no restart. This demonstrates reclaim pressure, with no spare-headroom claim. Provider requests
are serialized before MCP reads request bodies, with at most 16 Uvicorn connections. The
synthetic stack snapshot totaled 847.83 MiB actual Docker stats across 13 containers, including
fake services, while idle/creating a base CV. That snapshot and the configured limit sum are
different quantities; neither proves physical Jetson capacity.

Validation: the full apply/mail/migration suite had 5808 passing tests and two concrete
regressions, an injected worker-service lookup and the former mailto refusal expectation.
After correction, all 310 tests in the affected A14/start/Lever/provider/setup files passed.
The final A14 browser journey passed in 25.5 seconds; the seven existing M4 account journeys
passed in 9 seconds. Deployment contracts passed 175 checks, the local allocator/gate harness
52, and CI-plan checks 81. API Ruff, web type checking and pinned shellcheck passed. The
firewall's 14 static/fake checks passed; its Docker iptables namespace section still requires
the combined gate, and no host firewall install or real deployment was performed.

The shared “It was submitted” action now resolves the exact owned application, active MailSend
and draft atomically. Source `4bd8d680` (integrated as `5b2b91c8`) passed 25 real-database cases,
including fresh requests through both confirmation routes, repeated requests, later
reconciliation, mismatched bindings and provider/user races in both orders. Repeating an
equivalent email confirmation does not add another event or freeze; the provider result is
preserved. It makes no send request. The earlier successful fake browser send/transport
evidence remains valid. Final combined regression, firewall namespace proof and independent
review remain before A14 acceptance.
