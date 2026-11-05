# OpenPets Linux GTK3 MVP CI image.
# Build context: repository root.
#   docker build -f Linux/Dockerfile -t openpets-linux-test .
#
# Runtime: native system GTK3 + Python GTK3 (PyGObject) bindings, Xvfb for
# headless GTK smoke tests, Ayatana AppIndicator for Linux tray. Kept lean for
# memory-constrained hosts.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/work/Linux \
    PYTHONUNBUFFERED=1

# Install runtime dependencies in a single RUN so apt-get update runs before
# install and the package cache is removed from the same layer (Docker
# best practice: https://docs.docker.com/build/building/best-practices).
# --no-install-recommends keeps the image lean.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    xvfb \
    xauth \
    dpkg-dev \
    webp-pixbuf-loader \
    python3-venv \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Verify the expected apt packages landed in the image at build time.
RUN dpkg -s python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 xvfb xauth dpkg-dev webp-pixbuf-loader >/dev/null

WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 HOME=/home/openpets

COPY Linux/requirements-mcp.txt /work/Linux/requirements-mcp.txt
RUN python3 -m venv --system-site-packages /opt/openpets-mcp \
    && /opt/openpets-mcp/bin/python -m pip install --no-cache-dir --only-binary=:all: -r /work/Linux/requirements-mcp.txt
ENV PATH=/opt/openpets-mcp/bin:$PATH

# Changing application code does not invalidate dependency-install layers.
COPY Linux /work/Linux
COPY scripts/package-linux.sh /work/scripts/package-linux.sh
COPY LICENSE /work/LICENSE

# Tests and package staging do not need root privileges.
RUN mkdir -p /home/openpets && chown 1000:1000 /home/openpets
USER 1000:1000

# Pure-Python unit tests run without a display. The GTK smoke test
#   xvfb-run python3 -m openpets_linux --smoke-test
# is invoked by the caller overriding this command; PYTHONPATH is set above.
CMD ["python3", "-m", "unittest", "discover", "-s", "/work/Linux/tests"]
