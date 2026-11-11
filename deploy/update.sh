#!/bin/sh
# snug update: bring this server's Snug up to date.
#
# Run on the server as sudo snug update. It downloads the deploy files of
# the newest release from GitHub, as cloud-init did when the server was
# made, replacing them only once every one has arrived. snug.env and
# backup/, with the settings and the backup key, are never touched. Then it pulls the new
# images and restarts what changed. Snug applies any database migrations as
# it starts. docs/deployment.md explains it.

set -eu
cd "$(dirname "$0")"

if [ "$(id -u)" != 0 ]; then
	echo "snug update must run as root: sudo snug update" >&2
	exit 1
fi

# The newest release, from where GitHub redirects its latest release, unless
# SNUG_REF names a branch or tag. Before the first release there is none, so main.
ref="${SNUG_REF:-}"
if [ -z "$ref" ]; then
	latest=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/Negroni-Venture-Studios/snug/releases/latest || true)
	case "$latest" in
		*/releases/tag/*) ref="${latest##*/}" ;;
		*) ref=main ;;
	esac
fi
echo "Updating to $ref."
base="https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/$ref/deploy"
files="compose.yaml Caddyfile litestream.yml backup.sh restore.sh setup.sh update.sh snug"

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

echo "Downloading the latest images..."
docker compose pull
echo "Restarting Snug..."
docker compose up --detach --wait
echo
echo "Snug is up to date. To check everything is working: sudo snug doctor"
