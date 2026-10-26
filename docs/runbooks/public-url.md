# Public URL

The Tailscale Funnel public entry point, headers, request limits and the Cloudflare quick tunnel fallback.

The service `tailscale` puts the Jetson on Harald's tailnet (`taild83d16.ts.net`, free plan) and
opens Caddy to the internet with [Tailscale Funnel](https://tailscale.com/kb/1223/funnel). The URL
is stable, `https://jobapp.taild83d16.ts.net` (the node name `TS_HOSTNAME` plus the tailnet), so
OAuth redirect URIs can point at it. It survives restarts, redeploys and reboots. The container only
makes outbound connections (no port opened on the router, none published on the Jetson) and runs
with userspace networking: no `/dev/net/tun`, no `NET_ADMIN`, every capability dropped, no sudo.

```bash
./deploy.sh url                          # https://jobapp.taild83d16.ts.net
curl "$(./deploy.sh url)/api/health"     # {"status":"ok"} from anywhere
```

`./deploy.sh url` asks the running container (`tailscale status --json`, `Self.DNSName`) and waits
up to 30s for it to connect. A missing or stopped container, a node that is not logged in (it then
shows the login link) or one without a ts.net name is an exit 1 with the reason, nothing on stdout.
If another device of the tailnet already holds the name, the node becomes `jobapp-1`: `url` prints
the real name with a warning, and the old device must be removed in the admin console (Machines).
`./deploy.sh` prints the URL at the end; `.github/workflows/deploy.yml` puts it in the job summary.

**Login.** The first start needs a one-time auth key from the admin console (Settings, Keys), as
`TS_AUTHKEY=` in the Jetson's `~/jobapp/.env` next to `TS_HOSTNAME=jobapp`. It is read once
(`TS_AUTH_ONCE`): the node's login, key and HTTPS certificate then live in the `tsstate` volume, so
**remove `TS_AUTHKEY` from the Jetson's `.env` (and from `.env.jetson`) once `./deploy.sh url`
answers**. A new key is needed only if `tsstate` is deleted or the node is removed from the
tailnet. Without a key and without a saved login the container never turns healthy (its logs say
`To authenticate, visit: ...`, then `failed to auth tailscale`), so a deploy fails loudly. The api
reads the same `.env`, so the key is in its environment until it is removed: another reason to
remove it. `./deploy.sh` warns at its end, once the node is logged in, when `TS_AUTHKEY` still has
a value in `.env.jetson` or in the Jetson's `.env` (it never edits either file), and
`tests/jetson_smoke.sh` warns the same way; the key was removed from the Jetson on 2026-10-10. Funnel needs the `funnel` node attribute in the tailnet policy (granted to
`autogroup:member`) and HTTPS certificates enabled in the tailnet, both set on 2026-10-10.

**Headers.** `tailscale/serve.json` (its folder mounted read only, `TS_SERVE_CONFIG`) terminates TLS for
`${TS_CERT_DOMAIN}:443`, proxies everything to Caddy's own listener `caddy:8080` (never
published) and allows Funnel on that port only. Funnel's proxy sets `X-Forwarded-For` to the
address the visitor connected from, replacing anything the visitor sent, and `X-Forwarded-Proto:
https`. The `:8080` listener trusts the Compose network as a proxy and reads the client from
`X-Forwarded-For` only (strictly right to left); `Cf-Connecting-Ip`, `X-Real-Ip` and the like pass
through Funnel untouched, so they are never trusted. The published `:80` trusts nobody. Either way
the api receives exactly one `X-Forwarded-For` and logs it as `client`. `./deploy.sh smoke` proves
it from outside: one request forging every client address header, and the api must log this
machine's public address. `tests/tailscale_test.sh` checks the Compose definition, Caddy's trust
rules and a start without a key, locally and without joining the tailnet.

The service sits in the Compose profile `tunnel`, which `deploy.sh` enables on the Jetson. Neither
the dev stack nor a plain `docker compose -f compose.yml up` on the Mac joins the tailnet.

## Request limits (issue #100)

The app is on the internet through Funnel. FastAPI reads a JSON body before a route's
dependencies run, so on `main` a caller with no session could make the api hold a body on any
JSON route, the ones that answer 401 included: thirty stalled 9 MiB sign ins took the api from
115 to 375 MiB of its 512. What bounds it now, by layer:

- **Caddy** (`caddy/Caddyfile`). Three body classes, each request in exactly one: the routes
  under `/api/auth/` **64 KiB**, the document upload `POST /api/ingestion/documents` **20 MiB**,
  everything else **10 MiB**. Over a declared length is Caddy's own 413, nothing proxied; a body
  streamed past its limit is cut. Request headers must arrive within **10s** (`read_header`), and
  an idle keep-alive connection is closed after **60s** (`idle`). Caddy's rule for a request body that
  goes silent is left at its default, so that the upload and the paste, which answer a stalled
  body themselves after 30s and 60s, speak first. That default does not cover small bodies:
  measured in the review, Caddy ended a silent body after a minute only when some hundreds of
  KiB of the declared length were still missing, and did nothing at all for a sign in body.
- **api** (`api/app/body_limit.py`, before any body is parsed). **No session, no body**: under
  `/api`, a request that announces a body on a route that is not public is answered 401 before a
  byte of it is read. Then a size by class: `/api/auth/` 64 KiB, other JSON routes **1 MiB**; the
  routes that read their own body keep their own limit and are not read twice (profile photo and
  import 2 MiB, jobs import 10 MiB, document upload 20 MiB and paste). Over the size is 413. A
  body the api caps must bring something every 20s and be whole within 60s, on `/api/auth/`
  every 5s and within 10s, else 408. `POST /api/jobs` has a size of its own, from its schema (a
  posting of 1,000,000 characters at four bytes each).
- **uvicorn** (`api/Dockerfile`). `--limit-concurrency 256`: with 256 connections open, the next
  request is answered 503. `--timeout-keep-alive 5`.

What remains possible, for a small private app (measured once each, on a scratch stack by the
author, `tasks/evidence/platform-100/`, or by the reviewer of pull request #106 where said):

- **Sign ins at once are bounded since issue #108.** Each password check takes 64 MiB by design,
  so the api runs two at once (128 MiB of its 512), lets 16 callers wait at most 3 seconds, and
  answers the rest 503 with `Retry-After: 2`. On the development Mac 120 sign ins at once peaked
  at 782 MiB before and 283 MiB after (`tasks/evidence/identity-108/`). A caller who keeps those
  places taken makes real sign ins answer 503 while the flood lasts; people already signed in are
  slowed, not locked out. From one IPv4 address or one IPv6 /64 the flood ends by itself at 20
  failures (429 before any slot); a distributed flood can still keep the sign in door answering
  503 while it lasts (issue #152). A signed in user has at most six requests with a body in flight, the next is a
  429 (`api/app/body_limit.py`).
- **256 slow connections stop the app answering while they last.** That is the price of the
  uvicorn limit, which keeps memory bounded for callers with no session (a signed in user's
  bodies are bounded per user, six in flight): 260 connections each holding a sign in body just
  under 64 KiB cost about 42 MiB at the api and 35 MiB at Caddy, and every other request, the
  health check included, was answered 503 until they were released. A silent sign in holds its
  place for 5s and a trickling one for 10s (20s and 60s when that was measured), so the caller
  must keep opening them. The container's own health check shares the limit: during such a flood
  it reads unhealthy, and a deploy started then would fail and roll back.
- **A small body that goes silent or trickles keeps its connection to Caddy for as long as the
  client likes** (reviewer: 240s with no answer for a sign in body of 1,000 declared bytes). The
  api lets go at its own times; Caddy, with 128 MiB and no limit on connections, does not, and
  has no limit per client address without a third party module (through Funnel every visitor
  reaches it from the same peer anyway). The reviewer saw 2,000 connections with unfinished
  headers take Caddy from 22 to 63 MiB for the 10s they are allowed. This was so before; it is
  not bounded now either.

**Bringing the Cloudflare quick tunnel back** (it was the public entry point until 2026-10-10:
free, no account, but a random `*.trycloudflare.com` URL on every start): restore the
`cloudflared` service of compose.yml (`cloudflare/cloudflared` running `tunnel --no-autoupdate
--url http://caddy:8080`, profile `tunnel`), add `Cf-Connecting-Ip` back to the `:8080`
`client_ip_headers` of caddy/Caddyfile only if tailscale is removed at the same time (Funnel
does not strip it), and take `tunnel_url` and its tests from the history of deploy.sh (git log
`-S trycloudflare`).
