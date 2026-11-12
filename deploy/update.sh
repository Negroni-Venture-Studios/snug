#!/bin/sh
# snug update: bring this server's Snug up to date.
#
# Run on the server as sudo snug update. It downloads the deploy files of
# the newest release from GitHub, as cloud-init did when the server was
# made, replacing them only once every one has arrived. snug.env and
# backup/, with the settings and the backup key, are never touched. Then it
# pulls the new images and restarts what changed. Snug applies any database
# migrations as it starts. docs/deployment.md explains it.

set -eu
cd "$(dirname "$0")"

usage() {
	echo "Usage: sudo snug update [--with-office | --without-office]" >&2
	exit 2
}

# --with-office adds the LibreOffice converter, --without-office removes it (docs/office.md).
office=""
while [ $# -gt 0 ]; do
	case "$1" in
		--with-office) office=on ;;
		--without-office) office=off ;;
		*) usage ;;
	esac
	shift
done

if [ "$(id -u)" != 0 ]; then
	echo "snug update must run as root: sudo snug update" >&2
	exit 1
fi

# The newest release, from where GitHub redirects its latest release, unless
# SNUG_REF is set to a branch or tag. If the lookup fails, stop: never fall back
# to main, which holds code that has not been released.
ref="${SNUG_REF:-}"
if [ -z "$ref" ]; then
	latest=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/Negroni-Venture-Studios/snug/releases/latest || true)
	case "$latest" in
		*/releases/tag/*) ref="${latest##*/}" ;;
		*)
			echo "snug update: could not find Snug's newest release on GitHub. Nothing has changed." >&2
			echo "Try again in a few minutes." >&2
			exit 1
			;;
	esac
fi
echo "Updating to $ref."
base="https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/$ref/deploy"
files="compose.yaml Caddyfile litestream.yml backup.sh restore.sh setup.sh update.sh office.sh snug"

echo "Downloading Snug's server files..."
fresh=".update-$$"
rm -rf "$fresh"
mkdir "$fresh"
for file in $files; do
	if ! curl -fsSL --retry 5 -o "$fresh/$file" "$base/$file"; then
		rm -rf "$fresh"
		echo "snug update: could not download $file. Nothing has changed." >&2
		exit 1
	fi
done
for file in $files; do
	mv "$fresh/$file" "$file"
done
rm -rf "$fresh"
install -m 755 "$(pwd)/snug" /usr/local/bin/snug

# shellcheck source=deploy/office.sh
. ./office.sh
if [ "$office" = on ]; then
	if ! office_memory_ok; then
		echo "This server has $(office_memory_mb) MB of memory. With LibreOffice, Snug needs 2 GB:"
		echo "resize the droplet, or Snug may be stopped for want of memory."
	fi
	office_on
elif [ "$office" = off ]; then
	office_off
elif office_is_on; then
	office_install_gvisor  # a newer pinned release, if this one moved it
fi

echo "Downloading the latest images..."
docker compose pull
echo "Restarting Snug..."
docker compose up --detach --wait
echo
echo "Snug is up to date. To check everything is working: sudo snug doctor"
if ! office_is_on && [ "$office" != off ]; then
	echo "To show Word, PowerPoint and Excel files in the browser: sudo snug update --with-office"
fi
