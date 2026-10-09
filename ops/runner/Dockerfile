# Self-hosted GitHub Actions runner for the Jetson (platform: GitHub delivery), no sudo needed.
#
# The official runner release for linux-arm64, on the same Ubuntu as the Jetson, run as a user
# with the Jetson user's name, uid and home, so ~/jobapp and ~/jobapp-backups (bind-mounted at the
# same paths) mean the same thing inside and outside. A job-started hook (job-guard.sh) refuses
# every job but deploy.yml from main. The Docker CLI and its compose and buildx
# plugins are bind-mounted read-only from the host by ./deploy.sh runner install, so they always
# match the daemon they talk to. Built on the Jetson by ./deploy.sh runner install:
#
#   docker build --build-arg RUNNER_USER=yessai --build-arg RUNNER_UID=1000 \
#     --build-arg RUNNER_GID=1000 -t jobapp-runner:2.338.0 - < context.tar
#
# The runner itself (binaries, registration, self-updates) lives in a named volume at
# ~/actions-runner; entrypoint.sh seeds it from /opt/actions-runner on first start.

# ubuntu:22.04, multi-arch index digest of 2026-10-09
FROM ubuntu:22.04@sha256:5ec03bb3441e8b0bf3b4f9cd4629a1ae763010dc3035bb8da3ae6cf026486401

ARG RUNNER_VERSION=2.338.0
# sha256 of actions-runner-linux-arm64-2.338.0.tar.gz, from the release notes
ARG RUNNER_SHA256=628b4a7258487b80c1d3c221095a7ded349f5ae0175fd3dfab708d07428f041b
ARG RUNNER_USER=runner
ARG RUNNER_UID=1000
ARG RUNNER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# What the deploy workflow runs besides docker: git (checkout), rsync, curl, python3 (smoke),
# jq, openssl. tini reaps the runner's children.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl git jq openssl python3 rsync tini iproute2 \
    && rm -rf /var/lib/apt/lists/*

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

RUN groupadd --gid "${RUNNER_GID}" "${RUNNER_USER}" \
    && useradd --uid "${RUNNER_UID}" --gid "${RUNNER_GID}" --create-home \
       --home-dir "/home/${RUNNER_USER}" --shell /bin/bash "${RUNNER_USER}" \
    && mkdir -p "/home/${RUNNER_USER}/actions-runner" \
    && chown "${RUNNER_UID}:${RUNNER_GID}" "/home/${RUNNER_USER}/actions-runner"

COPY --chmod=755 entrypoint.sh /usr/local/bin/runner-entrypoint
# Every job runs this first and fails unless it is deploy.yml from main (job-guard.sh). Set in the
# image, not in the repository, so no branch or workflow can switch it off. The runner only takes
# a hook path ending in .sh (it refused /usr/local/bin/runner-job-guard on 2026-10-09).
COPY --chmod=755 job-guard.sh /usr/local/bin/runner-job-guard.sh
ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=/usr/local/bin/runner-job-guard.sh

ENV RUNNER_HOME=/home/${RUNNER_USER}/actions-runner \
    RUNNER_ALLOW_RUNASROOT=0
USER ${RUNNER_UID}:${RUNNER_GID}
WORKDIR /home/${RUNNER_USER}
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/runner-entrypoint"]
CMD ["run"]
