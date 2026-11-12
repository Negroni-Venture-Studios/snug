# Snug's container image: one image, run by Docker Compose behind Caddy.
#
# Base images are pinned by digest, so a build always starts from the same
# bytes; Dependabot proposes new digests. Python 3.14.10 is an expedited
# security release (CVE-2026-115557, CVE-2026-114583), taken under the
# exception for security releases in AGENTS.md.

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

# ---------------------------------------------------------------- build
FROM python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d AS build

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_LICENCES.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY snug ./snug
RUN uv sync --locked --no-dev

# Static files are collected into the image. These settings exist only for
# this step: Snug refuses to start without a real key and domain.
RUN SNUG_SECRET_KEY="build-only-$(head -c 48 /dev/urandom | base64 | tr -d '/+=\n')" \
    SNUG_DOMAIN=localhost SNUG_HTTPS=false SNUG_DATA_DIR=/tmp/build-data \
    /app/.venv/bin/snug collectstatic --noinput --verbosity 0 \
 && rm -rf /tmp/build-data

# ---------------------------------------------------------------- runtime
FROM python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d

LABEL org.opencontainers.image.source="https://github.com/Negroni-Venture-Studios/snug" \
      org.opencontainers.image.description="Snug: a self-hosted data room for early-stage founders" \
      org.opencontainers.image.licenses="MIT"

# Fonts for PDFs that do not embed theirs. The render worker may read only
# these (Landlock), so they must be in the system font folders.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends fonts-dejavu-core fonts-liberation2 \
 && rm -rf /var/lib/apt/lists/*

# Snug and its render worker run as one unprivileged user. The worker is
# confined by Landlock, seccomp and resource limits, not a second user
# (docs/design.md).
RUN groupadd --system --gid 10001 snug \
 && useradd --system --uid 10001 --gid snug --home-dir /var/lib/snug --shell /usr/sbin/nologin snug \
 && install --directory --owner snug --group snug --mode 0700 /var/lib/snug \
 # The folders shared with the LibreOffice converter, if it is installed: Snug
 # writes jobs to the inbox, which the converter only reads, and the converter
 # writes PDFs to the outbox, which it cannot list (docs/office.md).
 && install --directory --owner snug --group snug --mode 0755 /var/lib/snug-office/inbox \
 && install --directory --owner snug --group snug --mode 1733 /var/lib/snug-office/outbox

COPY --from=build --chown=root:root /app /app

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SNUG_DATA_DIR=/var/lib/snug

# Which Snug this is, shown on the Overview: a release's version, or "edge"
# and the commit. Set by the Image workflow; empty in a local build.
ARG SNUG_BUILD_VERSION=""
ARG SNUG_BUILD_COMMIT=""
ENV SNUG_BUILD_VERSION=$SNUG_BUILD_VERSION \
    SNUG_BUILD_COMMIT=$SNUG_BUILD_COMMIT

USER snug
WORKDIR /var/lib/snug
VOLUME /var/lib/snug
EXPOSE 8000

# /healthz is answered before the host check, so Docker can probe it by IP.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

CMD ["snug", "serve", "--bind", "0.0.0.0:8000"]
