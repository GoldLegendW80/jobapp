# web image: builds the React app with pnpm, then serves it with Caddy.
# Stages:
#   build   pnpm install and vite build into /web/dist
#   caddy   caddy:2.x-alpine with caddy/Caddyfile and the build in /srv; the `caddy` service of
#           compose.yml builds this target, passing caddy/ as the named context `caddy-config`:
#             docker build --target caddy --build-context caddy-config=caddy -t jobapp-caddy web/
#   dist    nothing but /dist, to export the static files locally with
#             docker build --target dist --output type=local,dest=out web/

ARG NODE_IMAGE=node:22.23.3-alpine3.24
ARG CADDY_IMAGE=caddy:2.11.7-alpine

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

FROM ${CADDY_IMAGE} AS caddy
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
