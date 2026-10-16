# The renderer service (documents D3): Playwright's Chromium headless shell for linux arm64, the
# engine D1 measured and chose on the Jetson, behind server.py. About 965 MB in all, 820 MB over
# the Python base the worker image already brings to the Jetson (D1).
#
# Versions: Playwright 1.55.0 pins its headless shell (140.0.7339.16), the build D1 measured;
# upgrade both together and measure again with tests/documents_render_jetson.sh.
# DejaVu is the one installed family: the templates embed theirs as data: addresses, and the
# render fails when the PDF holds a font the HTML did not ask for (render_client.py), so an
# installed font is never a silent fallback. Its fontconfig cache is built here, since the root
# is read only at run time.
ARG PYTHON_IMAGE=python:3.12.15-slim-trixie
FROM ${PYTHON_IMAGE}
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# extra_ca: a bundle pip and Playwright's download trust beside their own, given only behind a TLS
# inspecting proxy (README); apt reads deb.debian.org over http and needs none.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca >/tmp/extra-ca.pem \
      && export PIP_CERT=/tmp/extra-ca.pem SSL_CERT_FILE=/tmp/extra-ca.pem \
         NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; \
    fi \
    && pip install --no-cache-dir playwright==1.55.0 \
    && playwright install --with-deps --only-shell chromium \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/* /root/.cache /tmp/extra-ca.pem \
    && groupadd --system --gid 10003 renderer \
    && useradd --system --uid 10003 --gid renderer --no-create-home --home-dir /tmp \
       --shell /usr/sbin/nologin renderer
COPY server.py /renderer/server.py
USER 10003:10003
EXPOSE 7071
CMD ["python", "-I", "/renderer/server.py"]
