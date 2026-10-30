# Application email setup

This page covers part 2 of #604: the Gmail provider (`gmailmcp`), its one time send key, its
firewall, its credentials and their deployment. With this part alone nothing sends: no app module
calls the provider's `send_message` tool yet. What uses it arrives in part 4 of #604 and is marked
"coming in a later part" below.

Coming in a later part (part 4 of #604): email applications that use the same application
approval, document versions, Full auto hold, company caps, pause switches and rehearsal rules as
forms; the complete message and its new recipient shown in the approval; addresses taken only
from the posting, a choice when there are several, and in Full auto only an address whose domain
belongs to the posting's own company website, never a public mailbox or an ATS host.

Coming in a later part: the worker prepares drafts and performs the Full auto text check, and
only the API process sends, through a dispatcher of its own that checks due items every 15
seconds, at most five per pass.

In this part: the mail gate's key, `app/mail/send_key.py`, binds one send to the exact MIME
bytes for one minute. The Gmail provider marks that key used in Redis before its single Gmail
call. A timeout or a Gmail 5xx answers `unknown` and the key stays used, so the same key never
reaches Gmail again (coming in a later part: Sent reconciliation, or the person's decision,
settles such a send; a background task never sends it again).

The regular deployment starts the base `gmailmcp` service in read-only mode after checking its
separate host firewall. OAuth refresh tokens remain encrypted in the API database. The provider
receives one access token per call, verifies its connected address, and stores no credentials.
Its memory limit is 128 MiB. The declared core service limits total 5760 MiB (5248 MiB before
the browser went to 1536 MiB on 2026-10-30). With the provider they total 5888 MiB, within the
6144 MiB platform budget (5120 MiB until then). No existing limit was reduced.
Physical Jetson memory acceptance remains an operator measurement before deployment.

The sending overlay is adopted automatically by `deploy.sh` when `.env.mailsend` exists.
It loads that file into API and provider alone. API and worker receive `.env.redis`, whose
default Redis credential differs from the provider's file credential. The provider's ACL permits
only `SET` on `mailmcp:send:*`; it cannot use Redis's default user without that user's password.
The signing key never belongs in the general `.env`, and the worker refuses to start when its
settings hold `MAIL_SEND_KEY` (`app/config.py`, `refuse_send_key`).

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
with sending permission. Coming in a later part: an absent connection, missing send scope or
missing send configuration leaves the application with “Needs you” and a concrete reason.

First deploy the read-only provider after installing its firewall, before creating sending
credentials. Verify the normal Account connect/read behavior. Then run the credential setup and
the checked deployment together; the prior Redis process must be recreated with its ACL before
API and worker use the new Redis credential. Provider firewall installation without credential
setup enables mailbox reads while keeping sending unavailable. Until the provider's firewall is
installed, the provider stays down and connecting Gmail fails as unreachable; the deployment
warns about it. After a reboot the provider stays down until a deployment checks the firewall
again, as the browser does. Confirm the provider's memory on the Jetson before enabling
Full auto email. The human mail checkpoint uses a second address of the owner's own, never a
company address. No test uses a real Gmail account.

Synthetic evidence of this part lives in `tests/mail/test_mcp_send.py` (a send, a replay, the
quota, Redis down, Gmail 5xx and timeout, a missing or forged key), `tests/mail/test_send_key.py`
(the key, and the worker's refusal of the signing key), `tests/mail/test_setup.py` (the setup
script and the Redis ACL text), `tests/test_compose_mail.py` (which service gets which credential
file) and `tests/deploy_test.sh` (the provider starts only after its firewall check passes).

The mail journey group starts the provider read only, without the synthetic send overlay. The
apply journey group starts both Gmail and ATS fixtures and the synthetic
`tests/mail-send.compose.yml` ACL and key settings with the `browser,mail` Compose profiles.
Coming in a later part: the email application journey that sends through them.

On #604's branch, on the isolated Docker Desktop arm64 fixture stack, a single maximum
12582912-byte raw message passed through the installed MCP 2.3.0 HTTP transport and fake Gmail
under a 134217728-byte provider cgroup. Baseline memory.current was 79577088 bytes; memory.peak
reached the cap; the initial probe recorded 215 memory.events max events, zero oom and zero
oom_kill. A repeat on the final serialized provider also sent successfully, with 292 max events,
zero oom and zero oom_kill, and memory.current 103272448 bytes afterward. The provider survived
with no restart. This demonstrates reclaim pressure, with no spare-headroom claim. Provider
requests are serialized before MCP reads request bodies, with at most 16 Uvicorn connections.
Neither this measurement nor the configured limit sum proves physical Jetson capacity.
