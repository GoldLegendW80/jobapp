# The browser container, first version (browser B1). Debian's own Chromium for arm64, headed on
# a virtual display (Xvfb), the live view's pieces (x11vnc, websockify, noVNC), the fonts pages
# expect on a desktop, and Python with Playwright pinned. Playwright never launches a browser
# here: Chromium is started by the service and Playwright attaches to it over CDP on 127.0.0.1.
# B1 runs it through ops/browser-measure.sh; B2 adds browserd and owns this file from then on.
#
# Chromium is pinned by Debian version (bookworm-security) and the image is rebuilt when Debian
# publishes a security update: build with --build-arg CHROMIUM_VERSION=<new version>.
FROM debian:bookworm-slim

ARG CHROMIUM_VERSION=155.0.8059.39-1~deb12u1
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

RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir "playwright==${PLAYWRIGHT_VERSION}"

# One unprivileged user runs everything. The X socket directory exists with the sticky mode X
# expects, so Xvfb never needs root. /profiles holds the browser profiles (a volume from B2 on).
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin browser \
 && mkdir -p /profiles /tmp/.X11-unix \
 && chmod 1777 /tmp/.X11-unix \
 && chown browser:browser /profiles

USER browser
WORKDIR /home/browser
ENV PATH=/opt/venv/bin:$PATH \
    DISPLAY=:1 \
    TZ=Europe/Paris

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
