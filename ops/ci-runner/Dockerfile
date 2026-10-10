# CI runner for the Mac (issue #83): the official GitHub Actions runner for linux-arm64 with a
# Docker daemon of its own inside the container. ops/ci-runners.sh builds it and starts several
# instances in Docker Desktop; .github/workflows/ci.yml targets them with [self-hosted, mac-ci].
#
# Why a daemon inside: each runner then looks like one of GitHub's machines to a job. `services:`
# works (Linux and Docker, as the workflow syntax asks), published ports are on the runner's own
# 127.0.0.1, fixed container names, Compose project names and ports cannot collide between
# parallel jobs, and nothing a job creates or prunes can touch the Mac's own Docker daemon, the
# owner's containers or other agents' scratch stacks. The price is --privileged on the container
# (README, Continuous delivery, Security).
#
# Same platform as the Jetson (linux/arm64) and the same Docker Engine and Compose versions.
# Never used on the Jetson: its runner is ops/runner/.

# ubuntu:24.04 (what ubuntu-latest is), multi-arch index digest of 2026-10-10
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ARG RUNNER_VERSION=2.338.0
# sha256 of actions-runner-linux-arm64-2.338.0.tar.gz, from the release notes of v2.338.0
ARG RUNNER_SHA256=628b4a7258487b80c1d3c221095a7ded349f5ae0175fd3dfab708d07428f041b
# Docker's apt repository for noble; the Jetson runs Engine 29.8.0 and Compose v5.5.1.
ARG DOCKER_VERSION=5:29.8.0-1~ubuntu.24.04~noble
ARG CONTAINERD_VERSION=2.3.6-1~ubuntu.24.04~noble
ARG BUILDX_VERSION=0.37.1-1~ubuntu.24.04~noble
ARG COMPOSE_VERSION=5.5.1-1~ubuntu.24.04~noble
# sha256 of https://download.docker.com/linux/ubuntu/gpg, the key that signs that repository
ARG DOCKER_GPG_SHA256=1500c1f56fa9e26b9b8f42452a553675796ade0807cdce11975eb98170b3a570

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# What the jobs use from GitHub's image besides the setup actions: git, python3 (the scripts of
# .github/scripts, with yaml), curl, jq, openssl, rsync, ssh, archive tools, sudo (playwright
# install --with-deps calls it), and the libraries and fonts Playwright's Chromium needs, so that
# --with-deps finds them installed once in the image instead of once per runner. libatomic1:
# the pnpm that pnpm/action-setup installs (@pnpm/exe) links to it; it used to come with the
# compiler, and the first real run failed on it (run 38066771735). No compiler:
# every dependency of api/uv.lock has an arm64 wheel (a job that needs one can apt-get it).
# Size: about 2 GB, of which the runner and its two bundled Node versions, the Docker Engine
# with buildx and compose, and Chromium's libraries and fonts are nearly all; it is stored once
# for all runners.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl git jq openssl python3 python3-venv python3-yaml rsync tini sudo \
       iproute2 iptables kmod procps util-linux libatomic1 \
       openssh-client unzip zip xz-utils zstd bzip2 \
       libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64 \
       libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 libnspr4 libnss3 libpango-1.0-0 libx11-6 \
       libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 xvfb \
       fonts-noto-color-emoji fonts-unifont libfontconfig1 libfreetype6 xfonts-cyrillic \
       xfonts-scalable fonts-liberation fonts-ipafont-gothic fonts-wqy-zenhei \
       fonts-tlwg-loma-otf fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc; \
    echo "${DOCKER_GPG_SHA256}  /etc/apt/keyrings/docker.asc" | sha256sum -c -; \
    echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
      > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      "docker-ce=${DOCKER_VERSION}" "docker-ce-cli=${DOCKER_VERSION}" \
      "containerd.io=${CONTAINERD_VERSION}" "docker-buildx-plugin=${BUILDX_VERSION}" \
      "docker-compose-plugin=${COMPOSE_VERSION}"; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p /opt/actions-runner; \
    cd /opt/actions-runner; \
    curl -fsSL -o runner.tar.gz \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz"; \
    echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum -c -; \
    tar xzf runner.tar.gz; \
    rm runner.tar.gz; \
    ./bin/installdependencies.sh; \
    rm -rf /var/lib/apt/lists/*; \
    echo "${RUNNER_VERSION}" > /opt/actions-runner/.image-version

# uid 1001 and the name `runner`, as on GitHub's machines (ubuntu:24.04 already has uid 1000).
# Passwordless sudo, as there: a job already drives the container's Docker daemon, which is root
# inside this container, so sudo gives it nothing more.
RUN groupadd --gid 1001 runner \
    && useradd --uid 1001 --gid 1001 --no-create-home --home-dir /ci/home --shell /bin/bash runner \
    && usermod -aG docker runner \
    && echo 'runner ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner

# The inner daemon: classic overlay2 store (smaller on disk than the containerd image store),
# address pools away from 172.17.0.0/16, which is the network this container itself sits on.
COPY daemon.json /etc/docker/daemon.json
COPY --chmod=755 entrypoint.sh /usr/local/bin/ci-runner-entrypoint
# The hooks live in the image, outside the runner directory and outside any checkout, so a
# commit cannot switch them off by accident. A job has sudo here, so one that means to can:
# guard rails, not a boundary (README, Security). The runner only takes hook paths ending in .sh.
COPY --chmod=755 job-started.sh /usr/local/bin/ci-job-started.sh
COPY --chmod=755 job-completed.sh /usr/local/bin/ci-job-completed.sh
COPY --chmod=755 ci-clean.sh /usr/local/bin/ci-clean.sh
ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/ci-job-started.sh \
    ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/usr/local/bin/ci-job-completed.sh

# Mac only, in every process a job starts and, through CI_COMPOSE_OVERLAY, in the api and worker
# containers of its scratch stacks (the workflow adds `-f $CI_COMPOSE_OVERLAY` when it is set):
# in Docker Desktop's Linux guest on an M4, importing `cryptography` ends in SIGILL unless
# OpenSSL skips its CPU detection (compose.mac.yml, issue #19). The workflow file never names
# the variable (api/tests/test_openssl_armcap.py), and neither does anything the Jetson runs.
ENV OPENSSL_armcap=0 \
    CI_COMPOSE_OVERLAY=compose.mac.yml

# Caches shared by the runner instances (one volume, /ci), each tool locking its own:
# uv's wheels and Pythons, the pnpm store, Playwright's browsers, pre-commit's environments.
# Hard links from the uv cache and the pnpm store into a checkout need the same file system,
# which is why the runner directories live in that volume too.
ENV UV_CACHE_DIR=/ci/cache/uv \
    UV_PYTHON_INSTALL_DIR=/ci/cache/uv-python \
    npm_config_store_dir=/ci/cache/pnpm-store \
    PLAYWRIGHT_BROWSERS_PATH=/ci/cache/ms-playwright \
    PRE_COMMIT_HOME=/ci/cache/pre-commit \
    RUNNER_ALLOW_RUNASROOT=0

LABEL jobapp.ci=runner
WORKDIR /ci
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/ci-runner-entrypoint"]
CMD ["run"]
