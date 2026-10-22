# The browser container (browser B1, then B2). Debian's own Chromium for arm64, headed on a
# virtual display (Xvfb), the live view's pieces (x11vnc, websockify, noVNC; B4 starts them), the
# fonts pages expect on a desktop, Python with Playwright pinned, and browserd (browserd/), the
# service compose.yml starts under tini. Playwright never launches a browser here: browserd
# starts Chromium for a lease and attaches Playwright to it over CDP on 127.0.0.1.
#
# Playwright is pinned exactly: browserd/pages.py calls its private `snapshotForAI` (the AI
# snapshot with refs), which a release may drop; api/tests/browser/test_browser_container.py
# fails loudly when it does. Bump PLAYWRIGHT_VERSION and browserd/pages.py together, on purpose.
#
# Chromium is pinned by Debian version (bookworm-security) and the image is rebuilt when Debian
# publishes a security update: build with --build-arg CHROMIUM_VERSION=<new version>.
# bookworm-security serves only its latest Chromium, so an older pin fails the build. To bump,
# set the Candidate this prints: docker run --rm debian:bookworm-slim sh -c
# 'apt-get update -qq && apt-cache policy chromium'
FROM debian:bookworm-slim

ARG CHROMIUM_VERSION=155.0.8059.79-1~deb12u1
ARG PLAYWRIGHT_VERSION=1.55.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      "chromium${CHROMIUM_VERSION:+=${CHROMIUM_VERSION}}" \
      xvfb x11vnc websockify novnc \
      fonts-liberation fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji \
      python3 python3-venv ca-certificates tzdata tini \
 && rm -rf /var/lib/apt/lists/*

# extra_ca: a bundle pip trusts beside the image's own, given only behind a TLS inspecting proxy
# (README); the apt step above reads deb.debian.org over http and needs none.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca >/tmp/extra-ca.pem \
      && export PIP_CERT=/tmp/extra-ca.pem SSL_CERT_FILE=/tmp/extra-ca.pem; \
    fi \
 && python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}" \
 && rm -f /tmp/extra-ca.pem

# Users: browserd runs as root with SETUID and SETGID only (compose.yml drops every other
# capability), Xvfb as `browser` (uid 1000), each person's Chromium as the profile's own uid
# (20000 and up, no passwd entry needed). /profiles is the volume `browserprofiles`, mode 1733:
# every uid may create its own 0700 directory there, none may list it. /leases is a tmpfs.
RUN useradd --no-create-home --home-dir /nonexistent --uid 1000 --shell /usr/sbin/nologin browser \
 && mkdir -p /profiles /leases \
 && chmod 1733 /profiles /leases

# Chromium's managed policy, which no profile state can undo: WebRTC sends no UDP outside the
# proxy (WebRtcIPHandling; Debian's Chromium ignores the switch of the same name) and no QUIC.
# WebRtcUdpPortRange stays unset on purpose. The profile's preferences (browserd/prep.py) say
# the same again. Every chrome:// page is blocked in every session (owner, 2026-10-20), but
# chrome://policy and chrome://sandbox (browserd.probe), the new tab page and the browser's own
# UI (chrome://*.top-chrome, the omnibox popup among them): chrome://inspect's remote debugging
# switch opens a DevTools port in a session started without one (measured for B7),
# chrome://flags and the settings pages change the browser. chrome://policy's test page is off
# (PolicyTestPageEnabled): its "Apply policies" replaced this file's policies.
# RemoteDebuggingAllowed and DeveloperToolsAvailability would also take CDP from the agents'
# leases. No extension may be installed (Debian's wrapper adds --enable-remote-extensions, and
# chrome://extensions is blocked). Chromium never offers to save a password
# (PasswordManagerEnabled): the app stores none.
COPY policies/browserd.json /etc/chromium/policies/managed/browserd.json

COPY browserd /opt/browserd/browserd
WORKDIR /opt/browserd
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/browserd \
    DISPLAY=:1 \
    TZ=Europe/Paris

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "browserd"]
