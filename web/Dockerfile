# web image: builds the React app with pnpm, then serves it with Caddy.
# Stages:
#   build   pnpm install and vite build into /web/dist
#   caddy-bin   xcaddy builds the caddy binary with one free, open source module (Apache 2.0):
#               github.com/mholt/caddy-ratelimit, the `rate_limit` of caddy/Caddyfile (issue #152)
#   caddy-base  caddy:2.x-alpine with that binary in place of its own, nothing else; the shell
#               tests run it with caddy/Caddyfile mounted (tests/tailscale_test.sh)
#   caddy   caddy-base with caddy/Caddyfile and the build in /srv; the `caddy` service of
#           compose.yml builds this target, passing caddy/ as the named context `caddy-config`:
#             docker build --target caddy --build-context caddy-config=caddy -t jobapp-caddy web/
#   dist    nothing but /dist, to export the static files locally with
#             docker build --target dist --output type=local,dest=out web/

ARG NODE_IMAGE=node:22.23.3-alpine3.24
ARG CADDY_IMAGE=caddy:2.11.7-alpine
# The builder of the same Caddy release, and the module pinned to one commit (2026-06-12, the
# ipv6_prefix of #97 and the metrics fix of #102); go.sum entries are checked against the Go
# checksum database while xcaddy downloads.
ARG CADDY_BUILDER_IMAGE=caddy:2.11.7-builder-alpine
ARG CADDY_VERSION=v2.11.7
ARG CADDY_RATELIMIT=github.com/mholt/caddy-ratelimit@v0.1.1-0.20260612195517-5625512f24f6

FROM ${NODE_IMAGE} AS build
ENV PNPM_HOME=/pnpm \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    CI=true
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable
WORKDIR /web
COPY package.json pnpm-lock.yaml ./
# Downloads the pnpm version pinned in packageManager, then installs from the lockfile only.
# extra_ca: a bundle Node trusts beside its own, given only behind a TLS inspecting proxy (docs/runbooks/develop-new-machine.md).
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi \
    && pnpm config set store-dir /pnpm/store \
    && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM ${CADDY_BUILDER_IMAGE} AS caddy-bin
ARG CADDY_VERSION
ARG CADDY_RATELIMIT
# extra_ca: a bundle Go trusts beside the image's own, given only behind a TLS inspecting proxy (README).
RUN --mount=type=cache,id=caddy-gomod,target=/go/pkg/mod \
    --mount=type=cache,id=caddy-gobuild,target=/root/.cache/go-build \
    --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca >/tmp/extra-ca.pem \
      && export SSL_CERT_FILE=/tmp/extra-ca.pem; \
    fi \
    && xcaddy build "$CADDY_VERSION" --output /usr/bin/caddy --with "$CADDY_RATELIMIT" \
    && rm -f /tmp/extra-ca.pem \
    && /usr/bin/caddy list-modules | grep -qx 'http.handlers.rate_limit'

FROM ${CADDY_IMAGE} AS caddy-base
COPY --from=caddy-bin /usr/bin/caddy /usr/bin/caddy

FROM caddy-base AS caddy
COPY --from=caddy-config Caddyfile /etc/caddy/Caddyfile
# Fails the build on an invalid or unformatted Caddyfile.
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
    && caddy fmt --diff /etc/caddy/Caddyfile >/dev/null
COPY --from=build /web/dist /srv
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/healthz"]

FROM scratch AS dist
COPY --from=build /web/dist /dist
