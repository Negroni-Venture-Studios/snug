#!/bin/sh
# snug update: bring this server's Snug up to date.
#
# Run on the server as sudo snug update. It downloads the deploy files of
# the newest release from GitHub, and checks each one against the checksums
# published with the release. It pins the release's two images by the
# digests published with it, so Docker refuses any other image under their
# tags (issue #168). The files replace the old ones as a set once every one
# has arrived and been checked, and an interrupted switch is finished by
# the next run (issue #171). snug.env and backup/, with the settings and the
# backup key, are never touched. Then it pulls the new images and restarts
# what changed. Snug applies any database migrations as it starts.
#
# With --files-only it stops once the files are in place: cloud-init and a
# hand installation run it that way, before snug setup. docs/deployment.md
# explains it.

set -eu
cd "$(dirname "$0")"

usage() {
	echo "Usage: sudo snug update [--with-office | --without-office]" >&2
	exit 2
}

# --with-office adds the LibreOffice converter, --without-office removes it (docs/office.md).
office=""
files_only=no
while [ $# -gt 0 ]; do
	case "$1" in
		--with-office) office=on ;;
		--without-office) office=off ;;
		--files-only) files_only=yes ;;
		*) usage ;;
	esac
	shift
done

if [ "$(id -u)" != 0 ]; then
	echo "snug update must run as root: sudo snug update" >&2
	exit 1
fi

files="compose.yaml Caddyfile litestream.yml backup.sh restore.sh setup.sh update.sh office.sh snug snug.env.example"

# An update's files arrive in .update-new, and .update-new/.ready marks that
# all of them have been checked and the switch has begun. If the last update
# stopped part way through it, finish it before anything else, so the files
# are never left a mix of two releases.
new=.update-new
switch() {
	for path in "$new"/*; do
		[ ! -e "$path" ] || mv "$path" "${path##*/}"
	done
	rm -rf "$new"
	install -m 755 "$(pwd)/snug" /usr/local/bin/snug
}
if [ -e "$new/.ready" ]; then
	echo "Finishing the update that was interrupted..."
	switch
fi
rm -rf "$new"

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
# Never backwards (issue #169): releases/latest is the most recently
# published release, not the highest version, so a release published again
# could otherwise move this server back, and a migration the older code does
# not know could then break it. SNUG_REF goes back on purpose.
#
# older A B: whether release A is older than release B. Versions are X.Y.Z,
# or X.Y.Z-rc.N for a candidate, which ranks below its own release.
older() {
	awk -v a="${1#v}" -v b="${2#v}" '
		function key(v, k,   n, f, i) {
			n = split(v, f, /[.-]/)
			if (n != 3 && !(n == 5 && f[4] == "rc")) return 0
			for (i = 1; i <= n; i++) if (i != 4 && f[i] !~ /^[0-9]+$/) return 0
			k[1] = f[1] + 0; k[2] = f[2] + 0; k[3] = f[3] + 0
			k[4] = n == 5 ? f[5] + 0 : 1000000000
			return 1
		}
		BEGIN {
			if (!key(a, x) || !key(b, y)) exit 1
			for (i = 1; i <= 4; i++) if (x[i] != y[i]) exit !(x[i] < y[i])
			exit 1
		}'
}
if [ -z "${SNUG_REF:-}" ]; then
	# The release this server runs: the version its compose.yaml pins for Snug's image.
	running=$(sed -n 's|.*ghcr\.io/negroni-venture-studios/snug:\([0-9][0-9a-z.-]*\).*|\1|p' compose.yaml 2>/dev/null | head -n 1)
	if [ -n "$running" ] && older "$ref" "$running"; then
		echo "snug update: the newest release on GitHub, $ref, is older than this server's Snug, $running." >&2
		echo "Nothing has changed. To install $ref anyway: sudo env SNUG_REF=$ref snug update" >&2
		exit 1
	fi
fi

echo "Updating to $ref."
base="https://raw.githubusercontent.com/Negroni-Venture-Studios/snug/$ref/deploy"
assets="https://github.com/Negroni-Venture-Studios/snug/releases/download/$ref"

# stop MESSAGE...: say why, leave everything as it was, and exit.
stop() {
	rm -rf "$new"
	for line in "$@"; do echo "snug update: $line" >&2; done
	echo "Nothing has changed." >&2
	exit 1
}

echo "Downloading Snug's server files..."
mkdir "$new"
for file in $files; do
	curl -fsSL --retry 5 -o "$new/$file" "$base/$file" || stop "could not download $file."
done

# The checksums and digests published with the release, by the Release
# checksums workflow, as assets of the release on GitHub. Someone able to
# replace an image in GitHub's container registry cannot change them. Every
# release from 1.2.0 on publishes them, so for one, failing to download
# them stops the update. Only a branch, a release candidate, or an older
# release, chosen with SNUG_REF, has none, which GitHub answers with a 404,
# and its files and images are used unchecked. Any other failure stops it.

# publishes_checksums REF: whether REF is a release from 1.2.0 on.
publishes_checksums() {
	printf '%s\n' "${1#v}" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+' || return 1
	case "$1" in v*) ;; *) return 1 ;; esac
	! older "$1" v1.2.0
}
status=$(curl -sSL --retry 5 -o "$new/.sha256" -w '%{http_code}' "$assets/deploy.sha256") || status=failed
checked=yes
if [ "$status" = 404 ] && [ -n "${SNUG_REF:-}" ] && ! publishes_checksums "$ref"; then
	checked=no
elif [ "$status" != 200 ]; then
	stop "could not download the checksums published with $ref." "Try again in a few minutes."
fi
if [ "$checked" = yes ]; then
	for image in snug snug-office; do
		curl -fsSL --retry 5 -o "$new/.$image" "$assets/$image-image.digest" ||
			stop "could not download the digest of $ref's $image image." "Try again in a few minutes."
	done
	for file in $files; do
		want=$(awk -v f="$file" '$2 == f || $2 == "*" f { print $1; exit }' "$new/.sha256")
		have=$(sha256sum "$new/$file" | cut -d ' ' -f 1)
		if [ -z "$want" ] || [ "$want" != "$have" ]; then
			stop "$file does not match the checksum published with $ref."
		fi
	done
	for image in snug snug-office; do
		digest=$(cat "$new/.$image")
		printf '%s\n' "$digest" | grep -Eqx 'sha256:[0-9a-f]{64}' ||
			stop "what is published as the digest of $ref's $image image is not a digest."
		# image: ${SNUG_IMAGE:-ghcr.io/negroni-venture-studios/snug:1.2.0} gains @<digest>.
		sed "s|\(ghcr\.io/negroni-venture-studios/$image:[0-9A-Za-z.-]*\)}|\1@$digest}|" \
			"$new/compose.yaml" > "$new/.compose"
		mv "$new/.compose" "$new/compose.yaml"
		grep -q "/$image:[0-9A-Za-z.-]*@$digest}" "$new/compose.yaml" ||
			stop "could not pin $ref's $image image in compose.yaml."
	done
	rm -f "$new/.sha256" "$new/.snug" "$new/.snug-office"
else
	rm -f "$new/.sha256"
	echo "$ref has no published checksums, so its files and images are used without checking them."
fi

: > "$new/.ready"
switch

if [ "$files_only" = yes ]; then
	echo "Snug's server files are in place."
	exit 0
fi

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
	echo "To show Word, PowerPoint, and Excel files in the browser: sudo snug update --with-office"
fi
