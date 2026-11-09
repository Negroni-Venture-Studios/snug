#!/bin/sh
# snug setup: the questions that turn a fresh server into a running Snug.
#
# Run as `sudo snug setup` after cloud-init (cloud-init.yaml) has installed
# Docker and these files in /opt/snug. It asks for the domain and the
# backup bucket, writes snug.env and backup/rclone.conf, and starts Snug.
# Secrets are typed here, on the server, so none of them is ever stored with
# the hosting provider. Email is set up afterwards, in Settings.
# docs/deployment.md explains it.
#
# If backup/rclone.conf is already here, copied from a password manager, and
# its bucket holds backups, setup restores them instead of starting afresh.

set -eu
cd "$(dirname "$0")"

if [ "$(id -u)" != 0 ]; then
	echo "snug setup must run as root: sudo snug setup" >&2
	exit 1
fi

say() { printf '%s\n' "$*"; }

# ask PROMPT [DEFAULT]: the answer, or the default, in $reply.
ask() {
	if [ -n "${2:-}" ]; then printf '%s [%s]: ' "$1" "$2"; else printf '%s: ' "$1"; fi
	read -r reply || reply=""
	[ -n "$reply" ] || reply="${2:-}"
}

# secret PROMPT: like ask, without showing what is typed.
secret() {
	printf '%s: ' "$1"
	stty -echo 2> /dev/null || true
	read -r reply || reply=""
	stty echo 2> /dev/null || true
	printf '\n'
}

# set_env KEY VALUE: replace KEY's line in snug.env, single-quoted so that
# Compose takes $ and spaces literally.
set_env() {
	case "$2" in
		*"'"*)
			say "Snug cannot store a value containing ' (a single quote). Please choose another."
			exit 1
			;;
	esac
	kept=$(grep -v "^$1=" snug.env || true)
	printf "%s\n%s='%s'\n" "$kept" "$1" "$2" > snug.env
}

# rclone inside the backup container, with no input: it would swallow answers.
rclone() { docker compose run --rm --no-deps -T --entrypoint rclone backup "$@" < /dev/null; }

[ -f snug.env ] || : > snug.env
chmod 600 snug.env
[ -e .env ] || ln -s snug.env .env
mkdir -p backup

say "Setting up Snug. Press Enter to accept a suggestion in [brackets]."
say

# ---------------------------------------------------------------- the secret key
if ! grep -q "^SNUG_SECRET_KEY=." snug.env; then
	set_env SNUG_SECRET_KEY "$(openssl rand -base64 64 | tr -d '\n=' | tr '+/' '-_')"
fi

# ---------------------------------------------------------------- the domain
current=$(sed -n "s/^SNUG_DOMAIN='\{0,1\}\([^']*\)'\{0,1\}$/\1/p" snug.env | tail -n 1)
[ "$current" != "room.example.com" ] || current=""  # the example file's
ask "The domain investors will use, like room.example.com" "$current"
domain="$reply"
[ -n "$domain" ] || { say "A domain is needed."; exit 1; }
set_env SNUG_DOMAIN "$domain"

here=$(hostname -I 2> /dev/null | cut -d' ' -f1)
points=$(getent ahostsv4 "$domain" 2> /dev/null | head -n 1 | cut -d' ' -f1 || true)
if [ "$domain" != "localhost" ] && [ -n "$here" ] && [ "$points" != "$here" ]; then
	say
	say "$domain points to ${points:-nowhere yet}, not to this server ($here)."
	say "Caddy cannot get a certificate until its A record points here. You can carry on"
	say "and fix the record; Caddy keeps trying."
	say
fi

ask "Your company's name, as investors know it (you can change it later in Settings)" ""
[ -z "$reply" ] || set_env SNUG_COMPANY_NAME "$reply"

# ---------------------------------------------------------------- backups
say
restoring=no
if [ -s backup/rclone.conf ]; then
	say "Using the backup settings already in backup/rclone.conf."
	if [ -n "$(rclone lsf snug-backup:database 2> /dev/null || true)" ]; then
		restoring=yes
		say "Its bucket holds a Snug's backups: setup will restore them."
	fi
else
	say "Backups go to a storage bucket of your own, encrypted on this server first."
	say "docs/backups.md explains how to create a DigitalOcean Spaces bucket and a key."
	ask "Spaces region" "lon1"
	region="$reply"
	ask "Bucket name" ""
	bucket="$reply"
	ask "Access key" ""
	access="$reply"
	secret "Secret key"
	secret_key="$reply"
	if [ -z "$bucket" ] || [ -z "$access" ] || [ -z "$secret_key" ]; then
		say "Without a bucket there are no backups. Run sudo snug setup again to add one."
	else
		password=$(rclone obscure "$(openssl rand -base64 32)")
		salt=$(rclone obscure "$(openssl rand -base64 32)")
		umask 077
		cat > backup/rclone.conf <<-CONF
			# Snug's backups: the bucket, and the key that encrypts everything in it.
			# Keep a copy of this whole file in your password manager. Without it,
			# nobody can read the backups, including you.

			[bucket]
			type = s3
			provider = DigitalOcean
			access_key_id = $access
			secret_access_key = $secret_key
			endpoint = $region.digitaloceanspaces.com
			acl = private
			no_check_bucket = true

			[snug-backup]
			type = crypt
			remote = bucket:$bucket/snug
			password = $password
			password2 = $salt
		CONF
		umask 022
		say
		say "Copy everything between the lines into your password manager now. It is"
		say "the only way to read the backups if this server is lost."
		say "------------------------------------------------------------------------"
		cat backup/rclone.conf
		say "------------------------------------------------------------------------"
		answer=""
		while [ "$answer" != "saved" ]; do
			printf 'Type saved once it is in your password manager: '
			read -r answer || exit 1
		done
	fi
fi
if [ -s backup/rclone.conf ]; then
	# Readable by the backup container's user alone.
	chown 10001:10001 backup/rclone.conf
	chmod 600 backup/rclone.conf
fi

# ---------------------------------------------------------------- start
say
say "Checking the settings..."
# In a folder of its own, so the check creates no database in the real one.
docker compose run --rm --no-deps -T --env SNUG_DATA_DIR=/tmp/check snug snug check < /dev/null

if [ "$restoring" = yes ]; then
	sh ./restore.sh < /dev/null
	say
	say "Snug is restored at https://$domain. Sign in as before."
else
	say "Starting Snug..."
	docker compose up --detach --wait
	say
	if [ -t 0 ]; then
		say "Now the first admin."
		docker compose exec snug snug createadmin
	else
		say "Create the first admin with: sudo snug createadmin"
	fi
	say
	say "Snug is running at https://$domain. Sign in at https://$domain/admin/"
	say "Then set up email in Settings, under Email: docs/email.md explains how."
fi
say "Run sudo snug doctor at any time to check everything is working."
