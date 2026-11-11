#!/bin/sh
# snug restore: bring Snug back from its encrypted backups in the bucket.
#
# Run on the server, from the folder holding compose.yaml (/opt/snug), with
# backup/rclone.conf in place: usually through the snug command, as
#   sudo snug restore [--at 2026-11-05T14:00:00Z]
# docs/backups.md explains when and how.
#
# It stops Snug and its backups, moves any data already on this server aside
# (never deleting it), fetches the uploads and the database replica from the
# bucket, rebuilds the database with Litestream, to the latest moment or the
# one given with --at, and starts everything again.

set -eu
cd "$(dirname "$0")"

at=""
while [ $# -gt 0 ]; do
	case "$1" in
		--at)
			[ $# -ge 2 ] || { echo "snug restore: --at needs a time, like 2026-11-05T14:00:00Z." >&2; exit 2; }
			at="$2"
			shift 2
			;;
		-h | --help)
			echo "Usage: sudo snug restore [--at TIME]"
			echo "Restores Snug from its backups: the latest, or as it was at TIME (UTC, like"
			echo "2026-11-05T14:00:00Z), within the last 7 days."
			exit 0
			;;
		*)
			echo "snug restore: unknown option $1. Try sudo snug restore --help." >&2
			exit 2
			;;
	esac
done

if [ ! -s backup/rclone.conf ]; then
	echo "snug restore: there is no backup/rclone.conf. On a new server, run sudo snug setup" >&2
	echo "and answer yes when it asks whether you are restoring." >&2
	exit 1
fi

# Commands inside the containers, with the data volume mounted. The backup
# container holds the bucket's keys; Litestream rebuilds the database. They
# get no input, or they would swallow the answer typed at the prompt below.
in_backup() { docker compose run --rm --no-deps -T --entrypoint sh backup -c "$1" < /dev/null; }
litestream() { docker compose run --rm --no-deps -T litestream "$@" < /dev/null; }

echo "Checking the bucket..."
if ! in_backup 'rclone lsf snug-backup:database >/dev/null 2>&1 && rclone lsf snug-backup:database | grep -q .'; then
	echo "snug restore: the bucket has no database backup to restore." >&2
	exit 1
fi

echo "Stopping Snug and its backups..."
docker compose stop snug litestream backup
# On a server where Snug has never run, the data volume does not exist yet.
# Created through Snug's own container, it is owned by Snug's user; created
# by the backup container, it would belong to root.
docker compose run --rm --no-deps -T --entrypoint true snug < /dev/null

if in_backup 'test -e /var/lib/snug/snug.sqlite3'; then
	echo
	echo "This server already has a Snug database. Restoring replaces it, and anything"
	echo "uploaded here since. The current data is kept in the data volume, in a folder"
	echo "named previous-<time>."
	printf 'Type replace to go on: '
	read -r answer || answer=""
	if [ "$answer" != "replace" ]; then
		echo "Nothing has changed. Starting Snug again."
		docker compose up --detach
		exit 1
	fi
fi

echo "Fetching the backups..."
# shellcheck disable=SC2016 # expanded inside the container, not here
in_backup '
	set -eu
	cd /var/lib/snug
	kept="previous-$(date -u +%Y%m%dT%H%M%SZ)"
	for name in snug.sqlite3 snug.sqlite3-wal snug.sqlite3-shm .snug.sqlite3-litestream originals branding backup renders; do
		if [ -e "$name" ]; then
			mkdir -p "$kept"
			mv "$name" "$kept/"
		fi
	done
	[ ! -d "$kept" ] || echo "The previous data is in $kept, in the data volume."
	fetch() {  # fetch FOLDER: one that was never backed up (status 3) is skipped
		listed=0
		rclone lsf "snug-backup:$1" > /dev/null 2>&1 || listed=$?
		[ "$listed" = 3 ] && return 0
		[ "$listed" = 0 ] || return "$listed"
		rclone copy "snug-backup:$1" "$2"
	}
	mkdir -p backup
	fetch database backup/database
	fetch originals originals
	fetch branding branding
	rclone cat snug-backup:instance > backup/instance
	chmod -R go= backup originals branding 2>/dev/null || true
'

echo "Rebuilding the database..."
if [ -n "$at" ]; then
	litestream restore -timestamp "$at" /var/lib/snug/snug.sqlite3
else
	litestream restore /var/lib/snug/snug.sqlite3
fi

echo "Starting Snug..."
docker compose up --detach --wait
echo
echo "Restored. Sign in to check, then run: sudo snug doctor"
