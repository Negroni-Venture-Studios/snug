#!/bin/sh
# Snug's backup sidecar, run in the rclone container by compose.yaml.
#
# Every 5 minutes it copies Litestream's replica of the database, the
# uploaded originals and the branding images to the bucket, through the
# crypt remote "snug-backup" in /config/rclone/rclone.conf, which encrypts
# everything before it leaves the server. Files that are deleted or replaced,
# including the database replica's, are kept under deleted/<date>/ in the
# bucket for 30 days.
#
# It records the outcome in the data volume for `snug doctor`:
#   backup/last-success   the time of the last complete copy (UTC)
#   backup/problem        why the last attempt failed; removed on success

set -u

DATA=/var/lib/snug
STATUS="$DATA/backup"
REMOTE=snug-backup:
EVERY="${SNUG_BACKUP_EVERY:-300}"
KEEP_DELETED_DAYS=30

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

record() {  # record FILE TEXT: written whole, so a reader never sees half
	printf '%s\n' "$2" > "$STATUS/.$1.tmp" && mv "$STATUS/.$1.tmp" "$STATUS/$1"
}

copy() {  # copy FOLDER NAME: mirror a folder of the data volume into the bucket
	[ -d "$DATA/$1" ] || return 0  # nothing there yet
	# Whatever the mirror would delete or overwrite is moved aside instead, so
	# a wiped or replaced folder on the server cannot wipe the bucket.
	rclone sync "$DATA/$1" "$REMOTE$2" \
		--backup-dir "${REMOTE}deleted/$(date -u +%Y-%m-%d)/$2"
}

prune() {  # purge deleted/<date>/ folders older than KEEP_DELETED_DAYS
	cutoff=$(date -u -d "@$(( $(date +%s) - KEEP_DELETED_DAYS * 86400 ))" +%Y%m%d)
	rclone lsf --dirs-only "${REMOTE}deleted" 2>/dev/null | while read -r day; do
		day="${day%/}"
		stamp=$(printf '%s' "$day" | tr -d -)
		case "$stamp" in
			[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
			*) continue ;;
		esac
		if [ "$stamp" -lt "$cutoff" ]; then
			rclone purge "${REMOTE}deleted/$day"
		fi
	done
}

mkdir -p "$STATUS"
while :; do
	if [ ! -s /config/rclone/rclone.conf ]; then
		record problem "Backups are not set up: there is no rclone.conf."
	elif [ -f "$DATA/snug.sqlite3" ] && [ ! -d "$DATA/backup/database" ]; then
		record problem "Litestream has not made a replica of the database."
	elif ! copy backup/database database; then
		record problem "Copying the database to the bucket failed at $(now)."
	elif ! copy originals originals || ! copy branding branding; then
		record problem "Copying files to the bucket failed at $(now)."
	else
		record last-success "$(now)"
		rm -f "$STATUS/problem"
		prune
	fi
	sleep "$EVERY"
done
