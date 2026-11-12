# The LibreOffice converter (docs/office.md): an optional second image, run
# with runtime runsc (gVisor), no network, a read-only root and its own user.
# It converts one Word, PowerPoint or Excel file to PDF, then exits, and Docker
# starts a fresh container for the next (snug/office/supervisor.py).
#
#   docker build -f converter/Dockerfile -t snug-office .
#
# The same Python base as Snug's own image, so one pin covers both.
FROM python:3.14.10-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d

LABEL org.opencontainers.image.source="https://github.com/Negroni-Venture-Studios/snug" \
      org.opencontainers.image.description="Snug's LibreOffice converter: Office files to PDF, in a sandbox" \
      org.opencontainers.image.licenses="MIT AND MPL-2.0"

# LibreOffice without its desktop, for Word, PowerPoint and Excel only, and
# fonts whose metrics match Calibri, Cambria, Arial and Times. No Java, and no
# script providers, so no macro language can run even if a check failed.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends \
      libreoffice-writer-nogui libreoffice-impress-nogui libreoffice-calc-nogui \
      fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation2 fonts-dejavu-core fonts-noto-core \
 && { apt-get purge --yes --auto-remove 'libreoffice-script-provider-*' 'default-jre*' 'openjdk-*' || true; } \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache --force \
 && test -x /usr/lib/libreoffice/program/soffice.bin \
 && ! dpkg-query --show --showformat='${Package}\n' | grep -E '^(openjdk|default-jre|libreoffice-script-provider)'

# What the Settings panel shows: the LibreOffice version and when this image was built.
ARG SNUG_OFFICE_BUILT=""
RUN version="$(dpkg-query --show --showformat='${Version}' libreoffice-core-nogui)" \
 && built="${SNUG_OFFICE_BUILT:-$(date -u +%Y-%m-%d)}" \
 && printf '{"libreoffice": "%s", "built": "%s"}\n' "$version" "$built" > /etc/snug-office.json

# The supervisor and the seccomp filter, as a package of their own: none of Snug comes with them.
COPY snug/rendering/seccomp.py /usr/local/lib/python3.14/site-packages/snug/rendering/seccomp.py
COPY snug/office/supervisor.py /usr/local/lib/python3.14/site-packages/snug/office/supervisor.py
COPY converter/registrymodifications.xcu /opt/snug-office/profile/registrymodifications.xcu
RUN touch /usr/local/lib/python3.14/site-packages/snug/__init__.py \
          /usr/local/lib/python3.14/site-packages/snug/rendering/__init__.py \
          /usr/local/lib/python3.14/site-packages/snug/office/__init__.py \
 && python3 -I -c "import snug.office.supervisor" \
 && groupadd --system --gid 10002 office \
 && useradd --system --uid 10002 --gid office --home-dir /nonexistent --shell /usr/sbin/nologin office \
 # Mount points owned as Snug's are, whichever container first fills a new volume.
 && install --directory --owner 10001 --group 10001 --mode 0755 /exchange/inbox \
 && install --directory --owner 10001 --group 10001 --mode 1733 /exchange/outbox

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER office
WORKDIR /tmp
CMD ["python3", "-I", "-m", "snug.office.supervisor"]
