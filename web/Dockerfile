# web image: builds the React app with pnpm, then keeps only the static files.
# The final stage `dist` holds nothing but /dist, for Caddy to copy with
#   COPY --from=web /dist /srv
# or to export locally with
#   docker build --target dist --output type=local,dest=out web/

ARG NODE_IMAGE=node:22.23.3-alpine3.24

FROM ${NODE_IMAGE} AS build
ENV PNPM_HOME=/pnpm \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    CI=true
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable
WORKDIR /web
COPY package.json pnpm-lock.yaml ./
# Downloads the pnpm version pinned in packageManager, then installs from the lockfile only.
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm config set store-dir /pnpm/store \
    && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM scratch AS dist
COPY --from=build /web/dist /dist
